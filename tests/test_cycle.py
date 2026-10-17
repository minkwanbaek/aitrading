import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import broker
import collection
import cycle
import managed
import research
import voting
import trade
from test_voting import ballots, frozen
from test_managed import state


def inputs():
    data = frozen()
    data.update(news_status='ok', news_failed_codes=[], account={}, execution=[])
    data['input_id'] = voting.digest({k: v for k, v in data.items() if k != 'input_id'})
    plan = voting.aggregate(copy.deepcopy(data), ballots(data, ('hold', 'hold', 'hold')))
    return data, plan


class GateTests(unittest.TestCase):
    def test_risk_scan_does_not_fetch_unheld_candidate_prices_a_second_time(self):
        with tempfile.TemporaryDirectory() as folder:
            with (mock.patch.object(managed, 'state_path', return_value=Path(folder) / 'state.json'),
                  mock.patch.object(broker, 'domestic_snapshot', return_value={'equity': 500000000, 'cash_d2': 500000000}),
                  mock.patch.object(broker, 'holdings', return_value={}),
                  mock.patch.object(broker, 'domestic_orders', return_value=[]),
                  mock.patch.object(research, 'load', return_value={'companies': [{'code': '000000'}]}),
                  mock.patch.object(research, 'status', return_value={}),
                  mock.patch.object(trade, 'kr_open', return_value=True),
                  mock.patch.object(trade, 'context') as quotes):
                managed.run('test', '--scan')
                quotes.assert_not_called()

    def test_first_eligible_cycle_requests_but_empty_watch_universe_skips(self):
        data, plan = inputs()
        self.assertEqual(cycle.inspect(data)['decision'], 'request')
        data['stocks']['000000']['eligible'] = False
        gate = cycle.inspect(data)
        self.assertEqual(gate['decision'], 'skip')
        self.assertFalse(gate['execute_existing'])

    def test_unchanged_data_skips_without_claiming_new_hold_vote(self):
        data, plan = inputs()
        gate = cycle.inspect(data, plan)
        self.assertEqual(gate['decision'], 'skip')
        self.assertTrue(gate['execute_existing'])
        self.assertNotIn('votes', gate)

    def test_only_timestamp_or_source_order_changes_do_not_trigger(self):
        data, plan = inputs()
        fresh = copy.deepcopy(data)
        fresh['stocks']['000000']['news'][0]['checked_at'] = managed.now().isoformat()
        self.assertEqual(cycle.inspect(fresh, plan)['decision'], 'skip')

    def test_news_and_business_changes_request_votes(self):
        data, plan = inputs()
        for field, value in [('news', [{'url': 'https://example.com/disclosure', 'summary': '공시 변경'}]),
                             ('company', {'financial_case': '재무 내용 변경'})]:
            changed = copy.deepcopy(data)
            changed['stocks']['000000'][field] = value
            self.assertEqual(cycle.inspect(changed, plan)['decision'], 'request')

    def test_price_threshold_is_cumulative_from_last_actual_judgment(self):
        data, plan = inputs()
        for price, expected in [(50500, 'skip'), (50999, 'skip'), (51000, 'request'), (49000, 'request')]:
            data['stocks']['000000']['price'] = price
            self.assertEqual(cycle.inspect(data, plan)['decision'], expected)

    def test_technical_threshold_crossing_requests_even_with_small_price_change(self):
        data, plan = inputs()
        data['stocks']['000000']['signals'] = {'above_ma5': True}
        self.assertEqual(cycle.inspect(data, plan)['decision'], 'request')

    def test_missing_news_or_quote_is_blocked_not_a_hold(self):
        data, plan = inputs()
        for modification in [{'news_status': 'error'}, {'news_failed_codes': ['000000']}]:
            changed = {**data, **modification}
            gate = cycle.inspect(changed, plan)
            self.assertEqual(gate['decision'], 'blocked')
            self.assertFalse(gate['execute_existing'])
        data['stocks']['000000']['error'] = '조회 실패'
        self.assertEqual(cycle.inspect(data, plan)['decision'], 'blocked')

    def test_unexecuted_buy_and_due_review_request_votes(self):
        data, plan = inputs()
        buy = voting.aggregate(data, ballots(data))
        self.assertEqual(cycle.inspect(data, buy)['decision'], 'request')
        data['stocks']['000000'].update(held=True, review_due=True)
        self.assertEqual(cycle.inspect(data, plan)['decision'], 'request')

    def test_fill_cancel_and_risk_state_changes_request_votes(self):
        data, plan = inputs()
        for key, value in [('execution', [{'code': '000000', 'closed': True, 'filled': 1}]),
                           ('account', {'체결동기화대기': True})]:
            self.assertEqual(cycle.inspect({**data, key: value}, plan)['decision'], 'request')

    def test_failed_new_judgment_never_falls_back_to_previous_plan(self):
        data, previous = inputs()
        audit = {'input_id': 'new', 'checked_at': managed.now().isoformat(), 'decision': 'request'}
        with (mock.patch.object(collection, 'load_current', return_value=(Path('input'), {**data, 'input_id': 'new'})),
              mock.patch.object(voting, 'read', return_value=audit),
              mock.patch.object(voting, 'load_plan', return_value=previous), mock.patch.object(broker, 'MOCK', True)):
            with self.assertRaisesRegex(managed.GuardError, '이번 회차 독립 판단'):
                cycle.execution_plan()

    def test_skip_requires_same_prior_plan_and_recent_matching_cycle(self):
        data, plan = inputs()
        audit = cycle.inspect(data, plan)
        with (mock.patch.object(collection, 'load_current', return_value=(Path('input'), data)),
              mock.patch.object(voting, 'read', return_value=audit),
              mock.patch.object(voting, 'load_plan', return_value=plan), mock.patch.object(broker, 'MOCK', True)):
            self.assertEqual(cycle.execution_plan(), voting.PLAN)
            audit['previous_plan_id'] = 'other'
            with self.assertRaises(managed.GuardError):
                cycle.execution_plan()
            audit['checked_at'] = (managed.now() - dt.timedelta(minutes=31)).isoformat()
            with self.assertRaises(managed.GuardError):
                cycle.execution_plan()

    def test_daily_updates_do_not_postpone_weekly_full_review(self):
        clock = managed.now()
        data = {'checked_at': clock.isoformat(), 'full_reviewed_at': (clock - dt.timedelta(days=8)).isoformat()}
        self.assertTrue(research.full_review_due(data, clock))


