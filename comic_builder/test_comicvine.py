import json
import threading
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs
from .comicvine import ComicVineClient, ComicVineError, LookupCancelled, issue_fields, site_url
from .key_store import can_remember_key, protect_key, retrieve_key

ISSUE = {'id': 123, 'name': 'Book &amp; title', 'issue_number': '0',
         'volume': {'id': 456, 'name': 'A series'}, 'cover_date': '2021-05-01',
         'store_date': '2021-04-20', 'description': '<p>Summary &amp; details.</p><script>discard this</script><p>Second paragraph.</p>',
         'person_credits': [{'name': 'A writer', 'role': 'writer, cover'},
                            {'name': 'An artist', 'role': 'penciler; inker'},
                            {'name': 'A writer', 'role': 'writer'}, {'name': 'Unknown', 'role': 'unknown'}],
         'character_credits': [{'name': 'Hero'}, {'name': 'Hero'}],
         'site_detail_url': 'https://comicvine.gamespot.com/book/4000-123/'}
VOLUME = {'id': 456, 'name': 'A series', 'start_year': '1980', 'publisher': {'name': 'Publisher'}}


class ComicVineTests(unittest.TestCase):
    def test_useful_fields_and_credits_without_inventing_volume_or_page_count(self):
        result = issue_fields(ISSUE, VOLUME)
        fields = result['fields']
        self.assertEqual(fields['Title'], 'Book & title')
        self.assertEqual(fields['Summary'], 'Summary & details.\nSecond paragraph.')
        self.assertEqual(fields['Writer'], 'A writer')
        self.assertEqual(fields['CoverArtist'], 'A writer')
        self.assertEqual(fields['Penciller'], 'An artist')
        self.assertEqual(fields['Inker'], 'An artist')
        self.assertEqual(fields['Characters'], 'Hero')
        self.assertEqual(fields['Number'], '0')
        self.assertEqual(fields['Publisher'], 'Publisher')
        self.assertEqual(fields['Month'], '5')
        self.assertEqual(result['date_source'], 'Cover date')
        for field in ('Volume', 'PageCount', 'Count', 'CommunityRating'): self.assertNotIn(field, fields)
        fallback = dict(ISSUE, cover_date=None)
        self.assertEqual(issue_fields(fallback, VOLUME)['fields']['Day'], '20')
        self.assertEqual(issue_fields({}, {})['fields'], {})

    def test_documented_endpoints_cache_and_bounded_field_requests(self):
        calls = []
        def transport(url, headers, timeout):
            calls.append((url, headers, timeout))
            path = urlsplit(url).path
            result = ISSUE if '/issue/' in path else VOLUME if '/volume/' in path else []
            return json.dumps({'status_code': 1, 'results': result, 'number_of_total_results': 0}).encode()
        client = ComicVineClient('test-key', transport=transport, interval=0)
        client.search('Book & title', 'volume')
        client.search('Book & title', 'volume')
        self.assertEqual(len(calls), 1)
        query = parse_qs(urlsplit(calls[0][0]).query)
        self.assertEqual(query['query'], ['Book & title'])
        self.assertEqual(query['resources'], ['volume'])
        self.assertEqual(query['limit'], ['10'])
        self.assertNotIn('description', query['field_list'][0])
        first = client.details(123); first['fields']['Title'] = 'Changed'
        self.assertEqual(client.details(123)['fields']['Title'], 'Book & title')
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(headers['User-Agent'].startswith('ComicStitcher/') for _, headers, _ in calls))
        client.issues(456, offset=20)
        query = parse_qs(urlsplit(calls[-1][0]).query)
        self.assertEqual(query['filter'], ['volume:456']); self.assertEqual(query['offset'], ['20'])
        client.set_key('another-key'); client.search('Book & title')
        self.assertEqual(len(calls), 5)

    def test_api_and_network_errors_never_expose_key_or_response_text(self):
        key = 'secret-test-key'
        for response in ({'status_code': 100, 'error': key}, {'status_code': 101}, {'status_code': 107},
                         {'status_code': 104, 'error': key}, {'status_code': 1, 'results': None}):
            client = ComicVineClient(key, transport=lambda *args, response=response: json.dumps(response).encode(), interval=0)
            if response['status_code'] == 1: continue
            with self.assertRaises(ComicVineError) as error: client.search('Book')
            self.assertNotIn(key, str(error.exception))
        for error in (HTTPError('https://host/?api_key='+key, 403, key, {}, None),
                      URLError(key), TimeoutError(key)):
            client = ComicVineClient(key, transport=lambda *args, error=error: (_ for _ in ()).throw(error), interval=0)
            with self.assertRaises(ComicVineError) as caught: client.search('Book')
            self.assertNotIn(key, str(caught.exception))
        client = ComicVineClient(key, transport=lambda *args: b'<html>blocked</html>', interval=0)
        with self.assertRaisesRegex(ComicVineError, 'unreadable'): client.search('Book')

    def test_publisher_failure_does_not_discard_available_issue_fields(self):
        def transport(url, *args):
            if '/volume/' in url: raise URLError('Unavailable')
            return json.dumps({'status_code': 1, 'results': ISSUE}).encode()
        result = ComicVineClient('test', transport=transport, interval=0).details(123)
        self.assertEqual(result['fields']['Title'], 'Book & title')
        self.assertNotIn('Publisher', result['fields'])
        self.assertIn('Publisher', result['warning'])

    def test_limits_and_cancellation_do_not_retry(self):
        calls = []
        def blocked(*args):
            calls.append(1); return b'{"status_code":107}'
        client = ComicVineClient('test', transport=blocked, interval=0)
        for query in ('First', 'Second'):
            with self.assertRaises(ComicVineError): client.search(query)
        self.assertEqual(len(calls), 1)
        cancel = threading.Event(); cancel.set()
        with self.assertRaises(LookupCancelled): client.search('Cancelled', cancel=cancel)
        self.assertEqual(len(calls), 1)
        client = ComicVineClient('test', transport=lambda *args: calls.append(1), interval=0)
        client._counts['search'].extend([client._clock()]*190)
        with self.assertRaisesRegex(ComicVineError, 'hourly'): client.search('Too many')
        self.assertEqual(len(calls), 1)
        for bad_id in ('../other', 'https://elsewhere', -1, True):
            with self.assertRaises(ComicVineError): client.details(bad_id)
        self.assertEqual(len(calls), 1)

    def test_external_links_are_restricted_and_sensitive_queries_removed(self):
        self.assertEqual(site_url('http://comicvine.gamespot.com/book/?api_key=secret'), 'https://comicvine.gamespot.com/book/')
        for url in ('https://evil.example/', 'javascript:alert(1)', 'https://secret@comicvine.gamespot.com/'):
            self.assertEqual(site_url(url), '')

    @unittest.skipUnless(can_remember_key(), 'Windows DPAPI requires Windows')
    def test_windows_remembered_key_is_protected(self):
        protected = protect_key('test-key-only')
        self.assertNotIn('test-key-only', protected)
        self.assertEqual(retrieve_key(protected), 'test-key-only')
