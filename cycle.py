"""장중 회차의 판단 필요성 검사와 검증된 결정서 실행 연결. 투표를 흉내 내지 않습니다."""
import argparse
import datetime as dt
import json
import subprocess
import sys

import broker
import collection
import managed
import research
import voting

AUDIT = voting.ROOT / '.cache' / 'gate.json'
PRICE_CHANGE_PCT = 2.0  # 마지막 실제 판단 가격 대비 누적 변화. 투자 한도를 바꾸는 값이 아님.


def evidence(stock):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in ('checked_at', 'observed_at')}
        if isinstance(value, list):
            return sorted((clean(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
        return value
    return clean({'company': stock.get('company'), 'eligible': stock.get('eligible'), 'news': stock.get('news', [])})


def inspect(data, previous=None):
    """순수 규칙 검사. 변화가 없는 경우에도 새 AI 보유 의견으로 표시하지 않습니다."""
    result = {'version': 1, 'input_id': data['input_id'], 'checked_at': managed.now().isoformat(),
              'decision': 'skip', 'reasons': [], 'execute_existing': False, 'previous_plan_id': None}
    relevant = {c: s for c, s in data['stocks'].items() if s.get('held') or s.get('eligible')}
    if not relevant:
        result['reasons'] = ['보유 기업과 사업·재무 검증 완료 기업이 없어 AI 호출 생략']
        return result
    if any(s.get('error') for s in relevant.values()):
        result.update(decision='blocked', reasons=['판단 대상의 시세 수집 실패. 새 의견을 만들지 않음'])
        return result
    if data.get('news_status') != 'ok' or set(relevant) & set(data.get('news_failed_codes', [])):
        result.update(decision='blocked', reasons=['이번 회차 뉴스 수집 실패·미확인. 보유 의견으로 위장하지 않음'])
        return result
    if previous is None:
        result.update(decision='request', reasons=['유효한 이전 결정서가 없어 독립 판단 필요'])
        return result
    old = previous['input']
    result['previous_plan_id'] = previous['plan_id']
    old_relevant = {c for c, s in old['stocks'].items() if s.get('held') or s.get('eligible')}
    reasons = result['reasons']
    if set(relevant) != old_relevant:
        reasons.append('보유·검증 완료 기업 구성 변경')
    if data.get('execution', []) != old.get('execution', []):
        reasons.append('주문·체결·취소 상태 변경')
    for field in ('매수중단', '체결동기화대기', '미체결확인중'):
        if data.get('account', {}).get(field) != old.get('account', {}).get(field):
            reasons.append('계좌 위험·미체결 상태 변경')
            break
    pending = {o['code'] for o in data.get('execution', []) if not o.get('closed')}
    for code, stock in relevant.items():
        before = old['stocks'].get(code, {})
        if evidence(stock) != evidence(before):
            reasons.append(code + ': 뉴스·사업·재무 근거 변경')
        if stock.get('signals') != before.get('signals'):
            reasons.append(code + ': 이동평균·RSI·MACD·매수 제외 조건 변화')
        old_price = before.get('price', 0)
        if old_price <= 0 or abs(stock['price'] / old_price - 1) * 100 >= PRICE_CHANGE_PCT - 1e-9:
            reasons.append(code + ': 마지막 판단 가격 대비 2% 이상 변화')
        facts, prior = stock.get('facts', {}), before.get('facts', {})
        if any(facts.get(k) != prior.get(k) for k in ('수량', '평균매입가')):
            reasons.append(code + ': 보유 수량·평균매입가 변경')
        if stock.get('review_due'):
            reasons.append(code + ': 21일 보유 근거 재평가 필요')
        call = previous['actions'].get(code, {})
        if code not in pending and ((call.get('decision') == 'buy' and not stock.get('held') and stock.get('buy_room', 0) > 0)
                                   or (call.get('decision') == 'sell' and stock.get('held'))):
            reasons.append(code + ': 아직 실행되지 않은 매매 의견 재검토')
    if reasons:
        result['decision'] = 'request'
    else:
        result.update(reasons=['중요한 변화·미실행 후보·재평가 대상이 없어 AI 호출 생략'], execute_existing=True)
    return result


def gate():
    _, data = collection.load_current()
    try:
        previous = voting.load_plan()
        if previous['input']['account_id'] != data['account_id']:
            previous = None
    except managed.GuardError:
        previous = None
    result = inspect(data, previous)
    managed.save(AUDIT, result)
    managed.save(voting.ROOT / '.cache' / 'voting' / data['input_id'] / 'gate.json', result)
    return result


def execution_plan():
    """자료와 점검 결과와 결정서가 같은 회차인지 확인. 실패하면 이전 매수 의견도 실행하지 않음."""
    if not broker.MOCK:
        raise managed.GuardError('이 장중 작업은 모의투자 전용입니다.')
    _, data = collection.load_current()
    audit = voting.read(AUDIT)
    age = managed.now() - research.timestamp(audit['checked_at'])
    if audit['input_id'] != data['input_id'] or not dt.timedelta(0) <= age <= dt.timedelta(minutes=30):
        raise managed.GuardError('회차 자료와 판단 점검이 다르거나 점검이 만료됐습니다.')
    if audit['decision'] == 'blocked' or (audit['decision'] == 'skip' and not audit['execute_existing']):
        return None
    plan = voting.load_plan()
    if audit['decision'] == 'request':
        if plan['input']['input_id'] != data['input_id']:
            raise managed.GuardError('이번 회차 독립 판단이 완료되지 않아 기존 결정서는 실행하지 않습니다.')
    elif audit['decision'] != 'skip' or plan['plan_id'] != audit['previous_plan_id']:
        raise managed.GuardError('판단을 생략한 회차의 기존 결정서가 일치하지 않습니다.')
    return voting.PLAN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--gate', action='store_true')
    group.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.gate:
        print(json.dumps(gate(), ensure_ascii=False, indent=2))
    else:
        path = execution_plan()
        if path is None:
            print(json.dumps({'상태': '판단 생략·보류: 신규 결정서 주문 없음. 손절은 별도 점검.'}, ensure_ascii=False))
        else:
            subprocess.run([sys.executable, str(voting.ROOT / 'trade.py'), '--do', str(path)],
                           cwd=voting.ROOT, check=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'상태': '회차 주문 보류', '이유': managed.safe_error(exc)}, ensure_ascii=False))
        raise SystemExit(1)
