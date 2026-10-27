"""Keep stored evidence while exposing only the bookmark and its direct sources."""
import json, re
from pathlib import Path


def post_id(value):
    value = str(value or "")
    match = re.search(r"/(?:status|statuses)/(\d+)", value)
    return match[1] if match else value if value.isdigit() else None


def migrate(c):
    from store import HOME
    c.executescript("""
    CREATE TABLE IF NOT EXISTS chunk_owners(chunk TEXT,post TEXT,PRIMARY KEY(chunk,post));
    CREATE TABLE IF NOT EXISTS post_presentations(id TEXT PRIMARY KEY,payload TEXT);
    CREATE VIEW IF NOT EXISTS source_chunks AS SELECT c.* FROM chunks c JOIN items i ON i.id=c.item
      WHERE c.kind!='summary' AND EXISTS(SELECT 1 FROM chunk_owners o WHERE o.chunk=c.id AND
        (o.post=i.id OR EXISTS(SELECT 1 FROM json_each(i.raw,'$.post.referenced_tweets') r
          WHERE json_extract(r.value,'$.id')=o.post AND json_extract(r.value,'$.type') IN ('replied_to','quoted'))));
    CREATE VIEW IF NOT EXISTS scoped_chunks AS SELECT * FROM source_chunks UNION ALL
      SELECT c.* FROM chunks c WHERE c.kind='summary'
      AND json_array_length(c.metadata,'$.evidence_ids')>0
      AND NOT EXISTS(SELECT 1 FROM json_each(c.metadata,'$.evidence_ids') e
        WHERE NOT EXISTS(SELECT 1 FROM source_chunks s WHERE s.id=e.value AND s.item=c.item));
    """)
    if c.execute("SELECT 1 FROM settings WHERE key='evidence_scope_v1'").fetchone():
        return
    for item in c.execute("SELECT id,raw FROM items").fetchall():
        raw = json.loads(item['raw']); posts = [raw['post']]
        cached = HOME / 'items' / item['id'] / 'context.json'
        if cached.is_file():
            try:
                posts += json.loads(cached.read_text()).get('posts', [])
            except (ValueError, OSError):
                pass
        for ref in raw['post'].get('referenced_tweets', []):
            row = c.execute('SELECT payload FROM captured_sources WHERE id=? ORDER BY at DESC LIMIT 1', (ref['id'],)).fetchone()
            if row:
                posts.append(json.loads(row[0]))
        from pipeline import media_keys
        media_owners = {}
        for post in posts:
            for key in media_keys(post):
                media_owners.setdefault(key, set()).add(post['id'])
        for row in c.execute("SELECT id,kind,metadata FROM chunks WHERE item=? AND kind!='summary'", (item['id'],)).fetchall():
            meta = json.loads(row['metadata']); owners = set()
            if row['kind'] == 'text':
                owners.add(item['id'])
            elif row['kind'] == 'context':
                owners.add(post_id(meta.get('source_id') or meta.get('url')))
            elif meta.get('linked_from'):
                linked = meta['linked_from']
                owners.update(post_id(v) for v in (linked if isinstance(linked, list) else [linked]))
            elif meta.get('owner_post'):
                owners.add(meta['owner_post'])
            elif str(meta.get('source_id', '')).startswith('page:'):
                for stage in c.execute("SELECT payload FROM stages WHERE item=? AND source=? AND kind='page'", (item['id'], meta['source_id'])):
                    owners.add(json.loads(stage[0]).get('post'))
            elif meta.get('asset_id'):
                for stage in c.execute("SELECT payload FROM stages WHERE item=? AND (source=? OR json_extract(payload,'$.asset')=?)", (item['id'],meta['asset_id'],meta['asset_id'])):
                    owners.add(json.loads(stage[0]).get('post'))
            else:
                paths = [meta.get('path', ''), *[im.get('path', '') for im in meta.get('images', [])]]
                matched = []
                for path in paths:
                    key = re.match(r'(\d+_\d+)', Path(path).name)
                    if key:
                        matched.append(media_owners.get(key[1], set()))
                # Combined image evidence is usable only if every image has known ownership.
                if matched and all(matched):
                    owners.update(set.intersection(*matched))
            c.executemany("INSERT OR IGNORE INTO chunk_owners VALUES (?,?)", [(row['id'],o) for o in owners if o])
    # Scope changes the evidence revision even when the saved source bytes do not.
    c.execute("UPDATE items SET source_revision=''")
    c.execute("INSERT INTO settings VALUES ('evidence_scope_v1','true')")


def own(c, chunk, post):
    if post:
        c.execute("INSERT OR IGNORE INTO chunk_owners VALUES (?,?)", (chunk, str(post)))
