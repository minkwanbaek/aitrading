import contextlib
import datetime as dt
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import broker
import collection
import managed
import research
import voting
import strategy
from test_voting import ballots, frozen


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        for target, value in [('ROOT', self.root), ('LATEST', self.root / '.cache' / 'collection.json')]:
            patch = mock.patch.object(collection, target, value)
            patch.start()
            self.addCleanup(patch.stop)

    def publish_fixture(self):
        data = frozen()
        path = self.root / '.cache' / 'voting' / data['input_id'] / 'input.json'
        managed.save(path, data)
        with mock.patch.object(voting, 'prepare', return_value=(path, data)):
            collection.publish()
        return path, data

    def test_selection_reads_complete_artifact_without_network_or_writes(self):
        path, data = self.publish_fixture()
        before = path.read_bytes(), collection.LATEST.read_bytes()
        with (mock.patch.object(broker, 'account', side_effect=AssertionError('선정 단계 외부 조회 금지')),
              mock.patch.object(voting, 'prepare', side_effect=AssertionError('자료 재수집 금지')),
              mock.patch.object(managed, 'save', side_effect=AssertionError('입력 변경 금지'))):
            found, snapshot = collection.load_current()
            self.assertEqual((found, snapshot), (path, data))
        self.assertEqual(before, (path.read_bytes(), collection.LATEST.read_bytes()))

    def test_unpublished_input_cannot_be_selected(self):
        data = frozen()
        managed.save(self.root / '.cache' / 'voting' / data['input_id'] / 'input.json', data)
        with self.assertRaises(managed.GuardError):
            collection.load_current()

    def test_failed_collection_preserves_previous_complete_receipt(self):
        self.publish_fixture()
        before = collection.LATEST.read_bytes()
        with mock.patch.object(voting, 'prepare', side_effect=managed.GuardError('NH 수집 실패')):
            with self.assertRaises(managed.GuardError):
                collection.publish()
        self.assertEqual(before, collection.LATEST.read_bytes())

    def test_all_quotes_failed_never_marks_collection_complete(self):
        data = frozen()
        data['stocks']['000000']['error'] = '시세 오류'
        data['input_id'] = voting.digest({k: v for k, v in data.items() if k != 'input_id'})
        with mock.patch.object(voting, 'prepare', return_value=(Path('unused'), data)):
            with self.assertRaises(managed.GuardError):
                collection.publish()
        self.assertFalse(collection.LATEST.exists())

    def test_selection_rejects_snapshot_older_than_ninety_minutes_even_before_plan_expiry(self):
        path, data = self.publish_fixture()
        created = research.timestamp(data['created_at']).astimezone(voting.SEOUL).replace(hour=9, minute=0, second=0)
        data['created_at'] = created.isoformat()
        data['expires_at'] = (created + dt.timedelta(hours=4)).isoformat()
        data['input_id'] = voting.digest({k: v for k, v in data.items() if k != 'input_id'})
        path = self.root / '.cache' / 'voting' / data['input_id'] / 'input.json'
        managed.save(path, data)
        managed.save(collection.LATEST, {'version': 1, 'input_id': data['input_id'],
                                        'collected_at': data['created_at'], 'published_at': data['created_at']})
        with self.assertRaisesRegex(managed.GuardError, '90분'):
            collection.load_current(created + dt.timedelta(minutes=91))

    def test_tampering_receipt_path_or_snapshot_blocks_selection(self):
        path, data = self.publish_fixture()
        receipt = voting.read(collection.LATEST)
        receipt['input_id'] = '../outside'
        managed.save(collection.LATEST, receipt)
        with self.assertRaises(managed.GuardError):
            collection.load_current()
        path, data = self.publish_fixture()
        data['available_krw'] = 999999999
        managed.save(path, data)
        with self.assertRaises(managed.GuardError):
            collection.load_current()

    def test_cli_aggregation_requires_current_published_input(self):
        path, data = self.publish_fixture()
        other = frozen(2)
        other_path = self.root / 'other.json'
        managed.save(other_path, other)
        with (mock.patch('sys.argv', ['voting.py', '--aggregate', str(other_path), 'A', 'B', 'C']),
              mock.patch.object(voting, 'PLAN', self.root / 'plan.json')):
            with self.assertRaisesRegex(managed.GuardError, '수집 완료본과 다른'):
                voting.main()
        self.assertFalse((self.root / 'plan.json').exists())

    @mock.patch.object(strategy, 'QUANT_ENABLED', False)
    def test_published_input_and_three_votes_produce_plan_without_fetching_data_again(self):
        path, data = self.publish_fixture()
        files = []
        for vote in ballots(data):
            target = self.root / (vote['voter'] + '.json')
            managed.save(target, vote)
            files.append(str(target))
        target = self.root / 'plan.json'
        with (mock.patch('sys.argv', ['voting.py', '--aggregate', str(path), *files]),
              mock.patch.object(voting, 'ROOT', self.root), mock.patch.object(voting, 'PLAN', target),
              mock.patch.object(broker, 'account', side_effect=AssertionError('외부 조회 금지')),
              contextlib.redirect_stdout(io.StringIO())):
            voting.main()
        self.assertEqual(voting.load_plan(target)['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
