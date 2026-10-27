"""On-demand ComicVine lookups. Requests and errors never enter app logs."""
from __future__ import annotations
import copy
from collections import defaultdict, deque
from datetime import date
from html.parser import HTMLParser
import json
import re
import threading
import time
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from . import __version__

BASE = 'https://comicvine.gamespot.com/api/'
SEARCH_FIELDS = 'id,name,resource_type,issue_number,volume,cover_date,start_year,publisher,site_detail_url'
ISSUE_FIELDS = ('id,name,issue_number,volume,cover_date,store_date,deck,description,person_credits,'
                'character_credits,team_credits,location_credits,story_arc_credits,site_detail_url')
VOLUME_FIELDS = 'id,name,publisher,site_detail_url'
MAX_RESPONSE = 4 * 1024 * 1024


class ComicVineError(ValueError):
    pass


class LookupCancelled(ComicVineError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the user's key to a redirected destination.


def _transport(url, headers, timeout):
    with build_opener(_NoRedirect()).open(Request(url, headers=headers), timeout=timeout) as response:
        data = response.read(MAX_RESPONSE + 1)
    return data


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        elif tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip-1)
        elif tag in ('p', 'div', 'li'): self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def plain_text(value):
    if not isinstance(value, str): return ''
    parser = _Text(); parser.feed(value[:200000])
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip()).strip()


def site_url(value):
    if not isinstance(value, str): return ''
    try:
        parsed = urlsplit(value)
        if parsed.hostname != 'comicvine.gamespot.com' or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            return ''
        return urlunsplit(('https', 'comicvine.gamespot.com', parsed.path, '', ''))
    except ValueError:
        return ''


def _id(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) < 10**12:
        raise ComicVineError('ComicVine returned an unsupported record ID.')
    return int(value)


def _names(value):
    if not isinstance(value, list): return ''
    return ', '.join(dict.fromkeys(plain_text(p.get('name')) for p in value if isinstance(p, dict) and plain_text(p.get('name'))))


def issue_fields(issue, volume):
    """Only documented, useful ComicInfo fields; never infer a book volume."""
    fields = {}
    series = issue.get('volume') if isinstance(issue.get('volume'), dict) else {}
    pairs = {'Title': issue.get('name'), 'Series': series.get('name') or volume.get('name'),
             'Number': str(issue['issue_number']) if issue.get('issue_number') is not None else None,
             'Summary': issue.get('deck') or issue.get('description')}
    publisher = volume.get('publisher')
    if isinstance(publisher, dict): pairs['Publisher'] = publisher.get('name')
    for key, value in pairs.items():
        clean = plain_text(value)
        if clean: fields[key] = clean
    url = site_url(issue.get('site_detail_url'))
    if url: fields['Web'] = url
    raw_date = issue.get('cover_date') or issue.get('store_date')
    date_source = 'Cover date' if issue.get('cover_date') else 'Store release date'
    if isinstance(raw_date, str):
        try:
            parsed = date.fromisoformat(raw_date)
            fields.update(Year=str(parsed.year), Month=str(parsed.month), Day=str(parsed.day))
        except ValueError: pass
    credits = defaultdict(list)
    people = issue.get('person_credits')
    for person in people if isinstance(people, list) else []:
        if not isinstance(person, dict): continue
        name = plain_text(person.get('name')); role = person.get('role')
        if not name or not isinstance(role, str): continue
        for token in re.split(r'[,;/]', role.lower()):
            token = token.strip()
            key = {'writer': 'Writer', 'penciler': 'Penciller', 'penciller': 'Penciller', 'artist': 'Penciller',
                   'inker': 'Inker', 'colorist': 'Colorist', 'colourist': 'Colorist', 'letterer': 'Letterer',
                   'editor': 'Editor', 'cover': 'CoverArtist', 'cover artist': 'CoverArtist'}.get(token)
            if key and name not in credits[key]: credits[key].append(name)
    fields.update({key: ', '.join(names) for key, names in credits.items()})
    for source, target in (('character_credits', 'Characters'), ('team_credits', 'Teams'),
                           ('location_credits', 'Locations'), ('story_arc_credits', 'StoryArc')):
        names = _names(issue.get(source))
        if names: fields[target] = names
    return {'fields': fields, 'source_url': url, 'date_source': date_source if 'Year' in fields else ''}


