"""Codex의 출처 있는 밸류체인 조사 결과를 검증·저장합니다. 주문하지 않습니다."""
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import strategy

CATALOG = Path(__file__).with_name('research') / 'universe.json'
UTC = dt.timezone.utc


def now():
    return dt.datetime.now(UTC)


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('조사 시각에 시간대가 필요합니다.')
    return parsed.astimezone(UTC)


def valid_url(value):
    if not isinstance(value, str):
        return False
    url = urlparse(value)
    return url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password


def validate(data, clock=None):
    clock = clock or now()
    checked = timestamp(data['checked_at'])
    if checked > clock + dt.timedelta(minutes=5):
        raise ValueError('미래의 조사 시각은 허용하지 않습니다.')
    if data.get('full_reviewed_at') and timestamp(data['full_reviewed_at']) > checked:
        raise ValueError('전체 검토 시각은 마지막 조사 시각보다 미래일 수 없습니다.')
    themes = data.get('themes', [])
    if len(themes) != 3 or len(set(themes)) != 3 or any(not isinstance(t, str) or not t.strip() for t in themes):
        raise ValueError('서로 다른 조사 테마 3개가 필요합니다.')
    rows = data.get('companies', [])
    if not 15 <= len(rows) <= 25:
        raise ValueError('중복을 제외한 후보는 15~25개여야 합니다.')
    codes = set()
    for row in rows:
        code = row.get('code', '')
        if not re.fullmatch(r'\d{6}', code) or code in codes:
            raise ValueError('종목코드 중복 또는 형식 오류입니다.')
        codes.add(code)
        if row.get('theme') not in themes:
            raise ValueError('후보의 테마가 목록에 없습니다.')
        for field in ('name', 'role', 'linkage', 'risk'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError('기업명·역할·사업 연관성·위험을 기록해야 합니다.')
        sources = row.get('sources', [])
        if not sources or any(not valid_url(s.get('url')) or not s.get('summary') for s in sources):
            raise ValueError('설명과 HTTPS 출처가 필요합니다.')
        if not any(s.get('primary') is True for s in sources):
            raise ValueError('기업 공식자료 또는 공시 출처가 필요합니다.')
        if row.get('status') not in ('watch', 'eligible'):
            raise ValueError('기업 상태는 watch 또는 eligible입니다.')
        if row.get('status') == 'eligible':
            if len({s['url'] for s in sources}) < 2 or not row.get('financial_case'):
                raise ValueError('매수 검토 후보에는 사업·재무 검증과 서로 다른 출처 2개가 필요합니다.')
            if not row.get('invalidation'):
                raise ValueError('투자 근거가 무효가 되는 조건을 적어야 합니다.')
            for purpose in ('business', 'financial'):
                if not any(s.get('primary') is True and s.get('verified') is True
                           and s.get('purpose') == purpose and s.get('checked_at')
                           and timestamp(s['checked_at']) <= clock + dt.timedelta(minutes=5)
                           for s in sources):
                    raise ValueError('최근 확인한 공식 사업 자료와 재무 자료가 각각 필요합니다.')
    return data


def load():
    if not CATALOG.exists():
        return None
    return validate(json.loads(CATALOG.read_text(encoding='utf-8-sig')))


def fresh(data, clock=None):
    return bool(data) and (clock or now()) - timestamp(data['checked_at']) <= dt.timedelta(days=strategy.RESEARCH_DAYS)


def full_review_due(data, clock=None):
    return not data or (clock or now()) - timestamp(data.get('full_reviewed_at') or data['checked_at']) >= dt.timedelta(days=7)


def companies(data):
    return {row['code']: row for row in (data or {}).get('companies', [])}


def eligible(data, code):
    row = companies(data).get(code, {})
    if not fresh(data) or row.get('status') != 'eligible':
        return False
    return all(any(s.get('purpose') == purpose and s.get('verified') is True
                   and s.get('primary') is True and s.get('checked_at')
                   and dt.timedelta(0) <= now() - timestamp(s['checked_at']) <= dt.timedelta(days=7)
                   for s in row['sources']) for purpose in ('business', 'financial'))


def market_master():
    """종목명·상장시장·거래정지 여부는 인증이 필요 없는 NH 공식 마스터로 확인합니다."""
    from nhplug.instruments import load_master
    rows = load_master('m_new_stock')
    if hasattr(rows, 'to_dict'):
        rows = rows.to_dict(orient='records')
    return {str(row['sCode']): row for row in rows}


def verify_instruments(data):
    master = market_master()
    for row in data['companies']:
        stock = master.get(row['code'])
        if not stock or stock.get('sMarket') not in ('1', '4'):
            raise ValueError('국내 상장 주식 종목을 확인하지 못했습니다.')
        if stock.get('name', stock.get('sKorName', '').lstrip('*#')).replace(' ', '') != row['name'].replace(' ', ''):
            raise ValueError('종목명과 공식 마스터가 일치하지 않습니다: ' + row['code'])
        if stock.get('eStop') != 'N':
            raise ValueError('거래정지 여부를 확인해야 합니다: ' + row['code'])


def status():
    data = load()
    return {'조사필요': not fresh(data),
            '전체검토필요': full_review_due(data),
            '테마': (data or {}).get('themes', []),
            '후보수': len(companies(data)),
            '매수검토가능': sum(eligible(data, code) for code in companies(data)),
            '설명': '기업 공식자료·공시로 조사하며 매수 추천과 실제 주문은 별도입니다.'}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--import':
        data = validate(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig')))
        if not data.get('full_reviewed_at'):
            previous = load()
            data['full_reviewed_at'] = (previous or {}).get('full_reviewed_at') or (previous or data)['checked_at']
        validate(data)
        if not fresh(data):
            raise ValueError('오래된 조사 결과는 등록하지 않습니다.')
        verify_instruments(data)
        CATALOG.parent.mkdir(parents=True, exist_ok=True)
        temp = CATALOG.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(CATALOG)
    elif sys.argv[1:] not in ([], ['--status']):
        raise ValueError('지원하지 않는 조사 명령입니다.')
    print(json.dumps(status(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
