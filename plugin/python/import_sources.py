"""Direct X dependencies are collected by imports; processing only reads them."""
import json
import time
from store import db, fingerprint, content_fingerprint


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS x_sources(id TEXT PRIMARY KEY,payload TEXT NOT NULL,revision TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS x_dependencies(item TEXT,source TEXT,relation TEXT,state TEXT,error TEXT,
      PRIMARY KEY(item,source,relation));
    CREATE INDEX IF NOT EXISTS x_dependencies_pending ON x_dependencies(state,item);
    ''')
    if not c.execute("SELECT 1 FROM settings WHERE key='import_sources_v1'").fetchone():
        for r in c.execute('SELECT raw,id FROM items').fetchall():
            raw = json.loads(r['raw'])
            remember(c, raw['post'], raw.get('includes', {}), observe=False)
            discover(c, raw['post'])
        # Presentations are already limited to captured posts; no recursive expansion.
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'post_presentations' in tables:
            for r in c.execute('SELECT payload FROM post_presentations').fetchall():
                raw = json.loads(r[0])
                remember(c, raw['post'], raw.get('includes', {}), observe=False)
        reuse(c)
        __import__('workplan').refresh_available(c, [r[0] for r in c.execute('SELECT id FROM items')])
        c.execute("INSERT INTO settings VALUES ('import_sources_v1','true')")


def remember(c, post, includes, observe=True):
    from pipeline import media_keys, snapshot_metrics
    media = [m for m in includes.get('media', []) if m.get('media_key') in media_keys(post)]
    users = [u for u in includes.get('users', []) if u.get('id') == post.get('author_id')]
    payload = {'data': post, 'includes': {'media': media, 'users': users}}
    revision = content_fingerprint(post, media)
    old = c.execute('SELECT revision,payload FROM x_sources WHERE id=?', (post['id'],)).fetchone()
    # A less complete expansion must not replace a more complete copy of the same post.
    if old and content_fingerprint(json.loads(old['payload'])['data'], []) == content_fingerprint(post, []):
        saved = json.loads(old['payload'])['includes']
        media = list({m['media_key']: m for m in [*saved.get('media', []), *media]}.values())
        payload['includes'] = {'media': media, 'users': users or saved.get('users', [])}
        revision = content_fingerprint(post, media)
    c.execute('INSERT INTO x_sources VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision',
              (post['id'], json.dumps(payload), revision))
    if observe:
        snapshot_metrics(c, post, media)
    if (not old or old['revision'] != revision) and c.execute("SELECT 1 FROM settings WHERE key='import_sources_v1'").fetchone():
        c.execute('DELETE FROM work_plan WHERE item IN (SELECT item FROM x_dependencies WHERE source=?)', (post['id'],))
        __import__('workplan').refresh_available(c, [r[0] for r in c.execute('SELECT item FROM x_dependencies WHERE source=?', (post['id'],))])
    return payload


def discover(c, post):
    refs = [(r['id'], r['type']) for r in post.get('referenced_tweets', []) if r.get('type') in ('replied_to', 'quoted')]
    refs.append((post['id'], 'media'))
    existing = {(r['source'], r['relation']) for r in c.execute('SELECT source,relation FROM x_dependencies WHERE item=?', (post['id'],))}
    for source, relation in existing - set(refs):
        c.execute('DELETE FROM x_dependencies WHERE item=? AND source=? AND relation=?', (post['id'], source, relation))
    for source, relation in refs:
        c.execute("INSERT OR IGNORE INTO x_dependencies VALUES (?,?,?,'pending',NULL)", (post['id'], source, relation))


def ingest(c, page):
    posts = page.get('data') or []
    if isinstance(posts, dict):
        posts = [posts]
    includes = page.get('includes', {})
    for p in [*posts, *includes.get('tweets', [])]:
        remember(c, p, includes)
    for p in posts:
        discover(c, p)
    reuse(c)


def reuse(c):
    from pipeline import media_keys
    for r in c.execute("SELECT DISTINCT d.source,s.payload FROM x_dependencies d JOIN x_sources s ON s.id=d.source WHERE d.state='pending'").fetchall():
        p = json.loads(r['payload'])
        if media_keys(p['data']) <= {m.get('media_key') for m in p['includes'].get('media', [])}:
            c.execute("UPDATE x_dependencies SET state='complete',error=NULL WHERE source=? AND state='pending'", (r['source'],))


def saved(c, source):
    row = c.execute('SELECT payload FROM x_sources WHERE id=?', (source,)).fetchone()
    if row:
        return json.loads(row[0])
    # Reuse a pre-migration successful source request without contacting X.
    from sources import cached_json
    from store import HOME
    from xapi import FIELDS
    cache = cached_json(HOME / 'sources/x' / (fingerprint(['/tweets/' + source, FIELDS]) + '.json'))
    if cache and cache.get('response', {}).get('data'):
        p = cache['response']
        return p
    return None


def step(run, items=None):
    """One checkpointed, deduplicated lookup batch under the import's X budget."""
    from jobs import scan_page
    from xapi import FIELDS
    with db() as c:
        reuse(c)
        rows = c.execute("SELECT DISTINCT d.source FROM x_dependencies d JOIN items i ON i.id=d.item WHERE i.active=1 AND d.state='pending' AND (? OR i.seen_run=?) ORDER BY d.source LIMIT 100", (bool(items), run)).fetchall()
        ids = [r[0] for r in rows]
        for source in ids:
            result = saved(c, source)
            if result:
                remember(c, result['data'], result.get('includes', {}), observe=False)
        reuse(c)
        ids = [source for source in ids if c.execute("SELECT 1 FROM x_dependencies WHERE source=? AND state='pending'", (source,)).fetchone()]
    if not ids:
        return bool(rows)
    page, data, _ = scan_page(run, '/tweets', {**FIELDS, 'ids': ','.join(ids)}, lookup_ids=ids)
    returned = {p['id'] for p in data}
    from sources import unavailable_posts
    unavailable = unavailable_posts(page, ids)
    with db() as c:
        for post in data:
            remember(c, post, page.get('includes', {}))
        reuse(c)
        for source in ids:
            if source in unavailable:
                c.execute("UPDATE x_dependencies SET state='unavailable',error='source_unavailable' WHERE source=? AND state='pending'", (source,))
            elif source not in returned:
                # Partial API errors remain explicit instead of an endless cached-page retry.
                c.execute("UPDATE x_dependencies SET state='missing',error='partial_source_response' WHERE source=? AND state='pending'", (source,))
            else:
                c.execute("UPDATE x_dependencies SET state='missing',error='media_expansion_missing' WHERE source=? AND state='pending'", (source,))
    return True
