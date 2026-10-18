import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import broker
import managed
import research
import strategy
import trade
import voting


def frozen(count=1):
    clock = managed.now()
    data = {'version': 1, 'created_at': clock.isoformat(),
            'expires_at': (clock + dt.timedelta(minutes=30)).isoformat(),
            'account_id': managed.state_path('test').stem, 'policy': voting.policy(),
            'available_krw': 750000, 'available_slots': 5,
            'stocks': {f'{i:06}': {'price': 50000, 'held': False, 'eligible': True,
                                  'buy_room': 200000, 'company': {'sources': []},
                                  'news': [{'url': 'https://example.com/news', 'summary': '검증 자료'}]}
                       for i in range(count)}}
    data['input_id'] = voting.digest(data)
    return data


def ballots(data, choices=('buy', 'buy', 'hold')):
    return [{'voter': label, 'session_id': 'independent-' + label,
             'input_id': data['input_id'], 'status': 'ok',
             'decisions': {code: dict(decision=choice, reason='동일 자료를 독립 검토',
                                      **({'budget_krw': 150000, 'max_buy_price': 50000} if choice == 'buy' else {}))
                           for code in data['stocks']}}
            for label, choice in zip(voting.VOTERS, choices)]


class VotingTests(unittest.TestCase):
    def test_two_agree_and_conservative_amount_and_price(self):
        data = frozen()
        votes = ballots(data)
        votes[1]['decisions']['000000'].update(budget_krw=120000, max_buy_price=49000)
        plan = voting.aggregate(data, votes)
        call = plan['actions']['000000']
        self.assertEqual(call['decision'], 'buy')
        self.assertEqual(call['budget_krw'], 120000)
        self.assertEqual(call['max_buy_price'], 49000)
        voting.validate_plan(plan, 'test')

    def test_three_way_split_and_majority_hold(self):
        data = frozen()
        for choices, status in [(('buy', 'sell', 'hold'), 'split'), (('buy', 'hold', 'hold'), 'consensus')]:
            call = voting.aggregate(data, ballots(data, choices))['actions']['000000']
            self.assertEqual(call['decision'], 'hold')
            self.assertEqual(call['status'], status)

    def test_missing_failed_and_invalid_votes_are_not_hold_votes(self):
        data = frozen()
        variants = [ballots(data)[:2]]
        for field, value in [('status', 'error'), ('input_id', 'wrong'), ('session_id', 'independent-A')]:
            votes = ballots(data)
            votes[1][field] = value
            variants.append(votes)
        votes = ballots(data)
        del votes[1]['decisions']['000000']
        variants.append(votes)
        for votes in variants:
            plan = voting.aggregate(data, votes)
            self.assertEqual(plan['status'], 'blocked')
            self.assertEqual(plan['actions']['000000']['status'], 'blocked')
            self.assertTrue(plan['errors'])
            with self.assertRaises(managed.GuardError):
                voting.validate_plan(plan, 'test')

    def test_ai_cannot_change_policy_or_exceed_single_stock_amount(self):
        data = frozen()
        for modification in [{'STOP_LOSS_PCT': -10}, {'budget_krw': 200001}, {'max_buy_price': 0}]:
            votes = ballots(data)
            votes[0]['decisions']['000000'].update(modification)
            self.assertEqual(voting.aggregate(data, votes)['status'], 'blocked')

    def test_portfolio_budget_slots_and_reserve(self):
        data = frozen(15)
        plan = voting.aggregate(data, ballots(data))
        buys = [a for a in plan['actions'].values() if a['decision'] == 'buy']
        self.assertLessEqual(len(buys), 5)
        self.assertLessEqual(sum(a['budget_krw'] * 1.01 for a in buys), 750000)
        data['available_slots'] = 1
        self.assertEqual(sum(a['decision'] == 'buy' for a in voting.aggregate(data, ballots(data))['actions'].values()), 1)
        data['available_krw'] = 0
        self.assertTrue(all(a['decision'] == 'hold' for a in voting.aggregate(data, ballots(data))['actions'].values()))

    def test_watch_missing_news_and_held_cannot_buy(self):
        for field, value in [('eligible', False), ('news', []), ('held', True), ('error', '시세 실패')]:
            data = frozen()
            data['stocks']['000000'][field] = value
            self.assertEqual(voting.aggregate(data, ballots(data))['actions']['000000']['decision'], 'hold')

    def test_unheld_sell_blocked_and_held_sell_majority(self):
        data = frozen()
        votes = ballots(data, ('sell', 'sell', 'hold'))
        self.assertEqual(voting.aggregate(data, votes)['actions']['000000']['decision'], 'hold')
        data['stocks']['000000']['held'] = True
        self.assertEqual(voting.aggregate(data, votes)['actions']['000000']['decision'], 'sell')

    def test_expiry_account_environment_policy_and_hash(self):
        data = frozen()
        plan = voting.aggregate(data, ballots(data))
        with self.assertRaises(managed.GuardError):
            voting.validate_plan(plan, 'other')
        with mock.patch.object(strategy, 'STOP_LOSS_PCT', -5):
            with self.assertRaises(managed.GuardError):
                voting.validate_plan(plan, 'test')
        with mock.patch.object(broker, 'MOCK', not broker.MOCK):
            with self.assertRaises(managed.GuardError):
                voting.validate_plan(plan, 'test')
        with self.assertRaises(managed.GuardError):
            voting.validate_plan(plan, 'test', research.timestamp(data['expires_at']))
        plan['input']['stocks']['000000']['price'] = 1
        with self.assertRaises(managed.GuardError):
            voting.validate_plan(plan, 'test')

    def test_cannot_edit_aggregated_action(self):
        data = frozen()
        plan = voting.aggregate(data, ballots(data))
        plan['actions']['000000']['budget_krw'] = 200000
        with self.assertRaises(managed.GuardError):
            voting.validate_plan(plan, 'test')

    def test_review_renew_requires_two_hold_votes_and_official_sources(self):
        data = frozen()
        stock = data['stocks']['000000']
        stock.update(held=True, review_due=True, company={'sources': [
            {'url': 'https://example.com/ir', 'primary': True, 'verified': True, 'purpose': 'financial', 'checked_at': data['created_at']},
            {'url': 'https://example.com/business', 'primary': True, 'verified': True, 'purpose': 'business', 'checked_at': data['created_at']}]})
        votes = ballots(data, ('hold', 'hold', 'sell'))
        for vote in votes[:2]:
            vote['decisions']['000000'].update(review='renew', review_sources=['https://example.com/ir', 'https://example.com/business'])
        self.assertEqual(voting.aggregate(data, votes)['actions']['000000']['review'], 'renew')
        votes[1]['decisions']['000000']['review_sources'] = ['https://wrong.com/report']
        self.assertNotIn('review', voting.aggregate(data, votes)['actions']['000000'])

    def test_same_plan_cannot_rebuy_after_cancellation_or_stop_loss(self):
        state = {'orders': [{'code': '000000', 'plan_id': 'used', 'closed': True}], 'halted': ''}
        m = {'code': '000000', 'name': '검증', 'price': 50000}
        with mock.patch.object(broker, 'order') as send:
            result = managed.submit('test', state, Path('unused'), {}, {}, m, 'buy', {'plan_id': 'used'})
            send.assert_not_called()
            self.assertIn('재주문', result['한 일'])

    def test_price_ceiling_blocks_chasing(self):
        with mock.patch.object(broker, 'order') as send:
            result = managed.submit('test', {'orders': []}, Path('unused'), {}, {},
                                    {'code': '000000', 'name': '검증', 'price': 51000}, 'buy',
                                    {'plan_id': 'new', 'max_buy_price': 50000})
            send.assert_not_called()
            self.assertIn('상한 가격', result['한 일'])

    def test_plan_expiring_during_telegram_approval_never_sends(self):
        clock = managed.now()
        call = {'plan_id': 'new', 'max_buy_price': 50000, 'budget_krw': 150000,
                'expires_at': (clock + dt.timedelta(minutes=1)).isoformat()}
        def approval(*args):
            clock_mock.return_value = clock + dt.timedelta(minutes=2)
            return ''
        with (mock.patch.object(managed, 'now', return_value=clock) as clock_mock,
              mock.patch.object(managed, 'buy_room', return_value=200000),
              mock.patch.object(broker, 'buyable', return_value=10),
              mock.patch.object(trade, 'approved', side_effect=approval),
              mock.patch.object(broker, 'order') as send):
            result = managed.submit('test', {'orders': []}, Path('unused'), {}, {},
                                    {'code': '000000', 'name': '검증', 'price': 50000}, 'buy', call)
            send.assert_not_called()
            self.assertIn('승인 대기 중', result['한 일'])

    def test_engine_rejects_flat_single_opinion(self):
        with tempfile.TemporaryDirectory() as folder:
            with (mock.patch.object(managed, 'state_path', return_value=Path(folder) / 'state.json'),
                  mock.patch.object(broker, 'domestic_snapshot', return_value={'equity': 500000000, 'cash_d2': 500000000}),
                  mock.patch.object(broker, 'holdings', return_value={}),
                  mock.patch.object(broker, 'domestic_orders', return_value=[]),
                  mock.patch.object(research, 'load', return_value=None),
                  mock.patch.object(research, 'status', return_value={}),
                  mock.patch.object(trade, 'kr_open', return_value=True),
                  mock.patch.object(broker, 'order') as send):
                result = managed.run('test', '--do', {'000000': {'decision': 'buy', 'reason': '단독', 'as_of': managed.now().isoformat()}})
                send.assert_not_called()
                self.assertEqual(result['투표']['상태'], '주문 보류')

    @mock.patch.object(strategy, 'QUANT_ENABLED', False)
    def test_valid_consensus_flows_through_engine_and_reserves_plan_before_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with mock.patch.object(managed, 'state_path', return_value=path):
                data = frozen()
                plan = voting.aggregate(data, ballots(data))
                def send(act, action, code, qty, price):
                    saved = voting.read(path)
                    self.assertEqual(saved['orders'][-1]['plan_id'], plan['plan_id'])
                    self.assertTrue(saved['orders'][-1]['uncertain'])
                    self.assertEqual((action, code, qty, price), ('buy', '000000', 3, 50000))
                    return 123
                with (mock.patch.object(broker, 'domestic_snapshot', return_value={'equity': 500000000, 'cash_d2': 500000000}),
                      mock.patch.object(broker, 'holdings', return_value={}),
                      mock.patch.object(broker, 'domestic_orders', return_value=[]),
                      mock.patch.object(broker, 'buyable', return_value=100),
                      mock.patch.object(broker, 'order', side_effect=send) as order,
                      mock.patch.object(research, 'load', return_value={'companies': [{'code': '000000'}]}),
                      mock.patch.object(research, 'status', return_value={}),
                      mock.patch.object(research, 'eligible', return_value=True),
                      mock.patch.object(trade, 'context', return_value={'code': '000000', 'name': '검증', 'price': 50000, 'held': False}),
                      mock.patch.object(strategy, 'decide', return_value=('buy', '검증된 합의')),
                      mock.patch.object(trade, 'kr_open', return_value=True),
                      mock.patch.object(trade, 'approved', return_value=''),
                      mock.patch.object(managed.telegram, 'notify')):
                    result = managed.run('test', '--do', plan)
                    self.assertEqual(result['투표']['유효응답'], 3)
                    order.assert_called_once()
                    managed.run('test', '--do', plan)
                    order.assert_called_once()  # 조회 응답이 늦어도 같은 결정을 재전송하지 않음.

    def test_invalid_plan_does_not_block_four_percent_stop_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            managed.save(path, {'version': 1, 'baseline_equity': 500000000,
                                'last_equity': 499995000, 'last_cash': 499900000,
                                'halted': '', 'positions': {}, 'orders': [
                                    {'code': '000000', 'side': 'buy', 'qty': 2, 'price': 50000, 'day': '20261018',
                                     'filled': 2, 'amount': 100000, 'closed': True}]})
            held = {'000000': {'name': '검증', 'qty': 2, 'price': 47500, 'avg': 50000, 'pnl_pct': -5.0}}
            m = dict(held['000000'], code='000000', held=True, market='kr', currency='KRW')
            with (mock.patch.object(managed, 'state_path', return_value=path),
                  mock.patch.object(broker, 'domestic_snapshot', return_value={'equity': 499995000, 'cash_d2': 499900000}),
                  mock.patch.object(broker, 'holdings', return_value=held),
                  mock.patch.object(broker, 'domestic_orders', return_value=[]),
                  mock.patch.object(broker, 'sellable', return_value=2),
                  mock.patch.object(broker, 'order', return_value=123) as send,
                  mock.patch.object(research, 'load', return_value=None),
                  mock.patch.object(research, 'status', return_value={}),
                  mock.patch.object(trade, 'context', return_value=m),
                  mock.patch.object(trade, 'kr_open', return_value=True),
                  mock.patch.object(trade, 'approved', return_value=''),
                  mock.patch.object(managed.telegram, 'notify')):
                result = managed.run('test', '--do', {})
                self.assertEqual(result['투표']['상태'], '주문 보류')
                send.assert_called_once_with('test', 'sell', '000000', 2, 47500)


if __name__ == '__main__':
    unittest.main()