class PendingTests(unittest.TestCase):
    def pending(self):
        return {'code': '000000', 'side': 'buy', 'qty': 4, 'price': 50000, 'filled': 1,
                'amount': 50000, 'cancelled': 0, 'closed': False, 'uncertain': False,
                'day': managed.now().astimezone(voting.SEOUL).strftime('%Y%m%d'),
                'submitted_at': (managed.now() - dt.timedelta(minutes=61)).isoformat(),
                'market_order_no': '22', 'row_id': '11'}

    def test_cancel_uses_original_market_number_and_only_remaining_quantity(self):
        with mock.patch.object(broker, 'MOCK', True), mock.patch.object(broker, '_call', return_value={'Output_0': {'mkt_orr_no': 33}}) as call:
            self.assertEqual(broker.cancel_mock('test', '000000', '22', 3), 33)
            call.assert_called_once_with('/krstock/order/v1/cancel', {'act_no': 'test', 'iem_cd': '000000',
                'org_mkt_orr_no': 22, 'all_pat_dit_cd': '2', 'cor_qty': 3})
        with mock.patch.object(broker, 'MOCK', False), mock.patch.object(broker, '_call') as call:
            with self.assertRaises(RuntimeError):
                broker.cancel_mock('test', '000000', '22', 3)
            call.assert_not_called()

    def test_partial_fill_cancellation_is_recorded_before_network_and_never_repeated(self):
        s = state()
        s['orders'] = [self.pending()]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            def cancel(*args):
                self.assertTrue(voting.read(path)['orders'][0]['cancel_requested_at'])
                return 33
            with (mock.patch.object(broker, 'MOCK', True), mock.patch.object(broker, 'cancel_mock', side_effect=cancel) as api,
                  mock.patch.object(managed.telegram, 'notify')):
                managed.manage_pending('test', s, path)
                managed.manage_pending('test', s, path)
                api.assert_called_once_with('test', '000000', '22', 3)
                self.assertFalse(s['orders'][0]['closed'])
                self.assertEqual(managed.buy_room(s, {'equity': 500000000}, {}, '000000'), 0)

    def test_unknown_order_and_unknown_cancel_response_never_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            s = state()
            s['orders'] = [self.pending()]
            with (mock.patch.object(broker, 'MOCK', True), mock.patch.object(broker, 'cancel_mock', side_effect=TimeoutError) as api,
                  mock.patch.object(managed.telegram, 'notify')):
                s['orders'][0]['uncertain'] = True
                managed.manage_pending('test', s, path)
                api.assert_not_called()
                s['orders'][0]['uncertain'] = False
                managed.manage_pending('test', s, path)
                managed.manage_pending('test', s, path)
                self.assertEqual(api.call_count, 1)
                self.assertFalse(s['orders'][0]['closed'])

    def test_reservation_releases_only_after_broker_confirms_remaining_zero(self):
        s = state()
        s['last_cash'] = 499950000
        s['orders'] = [self.pending()]
        s['orders'][0]['cancel_requested_at'] = managed.now().isoformat()
        day = s['orders'][0]['day']
        row = {'iem_cd': '000000', 'sby_dit_cd_nm': '매수', 'orr_qty': 4, 'orr_pr': 50000,
               'itg_orr_no': 11, 'tot_cns_qty': 2, 'can_qty': 2, 'ny_cns_qty': 0, 'cns_amt': 100000}
        managed.reconcile(s, {'equity': 500000000, 'cash_d2': 499900000}, {'000000': {'qty': 2}}, {day: [row]})
        self.assertTrue(s['orders'][0]['closed'])
        self.assertEqual(s['orders'][0]['filled'], 2)
        self.assertEqual(s['orders'][0]['cancelled'], 2)
        self.assertFalse(s['sync_pending'])

    def test_recent_and_live_orders_are_not_automatically_cancelled(self):
        s = state()
        s['orders'] = [self.pending()]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with mock.patch.object(broker, 'cancel_mock') as api, mock.patch.object(managed.telegram, 'notify'):
                s['orders'][0]['submitted_at'] = managed.now().isoformat()
                managed.manage_pending('test', s, path)
                api.assert_not_called()
                s['orders'][0] = self.pending()
                with mock.patch.object(broker, 'MOCK', False):
                    managed.manage_pending('test', s, path)
                api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