class ComicVineClient:
    def __init__(self, key='', *, transport=None, clock=None, interval=5):
        self._key = key.strip()
        self._transport = transport or _transport
        self._clock = clock or time.monotonic
        self.interval = interval
        self._lock = threading.Lock()
        self._last_request = -float('inf')
        self._counts = defaultdict(deque)
        self._cache = {}
        self._blocked_until = 0

    @property
    def configured(self): return bool(self._key)

    def set_key(self, key):
        with self._lock:
            self._key = key.strip(); self._cache.clear(); self._counts.clear()
            self._blocked_until = 0

    def _request(self, endpoint, params, cancel=None):
        cancel = cancel or threading.Event()
        if cancel.is_set(): raise LookupCancelled('Lookup cancelled.')
        with self._lock:
            if not self._key: raise ComicVineError('Enter your ComicVine API key in Settings first.')
            cache_key = (endpoint, tuple(sorted(params.items())))
            now = self._clock()
            cached = self._cache.get(cache_key)
            if cached and now-cached[0] < 3600: return copy.deepcopy(cached[1])
            if now < self._blocked_until:
                raise ComicVineError('ComicVine asked this app to slow down. Please wait before another lookup.')
            resource = endpoint.split('/')[0]
            history = self._counts[resource]
            while history and now-history[0] >= 3600: history.popleft()
            if len(history) >= 190:
                raise ComicVineError('The hourly lookup allowance for this resource is nearly exhausted. Try later.')
            delay = max(0, self.interval-(now-self._last_request))
            if delay and cancel.wait(delay): raise LookupCancelled('Lookup cancelled.')
            if cancel.is_set(): raise LookupCancelled('Lookup cancelled.')
            self._last_request = self._clock(); history.append(self._last_request)
            url = BASE + endpoint + '?' + urlencode({'api_key': self._key, 'format': 'json', **params})
            try:
                data = self._transport(url, {'User-Agent': f'ComicStitcher/{__version__} (personal metadata lookup)',
                                             'Accept': 'application/json', 'Accept-Encoding': 'identity'}, 15)
                if cancel.is_set(): raise LookupCancelled('Lookup cancelled.')
                if len(data) > MAX_RESPONSE: raise ComicVineError('ComicVine returned an unexpectedly large response.')
                result = json.loads(data)
            except LookupCancelled:
                raise
            except HTTPError as exc:
                if exc.code == 429:
                    self._blocked_until = self._clock()+600
                    raise ComicVineError('ComicVine rate limit reached. Please wait before trying again.') from None
                if exc.code in (401, 403):
                    raise ComicVineError('ComicVine refused access. Check your API key, or try again later if the service is blocking requests.') from None
                raise ComicVineError('ComicVine could not complete this request. Please try again later.') from None
            except (URLError, TimeoutError, OSError):
                raise ComicVineError('Could not reach ComicVine. Check your connection or try again later.') from None
            except (ValueError, TypeError):
                raise ComicVineError('ComicVine returned an unreadable response. Please try again later.') from None
            if not isinstance(result, dict): raise ComicVineError('ComicVine returned an unsupported response.')
            status = result.get('status_code')
            if status == 100: raise ComicVineError('ComicVine rejected the API key. Check it in Settings.')
            if status == 107:
                self._blocked_until = self._clock()+600
                raise ComicVineError('ComicVine rate limit reached. Please wait before trying again.')
            if status == 101: raise ComicVineError('The selected ComicVine record was not found.')
            if status != 1: raise ComicVineError('ComicVine could not complete this lookup. Please try again later.')
            self._cache[cache_key] = (self._clock(), result)
            return copy.deepcopy(result)

    def search(self, query, resource='volume', offset=0, cancel=None):
        if resource not in ('volume', 'issue'): raise ComicVineError('Choose series or issues/collections.')
        if not query.strip(): raise ComicVineError('Enter a search title.')
        return self._request('search/', {'query': query.strip()[:500], 'resources': resource,
                             'field_list': SEARCH_FIELDS, 'limit': 10, 'offset': max(0, int(offset))}, cancel)

    def issues(self, volume_id, offset=0, cancel=None):
        return self._request('issues/', {'filter': f'volume:{_id(volume_id)}',
                             'field_list': 'id,name,issue_number,volume,cover_date,site_detail_url', 'limit': 20, 'offset': max(0, int(offset)),
                             'sort': 'issue_number:asc'}, cancel)

    def details(self, issue_id, cancel=None):
        response = self._request(f'issue/4000-{_id(issue_id)}/', {'field_list': ISSUE_FIELDS}, cancel)
        issue = response.get('results')
        if not isinstance(issue, dict): raise ComicVineError('ComicVine returned incomplete issue details.')
        volume = issue.get('volume'); volume_detail = {}
        warning = ''
        if isinstance(volume, dict) and volume.get('id') is not None:
            try:
                response = self._request(f'volume/4050-{_id(volume["id"])}/', {'field_list': VOLUME_FIELDS}, cancel)
                volume_detail = response.get('results')
                if not isinstance(volume_detail, dict): raise ComicVineError('Incomplete series details.')
            except LookupCancelled:
                raise
            except ComicVineError:
                volume_detail = {}
                warning = 'Publisher details could not be fetched. Available issue fields are shown; you can enter the publisher manually.'
        result = issue_fields(issue, volume_detail)
        result['warning'] = warning
        return result
