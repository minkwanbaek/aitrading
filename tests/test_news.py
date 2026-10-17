import datetime as dt
import email.utils
import unittest
from unittest import mock

import managed
import news


class NewsTests(unittest.TestCase):
    def test_only_recent_nonfuture_unique_headlines_are_collected(self):
        clock = managed.now()
        def item(url, hours):
            date = email.utils.format_datetime(clock - dt.timedelta(hours=hours))
            return f'<item><title>공시 제목</title><link>{url}</link><pubDate>{date}</pubDate></item>'
        body = ('<rss><channel>' + item('https://example.com/new', 1) + item('https://example.com/new', 1)
                + item('https://example.com/old', 25) + item('https://example.com/future', -1) + '</channel></rss>')
        with mock.patch.object(news.urllib.request, 'urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value = body.encode()
            result = news.fetch('테스트')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], 'https://example.com/new')
        self.assertIn('본문 미확인', result[0]['scope'])

    def test_empty_success_is_different_from_transport_failure(self):
        def fetch(name):
            if name == '실패':
                raise TimeoutError()
            return []
        with mock.patch.object(news, 'fetch', side_effect=fetch):
            data = news.collect({'000000': '성공빈결과', '000001': '실패'})
        self.assertEqual(data['items']['000000'], [])
        self.assertEqual(data['failed_codes'], ['000001'])

    def test_all_failures_are_not_reported_as_no_news(self):
        with mock.patch.object(news, 'fetch', side_effect=TimeoutError):
            data = news.collect({'000000': '실패'})
        self.assertEqual(data['status'], 'error')
        self.assertEqual(data['failed_codes'], ['000000'])

    def test_html_instead_of_rss_is_a_failure(self):
        with mock.patch.object(news.urllib.request, 'urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value = b'<html><body>unavailable</body></html>'
            with self.assertRaises(ValueError):
                news.fetch('테스트')


if __name__ == '__main__':
    unittest.main()
