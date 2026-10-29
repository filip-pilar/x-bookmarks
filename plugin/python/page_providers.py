"""Public-page fallbacks with persistent pacing and a free-only TinyFetch budget."""
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote
from urllib.request import Request, build_opener
from urllib.error import HTTPError
from store import HOME, atomic, config, db, fingerprint

JINA = 'https://r.jina.ai/'
TINYFISH = 'https://api.fetch.tinyfish.ai'


class ProviderWait(RuntimeError):
    def __init__(self, code, until):
        self.code, self.until = code, until
        super().__init__(code)


class PageUnavailable(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS page_provider_usage(provider TEXT,day INTEGER,used INTEGER,next_at REAL,PRIMARY KEY(provider,day));
    CREATE TABLE IF NOT EXISTS page_attempts(source TEXT,provider TEXT,state TEXT,updated REAL,PRIMARY KEY(source,provider));
    ''')


def public_target(url, html=''):
    """Forward public content addresses only, stripping known tracking parameters."""
    from content import public_url
    try:
        p = urlsplit(url)
        public_url(url)
    except (ValueError, OSError):
        return None
    path = unquote(p.path).lower()
    if any(p.hostname == host or p.hostname.endswith('.' + host) for host in ('docs.google.com', 'drive.google.com', 'notion.so', 'notion.site', 'sharepoint.com', 'dropbox.com')):
        return None
    if p.fragment or re.search(r'/(login|signin|account|settings|dashboard|private|share|invite|auth|oauth|admin|preview)(/|$)', path):
        return None
    if re.search(r'(?:^|/)[a-f0-9]{32,}(?:/|$)|[a-f0-9]{8}-[a-f0-9-]{27,}', path):
        return None
    if any(len(part) >= 24 and re.fullmatch(r'[A-Za-z0-9_-]+', part) and (sum(ch.isdigit() for ch in part) > 6 or any(ch.isupper() for ch in part) or '-' not in part) for part in unquote(p.path).split('/')):
        return None
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid', 'ref_src', 'ref_url')]
    if p.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        query = [(k,v) for k,v in query if k not in ('si','feature')]
    if query and not (p.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com') and p.path == '/watch' and len(query) == 1 and query[0][0] == 'v' and re.fullmatch(r'[A-Za-z0-9_-]{11}', query[0][1])):
        return None
    if re.search(r'<input[^>]+type\s*=\s*[\"\']?password|isaccessibleforfree\s*[\"\']?\s*:\s*false', html.lower()):
        return None
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ''))


def claim(provider):
    now = time.time()
    day = int(now // 86400)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM page_provider_usage WHERE provider=? AND day=?', (provider, day)).fetchone()
        last = c.execute('SELECT max(next_at) FROM page_provider_usage WHERE provider=?', (provider,)).fetchone()[0] or 0
        if last > now:
            raise ProviderWait(provider + '_rate_wait', last)
        if provider == 'tinyfish' and row and row['used'] >= 1000:
            raise ProviderWait('tinyfish_daily_wait', (day + 1) * 86400 + 5)
        c.execute('INSERT INTO page_provider_usage VALUES (?,?,1,?) ON CONFLICT(provider,day) DO UPDATE SET used=used+1,next_at=excluded.next_at',
                  (provider, day, now + (3.1 if provider == 'jina' else .5)))


def request(provider, url, key=''):
    from content import NoRedirect
    claim(provider)
    req = (Request(JINA + url, headers={'Accept': 'application/json', 'X-Engine': 'browser', 'X-Timeout': '30'}) if provider == 'jina' else
           Request(TINYFISH, data=json.dumps({'urls': [url], 'format': 'markdown', 'image_links': True, 'ttl': 86400, 'per_url_timeout_ms': 45000}).encode(),
                   headers={'X-API-Key': key, 'Content-Type': 'application/json'}))
    try:
        # Never follow a provider redirect with the user's API key.
        with build_opener(NoRedirect).open(req, timeout=55) as response:
            body = response.read(4 * 1024**2 + 1)
        if len(body) > 4 * 1024**2:
            raise PageUnavailable('reader_response_too_large')
        result = json.loads(body)
    except HTTPError as e:
        if e.code in (429, 402):
            try:
                delay = max(60, min(86400, float(e.headers.get('Retry-After', 3600))))
            except ValueError:
                delay = 3600
            until = time.time() + delay
            with db() as c:
                c.execute('UPDATE page_provider_usage SET next_at=max(next_at,?) WHERE provider=?', (until, provider))
            raise ProviderWait(provider + '_quota_wait', until) from None
        if e.code in (401, 403) and provider == 'tinyfish':
            raise PageUnavailable('tinyfish_key_rejected') from None
        raise RuntimeError(provider + '_http_' + str(e.code)) from None
    if provider == 'jina':
        doc = result.get('data') or {}
        text = doc.get('content', '')
        title = doc.get('title', '')
        final_url = doc.get('url', url)
        images = [{'url': address, 'alt_text': label} for label, address in re.findall(r'!\[([^\]]*)\]\((https?://[^\s)]+)\)', text)]
    else:
        docs = result.get('results') or []
        if not docs:
            errors = json.dumps(result.get('errors', [])).lower()
            if re.search(r'quota|rate.limit|429|402|wallet|credits', errors):
                until = time.time() + 3600
                with db() as c:
                    c.execute('UPDATE page_provider_usage SET next_at=max(next_at,?) WHERE provider=?', (until, provider))
                raise ProviderWait('tinyfish_quota_wait', until)
            raise RuntimeError('tinyfish_extraction_failed')
        doc = docs[0]
        text, title, final_url = doc.get('text', ''), doc.get('title', ''), doc.get('final_url', url)
        images = [{'url': u, 'alt_text': ''} for u in doc.get('image_links', []) if isinstance(u, str)]
    if not isinstance(text, str):
        raise RuntimeError('reader_invalid_response')
    from pages import reason
    if reason(text):
        raise RuntimeError('reader_incomplete')
    return {'text': text, 'title': title, 'final_url': final_url, 'images': images[:24],
            'method': provider + '_reader', 'coverage': ['Page layout and interactive state are not retained.']}


def fallback(url, html=''):
    from pages import BrowserNeeded
    target = public_target(url, html)
    if not target:
        raise BrowserNeeded('private_page_access')
    key = credentials()
    source = fingerprint(target)
    waits = []
    for provider in ('jina', 'tinyfish') if key else ('jina',):
        with db() as c:
            last = c.execute('SELECT state,updated FROM page_attempts WHERE source=? AND provider=?', (source, provider)).fetchone()
        if last and last['state'] == 'failed' and time.time() - last['updated'] < 3600:
            continue
        try:
            return request(provider, target, key)
        except ProviderWait as e:
            waits.append(e)
        except PageUnavailable as e:
            if e.code == 'tinyfish_key_rejected':
                raise
        except (OSError, ValueError, RuntimeError):
            with db() as c:
                c.execute("INSERT OR REPLACE INTO page_attempts VALUES (?,?,'failed',?)", (source, provider, time.time()))
    if waits:
        raise min(waits, key=lambda w: w.until)
    raise BrowserNeeded('public_extraction_incomplete')


def credentials():
    path = HOME / 'page-provider-private.json'
    return (json.loads(path.read_text()).get('tinyfish_key', '') if path.exists() else '')


def settings(args=None):
    if args is not None:
        if 'tinyfish_key' in args:
            key = str(args['tinyfish_key']).strip()
            if len(key) > 512 or any(ch.isspace() for ch in key):
                raise ValueError('Enter a valid TinyFish API key.')
            atomic(HOME / 'page-provider-private.json', {'tinyfish_key': key})
    now = time.time()
    with db() as c:
        usage = [dict(r) for r in c.execute('SELECT provider,used,next_at FROM page_provider_usage WHERE day=?', (int(now // 86400),))]
    return {'tinyfish_configured': bool(credentials()), 'tinyfish_daily_limit': 1000, 'usage': usage, 'started': False}
