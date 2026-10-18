"""역할별 로컬 Work의 공유 작업·완료 기록. 기존 매매 엔진을 우회하지 않는다."""
import argparse
import datetime as dt
import json
import subprocess
import sys
import uuid
from pathlib import Path

import collection
import managed
import research
import voting

ROOT = Path(__file__).resolve().parent
HOME = ROOT / '.cache' / 'multiflow'
ROLES = ('discovery', 'data', 'quant', 'A', 'B', 'C', 'execution', 'evaluation')


def save_once(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(payload)
    except FileExistsError:
        if voting.read(path) != value:
            raise managed.GuardError('완료된 작업·자료를 덮어쓸 수 없습니다.') from None


def inside(path):
    path = Path(path).resolve()
    if not path.is_relative_to((HOME / 'rounds').resolve()):
        raise managed.GuardError('공통 작업 폴더 밖의 작업입니다.')
    return path


def ready(role):
    result = {'role': role, 'root': str(ROOT), 'plan_hash': voting.digest(
        (ROOT / 'MULTIFLOW_PLAN.md').read_text(encoding='utf-8')), 'ready_at': managed.now().isoformat()}
    managed.save(HOME / 'ready' / (role + '.json'), result)
    return result


def start(mode):
    with managed.cycle_lock():
        return _start(mode)


def _start(mode):
    import broker
    if not broker.MOCK:
        raise managed.GuardError('멀티 워크는 모의 환경 전용입니다.')
    active = HOME / 'active.json'
    if active.exists():
        previous = voting.read(active)
        folder = inside(previous['folder'])
        manifest = voting.read(folder / 'manifest.json')
        if not (folder / 'finished.json').exists() and research.timestamp(manifest['expires_at']) > managed.now():
            raise managed.GuardError('진행 중인 회차가 있습니다. 새 회차 대신 기존 상태를 확인하세요.')
    identity = uuid.uuid4().hex
    folder = HOME / 'rounds' / identity
    result = {'run_id': identity, 'mode': mode, 'created_at': managed.now().isoformat(),
              'expires_at': (managed.now() + dt.timedelta(minutes=75)).isoformat(),
              'policy_id': voting.digest(voting.policy()), 'root': str(ROOT)}
    save_once(folder / 'manifest.json', result)
    managed.save(active, {'folder': str(folder)})
    return {'folder': str(folder), **result}


def manifest(folder):
    folder = inside(folder)
    value = voting.read(folder / 'manifest.json')
    if (value['run_id'] != folder.name or value['root'] != str(ROOT)
            or value['policy_id'] != voting.digest(voting.policy())
            or not research.timestamp(value['created_at']) <= managed.now() < research.timestamp(value['expires_at'])
            or (folder / 'finished.json').exists()):
        raise managed.GuardError('회차·정책·공통 루트가 다르거나 회차가 만료·종료됐습니다.')
    return folder, value


def done(folder, key):
    job_path = folder / (key + '.job.json')
    job = voting.read(job_path)
    receipt = voting.read(folder / (key + '.done.json'))
    if (receipt['job_hash'] != voting.digest(job) or receipt['run_id'] != folder.name
            or receipt['role'] != job['role'] or receipt['status'] != 'done'):
        raise managed.GuardError('선행 작업의 완료 기록이 일치하지 않습니다.')
    return receipt


def bound(folder):
    binding = voting.read(folder / 'input.json')
    path = Path(binding['input_file'])
    data = voting.validate_input(voting.read(path))
    if path.resolve() != (ROOT / '.cache' / 'voting' / data['input_id'] / 'input.json').resolve():
        raise managed.GuardError('정식 불변 수집 자료 경로가 아닙니다.')
    if binding['input_id'] != data['input_id']:
        raise managed.GuardError('회차의 공통 자료가 바뀌었습니다.')
    _, current = collection.load_current()
    if current['input_id'] != data['input_id']:
        raise managed.GuardError('다른 수집 회차가 게시되어 현재 회차를 보류합니다.')
    return path, data


def issue(folder, stage):
    folder, meta = manifest(folder)
    role = 'execution' if stage in ('risk', 'execute') else stage
    if role not in ROLES:
        raise managed.GuardError('알 수 없는 역할입니다.')
    input_id = None
    source = None
    if stage == 'data':
        done(folder, 'risk')
    if stage in ('quant', 'A', 'B', 'C', 'execute'):
        source, data = bound(folder)
        input_id = data['input_id']
        done(folder, 'data')
    if stage in ('A', 'B', 'C', 'execute'):
        receipt = done(folder, 'quant')
        if receipt['result']['input_id'] != input_id:
            raise managed.GuardError('퀀트 봉인과 입력이 다릅니다.')
        if stage in ('A', 'B', 'C') and meta['mode'] != 'verification' and receipt['result']['gate']['decision'] != 'request':
            raise managed.GuardError('새 독립 판단이 필요한 회차가 아닙니다.')
    if stage == 'execute':
        gate = done(folder, 'quant')['result']['gate']
        if gate['decision'] == 'blocked':
            raise managed.GuardError('판단 점검이 보류됐습니다.')
        if gate['decision'] == 'request' or meta['mode'] == 'verification':
            aggregate_check(folder)
    result = {'run_id': meta['run_id'], 'mode': meta['mode'], 'stage': stage, 'role': role,
              'input_id': input_id, 'input_file': str(source) if source else None,
              'instructions_file': str(ROOT / 'voter-instructions.md') if role in ('A', 'B', 'C') else None,
              'vote_file': str(folder / (role + '.vote.json')) if role in ('A', 'B', 'C') else None,
              'report_file': str(folder / 'evaluation-report.md') if role == 'evaluation' else None}
    path = folder / (stage + '.job.json')
    save_once(path, result)
    return {'job_file': str(path), **result}


def inspect(path, role):
    path = inside(path)
    folder, meta = manifest(path.parent)
    job = voting.read(path)
    if job['role'] != role or job['run_id'] != meta['run_id'] or job['mode'] != meta['mode']:
        raise managed.GuardError('배정 역할·회차가 다릅니다.')
    if path.name != job['stage'] + '.job.json':
        raise managed.GuardError('작업 이름이 다릅니다.')
    expected = issue(folder, job['stage'])
    if {k: v for k, v in expected.items() if k != 'job_file'} != job:
        raise managed.GuardError('원본 작업 명세가 달라졌습니다.')
    return folder, job


def command(*args):
    # 자식 명령의 원문은 계좌 정보를 포함할 수 있어 화면이나 완료 기록에 복사하지 않는다.
    result = subprocess.run([sys.executable, '-X', 'utf8', *map(str, args)], cwd=ROOT,
                            capture_output=True, encoding='utf-8', timeout=240)
    if result.returncode:
        raise managed.GuardError('기존 검사·수집·매매 도구가 실패했습니다. 주문을 보류합니다.')


def run(path, role):
    try:
        return _run(path, role)
    except Exception as exc:
        # 실패도 회차별로 보존한다. 성공/보유 의견으로 바꾸지 않는다.
        target = inside(path)
        failure = target.parent / (target.name.replace('.job.json', '') + '.failed.json')
        if not failure.exists():
            save_once(failure, {'role': role, 'status': 'error', 'reason': managed.safe_error(exc),
                                'failed_at': managed.now().isoformat()})
        raise


def _run(path, role):
    import broker
    import quant
    import cycle
    if not broker.MOCK:
        raise managed.GuardError('모의 환경만 지원합니다.')
    folder, job = inspect(path, role)
    receipt_path = folder / (job['stage'] + '.done.json')
    if receipt_path.exists():
        return done(folder, job['stage'])  # 완료된 부작용을 재실행하지 않는다.
    # 접수/중단/응답 불명 시 자동 재전송 금지. 새 회차의 기존 엔진 대조에 맡긴다.
    save_once(folder / (job['stage'] + '.started.json'), {'job_hash': voting.digest(job)})
    if (folder / (job['stage'] + '.attempted')).exists():
        raise managed.GuardError('이미 시작된 작업입니다. 불명 응답을 재실행하지 않습니다.')
    (folder / (job['stage'] + '.attempted')).touch(exist_ok=False)
    stage = job['stage']
    if stage == 'risk':
        command(ROOT / 'trade.py', '--preview' if job['mode'] == 'verification' else '--scan')
        result = {'risk_checked': True, 'no_orders': job['mode'] == 'verification'}
    elif stage == 'data':
        command(ROOT / 'news.py')
        path, data = collection.publish(voting.read(ROOT / '.cache' / 'research-news.json'))
        save_once(folder / 'input.json', {'input_id': data['input_id'], 'input_file': str(path)})
        result = {'input_id': data['input_id'], 'stocks': len(data['stocks'])}
    elif stage == 'quant':
        _, data = bound(folder)
        if data['quant_context'] != quant.context(data):
            raise managed.GuardError('공통 퀀트 결과를 재현하지 못했습니다.')
        result = {'input_id': data['input_id'], 'quant_hash': voting.digest(data['quant_context']), 'gate': cycle.gate()}
    elif role in ('A', 'B', 'C'):
        _, data = bound(folder)
        vote = voting.read(job['vote_file'])
        error = voting.vote_error(vote, data)
        if vote.get('voter') != role:
            error = '다른 판단자의 응답입니다.'
        if error:
            raise managed.GuardError(str(error))
        result = {'input_id': data['input_id'], 'vote_hash': voting.digest(vote), 'session_id': vote['session_id']}
    elif stage == 'execute':
        if job['mode'] == 'verification':
            result = {'no_orders': True, 'reason': '검증 전용 회차: 실행 엔진 호출 안 함'}
        else:
            command(ROOT / 'cycle.py', '--execute')
            command(ROOT / 'trade.py', '--account')
            result = {'engine_checked': True}
    elif stage == 'discovery':
        research.validate(research.load())
        result = research.status()
    elif stage == 'evaluation':
        import performance
        locations = sorted(performance.ROOT.glob('mock-*/' + __import__('strategy').QUANT_VERSION),
                           key=lambda p: p.stat().st_mtime)
        result = {'performance': performance.summary(locations[-1]) if locations else {'우위': '미입증'},
                  'completed_stages': sorted(p.stem.replace('.done', '') for p in folder.glob('*.done.json')),
                  'verification': job['mode'] == 'verification'}
        managed.save(folder / 'evaluation-summary.json', result)
    else:
        raise managed.GuardError('허용되지 않은 단계입니다.')
    receipt = {'run_id': job['run_id'], 'role': role, 'job_hash': voting.digest(job), 'status': 'done',
               'completed_at': managed.now().isoformat(), 'result': result}
    save_once(receipt_path, receipt)
    return receipt


def aggregate_check(folder):
    _, data = bound(folder)
    votes = []
    for role in ('A', 'B', 'C'):
        receipt = done(folder, role)
        vote = voting.read(folder / (role + '.vote.json'))
        if receipt['result']['vote_hash'] != voting.digest(vote) or receipt['result']['input_id'] != data['input_id']:
            raise managed.GuardError('제출한 원문 투표가 바뀌었습니다.')
        votes.append(vote)
    import hybrid
    plan = hybrid.aggregate(data, votes)
    if plan['status'] != 'ready':
        raise managed.GuardError('독립 세 투표가 유효하지 않습니다.')
    return plan


def aggregate(folder):
    folder, meta = manifest(folder)
    done(folder, 'quant')
    plan = aggregate_check(folder)
    if meta['mode'] != 'verification':
        if done(folder, 'quant')['result']['gate']['decision'] != 'request':
            raise managed.GuardError('새 결정서를 게시할 회차가 아닙니다.')
        import performance
        performance.record_plan(plan)
        managed.save(voting.PLAN, plan)
        managed.save(ROOT / '.cache' / 'voting' / plan['input']['input_id'] / 'plan.json', plan)
    save_once(folder / 'aggregate.json', plan)
    return {'status': plan['status'], 'input_id': plan['input']['input_id'], 'verification': meta['mode'] == 'verification',
            'votes': len(plan['votes']), 'actions': {c: a['decision'] for c, a in plan['actions'].items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--ready', choices=ROLES)
    group.add_argument('--start', choices=('mock', 'verification'))
    group.add_argument('--issue', nargs=2, metavar=('FOLDER', 'STAGE'))
    group.add_argument('--inspect')
    group.add_argument('--run')
    group.add_argument('--aggregate')
    group.add_argument('--finish')
    group.add_argument('--status', action='store_true')
    parser.add_argument('--role', choices=ROLES)
    args = parser.parse_args()
    if args.ready:
        result = ready(args.ready)
    elif args.start:
        result = start(args.start)
    elif args.issue:
        result = issue(*args.issue)
    elif args.inspect or args.run:
        if not args.role:
            parser.error('--role 필요')
        result = inspect(args.inspect, args.role)[1] if args.inspect else run(args.run, args.role)
    elif args.aggregate:
        result = aggregate(args.aggregate)
    elif args.finish:
        folder, meta = manifest(args.finish)
        result = {'run_id': meta['run_id'], 'finished_at': managed.now().isoformat()}
        save_once(folder / 'finished.json', result)
    else:
        registry = HOME / 'workers.json'
        result = {'workers': voting.read(registry) if registry.exists() else {},
                  'active': voting.read(HOME / 'active.json') if (HOME / 'active.json').exists() else None}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'reason': managed.safe_error(exc)}, ensure_ascii=False))
        raise SystemExit(1)
