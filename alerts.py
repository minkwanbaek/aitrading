"""NH자동매매 flow 알림. 주문하지 않으며 계좌 원문·키를 메시지에 넣지 않는다."""
import argparse
import datetime as dt
import functools
import hashlib
import json
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import telegram

ROOT = Path(__file__).resolve().parent
HOME = ROOT / '.cache' / 'notifications'
SEOUL = ZoneInfo('Asia/Seoul')
STAGES = ('risk', 'data', 'quant', 'A', 'B', 'C', 'execute', 'aggregate', 'discovery', 'evaluation', 'coordinator')
LABELS = dict(risk='위험 점검', data='자료 수집', quant='퀀트 검증', A='독립 판단 A',
              B='독립 판단 B', C='독립 판단 C', execute='매매 실행', aggregate='의견 집계',
              discovery='기업 발굴', evaluation='평가·보고', coordinator='워크 연결')


def now():
    return dt.datetime.now(dt.timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + os.urandom(6).hex() + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def protected(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            # Telegram 예외 문자열에는 토큰이 든 URL이 포함될 수 있다.
            return {'status': 'local_error', 'error_type': type(exc).__name__}
    return wrapped


def settings(account_id=None):
    config = read(HOME / 'settings.json')
    if not config.get('enabled') or (account_id and config['account_id'] != account_id):
        return None
    return config


@protected
def emit(kind, identity, body, account_id=None):
    config = settings(account_id)
    if not config:
        return {'status': 'disabled'}
    if not telegram.configured():
        return {'status': 'unconfigured'}
    key = digest([config['account_id'], kind, identity])
    path = HOME / 'events' / (key + '.json')
    claim = HOME / 'claims' / key
    claim.parent.mkdir(parents=True, exist_ok=True)
    try:
        claim.open('x').close()  # 재실행·동시 호출도 같은 사건을 한 번만 전송한다.
    except FileExistsError:
        return {'status': 'duplicate', 'prior_status': read(path).get('status') if path.exists() else 'attempting',
                'event_id': key}
    record = {'kind': kind, 'identity': identity, 'account_id': config['account_id'],
              'created_at': now().isoformat(), 'status': 'attempting',
              'text': ('NH자동매매 flow · 모의투자\n' + body)[:3800]}
    write(path, record)  # 응답 유실도 자동 재전송하지 않도록 호출 전에 기록한다.
    try:
        response = telegram._api('sendMessage', {'chat_id': telegram.CHAT_ID, 'text': record['text']}, timeout=10)
        if not isinstance(response, dict) or not response.get('message_id'):
            raise ValueError('응답 확인 불가')
        record.update(status='delivered', message_id=response['message_id'])
    except Exception as exc:
        record.update(status='unconfirmed', error_type=type(exc).__name__)
    write(path, record)
    return {'status': record['status'], 'event_id': key}


def account_id(act):
    import managed
    return managed.state_path(act).stem


def order_id(order):
    return digest({k: order.get(k) for k in ('code', 'side', 'qty', 'price', 'day', 'submitted_at')})


@protected
def order_sent(act, order, name, reason):
    side = '매수' if order['side'] == 'buy' else '매도'
    text = (f"주문 접수 확인 · {side}\n{name} ({order['code']}) · {order['qty']}주\n"
            f"지정가 {order['price']:,.0f}원 · 주문금액 {order['price'] * order['qty']:,.0f}원\n"
            f"이유: {str(reason)[:240]}\n체결 확인 대기")
    return emit('order', order_id(order), text, account_id(act))


@protected
def order_uncertain(act, order):
    return emit('order-uncertain', order_id(order),
                f"주문 접수 확인 필요\n{order['code']} · 접수 응답을 확인하지 못했습니다.\n"
                "재전송하지 않고 증권사 조회로 대조합니다.", account_id(act))


@protected
def execution_issue(act, code):
    return emit('execution-issue', [now().astimezone(SEOUL).strftime('%Y-%m-%d-%H'), code],
                f"매매 처리 확인 필요\n{code}: 시세·결정서·주문 조건 검사에 실패했습니다.\n"
                "해당 종목을 정상 처리한 것으로 간주하지 않습니다. 실행 Work 기록을 확인해 주세요.", account_id(act))


@protected
def observe(act, state, held, preview=False):
    if preview or not settings(account_id(act)):
        return {'status': 'disabled'}
    config = settings(account_id(act))
    progress_path = HOME / 'progress' / (config['account_id'] + '.json')
    progress = read(progress_path) if progress_path.exists() else {}
    names = {code: row.get('name', code) for code, row in held.items()}
    try:
        import research
        names = {**{c: r['name'] for c, r in research.companies(research.load()).items()}, **names}
    except Exception:
        pass
    for order in state['orders']:
        identity = order_id(order)
        current = {k: int(order.get(k, 0)) for k in ('filled', 'cancelled', 'amount')}
        previous = progress.get(identity)
        if previous is None:
            # 활성화 전의 이미 끝난 체결을 새 체결로 알리지 않는다.
            previous = current if order.get('submitted_at', '') < config['activated_at'] else dict(filled=0, cancelled=0, amount=0)
        if any(current[k] < previous[k] for k in current):
            continue
        side = '매수' if order['side'] == 'buy' else '매도'
        name = names.get(order['code'], order['code'])
        increase = current['filled'] - previous['filled']
        if increase > 0:
            status = '전량 체결' if current['filled'] == order['qty'] else '일부 체결'
            average = f"누적 평균 체결가 {current['amount'] / current['filled']:,.0f}원" if current['amount'] > 0 else '체결금액 확인 필요'
            emit('fill', [identity, current['filled'], current['amount']],
                 f"{status} 확인 · {side}\n{name} · 새로 확인 {increase}주\n"
                 f"누적 {current['filled']}/{order['qty']}주 · {average}\n"
                 "증권사 조회로 확인한 결과입니다.", config['account_id'])
        cancelled = current['cancelled'] - previous['cancelled']
        if cancelled > 0:
            emit('cancel', [identity, current['cancelled']],
                 f"취소 확정 확인\n{name} · 새로 취소 확인 {cancelled}주\n"
                 f"누적 체결 {current['filled']}주 · 누적 취소 {current['cancelled']}주", config['account_id'])
        progress[identity] = current
    write(progress_path, progress)
    return {'status': 'observed'}


def round_meta(folder):
    folder = Path(folder).resolve()
    if not folder.is_relative_to((ROOT / '.cache' / 'multiflow' / 'rounds').resolve()):
        raise ValueError('작업 폴더 오류')
    meta = read(folder / 'manifest.json')
    if meta['run_id'] != folder.name or meta['mode'] not in ('mock', 'verification'):
        raise ValueError('회차 오류')
    return folder, meta


@protected
def failure(folder, stage, category='blocked'):
    folder, meta = round_meta(folder)
    if meta['mode'] == 'verification':
        return {'status': 'verification'}
    descriptions = dict(timeout='응답 시간 초과', missing='필수 결과·파일 누락',
                        blocked='자료·형식·실행 제한 검사에서 차단', tool='실행 도구 오류')
    return emit('failure', [meta['run_id'], stage],
                f"실행 보류 · {LABELS[stage]}\n{descriptions[category]}\n"
                "신규 결정서 주문은 보류합니다. 손절 위험 점검은 별도로 시도합니다.\n"
                "자세한 원인은 해당 Work 기록을 확인해 주세요.")


def catalog_view():
    import research
    data = research.load()
    rows = {}
    for code, row in research.companies(data).items():
        body = {k: row.get(k) for k in ('name', 'theme', 'role', 'linkage', 'risk', 'financial_case', 'invalidation', 'status')}
        body['eligible'] = research.eligible(data, code)
        body['sources'] = [{k: s.get(k) for k in ('url', 'summary', 'verified', 'primary', 'purpose')} for s in row.get('sources', [])]
        rows[code] = body
    return {'themes': data['themes'], 'companies': rows}


@protected
def cli_failure(argv, exc):
    for flag in ('--run', '--inspect', '--issue', '--aggregate', '--finish'):
        if flag not in argv:
            continue
        source = Path(argv[argv.index(flag) + 1])
        if flag in ('--run', '--inspect'):
            folder = source.parent
            stage = source.name.replace('.job.json', '')
        else:
            folder = source
            stage = 'aggregate' if flag == '--aggregate' else 'coordinator'
            if flag == '--issue':
                stage = argv[argv.index(flag) + 2]
        return failure(folder, stage if stage in STAGES else 'coordinator',
                       'missing' if isinstance(exc, FileNotFoundError) else 'timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else 'blocked')
    return {'status': 'no_round'}


@protected
def capture_discovery(folder):
    folder, _ = round_meta(folder)
    target = folder / 'discovery-before.json'
    if not target.exists():
        write(target, catalog_view())
    return {'status': 'captured'}


@protected
def morning(folder):
    folder, meta = round_meta(folder)
    if meta['mode'] == 'verification':
        return {'status': 'verification'}
    before, after = read(folder / 'discovery-before.json'), catalog_view()
    if before == after:
        return {'status': 'unchanged'}
    old, new = before['companies'], after['companies']
    added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
    changed = sorted(c for c in set(old) & set(new) if old[c] != new[c])
    describe = lambda codes, rows: ', '.join(rows[c]['name'] for c in codes[:5]) + (' 외' if len(codes) > 5 else '') if codes else '없음'
    body = (f"아침 후보 변경 요약\n테마: {', '.join(after['themes'])}\n"
            f"추가: {describe(added, new)}\n제외: {describe(removed, old)}\n"
            f"근거·검토상태 변경: {describe(changed, new)}\n"
            f"관찰 {len(new)}개 · 매수 검토 가능 {sum(r['eligible'] for r in new.values())}개\n"
            "매매는 장중 독립 판단과 위험 검사 후 결정합니다.")
    return emit('morning', [now().astimezone(SEOUL).date().isoformat(), digest(after)], body)


@protected
def daily(folder):
    folder, meta = round_meta(folder)
    if meta['mode'] == 'verification':
        return {'status': 'verification'}
    # 보고서가 실제 완성된 뒤에만 장 마감 보고를 보낸다.
    if not (folder / 'evaluation-report.md').is_file():
        return failure(folder, 'evaluation', 'missing')
    clock = now().astimezone(SEOUL)
    if clock.weekday() >= 5 or clock.hour < 16:
        return {'status': 'outside_report_time'}
    created = dt.datetime.fromisoformat(meta['created_at']).astimezone(SEOUL)
    if created.date() != clock.date():
        return {'status': 'stale_report'}
    config = settings()
    try:
        board = read(ROOT / '.cache' / 'board.json')
    except (OSError, ValueError):
        board = {}
    try:
        stamp = dt.datetime.strptime(board['마지막실행'], '%Y-%m-%d %H:%M').replace(tzinfo=SEOUL)
    except (KeyError, ValueError):
        stamp = clock - dt.timedelta(days=1)
    fresh = (board.get('계좌') == '모의투자' and board.get('알림계좌식별자') == config['account_id']
             and not board.get('문제') and dt.timedelta(0) <= clock - stamp <= dt.timedelta(minutes=20))
    lines = [f"장 마감 종합 보고 · {clock:%Y-%m-%d}"]
    counts = dict(order=0, fill=0, cancel=0, failure=0)
    for path in (HOME / 'events').glob('*.json'):
        try:
            event = read(path)
            date = dt.datetime.fromisoformat(event['created_at']).astimezone(SEOUL).date()
            if event['account_id'] != config['account_id'] or date != clock.date():
                continue
            kind = event['kind']
            if kind in ('order-uncertain', 'execution-issue'):
                kind = 'failure'
            if kind in counts:
                counts[kind] += 1
        except (KeyError, ValueError, OSError):
            continue
    lines.append(f"오늘 새로 기록: 주문접수 {counts['order']}건 · 체결변화 {counts['fill']}회 · "
                 f"취소확인 {counts['cancel']}회 · 실패/확인필요 {counts['failure']}건")
    lines.append('체결변화에는 부분 체결이 포함됩니다. 알림 활성화 이후 확인 기록 기준입니다.')
    if fresh:
        run = board['운용']
        lines += [f"운용자산 {run['운용자산']:,.0f}원 · 누적손익 {run['누적손익']:+,.0f}원",
                  f"보유 {board['지금']['종목수']} · 현금 {board['지금']['주문가능현금']}",
                  f"미체결 확인 중 {run['미체결확인중']}건 · 계좌 매수 중단 {'적용' if run['매수중단'] else '없음'}"]
        if run['매수중단']:
            lines.append('중단 이유: ' + str(run['매수중단'])[:160])
    else:
        lines.append('최신 계좌 조회를 확인하지 못해 잔고·손익 표시는 보류합니다.')
    summary = folder / 'telegram-summary.txt'
    if summary.exists():
        lines += ['평가·개선 요약', summary.read_text(encoding='utf-8')[:600]]
    else:
        lines.append('평가 보고서 작성 완료. 상세 내용은 NH 평가 개선 Work에서 확인하세요.')
    lines.append('수익 우위는 아직 미입증입니다.')
    return emit('daily', clock.date().isoformat(), '\n'.join(lines), config['account_id'])


@protected
def activate():
    import broker
    import managed
    if not broker.MOCK or not telegram.configured():
        raise ValueError('모의 계좌와 텔레그램 연결 필요')
    act = broker.account()
    identity = account_id(act)
    existing = HOME / 'settings.json'
    if existing.exists() and read(existing).get('account_id') == identity and read(existing).get('enabled'):
        return {'status': 'already_enabled'}
    # 활성화 이전 누적 체결은 기준으로만 저장한다. 원장은 수정하지 않는다.
    state_file = managed.state_path(act)
    state = read(state_file) if state_file.exists() else {'orders': []}
    progress = {order_id(o): {k: int(o.get(k, 0)) for k in ('filled', 'cancelled', 'amount')} for o in state['orders']}
    write(HOME / 'progress' / (identity + '.json'), progress)
    write(existing, {'enabled': True, 'account_id': identity, 'activated_at': now().isoformat()})
    return {'status': 'enabled'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--activate', action='store_true')
    group.add_argument('--test', action='store_true')
    group.add_argument('--status', action='store_true')
    group.add_argument('--daily')
    group.add_argument('--failure')
    parser.add_argument('--stage', choices=STAGES, default='coordinator')
    parser.add_argument('--category', choices=('timeout', 'missing', 'blocked', 'tool'), default='blocked')
    args = parser.parse_args()
    if args.activate:
        result = activate()
    elif args.status:
        statuses = {}
        for path in (HOME / 'events').glob('*.json'):
            value = read(path)
            status = value.get('status', 'unknown')
            statuses[status] = statuses.get(status, 0) + 1
        result = {'status': 'ok', 'events': statuses}
    elif args.test:
        result = emit('test', ['notifications-v1', now().astimezone(SEOUL).date().isoformat()],
                      '알림 연결 테스트\n아침 후보 변경 · 주문/체결 · 실행 실패 · 장 마감 보고를 연결했습니다.\n이 메시지는 연결 테스트이며 주문을 실행하지 않았습니다.')
    elif args.daily:
        result = daily(args.daily)
    else:
        result = failure(args.failure, args.stage, args.category)
    print(json.dumps(result, ensure_ascii=False))
    if result['status'] in ('local_error', 'unconfirmed', 'unconfigured'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
