import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import flow
import broker
import managed
import voting
from test_hybrid import shared
from test_voting import ballots


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / '.cache' / 'multiflow'
        self.patches = [mock.patch.object(flow, 'ROOT', self.root), mock.patch.object(flow, 'HOME', self.home),
                        mock.patch.object(flow.managed, 'cycle_lock', __import__('contextlib').nullcontext),
                        mock.patch.object(broker, 'MOCK', True)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = Path(flow.start('verification')['folder'])

    def receipt(self, stage, result, role=None):
        role = role or stage
        job = {'role': role, 'stage': stage, 'run_id': self.folder.name}
        managed.save(self.folder / (stage + '.job.json'), job)
        managed.save(self.folder / (stage + '.done.json'), {
            'run_id': self.folder.name, 'role': role, 'status': 'done', 'job_hash': voting.digest(job), 'result': result})

    def test_active_round_blocks_overlap_and_expired_round_cannot_issue(self):
        with self.assertRaises(managed.GuardError):
            flow.start('mock')
        path = self.folder / 'manifest.json'
        meta = voting.read(path)
        meta['expires_at'] = (managed.now() - dt.timedelta(seconds=1)).isoformat()
        managed.save(path, meta)
        with self.assertRaises(managed.GuardError):
            flow.issue(self.folder, 'risk')

    def test_role_mismatch_and_external_paths_rejected(self):
        job = flow.issue(self.folder, 'risk')['job_file']
        with self.assertRaises(managed.GuardError):
            flow.inspect(job, 'data')
        with self.assertRaises(managed.GuardError):
            flow.inspect(self.root / 'foreign.json', 'data')

    def test_next_stage_needs_matching_predecessor_receipt(self):
        with self.assertRaises(FileNotFoundError):
            flow.issue(self.folder, 'data')
        self.receipt('risk', {}, 'execution')
        path = self.folder / 'risk.job.json'
        managed.save(path, {'role': 'execution', 'stage': 'risk', 'run_id': 'other'})
        with self.assertRaises(managed.GuardError):
            flow.issue(self.folder, 'data')

    def test_verification_risk_cannot_scan_or_repeat_side_effect(self):
        path = flow.issue(self.folder, 'risk')['job_file']
        with mock.patch.object(flow, 'command') as command:
            flow.run(path, 'execution')
            flow.run(path, 'execution')
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[-1], '--preview')

    def test_failed_side_effect_is_not_retried_as_new_work(self):
        path = flow.issue(self.folder, 'risk')['job_file']
        with mock.patch.object(flow, 'command', side_effect=managed.GuardError('timeout')) as command:
            with self.assertRaises(managed.GuardError):
                flow.run(path, 'execution')
            with self.assertRaises(managed.GuardError):
                flow.run(path, 'execution')
        self.assertEqual(command.call_count, 1)
        self.assertTrue((self.folder / 'risk.failed.json').exists())

    def test_changed_vote_and_duplicate_sessions_block_aggregation(self):
        data = shared()
        votes = ballots(data, ('hold', 'hold', 'hold'))
        for vote in votes:
            role = vote['voter']
            managed.save(self.folder / (role + '.vote.json'), vote)
            self.receipt(role, {'input_id': data['input_id'], 'vote_hash': voting.digest(vote)})
        with mock.patch.object(flow, 'bound', return_value=(None, data)):
            self.assertEqual(flow.aggregate_check(self.folder)['status'], 'ready')
            changed = copy.deepcopy(votes[0])
            changed['session_id'] = votes[1]['session_id']
            managed.save(self.folder / 'A.vote.json', changed)
            with self.assertRaises(managed.GuardError):
                flow.aggregate_check(self.folder)
            self.receipt('A', {'input_id': data['input_id'], 'vote_hash': voting.digest(changed)})
            with self.assertRaises(managed.GuardError):
                flow.aggregate_check(self.folder)

    def test_verification_aggregate_does_not_publish_trade_plan(self):
        data = shared()
        import hybrid
        plan = hybrid.aggregate(data, ballots(data, ('hold', 'hold', 'hold')))
        self.receipt('quant', {'input_id': data['input_id']})
        target = self.root / 'production-plan.json'
        with mock.patch.object(flow, 'aggregate_check', return_value=plan), mock.patch.object(voting, 'PLAN', target):
            result = flow.aggregate(self.folder)
        self.assertTrue(result['verification'])
        self.assertFalse(target.exists())
        self.assertTrue((self.folder / 'aggregate.json').exists())

    def test_changed_current_snapshot_is_not_replaced_silently(self):
        data = shared()
        source = self.root / '.cache' / 'voting' / data['input_id'] / 'input.json'
        managed.save(source, data)
        managed.save(self.folder / 'input.json', {'input_id': data['input_id'], 'input_file': str(source)})
        with mock.patch.object(flow.collection, 'load_current', return_value=(source, {'input_id': 'different'})):
            with self.assertRaises(managed.GuardError):
                flow.bound(self.folder)

    def test_verification_execute_never_calls_order_engine(self):
        job = {'stage': 'execute', 'role': 'execution', 'run_id': self.folder.name, 'mode': 'verification'}
        with (mock.patch.object(flow, 'inspect', return_value=(self.folder, job)),
              mock.patch.object(flow, 'command', side_effect=AssertionError('주문 엔진 호출 금지'))):
            result = flow.run(self.folder / 'execute.job.json', 'execution')
        self.assertTrue(result['result']['no_orders'])

    def test_live_environment_cannot_start_or_run_role_jobs(self):
        path = flow.issue(self.folder, 'risk')['job_file']
        with mock.patch.object(broker, 'MOCK', False):
            with self.assertRaises(managed.GuardError):
                flow.run(path, 'execution')
            with self.assertRaises(managed.GuardError):
                flow.start('mock')


if __name__ == '__main__':
    unittest.main()
