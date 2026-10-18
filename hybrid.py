"""퀀트 공통 근거 + 독립 세 판단의 다수결. 집계만 하며 주문하지 않습니다."""
import math

import broker
import managed
import quant
import research
import strategy
import voting


def aggregate(data, votes):
    baseline = quant.build(data)
    ballot = voting.aggregate(data, votes)  # 기존의 엄격한 3개 응답·독립 세션·필드 검사를 재사용
    errors = list(ballot['errors'])
    if data.get('quant_context') != quant.context(data):
        errors.append('공통 퀀트 계산 자료가 누락됐거나 실제 계산과 다릅니다.')
    actions = {}
    remaining = min(max(0, data['available_krw']), strategy.TOTAL_BUDGET - strategy.CASH_RESERVE_KRW)
    slots = min(data['available_slots'], strategy.MAX_HOLDINGS)
    pending = {o['code'] for o in data.get('execution', []) if not o.get('closed')}
    clock = research.timestamp(data['created_at'])
    today = clock.astimezone(voting.SEOUL).strftime('%Y%m%d')
    ranks = {code: n for n, code in enumerate(baseline['ranking'], 1)}
    ordered = sorted(data['stocks'], key=lambda c: (ranks.get(c, math.inf), c))
    for code in ordered:
        stock = data['stocks'][code]
        metric = baseline['metrics'].get(code)
        action = {'decision': 'hold', 'status': 'blocked' if errors else 'split',
                  'reason': '독립 응답 실패로 주문 보류' if errors else '세 의견이 갈려 보유·관찰',
                  'votes': [], 'news': stock.get('news', [])}
        if metric:
            action.update(signal_date=metric['signal_date'], score_pp=metric['score_pp'])
        if code in ranks:
            action['rank'] = ranks[code]
        if errors:
            actions[code] = action
            continue
        calls = [vote['decisions'][code] for vote in votes]
        action['votes'] = [{'voter': v['voter'], **v['decisions'][code]} for v in votes]
        winner = next((choice for choice in ('buy', 'sell', 'hold')
                       if sum(c['decision'] == choice for c in calls) >= 2), 'hold')
        winners = [c for c in calls if c['decision'] == winner]
        if len(winners) >= 2:
            action.update(decision=winner, status='consensus', reason=' / '.join(c['reason'] for c in winners))
        if stock.get('error'):
            action.update(decision='hold', status='blocked', reason='시세 조회 실패: 보유 의견으로 위장하지 않음')
        elif winner == 'buy':
            already = metric and any(o['code'] == code and o.get('side') == 'buy'
                                      and o.get('signal_date') == metric['signal_date']
                                      and o.get('strategy_version') == strategy.QUANT_VERSION
                                      for o in data.get('execution', []))
            if (stock.get('held') or not stock.get('eligible') or not metric
                    or metric['score_pp'] <= 0 or metric['avg_turnover_20'] < strategy.MIN_TURNOVER_KRW
                    or baseline['errors'] or code in pending or already
                    or (data.get('market_open') and stock.get('exchange_date') != today)):
                action.update(decision='hold', status='blocked', reason='AI 매수 합의는 있으나 퀀트·검증·자료·중복 조건 미충족')
            else:
                ceiling = min(int(stock['price'] * 1.01), *(c['max_buy_price'] for c in winners))
                amount = min(strategy.DEFAULT_BUY_KRW, strategy.BUY_AMOUNT, stock['buy_room'],
                             *(c['budget_krw'] for c in winners), int(remaining / (1 + managed.FEE_BUFFER)))
                if slots < 1 or ceiling <= 0 or amount < ceiling:
                    action.update(decision='hold', status='blocked', reason='AI 합의·퀀트는 적격이나 예산·보유·1주 가격 조건 미충족')
                else:
                    action.update(budget_krw=amount, max_buy_price=ceiling,
                                  reason=f"상대 강도 {metric['score_pp']:+.2f}%p · " + action['reason'])
                    remaining -= amount * (1 + managed.FEE_BUFFER)
                    slots -= 1
        elif winner == 'sell' and not stock.get('held'):
            action.update(decision='hold', status='blocked', reason='미보유 기업은 매도하지 않음')
        elif winner == 'hold' and stock.get('held') and stock.get('review_due'):
            prior = ballot['actions'][code]
            if metric and metric['score_pp'] > 0 and baseline['actions'][code].get('review') == 'renew' and prior.get('review') == 'renew':
                action.update(review='renew', review_sources=prior['review_sources'])
            else:
                action['reason'] += ' · 재평가 연장은 근거·점수·두 판단자의 명시적 확인 대기'
        actions[code] = action
    plan = {'version': 3, 'kind': 'quant_vote', 'strategy_version': strategy.QUANT_VERSION,
            'input': data, 'votes': votes, 'status': 'blocked' if errors or baseline['status'] != 'ready' else 'ready',
            'errors': errors + baseline['errors'], 'metrics': baseline['metrics'],
            'ranking': baseline['ranking'], 'actions': actions}
    plan['plan_id'] = voting.digest(plan)
    return plan


def validate_plan(plan, act=None, clock=None):
    if not strategy.QUANT_ENABLED or not broker.MOCK:
        raise managed.GuardError('현재 퀀트·세 AI 결합 전략은 모의 전용입니다.')
    data = voting.validate_input(plan['input'], act, clock)
    if plan.get('kind') != 'quant_vote' or plan != aggregate(data, plan.get('votes', [])):
        raise managed.GuardError('세 독립 응답과 고정 계산을 확인한 결정서가 필요합니다.')
    if plan['status'] != 'ready':
        raise managed.GuardError('세 독립 판단 또는 필수 자료가 유효하지 않아 주문을 보류합니다.')
    return plan


def load_plan(path=None, act=None):
    try:
        return validate_plan(voting.read(path or voting.PLAN), act)
    except managed.GuardError:
        raise
    except Exception:
        raise managed.GuardError('유효한 퀀트·세 AI 결정서를 읽지 못했습니다.') from None
