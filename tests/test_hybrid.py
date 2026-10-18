import copy
import contextlib
import datetime as dt
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import broker
import cycle
import collection
import hybrid
import managed
import performance
import quant
import strategy
import voting
from test_quant import fixture, rehash
from test_voting import ballots


def shared(count=2):
    data = fixture(count)
    data['quant_context'] = quant.context(data)
    return rehash(data)


class HybridTests(unittest.TestCase):
    def test_cli_uses_same_published_input_and_real_vote_files_without_fetching(self):
        data = shared()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / '.cache' / 'voting' / data['input_id'] / 'input.json'
            managed.save(source, data)
            receipt = root / '.cache' / 'collection.json'
            managed.save(receipt, dict(version=1, input_id=data['input_id'], collected_at=data['created_at'],
                                      published_at=data['created_at']))
            files = []
            for vote in ballots(data):
                path = root / (vote['voter'] + '.json')
                managed.save(path, vote)
                files.append(str(path))
            target = root / '.cache' / 'decision-plan.json'
            with (mock.patch.object(collection, 'ROOT', root), mock.patch.object(collection, 'LATEST', receipt),
                  mock.patch.object(voting, 'ROOT', root), mock.patch.object(voting, 'PLAN', target),
                  mock.patch.object(performance, 'ROOT', root / 'evaluation'),
                  mock.patch.object(broker, 'account', side_effect=AssertionError('집계 중 계좌 조회 금지')),
                  mock.patch.object(broker, 'order', side_effect=AssertionError('집계 중 주문 금지')),
                  mock.patch('sys.argv', ['voting.py', '--aggregate', str(source), *files]),
                  contextlib.redirect_stdout(io.StringIO())):
                voting.main()
                plan = hybrid.load_plan()
                self.assertEqual(plan['kind'], 'quant_vote')
                self.assertEqual(len(plan['votes']), 3)

    def test_positive_quant_still_needs_two_buy_votes(self):
        data = shared()
        votes = ballots(data, ('buy', 'hold', 'hold'))
        plan = hybrid.aggregate(data, votes)
        self.assertEqual(plan['actions']['000001']['decision'], 'hold')
        hybrid.validate_plan(plan)
        votes = ballots(data, ('buy', 'buy', 'hold'))
        self.assertEqual(hybrid.aggregate(data, votes)['actions']['000001']['decision'], 'buy')

    def test_missing_failed_or_reused_session_never_becomes_a_hold_vote(self):
        data = shared()
        cases = [ballots(data)[:2]]
        for key, value in [('status', 'error'), ('session_id', 'independent-A')]:
            votes = ballots(data)
            votes[1][key] = value
            cases.append(votes)
        for votes in cases:
            plan = hybrid.aggregate(data, votes)
            self.assertEqual(plan['status'], 'blocked')
            with self.assertRaises(managed.GuardError):
                hybrid.validate_plan(plan)

    def test_three_way_split_is_not_a_buy(self):
        data = shared()
        plan = hybrid.aggregate(data, ballots(data, ('buy', 'sell', 'hold')))
        self.assertEqual(plan['actions']['000001']['decision'], 'hold')
        self.assertEqual(plan['actions']['000001']['status'], 'split')

    def test_even_three_ai_cannot_buy_negative_score(self):
        data = fixture(1)
        data['stocks']['000000']['bars'] = copy.deepcopy(data['benchmark']['bars'])
        data['quant_context'] = quant.context(data)
        rehash(data)
        plan = hybrid.aggregate(data, ballots(data, ('buy', 'buy', 'buy')))
        self.assertEqual(plan['actions']['000000']['decision'], 'hold')

    def test_ai_can_sell_held_company_before_review_despite_positive_score(self):
        data = shared(1)
        data['stocks']['000000'].update(held=True, review_due=False)
        data['quant_context'] = quant.context(data)
        rehash(data)
        plan = hybrid.aggregate(data, ballots(data, ('sell', 'sell', 'hold')))
        self.assertEqual(plan['actions']['000000']['decision'], 'sell')

    def test_allocation_uses_quant_rank_and_conservative_winning_amount(self):
        data = shared(2)
        data['available_slots'] = 1
        rehash(data)
        votes = ballots(data)
        votes[1]['decisions']['000001'].update(budget_krw=110000, max_buy_price=49000)
        plan = hybrid.aggregate(data, votes)
        self.assertEqual(plan['actions']['000001']['decision'], 'buy')
        self.assertEqual(plan['actions']['000001']['budget_krw'], 110000)
        self.assertEqual(plan['actions']['000001']['max_buy_price'], 49000)
        self.assertEqual(plan['actions']['000000']['decision'], 'hold')
        for vote in votes[:2]:
            vote['decisions']['000001'] = {'decision': 'hold', 'reason': '사업 위험으로 보류'}
        plan = hybrid.aggregate(data, votes)
        self.assertEqual(plan['actions']['000000']['decision'], 'buy')

    def test_fake_or_missing_shared_calculation_is_blocked(self):
        for mutate in (lambda d: d.pop('quant_context'),
                       lambda d: d['quant_context']['metrics']['000001'].update(score_pp=99)):
            data = shared()
            mutate(data)
            rehash(data)
            self.assertEqual(hybrid.aggregate(data, ballots(data))['status'], 'blocked')

    def test_quant_only_and_old_vote_plans_cannot_execute(self):
        data = shared()
        for plan in (quant.build(data), voting.aggregate(data, ballots(data))):
            with self.assertRaises(managed.GuardError):
                hybrid.validate_plan(plan)

    def test_review_needs_two_explicit_renewals_and_positive_score(self):
        data = shared(1)
        stock = data['stocks']['000000']
        stock.update(held=True, review_due=True)
        stock['company']['sources'] = [dict(url='https://example.com/' + purpose, purpose=purpose,
                                           checked_at=data['created_at'], primary=True, verified=True)
                                      for purpose in ('business', 'financial')]
        data['quant_context'] = quant.context(data)
        rehash(data)
        votes = ballots(data, ('hold', 'hold', 'hold'))
        for vote in votes[:2]:
            vote['decisions']['000000'].update(review='renew', review_sources=[s['url'] for s in stock['company']['sources']])
        self.assertEqual(hybrid.aggregate(data, votes)['actions']['000000']['review'], 'renew')
        del votes[1]['decisions']['000000']['review']
        self.assertNotIn('review', hybrid.aggregate(data, votes)['actions']['000000'])

    def test_inactive_technical_indicators_do_not_request_extra_votes(self):
        data = shared(1)
        previous = hybrid.aggregate(data, ballots(data, ('hold', 'hold', 'hold')))
        fresh = copy.deepcopy(data)
        fresh['stocks']['000000']['signals'] = {'rsi_over_75': True}
        self.assertEqual(cycle.inspect(fresh, previous)['decision'], 'skip')
        fresh['quant_context']['metrics']['000000']['signal_date'] = '20261019'
        self.assertEqual(cycle.inspect(fresh, previous)['decision'], 'request')

    def test_review_waiting_is_not_repeated_hourly_without_new_evidence(self):
        data = shared(1)
        data['stocks']['000000'].update(held=True, review_due=True)
        data['quant_context'] = quant.context(data)
        rehash(data)
        previous = hybrid.aggregate(data, ballots(data, ('hold', 'hold', 'hold')))
        self.assertEqual(cycle.inspect(data, previous)['decision'], 'skip')

    def test_quant_draft_and_real_votes_are_separate_immutable_records(self):
        data = shared()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(performance, 'ROOT', Path(directory)):
            draft = quant.build(data)
            voted = hybrid.aggregate(data, ballots(data))
            performance.record_plan(draft)
            performance.record_plan(voted)
            location = performance.folder(data['account_id'])
            self.assertEqual(len(list((location / 'quant-drafts').glob('*.json'))), 1)
            self.assertEqual(len(list((location / 'decisions').glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
