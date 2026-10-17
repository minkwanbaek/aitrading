import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import broker
import managed
import research
import strategy
import trade


def state():
    return {'version': 1, 'baseline_equity': 500_000_000,
            'last_equity': 500_000_000, 'last_cash': 500_000_000,
            'orders': [], 'positions': {}, 'halted': ''}


def order(code='108490', qty=2, side='buy', closed=False):
    return {'code': code, 'qty': qty, 'price': 50000, 'side': side,
            'filled': 0, 'cancelled': 0, 'amount': 0, 'day': '20261019',
            'closed': closed, 'uncertain': False}


def catalog():
    return {'checked_at': managed.now().isoformat(), 'themes': ['로봇', '전력', '장비'],
            'companies': [{'code': f'{n:06}', 'name': f'기업{n}', 'theme': ['로봇', '전력', '장비'][n % 3],
                           'role': '부품', 'linkage': '공식 제품 소개', 'risk': '수요 둔화', 'status': 'watch',
                           'sources': [{'url': 'https://example.com/product', 'primary': True, 'summary': '제품'}]}
                          for n in range(15)]}


class BudgetTests(unittest.TestCase):
    def test_large_mock_balance_cannot_expand_one_million_budget(self):
        s = state()
        snap = {'equity': 500_000_000, 'cash_d2': 500_000_000}
        self.assertEqual(managed.equity(s, snap), 1_000_000)
        self.assertEqual(managed.buy_room(s, snap, {}, '108490'), 200_000)
        holdings = {f'{i:06}': {'qty': 1, 'price': 150000} for i in range(5)}
        self.assertEqual(managed.buy_room(s, snap, holdings, '108490'), 0)

    def test_pending_orders_use_budget_and_slots_and_block_same_stock(self):
        s = state()
        s['orders'] = [order(f'{i:06}', qty=4) for i in range(4)]
        snap = {'equity': 500_000_000}
        self.assertEqual(managed.buy_room(s, snap, {}, '108490'), 0)
        s['orders'] = [order('108490')]
        self.assertEqual(managed.buy_room(s, snap, {}, '108490'), 0)
        s['orders'][0]['uncertain'] = True
        self.assertEqual(managed.buy_room(s, snap, {}, '058610'), 0)

    def test_drawdown_and_reserve_reduce_spendable_amount(self):
        s = state()
        held = {'000001': {'price': 150000, 'qty': 4}}
        self.assertLessEqual(managed.buy_room(s, {'equity': 499_950_000}, held, '108490'), 100000)
        managed.pause(s, '손실 한도')
        self.assertEqual(managed.buy_room(s, {'equity': 500_000_000}, {}, '108490'), 0)

    def test_pause_stays_latched_even_when_equity_recovers(self):
        s = state()
        managed.pause(s, '손실 10만원')
        managed.pause(s, '다른 이유')
        self.assertEqual(s['halted'], '손실 10만원')

    def test_corrupt_state_never_reinitializes_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('{bad', encoding='utf-8')
            with self.assertRaises(managed.GuardError):
                managed.load_state(path, {'equity': 500_000_000}, {}, [])

    def test_preexisting_positions_and_orders_block_initialization(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            for held, orders in [({'108490': {}}, []), ({}, [{}])]:
                with self.assertRaises(managed.GuardError):
                    managed.load_state(path, {'equity': 500_000_000}, held, orders)

    def test_order_is_reserved_before_network_failure_and_not_repeated(self):
        s = state()
        m = {'code': '108490', 'name': '테스트', 'price': 50000, 'qty': 0}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with (mock.patch.object(broker, 'buyable', return_value=99),
                  mock.patch.object(trade, 'approved', return_value=''),
                  mock.patch.object(broker, 'order', side_effect=TimeoutError) as send):
                managed.submit('test', s, path, {'equity': 500_000_000}, {}, m, 'buy', {'reason': '검증'})
                stored = json.loads(path.read_text(encoding='utf-8'))
                self.assertTrue(stored['orders'][0]['uncertain'])
                managed.submit('test', s, path, {'equity': 500_000_000}, {}, m, 'buy', {'reason': '검증'})
                self.assertEqual(send.call_count, 1)

    def test_preview_cannot_place_or_reserve_orders(self):
        s = state()
        m = {'code': '108490', 'name': '테스트', 'price': 50000, 'qty': 0}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with (mock.patch.object(broker, 'buyable', return_value=99),
                  mock.patch.object(broker, 'order', side_effect=AssertionError('주문 금지'))):
                managed.submit('test', s, path, {'equity': 500_000_000}, {}, m, 'buy', dry=True)
            self.assertFalse(path.exists())
            self.assertEqual(s['orders'], [])


class ReconciliationTests(unittest.TestCase):
    def test_partial_fill_reserves_remaining_quantity_and_detects_no_phantom_fill(self):
        s = state()
        s['orders'] = [order()]
        row = {'iem_cd': '108490', 'sby_dit_cd_nm': '매수', 'orr_qty': 2, 'orr_pr': 50000,
               'tot_cns_qty': 1, 'ny_cns_qty': 1, 'can_qty': 0, 'cns_amt': 50000, 'itg_orr_no': 123}
        held = {'108490': {'qty': 1, 'price': 50000}}
        managed.reconcile(s, {'cash_d2': 499_949_990, 'equity': 499_999_990}, held, {'20261019': [row]})
        self.assertEqual(s['orders'][0]['filled'], 1)
        self.assertFalse(s['orders'][0]['closed'])
        self.assertFalse(s['sync_pending'])
        self.assertFalse(s['halted'])

    def test_ambiguous_or_missing_order_stays_reserved(self):
        s = state()
        s['orders'] = [order()]
        managed.reconcile(s, {'cash_d2': 500_000_000, 'equity': 500_000_000}, {}, {'20261019': []})
        self.assertTrue(s['orders'][0]['uncertain'])
        self.assertEqual(len(managed.active_orders(s)), 1)

    def test_deposit_cannot_hide_losses(self):
        s = state()
        managed.reconcile(s, {'cash_d2': 500_100_000, 'equity': 500_100_000}, {}, {'20261019': []})
        self.assertTrue(s['halted'])

    def test_held_company_remains_when_candidate_removed(self):
        s = state()
        s['orders'] = [dict(order(closed=True), filled=2, amount=100000)]
        s['last_cash'] = 499900000
        managed.reconcile(s, {'cash_d2': 499900000, 'equity': 500000000},
                          {'108490': {'qty': 2, 'price': 50000}}, {})
        self.assertIn('108490', s['positions'])

    def test_reviews_due_at_twenty_one_days_not_every_scan(self):
        clock = managed.now()
        old = clock - dt.timedelta(days=21)
        self.assertTrue(managed.review_due({'opened_at': old.isoformat(), 'reviewed_at': None}, clock))
        self.assertFalse(managed.review_due({'opened_at': old.isoformat(), 'reviewed_at': clock.isoformat()}, clock))


class ResearchTests(unittest.TestCase):
    def test_watch_only_and_stale_lists_cannot_authorize_buy(self):
        data = catalog()
        research.validate(data)
        self.assertFalse(research.eligible(data, '000001'))
        data['checked_at'] = (managed.now() - dt.timedelta(days=8)).isoformat()
        data['companies'][1]['status'] = 'eligible'
        self.assertFalse(research.eligible(data, '000001'))

    def test_duplicate_companies_and_missing_primary_source_rejected(self):
        data = catalog()
        data['companies'][1]['code'] = data['companies'][0]['code']
        with self.assertRaises(ValueError):
            research.validate(data)
        data = catalog()
        data['companies'][0]['sources'][0]['primary'] = False
        with self.assertRaises(ValueError):
            research.validate(data)

    def test_eligibility_needs_financial_evidence_and_invalidation(self):
        data = catalog()
        data['companies'][0]['status'] = 'eligible'
        with self.assertRaises(ValueError):
            research.validate(data)

    def test_future_check_time_rejected(self):
        data = catalog()
        data['checked_at'] = (managed.now() + dt.timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):
            research.validate(data)

    def test_live_approval_includes_stop_loss_sells(self):
        with (mock.patch.object(broker, 'MOCK', False),
              mock.patch.object(trade.telegram, 'configured', return_value=False)):
            self.assertIn('Telegram', trade.approved('test', {}, 'sell', 1, 100, '손절'))

    def test_current_limits_show_budget_and_disabled_fixed_takeprofit(self):
        text = json.dumps(trade.limits(), ensure_ascii=False)
        self.assertIn('1,000,000', text)
        self.assertIn('고정 익절 없음', text)
        self.assertIn('5종목', text)
        self.assertEqual(strategy.US_SYMBOLS, [])

    def test_user_stop_loss_at_four_percent_and_not_before(self):
        m = {'held': True, 'pnl_pct': -4.0, 'ai': None}
        self.assertEqual(strategy.STOP_LOSS_PCT, -4.0)
        self.assertEqual(strategy.decide(m)[0], 'sell')
        m['pnl_pct'] = -3.99
        self.assertEqual(strategy.decide(m)[0], 'hold')


class CycleTests(unittest.TestCase):
    def test_loss_limit_persists_even_when_market_closed_and_notifies_once(self):
        s = state()
        s['last_cash'] = 499_900_000
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            managed.save(path, s)
            with (mock.patch.object(managed, 'state_path', return_value=path),
                  mock.patch.object(broker, 'domestic_snapshot', return_value={'equity': 499_900_000, 'cash_d2': 499_900_000}),
                  mock.patch.object(broker, 'holdings', return_value={}),
                  mock.patch.object(broker, 'domestic_orders', return_value=[]),
                  mock.patch.object(trade, 'kr_open', return_value=False),
                  mock.patch.object(managed.telegram, 'notify') as notify):
                result = managed.run('test', '--scan')
                self.assertTrue(result['운용']['매수중단'])
                managed.run('test', '--scan')
                self.assertEqual(notify.call_count, 1)
                self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['halted'])

    def test_bad_research_never_blocks_stop_loss_for_removed_candidate(self):
        s = state()
        s['last_cash'] = 499900000
        s['orders'] = [dict(order(closed=True), filled=2, amount=100000)]
        s['positions'] = {'108490': {'opened_at': managed.now().isoformat(), 'reviewed_at': None}}
        held = {'108490': {'name': '테스트', 'qty': 2, 'price': 47750, 'avg': 50000, 'pnl_pct': -4.5}}
        m = dict(held['108490'], code='108490', held=True, market='kr', currency='KRW')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            managed.save(path, s)
            with (mock.patch.object(managed, 'state_path', return_value=path),
                  mock.patch.object(broker, 'domestic_snapshot', return_value={'equity': 499994000, 'cash_d2': 499900000}),
                  mock.patch.object(broker, 'holdings', return_value=held),
                  mock.patch.object(broker, 'domestic_orders', return_value=[]),
                  mock.patch.object(broker, 'sellable', return_value=2),
                  mock.patch.object(broker, 'order', return_value=123) as send,
                  mock.patch.object(research, 'load', side_effect=ValueError('bad catalog')),
                  mock.patch.object(trade, 'kr_open', return_value=True),
                  mock.patch.object(trade, 'context', return_value=m),
                  mock.patch.object(trade, 'approved', return_value=''),
                  mock.patch.object(managed.telegram, 'notify')):
                result = managed.run('test', '--scan')
                send.assert_called_once_with('test', 'sell', '108490', 2, 47750)
                self.assertIn('오류', result['조사'])

    def test_engine_preview_at_loss_threshold_never_writes_state_or_sends(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            s = state()
            s['last_cash'] = 499900000
            managed.save(path, s)
            before = path.read_bytes()
            with (mock.patch.object(managed, 'state_path', return_value=path),
                  mock.patch.object(broker, 'domestic_snapshot', return_value={'equity': 499900000, 'cash_d2': 499900000}),
                  mock.patch.object(broker, 'holdings', return_value={}),
                  mock.patch.object(broker, 'domestic_orders', return_value=[]),
                  mock.patch.object(trade, 'kr_open', return_value=False),
                  mock.patch.object(broker, 'order', side_effect=AssertionError('주문 금지')),
                  mock.patch.object(managed.telegram, 'notify') as notify):
                managed.run('test', '--preview')
                self.assertEqual(path.read_bytes(), before)
                notify.assert_not_called()

    def test_state_files_separate_account_and_environment(self):
        with mock.patch.object(broker, 'MOCK', True):
            first = managed.state_path('one')
            second = managed.state_path('two')
        with mock.patch.object(broker, 'MOCK', False):
            live = managed.state_path('one')
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, live)
        self.assertNotIn('one', first.name)

    def test_stale_or_undated_judgment_cannot_trigger_order(self):
        self.assertFalse(managed.valid_call({'decision': 'buy', 'reason': '오래된 판단'}))
        call = {'decision': 'buy', 'reason': '검증', 'as_of': (managed.now() - dt.timedelta(hours=1)).isoformat()}
        self.assertFalse(managed.valid_call(call))
        call['as_of'] = managed.now().isoformat()
        self.assertTrue(managed.valid_call(call))


if __name__ == '__main__':
    unittest.main()
