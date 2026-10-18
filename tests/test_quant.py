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
import performance
import quant
import research
import strategy
import trade
import voting
from test_voting import frozen, ballots


def fixture(count=2):
    data = frozen(count)
    clock = research.timestamp(data['created_at']).astimezone(voting.SEOUL)
    date = clock.date() - dt.timedelta(days=1)
    dates = []
    while len(dates) < 61:
        if date.weekday() < 5:
            dates.append(date.strftime('%Y%m%d'))
        date -= dt.timedelta(days=1)
    dates.reverse()

    def series(growth):
        result = []
        for n, day in enumerate(dates):
            price = 50000 * (1 + growth * n / 60)
            result.append({'date': day, 'close': price, 'low': price * .99, 'high': price * 1.01,
                           'volume': 1_000_000, 'turnover': price * 1_000_000,
                           'capital_event': '00', 'ex_event': '00'})
        return result
    data.update(news_status='ok', news_failed_codes=[], execution=[], market_open=False,
                benchmark={'code': strategy.BENCHMARK_CODE, 'bars': series(.03), 'mark': 51500})
    for n, stock in enumerate(data['stocks'].values()):
        stock.update(bars=series(.08 + .02 * n), exchange_date=clock.strftime('%Y%m%d'),
                     news=[], facts={}, review_due=False)
    return rehash(data)


def rehash(data):
    data['input_id'] = voting.digest({k: v for k, v in data.items() if k != 'input_id'})
    return data


