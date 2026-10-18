"""검증 중인 단순 상대 강도 전략. 계산·결정서 작성만 하며 주문하지 않습니다."""
import argparse
import datetime as dt
import json
import math

import broker
import managed
import research
import strategy
import voting


def completed(bars, clock):
    """오늘 미완성 봉 제외. 중복·비정상 값·자본 변동을 조용히 보정하지 않습니다."""
    today = clock.astimezone(voting.SEOUL).strftime('%Y%m%d')
    clean, dates = [], set()
    for bar in bars:
        day = bar['date']
        dt.datetime.strptime(day, '%Y%m%d')
        if day > today or day in dates:
            raise ValueError('미래 또는 중복 일봉')
        dates.add(day)
        if day == today:
            continue
        price = bar['close']
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise ValueError('종가 오류')
        clean.append(bar)
    clean.sort(key=lambda b: b['date'])
    needed = strategy.QUANT_DAYS + 1
    if len(clean) < needed:
        raise ValueError('완료된 61개 일봉 부족')
    clean = clean[-needed:]
    age = clock.astimezone(voting.SEOUL).date() - dt.datetime.strptime(clean[-1]['date'], '%Y%m%d').date()
    if not 1 <= age.days <= 7:
        raise ValueError('최근 완료 일봉이 오래됨')
    for bar in clean:
        if bar.get('capital_event', '00') not in ('', '0', '00') or bar.get('ex_event', '00') in ('01', '04'):
            raise ValueError('분할·병합·권리락: 가격 조정 확인 필요')
    return clean


def measure(stock, benchmark, clock):
    bars = completed(stock.get('bars', []), clock)
    if [b['date'] for b in bars] != [b['date'] for b in benchmark]:
        raise ValueError('기업과 비교 ETF의 61개 거래일 불일치')
    liquidity = [b.get('turnover') for b in bars[-20:]]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in liquidity):
        raise ValueError('20일 거래대금 부족·오류')
    for bar in bars[-20:]:
        volume, low, high = bar.get('volume', 0), bar.get('low', 0), bar.get('high', 0)
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
                   for v in (volume, low, high)) or low > high:
            raise ValueError('거래량·고저가 오류')
        if not low * volume * .99 <= bar['turnover'] <= high * volume * 1.01:
            raise ValueError('일봉 거래대금 단위 또는 가격·거래량 불일치')
    own = (bars[-1]['close'] / bars[0]['close'] - 1) * 100
    market = (benchmark[-1]['close'] / benchmark[0]['close'] - 1) * 100
    return {'signal_date': bars[-1]['date'], 'return_pct': own, 'benchmark_return_pct': market,
            'score_pp': own - market, 'avg_turnover_20': sum(liquidity) / 20,
            'last_close': bars[-1]['close']}


def renewal_sources(stock, clock):
    sources = (stock.get('company') or {}).get('sources', [])
    valid = [s for s in sources if s.get('primary') is True and s.get('verified') is True
             and s.get('purpose') in ('business', 'financial') and research.valid_url(s.get('url'))
             and s.get('checked_at') and dt.timedelta(0) <= clock - research.timestamp(s['checked_at']) <= dt.timedelta(days=7)]
    return sorted({s['url'] for s in valid}) if {s['purpose'] for s in valid} >= {'business', 'financial'} else []


