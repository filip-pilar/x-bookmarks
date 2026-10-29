"""Independent processing categories and durable preparation receipts."""
import contextvars
import json
from store import db

CATEGORIES = ('text', 'images', 'video', 'pages')
CATEGORY = contextvars.ContextVar('stage_category', default='text')


def migrate(c):
    c.execute('CREATE TABLE IF NOT EXISTS work_plan(item TEXT,category TEXT,revision TEXT,PRIMARY KEY(item,category))')
    c.execute('CREATE TABLE IF NOT EXISTS work_available(item TEXT,category TEXT,PRIMARY KEY(item,category))')
    if 'category' not in {r[1] for r in c.execute('PRAGMA table_info(stages)')}:
        c.execute("ALTER TABLE stages ADD COLUMN category TEXT DEFAULT 'text'")
        c.execute("UPDATE stages SET category=CASE WHEN kind='page' OR source LIKE 'page:%' OR json_extract(payload,'$.source_url') NOT LIKE 'https://x.com/%' THEN 'pages' WHEN kind IN ('video','video_ocr','audio','transcript','video_caption') OR (kind='download' AND json_extract(payload,'$.media.type')!='photo') THEN 'video' WHEN kind IN ('image','ocr','caption','download') THEN 'images' ELSE 'text' END")
        for row in c.execute('SELECT id,hash FROM items WHERE processed_hash=hash').fetchall():
            c.executemany('INSERT OR IGNORE INTO work_plan VALUES (?,?,?)', [(row['id'], k, row['hash']) for k in CATEGORIES])


def validate(categories=None, content='all'):
    if categories is not None and not isinstance(categories, list):
        raise ValueError('Choose processing categories as a list.')
    categories = list(dict.fromkeys(categories if categories is not None else CATEGORIES))
    if not categories or any(k not in CATEGORIES for k in categories):
        raise ValueError('Choose text, images, video/audio or linked pages.')
    if content not in ('all', 'text', 'images', 'video'):
        raise ValueError('Choose a bookmark type.')
    return categories


def material(c, row):
    import import_sources
    raw = json.loads(row['raw'])
    posts = [import_sources.saved(c, row['id']) or {'data': raw['post'], 'includes': raw.get('includes', {})}]
    for ref in raw['post'].get('referenced_tweets', []):
        if ref.get('type') in ('replied_to', 'quoted'):
            p = import_sources.saved(c, ref['id'])
            if p:
                posts.append(p)
    return posts


def applicable(posts):
    from context import links
    from urllib.parse import urlsplit
    kinds = {'text'}
    for p in posts:
        for m in p.get('includes', {}).get('media', []):
            if m.get('media_key') not in __import__('pipeline').media_keys(p['data']):
                continue
            kinds.add('images' if m.get('type') == 'photo' else 'video')
        if any(urlsplit(u).hostname not in ('x.com', 'twitter.com', 'www.x.com', 'www.twitter.com', 't.co') for u in links(p['data'])):
            kinds.add('pages')
    return kinds


def missing(c, row, categories=None):
    categories = set(categories or CATEGORIES) & {r[0] for r in c.execute('SELECT category FROM work_available WHERE item=?', (row['id'],))}
    done = {r[0] for r in c.execute('SELECT category FROM work_plan WHERE item=? AND revision=?', (row['id'], row['hash']))}
    return sorted(categories - done)


def refresh_available(c, items):
    for item in set(items):
        row = c.execute('SELECT * FROM items WHERE id=?', (item,)).fetchone()
        if not row:
            continue
        kinds = applicable(material(c, row))
        c.execute('DELETE FROM work_available WHERE item=?', (item,))
        c.executemany('INSERT INTO work_available VALUES (?,?)', [(item, k) for k in kinds])


def selected(c, args):
    categories = validate(args.get('categories'), args.get('content', 'all'))
    ids = args.get('items') or ([args['item']] if args.get('item') else None)
    if ids is not None and (not isinstance(ids, list) or not ids or len(ids) > 500 or any(not str(i).isdigit() for i in ids)):
        raise ValueError('Select between 1 and 500 bookmarks.')
    rows = c.execute('SELECT id,hash FROM items WHERE active=1 ORDER BY id DESC').fetchall()
    if ids is not None and set(ids) - {r['id'] for r in rows}:
        raise ValueError('A selected bookmark is no longer in this library.')
    available = {}
    for r in c.execute('SELECT item,category FROM work_available'):
        available.setdefault(r['item'], set()).add(r['category'])
    prepared = {}
    for r in c.execute('SELECT p.item,p.category FROM work_plan p JOIN items i ON i.id=p.item AND i.hash=p.revision'):
        prepared.setdefault(r['item'], set()).add(r['category'])
    busy = {r[0] for r in c.execute("SELECT item FROM jobs WHERE kind='process' AND state NOT IN ('complete','cancelled')")}
    result = []
    for row in rows:
        if ids is not None and row['id'] not in ids:
            continue
        kinds = available.get(row['id'], set())
        content = args.get('content', 'all')
        if content == 'text' and kinds & {'images', 'video'}:
            continue
        if content in ('images', 'video') and content not in kinds:
            continue
        # Existing work is controlled separately; selecting new work cannot resume old runs.
        if row['id'] in busy:
            continue
        todo = sorted((set(categories) & kinds) - prepared.get(row['id'], set()))
        if args.get('scope', 'pending') == 'all':
            todo = sorted(set(categories) & kinds)
        if todo:
            result.append((row['id'], todo))
    return result


def preview(args):
    with db() as c:
        rows = selected(c, args)
    return {'bookmarks': len(rows), 'categories': {k: sum(k in kinds for _, kinds in rows) for k in CATEGORIES}, 'started': False}