class QuantTests(unittest.TestCase):
    def test_relative_strength_uses_sixty_intervals_and_rank_not_code(self):
        data = fixture()
        plan = quant.build(data)
        self.assertAlmostEqual(plan['metrics']['000000']['score_pp'], 5)
        self.assertAlmostEqual(plan['metrics']['000001']['score_pp'], 7)
        self.assertEqual(plan['ranking'], ['000001', '000000'])
        self.assertEqual(plan['actions']['000001']['rank'], 1)
        quant.validate_plan(plan)

    def test_today_candle_cannot_change_score_or_ranking(self):
        data = fixture()
        expected = quant.build(data)['metrics']
        today = research.timestamp(data['created_at']).astimezone(voting.SEOUL).strftime('%Y%m%d')
        for stock in data['stocks'].values():
            stock['bars'].append({'date': today, 'close': 1})
        self.assertEqual(quant.build(data)['metrics'], expected)

    def test_missing_short_duplicate_future_and_date_mismatch_block_new_buys(self):
        for mutate in (
            lambda b: b.pop(),
            lambda b: b.append(copy.deepcopy(b[-1])),
            lambda b: b[-1].update(date='20990101'),
            lambda b: b[-1].update(date=b[-2]['date']),
            lambda b: b[-1].update(capital_event='01'),
            lambda b: b[-1].update(ex_event='01'),
        ):
            data = fixture()
            mutate(data['stocks']['000001']['bars'])
            plan = quant.build(data)
            self.assertFalse(any(a['decision'] == 'buy' for a in plan['actions'].values()))
            self.assertTrue(plan['errors'])

    def test_nonfinite_input_is_rejected_before_it_can_be_saved(self):
        data = fixture(1)
        data['stocks']['000000']['bars'][-1]['close'] = float('nan')
        with self.assertRaises(ValueError):
            quant.build(data)

    def test_turnover_unit_error_does_not_make_stock_look_liquid(self):
        data = fixture()
        for bar in data['stocks']['000000']['bars']:
            bar['turnover'] *= 1_000_000
        self.assertTrue(quant.build(data)['errors'])

    def test_liquidity_uses_completed_twenty_days_not_opening_cumulative_amount(self):
        data = fixture(1)
        data['stocks']['000000']['facts']['거래대금'] = 0
        self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'buy')
        for bar in data['stocks']['000000']['bars']:
            bar.update(volume=100, turnover=bar['close'] * 100)
        self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'hold')

    def test_empty_news_success_allowed_failure_blocked(self):
        data = fixture(1)
        self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'buy')
        data['news_failed_codes'] = ['000000']
        self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'hold')

    def test_nonpositive_score_watch_and_missing_today_quote_never_buy(self):
        for change in ({'eligible': False}, {'exchange_date': ''}):
            data = fixture(1)
            data['market_open'] = True
            data['stocks']['000000'].update(change)
            self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'hold')
        data = fixture(1)
        data['stocks']['000000']['bars'] = copy.deepcopy(data['benchmark']['bars'])
        self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'hold')

    def test_budget_slots_and_one_share_affordability(self):
        data = fixture(15)
        plan = quant.build(data)
        buys = [a for a in plan['actions'].values() if a['decision'] == 'buy']
        self.assertLessEqual(len(buys), 5)
        self.assertLessEqual(sum(a['budget_krw'] * (1 + managed.FEE_BUFFER) for a in buys), 750000)
        data.update(available_slots=1)
        plan = quant.build(data)
        self.assertEqual(sum(a['decision'] == 'buy' for a in plan['actions'].values()), 1)
        self.assertEqual(plan['actions']['000014']['decision'], 'buy')
        data['stocks']['000014']['price'] = 300000
        self.assertEqual(quant.build(data)['actions']['000014']['decision'], 'hold')

    def test_rank_decline_is_not_an_hourly_exit_or_topup(self):
        data = fixture()
        data['stocks']['000000'].update(held=True, review_due=False)
        data['stocks']['000000']['bars'] = copy.deepcopy(data['benchmark']['bars'])
        self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'hold')
        data['stocks']['000000']['review_due'] = True
        self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'sell')

    def test_review_renew_requires_positive_score_and_real_fresh_sources(self):
        data = fixture(1)
        stock = data['stocks']['000000']
        stock.update(held=True, review_due=True)
        self.assertNotIn('review', quant.build(data)['actions']['000000'])
        stock['company']['sources'] = [dict(url='https://example.com/' + p, primary=True, verified=True,
                                            purpose=p, checked_at=data['created_at']) for p in ('business', 'financial')]
        self.assertEqual(quant.build(data)['actions']['000000']['review'], 'renew')
        stock['company']['sources'][0]['checked_at'] = (managed.now() - dt.timedelta(days=8)).isoformat()
        self.assertNotIn('review', quant.build(data)['actions']['000000'])

    def test_same_signal_day_cancelled_buy_is_not_reentered(self):
        data = fixture(1)
        day = quant.build(data)['metrics']['000000']['signal_date']
        data['execution'] = [{'code': '000000', 'side': 'buy', 'closed': True,
                              'signal_date': day, 'strategy_version': strategy.QUANT_VERSION}]
        self.assertEqual(quant.build(data)['actions']['000000']['decision'], 'hold')
        with mock.patch.object(broker, 'order') as order:
            s = {'orders': data['execution']}
            result = managed.submit('test', s, Path('unused'), {}, {}, {'code': '000000', 'name': '검증'},
                                    'buy', {'signal_date': day})
            order.assert_not_called()
            self.assertIn('재진입', result['한 일'])

    def test_ai_votes_tampering_environment_and_policy_cannot_override_quant(self):
        data = fixture(1)
        with self.assertRaises(managed.GuardError):
            quant.validate_plan(voting.aggregate(data, ballots(data)))
        plan = quant.build(data)
        plan['actions']['000000']['budget_krw'] = 200000
        with self.assertRaises(managed.GuardError):
            quant.validate_plan(plan)
        plan = quant.build(data)
        with mock.patch.object(broker, 'MOCK', False), self.assertRaises(managed.GuardError):
            quant.validate_plan(plan)
        with mock.patch.object(strategy, 'QUANT_DAYS', 20), self.assertRaises(managed.GuardError):
            quant.validate_plan(plan)

    def test_different_cycle_never_executes_previous_quant_plan(self):
        data = fixture(1)
        plan = quant.build(data)
        audit = {'input_id': data['input_id'], 'checked_at': managed.now().isoformat(),
                 'decision': 'quant', 'plan_id': plan['plan_id']}
        with (mock.patch.object(collection, 'load_current', return_value=(Path('input'), data)),
              mock.patch.object(voting, 'read', return_value=audit), mock.patch.object(quant, 'load_plan', return_value=plan)):
            self.assertEqual(cycle.execution_plan(), voting.PLAN)
            audit['plan_id'] = 'stale'
            with self.assertRaises(managed.GuardError):
                cycle.execution_plan()

    def test_risk_context_does_not_fetch_daily_history(self):
        q = dict(price=47000, name='검증', change_pct=-6, turnover=0)
        with (mock.patch.object(broker, 'quote', return_value=q),
              mock.patch.object(broker, 'daily_bars', side_effect=AssertionError('손절은 일봉에 의존하지 않음')),
              mock.patch.object(broker, 'closes', side_effect=AssertionError('손절은 일봉에 의존하지 않음'))):
            m = trade.context('kr', '000000', {'000000': {'qty': 2, 'avg': 50000}}, 1000000, history=False)
        m['pnl_pct'] = -6
        self.assertEqual(strategy.decide(m)[0], 'sell')

    def test_period_units_are_preserved_and_regular_session_requested(self):
        response = {'Output_0': {'bsop_date': '20261016'}, 'Output_1': [
            {'bsop_date': '20261016', 'stck_prpr': '50000', 'stck_hgpr': '51000', 'stck_lwpr': '49000',
             'vol': '1000000', 'tr_pbmn': '50000000000'}]}
        with mock.patch.object(broker, '_call', return_value=response) as call:
            bars = broker.daily_bars('000000')
        self.assertEqual(bars[0]['turnover'], 50_000_000_000)
        self.assertEqual(bars[0]['volume'], 1_000_000)
        self.assertEqual(call.call_args.args[1]['view_main_yn'], 'Y')

    def test_quant_preview_never_publishes_or_orders(self):
        import contextlib
        import io
        data = fixture(1)
        with (mock.patch('sys.argv', ['quant.py', '--preview']),
              mock.patch.object(collection, 'load_current', return_value=(Path('input'), data)),
              mock.patch.object(managed, 'save', side_effect=AssertionError('미리보기 저장 금지')),
              mock.patch.object(broker, 'order', side_effect=AssertionError('미리보기 주문 금지')),
              contextlib.redirect_stdout(io.StringIO())):
            quant.main()

    def test_valid_quant_plan_reserves_before_send_and_cannot_send_twice(self):
        data = fixture(1)
        plan = quant.build(data)
        filename = managed.state_path('test').name
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / filename
            def send(act, side, code, qty, price):
                saved = voting.read(path)['orders'][-1]
                self.assertTrue(saved['uncertain'])
                self.assertEqual(saved['signal_date'], plan['metrics']['000000']['signal_date'])
                self.assertEqual(saved['plan_id'], plan['plan_id'])
                self.assertEqual((side, code, qty, price), ('buy', '000000', 3, 50000))
                return 123
            with (mock.patch.object(managed, 'state_path', return_value=path),
                  mock.patch.object(performance, 'ROOT', Path(directory) / 'evaluation'),
                  mock.patch.object(broker, 'domestic_snapshot', return_value={'equity': 500000000, 'cash_d2': 500000000}),
                  mock.patch.object(broker, 'holdings', return_value={}),
                  mock.patch.object(broker, 'domestic_orders', return_value=[]),
                  mock.patch.object(broker, 'buyable', return_value=100),
                  mock.patch.object(broker, 'order', side_effect=send) as order,
                  mock.patch.object(research, 'load', return_value={'companies': [{'code': '000000'}]}),
                  mock.patch.object(research, 'status', return_value={}),
                  mock.patch.object(research, 'eligible', return_value=True),
                  mock.patch.object(trade, 'context', return_value={'code': '000000', 'name': '검증', 'price': 50000, 'held': False}),
                  mock.patch.object(trade, 'kr_open', return_value=True),
                  mock.patch.object(trade, 'approved', return_value=''),
                  mock.patch.object(managed.telegram, 'notify')):
                result = managed.run('test', '--do', plan)
                self.assertEqual(result['투표']['유효응답'], 0)
                order.assert_called_once()
                managed.run('test', '--do', plan)
                order.assert_called_once()