def build(data):
    """동일 입력이면 동일 결정. 표·기사 유무·AI 자신감으로 순위를 바꾸지 않습니다."""
    clock = research.timestamp(data['created_at'])
    actions, metrics, errors, buy_errors = {}, {}, [], []
    try:
        if (data.get('benchmark') or {}).get('code') != strategy.BENCHMARK_CODE:
            raise ValueError('비교 ETF 누락')
        benchmark = completed(data['benchmark']['bars'], clock)
    except (ValueError, KeyError, TypeError):
        benchmark = []
        errors.append('비교 ETF의 완료 일봉을 검증하지 못했습니다.')
    for code, stock in sorted(data['stocks'].items()):
        action = {'decision': 'hold', 'status': 'blocked', 'reason': '자료 부족으로 관찰', 'news': stock.get('news', [])}
        try:
            if stock.get('error') or not benchmark:
                raise ValueError('시세·비교 자료 실패')
            metric = measure(stock, benchmark, clock)
            metrics[code] = metric
            score = metric['score_pp']
            action.update(status='quant', reason=f"60거래일 상대 강도 {score:+.2f}%p: 관찰 유지",
                          signal_date=metric['signal_date'], score_pp=score)
            if stock.get('held'):
                if stock.get('review_due'):
                    if score <= 0:
                        action.update(decision='sell', reason=f'21일 재평가: 상대 강도 {score:+.2f}%p로 0 이하')
                    else:
                        urls = renewal_sources(stock, clock)
                        if stock.get('eligible') and urls:
                            action.update(review='renew', review_sources=urls,
                                          reason=f'21일 재평가: 상대 강도 {score:+.2f}%p·최신 사업/재무 확인, 보유 연장')
                        else:
                            action.update(status='blocked', reason='21일 재평가: 사업·재무 확인 대기, 연장 완료 아님')
                else:
                    action['reason'] = f'재평가 전 보유 유지 (상대 강도 {score:+.2f}%p). -4% 손절은 별도 우선'
            elif not stock.get('eligible'):
                action['reason'] = '사업·재무 검증이 끝나지 않아 관찰'
            elif metric['avg_turnover_20'] < strategy.MIN_TURNOVER_KRW:
                action['reason'] = '완료된 최근 20거래일 평균 거래대금이 100억 원 미만'
            elif score <= 0:
                action['reason'] = f'상대 강도 {score:+.2f}%p로 0 이하: 신규 매수 안 함'
        except (ValueError, KeyError, TypeError, OverflowError):
            if stock.get('held') or stock.get('eligible'):
                buy_errors.append(code + ': 날짜·가격·거래대금 자료 확인 필요')
        actions[code] = action
    relevant = {c for c, s in data['stocks'].items() if s.get('held') or s.get('eligible')}
    if relevant and (data.get('news_status') != 'ok' or relevant & set(data.get('news_failed_codes', []))):
        buy_errors.append('뉴스 조회 실패·미확인 (검색 성공의 빈 결과는 허용)')
        for code in relevant:
            if actions[code].get('review') == 'renew':
                actions[code].pop('review')
                actions[code].pop('review_sources', None)
                actions[code].update(status='blocked', reason='뉴스 조회 실패로 재평가 연장 보류')
    errors.extend(buy_errors)
    ranked = sorted((c for c, m in metrics.items() if data['stocks'][c].get('eligible') and m['score_pp'] > 0
                     and m['avg_turnover_20'] >= strategy.MIN_TURNOVER_KRW),
                    key=lambda c: (-metrics[c]['score_pp'], c))
    remaining = min(max(0, data['available_krw']), strategy.TOTAL_BUDGET - strategy.CASH_RESERVE_KRW)
    slots = min(data['available_slots'], strategy.MAX_HOLDINGS)
    pending = {o['code'] for o in data.get('execution', []) if not o.get('closed')}
    for rank, code in enumerate(ranked, 1):
        stock, action = data['stocks'][code], actions[code]
        action['rank'] = rank
        if stock.get('held'):
            continue  # 순위 변경만으로 갈아타거나 물타기하지 않습니다.
        if errors:
            action.update(status='blocked', reason='필수 자료 확인 실패로 이번 회차 신규 매수 보류')
            continue
        today = clock.astimezone(voting.SEOUL).strftime('%Y%m%d')
        if data.get('market_open') and stock.get('exchange_date') != today:
            action.update(status='blocked', reason='오늘 거래소 시세인지 확인하지 못해 신규 매수 보류')
            continue
        if any(o['code'] == code and o.get('side') == 'buy' and o.get('signal_date') == action['signal_date']
               and o.get('strategy_version') == strategy.QUANT_VERSION for o in data.get('execution', [])):
            action['reason'] = '같은 신호일에 이미 매수를 시도해 재진입하지 않음'
            continue
        ceiling = int(stock['price'] * 1.01)
        amount = min(strategy.DEFAULT_BUY_KRW, strategy.BUY_AMOUNT, stock.get('buy_room', 0), int(remaining / (1 + managed.FEE_BUFFER)))
        if code in pending or slots < 1 or ceiling <= 0 or amount < ceiling:
            action['reason'] = '순위는 적격이나 예산·1주 가격·보유/미체결 한도로 대기'
            continue
        action.update(decision='buy', budget_krw=amount, max_buy_price=ceiling,
                      reason=f"상대 강도 {action['score_pp']:+.2f}%p, 적격 후보 {rank}위")
        remaining -= amount * (1 + managed.FEE_BUFFER)
        slots -= 1
    plan = {'version': 2, 'kind': 'quant', 'strategy_version': strategy.QUANT_VERSION,
            'input': data, 'status': 'ready' if benchmark else 'blocked',
            'errors': errors, 'metrics': metrics, 'ranking': ranked, 'actions': actions}
    plan['plan_id'] = voting.digest(plan)
    return plan


