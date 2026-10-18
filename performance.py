"""로컬 평가 원장. 주문하지 않으며 모의/실전·전략 버전별 기록을 섞지 않습니다."""
import json
from pathlib import Path

import managed
import research
import strategy
import voting

ROOT = Path(__file__).with_name('.cache') / 'evaluation'


def folder(account_id):
    # 계좌 원문은 저장하지 않습니다. 외부에서 임의 경로를 지정하지 못하게 합니다.
    if not account_id.startswith(('mock-', 'live-')) or len(account_id.split('-')[-1]) != 16:
        raise managed.GuardError('평가 원장의 계좌 식별자를 확인하지 못했습니다.')
    if any(c not in '0123456789abcdef' for c in account_id.split('-')[-1]):
        raise managed.GuardError('평가 원장의 계좌 식별자 오류')
    return ROOT / account_id / strategy.QUANT_VERSION


def record_plan(plan):
    """매수뿐 아니라 탈락·보유·자료 실패도 원본 입력과 연결해 보존합니다."""
    stage = 'decisions' if plan.get('kind') == 'quant_vote' else 'quant-drafts'
    target = folder(plan['input']['account_id']) / stage / (plan['input']['input_id'] + '.json')
    if target.exists():
        if voting.read(target) != plan:
            raise managed.GuardError('이미 기록된 평가 입력이 달라 덮어쓰지 않습니다.')
    else:
        managed.save(target, plan)


def observe(account_id, nav, state):
    """NH 순자산 변화와 실제 확인된 체결만 기록. 계획을 가상 체결로 바꾸지 않습니다."""
    location = folder(account_id)
    clock = managed.now().isoformat()
    mark = None
    try:
        plan = voting.read(voting.PLAN)
        data = voting.validate_input(plan['input'])
        age = managed.now() - research.timestamp(data['created_at'])
        if (data['account_id'] == account_id and plan.get('kind') == 'quant_vote'
                and 0 <= age.total_seconds() <= 90 * 60):
            mark = (data.get('benchmark') or {}).get('mark')
    except Exception:
        pass  # 비교 시세 없음은 보간하지 않습니다. 계좌 관측 자체는 남깁니다.
    row = {'observed_at': clock, 'nav': nav, 'benchmark_mark': mark,
           'implementation_id': voting.policy()['implementation_id'],
           'orders': [{k: o.get(k) for k in ('code', 'side', 'qty', 'price', 'filled', 'amount', 'cancelled',
                                           'closed', 'uncertain', 'submitted_at', 'plan_id', 'signal_date', 'strategy_version')}
                      for o in state['orders']],
           'halted': bool(state.get('halted')), 'sync_pending': bool(state.get('sync_pending'))}
    managed.save(location / 'observations' / (voting.digest(row) + '.json'), row)
    return summary(location)


def summary(location):
    rows = sorted((voting.read(p) for p in (location / 'observations').glob('*.json')), key=lambda r: r['observed_at'])
    if not rows:
        return {'상태': '평가 전', '우위': '미입증'}
    start, end = rows[0], rows[-1]
    peak, drawdown = start['nav'], 0.0
    for row in rows:
        peak = max(peak, row['nav'])
        drawdown = min(drawdown, (row['nav'] / peak - 1) * 100)
    comparable = [r for r in rows if isinstance(r.get('benchmark_mark'), (int, float)) and r['benchmark_mark'] > 0]
    benchmark_return = None
    strategy_matched = None
    if comparable:
        first, last = comparable[0], comparable[-1]
        exposure = (strategy.TOTAL_BUDGET - strategy.CASH_RESERVE_KRW) / strategy.TOTAL_BUDGET
        benchmark_return = (last['benchmark_mark'] / first['benchmark_mark'] - 1) * 100 * exposure
        strategy_matched = (last['nav'] / first['nav'] - 1) * 100
    return {'상태': '모의 관측 누적 중', '우위': '미입증', '관측수': len(rows),
            '관측시작': start['observed_at'], '관측종료': end['observed_at'],
            '운용수익률': round((end['nav'] / start['nav'] - 1) * 100, 4),
            '관측최대낙폭': round(drawdown, 4), '현금기준수익률': 0.0,
            '비교ETF75현금25수익률': round(benchmark_return, 4) if benchmark_return is not None else None,
            '동일비교구간전략수익률': round(strategy_matched, 4) if strategy_matched is not None else None,
            '확인된체결주문수': sum((o.get('filled') or 0) > 0 for o in end['orders']),
            '설명': '계좌 수익은 NH 순자산 기준. 비교군은 배당·거래비용·현금이자 제외 가격 비교이며 실제 투자 아님. '
                    '관측 수는 독립 거래 수가 아니며 승률·성공 확률을 추정하지 않음.'}


if __name__ == '__main__':
    # 가장 최근 로컬 모의 계좌의 공개 가능한 요약만 표시합니다.
    folders = sorted(ROOT.glob('mock-*/' + strategy.QUANT_VERSION), key=lambda p: p.stat().st_mtime)
    print(json.dumps(summary(folders[-1]) if folders else {'상태': '평가 기록 없음', '우위': '미입증'}, ensure_ascii=False, indent=2))
