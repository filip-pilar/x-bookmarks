"""Direct URLs attached to a captured post; no thread discovery."""


def links(post):
    entities = [post.get("entities", {}), post.get("note_tweet", {}).get("entities", {}), post.get("article", {}).get("entities", {})]
    return list(dict.fromkeys(
        u.get("unwound_url") or u.get("expanded_url")
        for group in entities for u in group.get("urls", [])
        if u.get("unwound_url") or u.get("expanded_url")
    ))
