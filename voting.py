"""동일한 입력 → 독립 세 응답 → 결정서. 이 모듈은 주문하지 않습니다."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import broker
import managed
import research
import strategy

ROOT = Path(__file__).resolve().parent
PLAN = ROOT / '.cache' / 'decision-plan.json'
SEOUL = ZoneInfo('Asia/Seoul')
VOTERS = ('A', 'B', 'C')
POLICY_FIELDS = ('TOTAL_BUDGET', 'BUY_AMOUNT', 'MAX_HOLDINGS', 'CASH_RESERVE_KRW',
                 'MAX_LOSS_KRW', 'REVIEW_DAYS', 'RESEARCH_DAYS', 'STOP_LOSS_PCT',
                 'TAKE_PROFIT_PCT', 'MIN_TURNOVER_KRW', 'MAX_NEAR_HIGH_PCT')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def policy():
    return {**{key: getattr(strategy, key) for key in POLICY_FIELDS},
            **{key: getattr(strategy, key) for key in ('QUANT_ENABLED', 'QUANT_VERSION', 'QUANT_DAYS',
                                                       'BENCHMARK_CODE', 'DEFAULT_BUY_KRW')},
            'implementation_id': digest({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                         for name in ('strategy.py', 'quant.py', 'broker.py', 'managed.py',
                                                      'collection.py', 'cycle.py', 'research.py',
                                                      'voting.py', 'trade.py', 'news.py', 'hybrid.py', 'voter-instructions.md')}),
            'market': 'KR', 'mock': broker.MOCK, 'instructions': strategy.INSTRUCTIONS}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate_input(data, act=None, clock=None):
    clock = clock or managed.now()
    body = {k: v for k, v in data.items() if k != 'input_id'}
    if data.get('input_id') != digest(body) or data.get('version') != 1:
        raise managed.GuardError('공통 입력의 해시·버전이 일치하지 않습니다.')
    if data['policy'] != policy():
        raise managed.GuardError('고정 제한 또는 실행 환경이 바뀌어 결정서를 사용할 수 없습니다.')
    created = research.timestamp(data['created_at'])
    expires = research.timestamp(data['expires_at'])
    if not created <= clock < expires or expires - created > dt.timedelta(hours=8):
        raise managed.GuardError('결정서 입력이 만료됐거나 미래 시각입니다.')
    if created.astimezone(SEOUL).date() != clock.astimezone(SEOUL).date():
        raise managed.GuardError('이전 날짜의 결정서는 사용하지 않습니다.')
    if act is not None and data['account_id'] != managed.state_path(act).stem:
        raise managed.GuardError('결정서와 현재 계좌가 다릅니다.')
    if not isinstance(data.get('stocks'), dict) or not data['stocks']:
        raise managed.GuardError('판단할 종목 자료가 없습니다.')
    return data


def prepare(news=None):
    """조회 자료를 한 번만 고정합니다. 키·계좌번호·원장을 AI에게 전달하지 않습니다."""
    import trade
    if not broker.MOCK:
        raise managed.GuardError('현재 기업선정 예약은 모의투자 전용입니다.')
    news = news or {}
    news_status, news_checked, failed_codes = 'not_checked', None, []
    if 'items' in news:
        news_status = news.get('status')
        news_checked = news.get('checked_at')
        if news_status not in ('ok', 'error') or not news_checked or not dt.timedelta(0) <= managed.now() - research.timestamp(news_checked) <= dt.timedelta(hours=1):
            raise managed.GuardError('이번 회차 뉴스 수집 상태·확인 시각이 필요합니다.')
        failed_codes = news.get('failed_codes', [])
        if not isinstance(failed_codes, list) or not isinstance(news['items'], dict):
            raise managed.GuardError('뉴스 수집 결과 형식이 잘못됐습니다.')
        news = news['items']
    catalog = research.load()
    if not catalog:
        raise managed.GuardError('기업선정 자료가 필요합니다.')
    with managed.cycle_lock():
        act = broker.account()
        account = managed.run(act, '--account')
        held = broker.holdings(act)
        snap = broker.domestic_snapshot(act)
        day = managed.now().astimezone(SEOUL).strftime('%Y%m%d')
        state = managed.load_state(managed.state_path(act), snap, held, broker.domestic_orders(act, day))
        days = {day: broker.domestic_orders(act, day)}
        for old_day in {o['day'] for o in managed.active_orders(state)} - {day}:
            days[old_day] = broker.domestic_orders(act, old_day)
        managed.reconcile(state, snap, held, days)  # 메모리에서만 대조하며 원장은 변경하지 않습니다.
        nav = managed.equity(state, snap)
        if nav <= strategy.TOTAL_BUDGET - strategy.MAX_LOSS_KRW:
            managed.pause(state, '손실 한도')
        pending = managed.active_orders(state)
        reserved = sum((o['qty'] - o.get('filled', 0) - o.get('cancelled', 0)) * o['price']
                       * (1 + managed.FEE_BUFFER) for o in pending if o['side'] == 'buy')
        available = max(0, min(strategy.TOTAL_BUDGET, nav) - strategy.CASH_RESERVE_KRW
                        - sum(v['qty'] * v['price'] for v in held.values()) - reserved)
        if state['halted'] or state.get('sync_pending') or any(o.get('uncertain') for o in pending):
            available = 0
        stocks = {}
        benchmark = None
        if strategy.QUANT_ENABLED:
            try:
                benchmark = {'code': strategy.BENCHMARK_CODE, 'name': 'KODEX 200 (지수 대용 ETF)',
                             'bars': broker.daily_bars(strategy.BENCHMARK_CODE, 90),
                             'mark': broker.quote(strategy.BENCHMARK_CODE)['price']}
            except Exception as exc:
                benchmark = {'error': managed.safe_error(exc)}
        rows = research.companies(catalog)
        if news_status == 'ok':
            failed_codes = sorted(set(failed_codes) | (set(rows) | set(held)) - set(news))
        for code in sorted(set(rows) | set(held)):
            articles = news.get(code, [])
            if not isinstance(articles, list) or any(
                not isinstance(a, dict) or not research.valid_url(a.get('url'))
                or not isinstance(a.get('summary'), str) or not a['summary'].strip()
                or not dt.timedelta(0) <= managed.now() - research.timestamp(a['checked_at']) <= dt.timedelta(days=1)
                for a in articles
            ):
                raise managed.GuardError('뉴스는 실제 확인한 URL·요약·최근 확인 시각이 필요합니다.')
            try:
                m = trade.context('kr', code, held, snap['cash_d2'])
                position = state['positions'].get(code)
                closes = m.get('closes', [])
                rsi = strategy.rsi(closes)
                macd, signal = strategy.macd(closes)
                signals = {'above_ma' + str(days): (m['price'] > strategy.moving_average(closes, days)
                           if strategy.moving_average(closes, days) is not None else None) for days in (5, 20)}
                signals.update(rsi_over_70=(rsi >= 70 if rsi is not None else None),
                               rsi_over_75=(rsi >= 75 if rsi is not None else None),
                               macd_positive=(macd > signal if macd is not None and signal is not None else None),
                               liquid=m.get('turnover', 0) >= strategy.MIN_TURNOVER_KRW,
                               near_high=(m['price'] >= m.get('high_52w', 0) * strategy.MAX_NEAR_HIGH_PCT / 100
                                          if m.get('high_52w') else None))
                stocks[code] = {'facts': trade.facts(m), 'price': m['price'],
                                'signals': signals,
                                'observed_at': managed.now().isoformat(),
                                'held': code in held, 'company': rows.get(code), 'news': articles,
                                'eligible': research.eligible(catalog, code),
                                'buy_room': managed.buy_room(state, snap, held, code),
                                'review_due': bool(position and managed.review_due(position))}
                if strategy.QUANT_ENABLED:
                    stocks[code].update(bars=m.get('bars', []), exchange_date=m.get('exchange_date'))
            except Exception as exc:
                stocks[code] = {'error': managed.safe_error(exc), 'held': code in held,
                                'eligible': False, 'buy_room': 0, 'company': rows.get(code), 'news': articles}
        created = managed.now()
        close = created.astimezone(SEOUL).replace(hour=15, minute=20, second=0, microsecond=0)
        expires = min(created + dt.timedelta(hours=8), close) if close > created else created + dt.timedelta(minutes=30)
        data = {'version': 1, 'created_at': created.isoformat(), 'expires_at': expires.isoformat(),
                'account_id': managed.state_path(act).stem, 'policy': policy(),
                'account': account['운용'], 'market_open': trade.kr_open(),
                'news_status': news_status, 'news_checked_at': news_checked, 'news_failed_codes': failed_codes,
                'execution': [{'code': o['code'], 'side': o['side'], 'qty': o['qty'],
                               'filled': o.get('filled', 0), 'cancelled': o.get('cancelled', 0),
                               'closed': o.get('closed', False), 'uncertain': o.get('uncertain', False),
                               'signal_date': o.get('signal_date'), 'strategy_version': o.get('strategy_version'),
                               'cancel_requested': bool(o.get('cancel_requested_at'))} for o in state['orders']],
                'price_note': '조회 시각의 NH 시세. 장 시작 전·휴장에는 직전 거래 시세이며 실시간 장중 시세가 아님.',
                'available_krw': int(available),
                'available_slots': max(0, strategy.MAX_HOLDINGS - len(set(held) | {o['code'] for o in pending if o['side'] == 'buy'})),
                'stocks': stocks}
        if strategy.QUANT_ENABLED:
            data['benchmark'] = benchmark
            import quant
            data['quant_context'] = quant.context(data)
        data['input_id'] = digest(data)
        target = ROOT / '.cache' / 'voting' / data['input_id'] / 'input.json'
        managed.save(target, data)
        return target, data


def vote_error(vote, data):
    try:
        if set(vote) != {'voter', 'session_id', 'input_id', 'status', 'decisions'}:
            return '응답 필드 오류'
        if vote['status'] != 'ok':
            return '판단자 실행 실패'
        if vote['input_id'] != data['input_id'] or vote['voter'] not in VOTERS:
            return '입력·판단자 불일치'
        if not isinstance(vote['session_id'], str) or not vote['session_id'].strip():
            return '독립 세션 식별자 누락'
        if set(vote['decisions']) != set(data['stocks']):
            return '종목별 응답 누락 또는 추가'
        for code, call in vote['decisions'].items():
            if not isinstance(call, dict) or set(call) - {'decision', 'reason', 'budget_krw', 'max_buy_price', 'review', 'review_sources'}:
                return '의견 형식 오류 또는 허용되지 않은 설정 변경'
            if call.get('decision') not in ('buy', 'sell', 'hold') or not isinstance(call.get('reason'), str) or not call['reason'].strip():
                return '판단·근거 누락'
            if call['decision'] == 'buy':
                if type(call.get('budget_krw')) is not int or not 0 < call['budget_krw'] <= strategy.BUY_AMOUNT:
                    return '종목별 예산 초과 또는 형식 오류'
                if type(call.get('max_buy_price')) is not int or call['max_buy_price'] <= 0:
                    return '매수 상한 가격 누락'
            if 'review' in call and call['review'] != 'renew':
                return '재평가 형식 오류'
            if 'review_sources' in call and (not isinstance(call['review_sources'], list)
                    or not all(research.valid_url(u) for u in call['review_sources'])):
                return '재평가 출처 오류'
        return ''
    except (KeyError, TypeError, ValueError):
        return '응답 형식 오류'


def aggregate(data, votes):
    """순수 집계 함수. AI 심판 없이 동일한 입력에서 동일한 결정서가 나옵니다."""
    errors = []
    if len(votes) != 3:
        errors.append('응답은 정확히 세 개가 필요합니다.')
    labels, sessions = [], []
    for index, vote in enumerate(votes):
        error = vote_error(vote, data)
        if error:
            errors.append(f'응답 {index + 1}: {error}')
        if isinstance(vote, dict):
            labels.append(vote.get('voter'))
            sessions.append(vote.get('session_id'))
    if sorted(str(x) for x in labels) != list(VOTERS) or len(set(str(x) for x in sessions)) != 3:
        errors.append('서로 다른 A/B/C와 독립 세션 세 개가 필요합니다.')
    actions = {}
    remaining = min(max(0, data['available_krw']), strategy.TOTAL_BUDGET - strategy.CASH_RESERVE_KRW)
    slots = min(data['available_slots'], strategy.MAX_HOLDINGS)
    ordered = sorted(data['stocks'], key=lambda c: (-sum(
        isinstance(v, dict) and isinstance(v.get('decisions'), dict)
        and isinstance(v['decisions'].get(c), dict) and v['decisions'][c].get('decision') == 'buy' for v in votes), c))
    for code in ordered:
        stock = data['stocks'][code]
        action = {'decision': 'hold', 'reason': '투표 실패로 주문 보류' if errors else '세 의견이 갈려 보유·관찰',
                  'status': 'blocked' if errors else 'split', 'votes': []}
        if not errors:
            calls = [v['decisions'][code] for v in votes]
            action['votes'] = [{'voter': v['voter'], **v['decisions'][code]} for v in votes]
            winner = next((choice for choice in ('buy', 'sell', 'hold')
                           if sum(c['decision'] == choice for c in calls) >= 2), 'hold')
            winners = [c for c in calls if c['decision'] == winner]
            if len(winners) >= 2:
                action.update(decision=winner, status='consensus', reason=' / '.join(c['reason'] for c in winners))
            if stock.get('error'):
                action.update(decision='hold', status='blocked', reason='공통 시세 자료 조회 실패')
            elif winner == 'buy':
                amount = min(c['budget_krw'] for c in winners)
                ceiling = min(c['max_buy_price'] for c in winners)
                # 시세에서 1% 이상 오른 가격까지 추격하도록 지시할 수 없습니다.
                ceiling = min(ceiling, int(stock['price'] * 1.01))
                amount = min(amount, stock['buy_room'], int(remaining / (1 + managed.FEE_BUFFER)))
                if not stock['eligible'] or not stock['news'] or stock['held'] or slots < 1 or ceiling <= 0 or amount < ceiling:
                    action.update(decision='hold', status='blocked', reason='검증·뉴스·예산·가격·보유 한도 미충족')
                else:
                    action.update(budget_krw=amount, max_buy_price=ceiling)
                    remaining -= amount * (1 + managed.FEE_BUFFER)
                    slots -= 1
            elif winner == 'sell' and not stock['held']:
                action.update(decision='hold', status='blocked', reason='보유하지 않은 기업은 매도하지 않음')
            elif winner == 'hold' and stock['held'] and stock.get('review_due') and stock['eligible']:
                official = {s['url'] for s in (stock.get('company') or {}).get('sources', [])
                            if s.get('verified') is True and s.get('primary') is True
                            and s.get('purpose') in ('business', 'financial') and s.get('checked_at')
                            and dt.timedelta(0) <= research.timestamp(data['created_at']) - research.timestamp(s['checked_at']) <= dt.timedelta(days=7)}
                purposes = {s['url']: s.get('purpose') for s in (stock.get('company') or {}).get('sources', [])}
                renew = [c for c in winners if c.get('review') == 'renew' and c.get('review_sources')
                         and set(c['review_sources']) <= official
                         and {purposes.get(u) for u in c['review_sources']} >= {'business', 'financial'}]
                if len(renew) >= 2:
                    action.update(review='renew', review_sources=sorted(set(u for c in renew for u in c['review_sources'])))
        action['news'] = stock.get('news', [])
        actions[code] = action
    result = {'version': 1, 'input': data, 'votes': votes, 'status': 'blocked' if errors else 'ready',
              'errors': errors, 'actions': actions}
    result['plan_id'] = digest(result)
    return result


def validate_plan(plan, act=None, clock=None):
    data = validate_input(plan['input'], act, clock)
    expected = aggregate(data, plan['votes'])
    if plan != expected:
        raise managed.GuardError('결정서가 원본 투표 집계와 일치하지 않습니다.')
    if plan['status'] != 'ready':
        raise managed.GuardError('독립 투표 세 개가 유효하지 않아 주문을 보류합니다.')
    return plan


def load_plan(path=PLAN, act=None):
    try:
        return validate_plan(read(path), act)
    except managed.GuardError:
        raise
    except Exception:
        raise managed.GuardError('결정서를 읽거나 검증하지 못했습니다. 주문을 보류합니다.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--aggregate', nargs=4, metavar=('INPUT', 'A', 'B', 'C'))
    group.add_argument('--status', action='store_true')
    parser.add_argument('--news', type=Path)
    args = parser.parse_args()
    if args.prepare:
        path, data = prepare(read(args.news) if args.news else None)
        print(json.dumps({'input_file': str(path), 'input_id': data['input_id'], '종목수': len(data['stocks']),
                          'expires_at': data['expires_at']}, ensure_ascii=False, indent=2))
    elif args.aggregate:
        source, *files = args.aggregate
        data = validate_input(read(source))
        import collection
        _, published = collection.load_current()
        if published['input_id'] != data['input_id']:
            raise managed.GuardError('현재 수집 완료본과 다른 입력입니다. 기업선정을 보류합니다.')
        votes = []
        for label, file in zip(VOTERS, files):
            try:
                votes.append(read(file))
            except Exception:
                votes.append({'voter': label, 'status': 'error', 'error': '응답 파일 누락·형식 오류'})
        if strategy.QUANT_ENABLED:
            import hybrid
            plan = hybrid.aggregate(data, votes)
            import performance
            performance.record_plan(plan)
        else:
            plan = aggregate(data, votes)
        managed.save(PLAN, plan)
        managed.save(ROOT / '.cache' / 'voting' / data['input_id'] / 'plan.json', plan)
        print(json.dumps({'상태': plan['status'], '결정서': str(PLAN), '오류': plan['errors'],
                          '매수': sum(a['decision'] == 'buy' for a in plan['actions'].values()),
                          '매도': sum(a['decision'] == 'sell' for a in plan['actions'].values())}, ensure_ascii=False, indent=2))
    else:
        try:
            if strategy.QUANT_ENABLED:
                import hybrid
                plan = hybrid.load_plan()
            else:
                plan = load_plan()
            result = {'상태': plan['status'], '투표수': len(plan['votes']),
                      '입력시각': plan['input']['created_at'], '만료시각': plan['input']['expires_at'],
                      '매수': sum(a['decision'] == 'buy' for a in plan['actions'].values()),
                      '매도': sum(a['decision'] == 'sell' for a in plan['actions'].values())}
        except managed.GuardError as exc:
            result = {'상태': '주문 보류', '이유': str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'오류': managed.safe_error(exc)}, ensure_ascii=False))
        raise SystemExit(1)