def context(data):
    """AI 세 판단자에게 똑같이 주는 계산 근거. 아직 매매 결정서가 아닙니다."""
    proposal = build(data)
    return {'strategy_version': strategy.QUANT_VERSION,
            'formula': '완료된 60거래일 기업 가격 수익률 - KODEX 200 ETF 가격 수익률 (%p)',
            'metrics': proposal['metrics'], 'ranking': proposal['ranking'], 'errors': proposal['errors'],
            'note': '양수 점수는 매수 명령이 아님. 세 AI가 같은 자료를 독립 검토하며 확률로 해석하지 않음.'}


def validate_plan(plan, act=None, clock=None):
    if not strategy.QUANT_ENABLED or not broker.MOCK:
        raise managed.GuardError('현재 퀀트 전략은 승인된 모의투자에서만 실행합니다.')
    data = voting.validate_input(plan['input'], act, clock)
    if plan != build(data) or plan.get('kind') != 'quant':
        raise managed.GuardError('결정서가 고정 퀀트 계산과 일치하지 않습니다.')
    if plan['status'] != 'ready':
        raise managed.GuardError('비교 자료 실패로 퀀트 결정을 보류합니다.')
    return plan


def load_plan(path=None, act=None):
    try:
        return validate_plan(voting.read(path or voting.PLAN), act)
    except managed.GuardError:
        raise
    except Exception:
        raise managed.GuardError('유효한 퀀트 결정서를 읽지 못했습니다.') from None


def publish(data):
    voting.validate_input(data)
    plan = build(data)
    target = voting.ROOT / '.cache' / 'voting' / data['input_id'] / 'quant-plan.json'
    managed.save(target, plan)
    import performance
    performance.record_plan(plan)  # 평가 기록을 보존한 뒤에만 실행용 결정서 게시
    # 세 독립 응답 없는 계산은 실행용 PLAN에 게시하지 않습니다.
    return plan


def describe(plan):
    return {'상태': plan['status'], '전략': plan['strategy_version'],
            '유효독립응답': 3 if plan.get('kind') == 'quant_vote' and plan['status'] == 'ready' else 0,
            '매수': sum(a['decision'] == 'buy' for a in plan['actions'].values()),
            '매도': sum(a['decision'] == 'sell' for a in plan['actions'].values()),
            '자료문제': plan['errors'], '순위': [
                {'기업': (plan['input']['stocks'][c].get('company') or {}).get('name', c),
                 '점수': round(plan['metrics'][c]['score_pp'], 2), '판단': plan['actions'][c]['decision'],
                 '이유': plan['actions'][c]['reason']} for c in plan['ranking']],
            '관찰기업수': len(plan['input']['stocks']), '계산완료기업수': len(plan['metrics']),
            '사업재무검증완료': sum(s.get('eligible', False) for s in plan['input']['stocks'].values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    import collection
    _, data = collection.load_current()
    if not args.preview:
        raise managed.GuardError('퀀트 단독으로 실행용 결정서를 만들지 않습니다. 세 독립 응답을 먼저 집계해야 합니다.')
    plan = build(data)
    print(json.dumps(describe(plan), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'상태': '퀀트 판단 보류', '이유': managed.safe_error(exc)}, ensure_ascii=False))
        raise SystemExit(1)
