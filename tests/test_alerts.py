import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import alerts
import flow
import managed
import research
import telegram


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / '.cache' / 'notifications'
        self.clock = dt.datetime(2026, 10, 19, 7, tzinfo=dt.timezone.utc)
        self.identity = 'mock-0123456789abcdef'
        self.api = mock.Mock(return_value={'message_id': 17})
        for patch in (mock.patch.object(alerts, 'ROOT', self.root), mock.patch.object(alerts, 'HOME', self.home),
                      mock.patch.object(alerts, 'now', return_value=self.clock),
                      mock.patch.object(alerts, 'account_id', return_value=self.identity),
                      mock.patch.object(telegram, 'configured', return_value=True),
                      mock.patch.object(telegram, '_api', self.api)):
            patch.start()
            self.addCleanup(patch.stop)
        alerts.write(self.home / 'settings.json', {'enabled': True, 'account_id': self.identity,
                                                 'activated_at': (self.clock - dt.timedelta(hours=1)).isoformat()})
        self.folder = self.root / '.cache' / 'multiflow' / 'rounds' / 'round-one'
        alerts.write(self.folder / 'manifest.json', {'run_id': 'round-one', 'mode': 'mock', 'created_at':self.clock.isoformat()})

    def body(self):
        return self.api.call_args.args[1]['text']

    def order(self):
        return dict(code='108490', side='buy', qty=3, price=50000, filled=0, amount=0, cancelled=0,
                    day='20261019', submitted_at=self.clock.isoformat())

    def test_same_event_is_sent_once_across_repeated_calls(self):
        self.assertEqual(alerts.emit('order', 'one', '주문')['status'], 'delivered')
        self.assertEqual(alerts.emit('order', 'one', '주문')['status'], 'duplicate')
        self.assertEqual(self.api.call_count, 1)

    def test_uncertain_delivery_is_recorded_without_retry_or_secret_error(self):
        self.api.side_effect = RuntimeError('https://api.telegram.org/botSECRET_TOKEN/method')
        self.assertEqual(alerts.emit('failure', 'one', '실패')['status'], 'unconfirmed')
        alerts.emit('failure', 'one', '실패')
        self.assertEqual(self.api.call_count, 1)
        record = next((self.home / 'events').glob('*.json')).read_text(encoding='utf-8')
        self.assertNotIn('SECRET_TOKEN', record)
        self.assertIn('RuntimeError', record)

    def test_order_message_includes_actual_limit_quantity_and_reason_not_fill_claim(self):
        alerts.order_sent('account', self.order(), '로보티즈', '-4% 손절')
        text = self.body()
        for expected in ('로보티즈', '매수', '3주', '50,000원', '150,000원', '-4% 손절', '체결 확인 대기'):
            self.assertIn(expected, text)
        self.assertNotIn('account', text)

    def test_partial_full_fill_and_cancel_are_confirmed_changes_only(self):
        order = self.order()
        held = {'108490': {'name': '로보티즈'}}
        state = {'orders': [order]}
        with mock.patch.object(research, 'load', side_effect=ValueError):
            alerts.observe('account', state, held)
            self.api.assert_not_called()
            order.update(filled=1, amount=49000)
            alerts.observe('account', state, held)
            self.assertIn('일부 체결', self.body())
            alerts.observe('account', state, held)
            self.assertEqual(self.api.call_count, 1)
            order.update(filled=2, amount=98000, cancelled=1)
            alerts.observe('account', state, held)
            self.assertEqual(self.api.call_count, 3)
            self.assertIn('취소 확정', self.body())
            alerts.observe('account', state, held)
            self.assertEqual(self.api.call_count, 3)

    def test_preview_and_other_account_never_send(self):
        order = self.order()
        order.update(filled=3, amount=150000)
        alerts.observe('account', {'orders': [order]}, {}, preview=True)
        with mock.patch.object(alerts, 'account_id', return_value='mock-other'):
            alerts.order_sent('other', order, '기업', '이유')
        self.api.assert_not_called()

    def test_historical_fills_are_seeded_without_old_notifications(self):
        order = self.order()
        order.update(submitted_at=(self.clock - dt.timedelta(days=1)).isoformat(), filled=2, amount=100000)
        with mock.patch.object(research, 'load', side_effect=ValueError):
            alerts.observe('account', {'orders': [order]}, {})
            self.api.assert_not_called()
            order.update(filled=3, amount=150000)
            alerts.observe('account', {'orders': [order]}, {})
        self.assertIn('전량 체결', self.body())
        self.assertIn('새로 확인 1주', self.body())

    def test_verification_never_sends_morning_daily_or_failure(self):
        alerts.write(self.folder / 'manifest.json', {'run_id': 'round-one', 'mode': 'verification'})
        for function in (alerts.morning, alerts.daily):
            self.assertEqual(function(self.folder)['status'], 'verification')
        self.assertEqual(alerts.failure(self.folder, 'A')['status'], 'verification')
        self.api.assert_not_called()

    def test_morning_date_only_change_is_silent_and_real_change_is_deduplicated(self):
        catalog = {'themes': ['a','b','c'], 'companies': [{'code':'108490','name':'회사','status':'watch','sources':[]}],
                   'checked_at': self.clock.isoformat()}
        with mock.patch.object(research, 'load', return_value=catalog), mock.patch.object(research, 'eligible', return_value=False):
            alerts.capture_discovery(self.folder)
            catalog['checked_at'] = (self.clock + dt.timedelta(minutes=1)).isoformat()
            self.assertEqual(alerts.morning(self.folder)['status'], 'unchanged')
            catalog['companies'][0]['risk'] = '새 위험'
            alerts.morning(self.folder)
            self.assertIn('근거·검토상태 변경: 회사', self.body())
            alerts.morning(self.folder)
        self.assertEqual(self.api.call_count, 1)

    def test_run_and_cli_failure_share_event_key_and_do_not_repeat(self):
        alerts.failure(self.folder, 'A', 'missing')
        alerts.cli_failure(['--run', str(self.folder / 'A.job.json'), '--role', 'A'], FileNotFoundError())
        self.assertEqual(self.api.call_count, 1)
        self.assertIn('독립 판단 A', self.body())
        self.assertIn('필수 결과·파일 누락', self.body())
        self.api.reset_mock()
        alerts.cli_failure(['--run', str(self.folder / 'B.job.json')], alerts.subprocess.TimeoutExpired('private-command', 240))
        self.assertIn('응답 시간 초과', self.body())
        self.assertNotIn('private-command', self.body())

    def test_daily_uses_only_fresh_matching_account_and_sends_once_per_day(self):
        (self.folder / 'evaluation-report.md').write_text('평가', encoding='utf-8')
        (self.folder / 'telegram-summary.txt').write_text('관찰 유지. 개선 실험은 검토 대기.', encoding='utf-8')
        alerts.write(self.root / '.cache' / 'board.json', {
            '계좌':'모의투자', '알림계좌식별자':self.identity, '마지막실행':'2026-10-19 16:00',
            '운용':{'운용자산':1001000,'누적손익':1000,'미체결확인중':0,'매수중단':''},
            '지금':{'종목수':'1 / 5','주문가능현금':'850,000원'}})
        alerts.daily(self.folder)
        self.assertIn('1,001,000원', self.body())
        self.assertIn('오늘 새로 기록: 주문접수 0건', self.body())
        self.assertIn('+1,000원', self.body())
        self.assertIn('관찰 유지', self.body())
        alerts.daily(self.folder)
        self.assertEqual(self.api.call_count, 1)

    def test_missing_old_or_wrong_account_board_does_not_claim_current_balance(self):
        (self.folder / 'evaluation-report.md').write_text('평가', encoding='utf-8')
        alerts.daily(self.folder)
        self.assertIn('잔고·손익 표시는 보류', self.body())
        self.assertNotIn('운용자산', self.body())
        self.api.reset_mock()
        alerts.write(self.root / '.cache' / 'board.json', {
            '계좌':'모의투자','알림계좌식별자':'mock-other','마지막실행':'2026-10-19 16:00'})
        with mock.patch.object(alerts, 'emit', wraps=alerts.emit) as send:
            # 새 날짜 식별자를 만들 필요 없이, 만들어진 본문을 검사한다.
            alerts.daily(self.folder)
            self.assertIn('잔고·손익 표시는 보류', send.call_args.args[2])

    def test_zero_exit_error_json_is_still_failure(self):
        child = mock.Mock(returncode=0, stdout='조회 중\n' + json.dumps({'오류':'원문 비공개'}, ensure_ascii=False))
        with mock.patch.object(flow.subprocess, 'run', return_value=child):
            with self.assertRaises(managed.GuardError):
                flow.command('trade.py', '--account')

    def test_runtime_and_cli_risk_failure_are_one_notification(self):
        with (mock.patch.object(flow, 'HOME', self.root / '.cache' / 'multiflow'),
              mock.patch.object(flow, '_run', side_effect=managed.GuardError('중단'))):
            with self.assertRaises(managed.GuardError):
                flow.run(self.folder / 'risk.job.json', 'execution')
        alerts.cli_failure(['--run', str(self.folder / 'risk.job.json'), '--role', 'execution'], managed.GuardError('중단'))
        self.assertEqual(self.api.call_count, 1)
        self.assertIn('위험 점검', self.body())

    def test_concurrent_duplicate_emit_is_claimed_once(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: alerts.emit('failure', 'same', '실패'), range(4)))
        self.assertEqual(self.api.call_count, 1)
        self.assertEqual(sum(o['status'] == 'delivered' for o in outcomes), 1)

    def test_old_future_or_other_account_snapshot_is_not_current_balance(self):
        (self.folder / 'evaluation-report.md').write_text('평가', encoding='utf-8')
        for stamp in ('2026-10-18 16:00', '2026-10-20 16:00'):
            alerts.write(self.root / '.cache' / 'board.json', {
                '계좌':'모의투자','알림계좌식별자':self.identity,'마지막실행':stamp})
            with mock.patch.object(alerts, 'emit', return_value={'status':'delivered'}) as send:
                alerts.daily(self.folder)
                self.assertIn('잔고·손익 표시는 보류', send.call_args.args[2])

    def test_previous_day_report_and_premarket_report_are_not_sent(self):
        (self.folder / 'evaluation-report.md').write_text('평가', encoding='utf-8')
        with mock.patch.object(alerts, 'now', return_value=self.clock-dt.timedelta(hours=1)):
            self.assertEqual(alerts.daily(self.folder)['status'], 'outside_report_time')
        alerts.write(self.folder / 'manifest.json', {'run_id':'round-one','mode':'mock',
                     'created_at':(self.clock-dt.timedelta(days=1)).isoformat()})
        self.assertEqual(alerts.daily(self.folder)['status'], 'stale_report')
        self.api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
