"""관찰·보유 기업의 Google News RSS 제목 수집. 본문 조사·투자 판단·주문 없음."""
import concurrent.futures
import datetime as dt
import email.utils
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import broker
import managed
import research

DESTINATION = managed.CACHE / 'research-news.json'


def fetch(name):
    params = urllib.parse.urlencode({'q': '"' + name + '" when:1d', 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'})
    with urllib.request.urlopen('https://news.google.com/rss/search?' + params, timeout=10) as response:
        root = ET.fromstring(response.read(2_000_000))
    if root.tag != 'rss':
        raise ValueError('RSS 응답이 아닙니다.')
    clock = managed.now()
    items, seen = [], set()
    for row in root.findall('./channel/item'):
        title = (row.findtext('title') or '').strip()
        url = (row.findtext('link') or '').strip()
        published = email.utils.parsedate_to_datetime(row.findtext('pubDate') or '')
        if not published.tzinfo or not title or not research.valid_url(url):
            raise ValueError('뉴스 제목·URL·발행 시각을 확인하지 못했습니다.')
        age = clock - published
        if age < -dt.timedelta(minutes=5) or age > dt.timedelta(days=1) or url in seen:
            continue
        seen.add(url)
        items.append({'url': url, 'summary': title, 'published_at': published.isoformat(),
                      'checked_at': clock.isoformat(), 'scope': 'RSS 제목·발행일만 확인. 본문 미확인'})
    return sorted(items, key=lambda v: v['published_at'], reverse=True)[:5]


def collect(companies):
    results, failed = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(fetch, name): code for code, name in companies.items()}
        for future in concurrent.futures.as_completed(jobs):
            code = jobs[future]
            try:
                results[code] = future.result()
            except Exception:
                results[code] = []
                failed.append(code)  # 검색 결과 없음과 조회 실패를 구분.
    return {'status': 'error' if failed and len(failed) == len(companies) else 'ok',
            'checked_at': managed.now().isoformat(), 'items': dict(sorted(results.items())),
            'failed_codes': sorted(failed)}


def main():
    if not broker.MOCK:
        raise managed.GuardError('현재 예약의 뉴스 수집은 모의 환경 전용입니다.')
    names = {code: row['name'] for code, row in research.companies(research.load()).items()}
    names.update({code: item['name'] for code, item in broker.holdings(broker.account()).items()})
    if not names or any(len(code) != 6 or not code.isdigit() for code in names):
        raise managed.GuardError('수집할 국내 관찰·보유 기업이 필요합니다.')
    result = collect(names)
    managed.save(DESTINATION, result)
    print(json.dumps({'상태': result['status'], '기업수': len(names), '뉴스수': sum(map(len, result['items'].values())),
                      '수집실패기업수': len(result['failed_codes']), '결과파일': str(DESTINATION)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'오류': managed.safe_error(exc)}, ensure_ascii=False))
        raise SystemExit(1)
