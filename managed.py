"""100만 원 국내 전용 운용. 잔고와 미체결을 확인하고 불확실하면 매수를 막습니다."""
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path

import broker
import research
import strategy
import telegram

CACHE = Path(__file__).with_name('.cache')
UTC = dt.timezone.utc
FEE_BUFFER = 0.01  # 주문 금액의 1%를 비용·결제 차이 대비로 추가 예약합니다.


class GuardError(Exception):
    pass


def safe_error(exc):
    return str(exc) if isinstance(exc, GuardError) else type(exc).__name__ + ' (설정·조회 결과를 확인하세요)'


def now():
    return dt.datetime.now(UTC)


@contextlib.contextmanager
def cycle_lock():
    CACHE.mkdir(exist_ok=True)
    with (CACHE / 'managed.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise GuardError('다른 회차가 실행 중입니다. 중복 실행하지 않습니다.') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def state_path(act):
    identity = hashlib.sha256(act.encode()).hexdigest()[:16]
    return CACHE / (('mock-' if broker.MOCK else 'live-') + identity + '.json')


def save(path, state):
    path.parent.mkdir(exist_ok=True, parents=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def load_state(path, snapshot, held, day_orders):
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            assert state['version'] == 1
            for field in ('baseline_equity', 'last_equity', 'last_cash'):
                assert isinstance(state[field], (int, float)) and math.isfinite(state[field])
            assert isinstance(state['orders'], list) and isinstance(state['positions'], dict)
            assert isinstance(state['halted'], str)
            return state
        except (ValueError, KeyError, AssertionError, TypeError):
            raise GuardError('운용 기록이 손상됐습니다. 초기화하지 않고 주문을 중단합니다.') from None
    if held or day_orders:
        raise GuardError('처음 시작할 계좌에 보유·주문 내역이 있습니다. 전용 모의 계좌를 확인해야 합니다.')
    if snapshot['equity'] < strategy.TOTAL_BUDGET:
        raise GuardError('계좌 순자산이 운용 예산보다 적습니다.')
    return {'version': 1, 'started_at': now().isoformat(),
            'baseline_equity': snapshot['equity'], 'last_equity': snapshot['equity'],
            'last_cash': snapshot['cash_d2'], 'orders': [], 'positions': {}, 'halted': ''}


def pause(state, reason):
    if not state['halted']:
        state['halted'] = reason
        return True
    return False


def active_orders(state):
    return [order for order in state['orders'] if not order.get('closed')]


def reconcile(state, snapshot, held, by_day):
    """시장주문번호와 통합주문번호를 같다고 가정하지 않습니다. 모호하면 예약 유지."""
    expected_cash = 0
    turnover = 0
    matched = set()
    for order in state['orders']:
        rows = by_day.get(order['day'])
        if rows is None:
            continue
        hits = []
        for row in rows:
            side_name = str(row.get('sby_dit_cd_nm') or '')
            side_ok = ('매수' in side_name) if order['side'] == 'buy' else ('매도' in side_name)
            if (str(row.get('iem_cd', '')).strip() == order['code'] and side_ok
                    and int(row.get('orr_qty') or 0) == order['qty']
                    and float(row.get('orr_pr') or 0) == order['price']):
                hits.append(row)
        if order.get('row_id') is not None:
            hits = [r for r in hits if str(r.get('itg_orr_no')) == order['row_id']]
        if len(hits) != 1:
            if not order.get('closed'):
                order['uncertain'] = True
            continue
        row = hits[0]
        identifier = (order['day'], str(row.get('itg_orr_no')))
        if identifier in matched:
            pause(state, '주문 대조가 중복됩니다. 확인이 필요합니다.')
            continue
        matched.add(identifier)
        filled = int(row.get('tot_cns_qty') or 0)
        cancelled = int(row.get('can_qty') or 0)
        remaining = int(row.get('ny_cns_qty') or 0)
        amount = int(row.get('cns_amt') or 0)
        if filled < order.get('filled', 0) or amount < order.get('amount', 0):
            pause(state, '체결 누적값이 감소했습니다. 내역 확인이 필요합니다.')
            continue
        if min(filled, cancelled, remaining, amount) < 0 or filled + cancelled + remaining != order['qty']:
            pause(state, '체결·취소·미체결 수량이 일치하지 않습니다.')
            continue
        increment = amount - order.get('amount', 0)
        expected_cash += increment * (1 if order['side'] == 'sell' else -1)
        turnover += increment
        order.update(filled=filled, amount=amount, cancelled=cancelled,
                     row_id=str(row.get('itg_orr_no')),
                     closed=(remaining == 0), uncertain=False)
    # 이 운용 프로그램 외의 주문이 있으면 자금을 섞지 않습니다.
    for day, rows in by_day.items():
        for row in rows:
            if (day, str(row.get('itg_orr_no'))) not in matched:
                pause(state, '계좌에 별도 주문이 발견됐습니다. 신규 매수를 중단합니다.')
    expected_qty = {}
    for order in state['orders']:
        sign = 1 if order['side'] == 'buy' else -1
        expected_qty[order['code']] = expected_qty.get(order['code'], 0) + sign * order.get('filled', 0)
    actual_qty = {code: item['qty'] for code, item in held.items()}
    if {k: v for k, v in expected_qty.items() if v} != actual_qty:
        # 수신 시차 동안 매수 금지. 다음 회차의 체결·잔고가 맞으면 해소됩니다.
        state['sync_pending'] = True
    else:
        state['sync_pending'] = False
    cash_change = snapshot['cash_d2'] - state['last_cash']
    residual = cash_change - expected_cash
    # 입금으로 손실이 가려지는 것을 막습니다. 출금도 자동으로 원금에 보태지 않습니다.
    if residual > 0 or residual < -max(10, math.ceil(turnover * FEE_BUFFER)):
        pause(state, '입출금 또는 설명되지 않는 현금 변동이 있어 확인이 필요합니다.')
    state['last_cash'] = snapshot['cash_d2']
    state['last_equity'] = snapshot['equity']
    for code, item in held.items():
        if code not in state['positions']:
            state['positions'][code] = {'opened_at': now().isoformat(), 'reviewed_at': None}
    for code in list(state['positions']):
        if code not in held and not any(o['code'] == code for o in active_orders(state)):
            del state['positions'][code]


def equity(state, snapshot):
    return strategy.TOTAL_BUDGET + snapshot['equity'] - state['baseline_equity']


def review_due(position, clock=None):
    date = position.get('reviewed_at') or position['opened_at']
    return (clock or now()) - research.timestamp(date) >= dt.timedelta(days=strategy.REVIEW_DAYS)


def buy_room(state, snapshot, held, code):
    if state['halted'] or state.get('sync_pending'):
        return 0
    pending = active_orders(state)
    if any(o.get('uncertain') for o in pending):
        return 0
    if code in held or any(o['code'] == code for o in pending):
        return 0
    slots = set(held) | {o['code'] for o in pending if o['side'] == 'buy'}
    if len(slots) >= strategy.MAX_HOLDINGS:
        return 0
    invested = sum(v['qty'] * v['price'] for v in held.values())
    reserved = sum((o['qty'] - o.get('filled', 0) - o.get('cancelled', 0)) * o['price'] * (1 + FEE_BUFFER)
                   for o in pending if o['side'] == 'buy')
    remaining = min(strategy.TOTAL_BUDGET, equity(state, snapshot)) - strategy.CASH_RESERVE_KRW - invested - reserved
    return max(0, min(strategy.BUY_AMOUNT, int(remaining / (1 + FEE_BUFFER))))


def valid_call(call):
    if not (isinstance(call, dict) and call.get('decision') in ('buy', 'sell', 'hold')
            and isinstance(call.get('reason'), str) and bool(call['reason'].strip())):
        return False
    try:
        age = now() - research.timestamp(call['as_of'])
        return dt.timedelta(0) <= age <= dt.timedelta(minutes=30)
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def submit(act, state, path, snapshot, held, m, action, call=None, dry=False):
    import trade
    call = call or {}
    code = m['code']
    reason = call.get('reason', '손절 기준 도달')
    if any(o['code'] == code for o in active_orders(state)):
        return trade.noted(trade.label(m['name'], code), '미체결·확인 중 주문이 있어 건너뜀', reason, '안 함')
    price = m['price']
    if action == 'buy':
        requested = call.get('budget_krw', 150_000)
        if type(requested) is not int or not 0 < requested <= strategy.BUY_AMOUNT:
            raise GuardError('종목별 매수 금액은 1~200,000원의 정수여야 합니다.')
        budget = min(requested, buy_room(state, snapshot, held, code))
        qty = min(int(budget // price), broker.buyable(act, code, price)) if budget else 0
    else:
        qty = min(m['qty'], broker.sellable(act, code))
    if qty < 1:
        return trade.noted(trade.label(m['name'], code), '예산·보유한도·주문가능수량 조건으로 주문하지 않음', reason, '안 함')
    if dry:
        return trade.noted(trade.label(m['name'], code), f'{action} {qty}주 검토 (미리보기)', reason)
    denied = trade.approved(act, m, action, qty, price, reason)
    if denied:
        return trade.noted(trade.label(m['name'], code), denied, reason, '안 함')
    # 응답을 못 받았어도 재전송하지 않도록 API 호출 전에 예약을 디스크에 남깁니다.
    order = {'code': code, 'side': action, 'qty': qty, 'price': price,
             'day': now().astimezone(trade.SEOUL).strftime('%Y%m%d'),
             'submitted_at': now().isoformat(), 'filled': 0, 'amount': 0,
             'cancelled': 0, 'closed': False, 'uncertain': True}
    state['orders'].append(order)
    save(path, state)
    try:
        number = broker.order(act, action, code, qty, price)
        if not number:
            raise GuardError('주문 응답의 주문번호를 확인하지 못했습니다.')
        order['market_order_no'] = str(number)
        order['uncertain'] = False
        save(path, state)
    except Exception:
        save(path, state)
        return trade.noted(trade.label(m['name'], code), '주문 접수 여부 불명확. 재전송 없이 조회 대기', reason, '안 함')
    telegram.notify(f"{'모의' if broker.MOCK else '실전'} {m['name']} {action} {qty}주 지정가 주문 전송")
    return trade.noted(trade.label(m['name'], code), f'{qty}주 지정가 주문 전송 (체결 확인 대기)', reason,
                       '매수' if action == 'buy' else '매도')


def run(act, mode, calls=None):
    import trade
    dry = mode in ('--preview', '--account')
    snapshot = broker.domestic_snapshot(act)
    held = broker.holdings(act)
    if any(not str(c).isdigit() or len(str(c)) != 6 for c in held):
        raise GuardError('국내 주식 전용 계좌가 필요합니다.')
    day = now().astimezone(trade.SEOUL).strftime('%Y%m%d')
    by_day = {day: broker.domestic_orders(act, day)}
    path = state_path(act)
    state = load_state(path, snapshot, held, by_day[day])
    for date in {o['day'] for o in active_orders(state)} - {day}:
        by_day[date] = broker.domestic_orders(act, date)
    old_halt = state['halted']
    reconcile(state, snapshot, held, by_day)
    nav = equity(state, snapshot)
    if nav <= strategy.TOTAL_BUDGET - strategy.MAX_LOSS_KRW:
        pause(state, '초기 100만 원 대비 손실 10만 원 도달. 신규 매수 중단')
    if not dry:
        save(path, state)
        if state['halted'] and not old_halt:
            telegram.notify('NH 운용 점검: ' + state['halted'])
    research_error = ''
    try:
        catalog = research.load()
        research_status = research.status()
    except Exception:
        catalog = None
        research_error = '후보 자료를 읽지 못해 신규 매수를 중단합니다. 보유 종목 위험 점검은 계속합니다.'
        research_status = {'조사필요': True, '후보수': 0, '매수검토가능': 0, '오류': research_error}
    rows = research.companies(catalog)
    result = {'계좌': '모의투자' if broker.MOCK else '실제 계좌',
              '지금': trade.portfolio(held, min(snapshot['cash_d2'], max(0, nav))),
              '운용': {'예산': strategy.TOTAL_BUDGET, '운용자산': nav,
                       '누적손익': nav - strategy.TOTAL_BUDGET, '매수중단': state['halted'],
                       '체결동기화대기': state.get('sync_pending', False),
                       '미체결확인중': len(active_orders(state))},
              '조사': research_status, '투자원칙': strategy.INSTRUCTIONS,
              '처리함': [], '판단해줘': [], '건너뜀': [],
              '장': '열림' if trade.kr_open() else '닫힘'}
    if mode == '--account':
        result['요약'] = f'운용 예산 100만 원 · 보유 {len(held)}종목 · {state["halted"] or "조회 정상"}'
        return result
    if not trade.kr_open():
        result['요약'] = '국내장이 닫혀 주문하지 않았습니다.'
        return result
    if mode == '--do' and not isinstance(calls, dict):
        raise GuardError('종목별 판단 파일이 필요합니다.')
    codes = sorted(set(held) | set(rows)) if mode != '--do' else sorted(set(held) | set(calls))
    for code in codes:
        if code in held and not any(o['code'] == code and o['side'] == 'buy' for o in state['orders']):
            result['건너뜀'].append(f'{code}: 이 프로그램 외의 보유 종목이므로 자동 매도하지 않음')
            continue
        if code not in held and code not in rows:
            result['건너뜀'].append(f'{code}: 현재 후보가 아닙니다.')
            continue
        try:
            m = trade.context('kr', code, held, snapshot['cash_d2'])
            if m['held']:
                if m['avg'] <= 0 or m['price'] <= 0:
                    raise GuardError('손익 계산에 필요한 매입가·현재가가 없습니다.')
                m['pnl_pct'] = (m['price'] / m['avg'] - 1) * 100
            call = (calls or {}).get(code)
            forced = m['held'] and m['pnl_pct'] <= strategy.STOP_LOSS_PCT
            if forced:
                action, reason = 'sell', f'평균 매입가 대비 {m["pnl_pct"]:+.2f}%: -5% 손절'
                call = {'reason': reason}
            elif mode == '--do' and valid_call(call):
                m['ai'] = call
                action, reason = strategy.decide(m)
                if action == 'buy' and (not research.eligible(catalog, code) or not call.get('news')):
                    action, reason = 'hold', '최신 사업·재무 검증 또는 뉴스 확인이 없어 매수하지 않음'
                # 재평가 연장은 명시적 결정과 근거 출처가 있어야만 인정합니다.
                position = state['positions'].get(code)
                urls = call.get('review_sources', [])
                if position and review_due(position) and call.get('review') == 'renew':
                    if not urls or not all(research.valid_url(u) for u in urls):
                        raise GuardError('보유 연장에는 새로 확인한 근거 출처가 필요합니다.')
                    if not dry and call['decision'] == 'hold':
                        position['reviewed_at'] = now().isoformat()
                        position['review_reason'] = reason
                        position['review_sources'] = urls
            else:
                action, reason = 'hold', '추가 판단 대기'
                facts = trade.facts(m)
                facts['밸류체인'] = rows.get(code)
                facts['매수가능예산'] = buy_room(state, snapshot, held, code) if research.eligible(catalog, code) else 0
                position = state['positions'].get(code)
                facts['3주재평가필요'] = bool(position and review_due(position))
                result['판단해줘'].append(facts)
            if action in ('buy', 'sell'):
                result['처리함'].append(submit(act, state, path, snapshot, held, m, action, call, dry))
            elif mode == '--do':
                result['처리함'].append(trade.noted(trade.label(m['name'], code), '보유·관찰 유지', reason))
        except Exception as exc:
            result['건너뜀'].append(f'{code}: ' + safe_error(exc))
    if not dry:
        save(path, state)
    sent = sum(item.get('구분') in ('매수', '매도') for item in result['처리함'])
    result['요약'] = f'보유 {len(held)}/5종목 · 판단 대상 {len(result["판단해줘"])}개 · 주문 전송 {sent}개'
    if state['halted']:
        result['요약'] += ' · 신규 매수 중단: ' + state['halted']
    return result
