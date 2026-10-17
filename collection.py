"""데이터 수집 완료본을 게시하고 읽습니다. 기업 선정·투표·주문은 하지 않습니다."""
import argparse
import datetime as dt
import json
import re
from pathlib import Path

import managed
import research
import voting

ROOT = Path(__file__).resolve().parent
LATEST = ROOT / '.cache' / 'collection.json'
MAX_SELECTION_AGE = dt.timedelta(minutes=90)


def load_current(clock=None):
    """완료 표식과 불변 입력을 함께 검증합니다. 외부 조회나 파일 수정은 하지 않습니다."""
    clock = clock or managed.now()
    try:
        receipt = voting.read(LATEST)
        identity = receipt['input_id']
        if receipt['version'] != 1 or not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
            raise ValueError('형식')
        path = ROOT / '.cache' / 'voting' / identity / 'input.json'
        data = voting.validate_input(voting.read(path), clock=clock)
        created = research.timestamp(data['created_at'])
        published = research.timestamp(receipt['published_at'])
        if data['input_id'] != identity or receipt['collected_at'] != data['created_at']:
            raise ValueError('입력 불일치')
        if not created <= published <= clock or clock - created > MAX_SELECTION_AGE:
            raise managed.GuardError('기업선정에 쓸 수집 완료본이 90분을 넘었거나 게시 시각이 잘못됐습니다.')
        return path, data
    except managed.GuardError:
        raise
    except Exception:
        raise managed.GuardError('검증된 수집 완료본이 없습니다. 기업선정을 보류합니다.') from None


def publish(news=None):
    """기존 읽기 전용 조회 도구를 끝낸 뒤에만 원자적으로 완료 표식을 교체합니다."""
    path, data = voting.prepare(news)
    voting.validate_input(data)
    if not any(not stock.get('error') and stock.get('price', 0) > 0 for stock in data['stocks'].values()):
        raise managed.GuardError('시세 수집에 모두 실패하여 완료본을 게시하지 않습니다.')
    receipt = {'version': 1, 'input_id': data['input_id'], 'collected_at': data['created_at'],
               'published_at': managed.now().isoformat()}
    managed.save(LATEST, receipt)  # 불변 input.json 저장 완료 후 마지막 단계에 게시합니다.
    return path, data


def summary(path, data):
    return {'상태': '수집 완료', 'input_file': str(path), 'input_id': data['input_id'],
            '수집시각': data['created_at'], '선정가능기한':
            min(research.timestamp(data['created_at']) + MAX_SELECTION_AGE,
                research.timestamp(data['expires_at'])).isoformat(),
            '종목수': len(data['stocks']), '시세실패': sum(bool(s.get('error')) for s in data['stocks'].values()),
            '사업재무검증완료': sum(s['eligible'] for s in data['stocks'].values()),
            '뉴스있는기업': sum(bool(s['news']) for s in data['stocks'].values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--publish', action='store_true')
    group.add_argument('--status', action='store_true')
    parser.add_argument('--news', type=Path)
    args = parser.parse_args()
    path, data = publish(voting.read(args.news) if args.news else None) if args.publish else load_current()
    print(json.dumps(summary(path, data), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'상태': '기업선정 보류', '이유': managed.safe_error(exc)}, ensure_ascii=False))
        raise SystemExit(1)