class EvaluationTests(unittest.TestCase):
    def test_decisions_are_immutable_and_observations_do_not_invent_trades(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(performance, 'ROOT', Path(directory)):
            data = fixture(1)
            plan = quant.build(data)
            performance.record_plan(plan)
            altered = copy.deepcopy(plan)
            altered['actions']['000000']['decision'] = 'sell'
            with self.assertRaises(managed.GuardError):
                performance.record_plan(altered)
            result = performance.observe(data['account_id'], 1000000, {'orders': []})
            self.assertEqual(result['확인된체결주문수'], 0)
            self.assertEqual(result['우위'], '미입증')

    def test_cash_market_and_drawdown_use_matching_observation_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            for n, (nav, mark) in enumerate(((1000000, None), (990000, 100), (1000000, 110))):
                managed.save(location / 'observations' / f'{n}.json',
                             {'observed_at': str(n), 'nav': nav, 'benchmark_mark': mark, 'orders': []})
            result = performance.summary(location)
            self.assertAlmostEqual(result['비교ETF75현금25수익률'], 7.5)
            self.assertAlmostEqual(result['동일비교구간전략수익률'], 1.0101, places=4)
            self.assertEqual(result['운용수익률'], 0)
            self.assertEqual(result['관측최대낙폭'], -1)
            self.assertEqual(result['현금기준수익률'], 0)


if __name__ == '__main__':
    unittest.main()
