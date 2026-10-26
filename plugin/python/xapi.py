import base64, hashlib, json, secrets, subprocess, threading, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from store import HOME, config, save_config, db, atomic

API_BASE = "https://api.x.com/2"
SCOPES = "tweet.read users.read bookmark.read offline.access"
REDIRECT = "http://127.0.0.1:8765/oauth/x/callback"
FIELDS = {
    "tweet.fields": "author_id,created_at,conversation_id,entities,attachments,referenced_tweets,note_tweet,article",
    "expansions": "author_id,attachments.media_keys,article.cover_media,article.media_entities",
    "user.fields": "name,username",
    "media.fields": "type,url,preview_image_url,duration_ms,variants,alt_text,width,height",
}
LOCK = threading.RLock()


class RetryLater(Exception):
    def __init__(self, message, when):
        super().__init__(message)
        self.when = when


class XError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def keychain(op, value=None):
    helper = HOME / "bin/native-helper"
    p = subprocess.run(
        [str(helper)],
        input=json.dumps(
            {
                "op": op,
                "service": "CodexBookmarkLibrary",
                "account": str(HOME),
                "value": value,
            }
        ),
        text=True,
        capture_output=True,
    )
    if p.returncode:
        raise RuntimeError(
            "macOS Keychain access failed. Allow Bookmark Library access and reconnect."
        )
    return json.loads(p.stdout or "null")


def token_request(data):
    req = Request(
        "https://api.x.com/2/oauth2/token",
        data=urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req, timeout=30) as r:
            token = json.load(r)
    except HTTPError as e:
        raise XError(
            e.code,
            f"X authorization returned HTTP {e.code}. Reconnect X if access was revoked.",
        ) from None
    token["expires_at"] = time.time() + int(token.get("expires_in", 7200))
    return token


def access_token(force=False):
    with LOCK:
        token = keychain("get")
        if not token:
            raise RuntimeError("Connect X before importing bookmarks.")
        if force or token.get("expires_at", 0) < time.time() + 90:
            new = token_request(
                {
                    "grant_type": "refresh_token",
                    "client_id": config()["client_id"],
                    "refresh_token": token["refresh_token"],
                }
            )
            new.setdefault("refresh_token", token["refresh_token"])
            keychain("set", new)
            token = new
        return token["access_token"]


def get(path, params=None):
    if config().get("offline"):
        raise RuntimeError("Network disabled for saved-sample validation.")
    url = API_BASE + path + ("?" + urlencode(params) if params else "")
    for attempt in range(2):
        token = access_token(force=attempt == 1)
        try:
            with urlopen(
                Request(url, headers={"Authorization": "Bearer " + token}), timeout=40
            ) as r:
                result = json.load(r)
                code = r.status
            with db() as c:
                c.execute(
                    "INSERT INTO calls(time,endpoint,status,records,details) VALUES (?,?,?,?,?)",
                    (
                        time.time(),
                        path,
                        code,
                        1
                        if isinstance(result.get("data"), dict)
                        else len(result.get("data") or []),
                        json.dumps({"next_page": bool(result.get("meta", {}).get("next_token")),
                                    "partial_errors": len(result.get("errors", [])),
                                    "rate_remaining": r.headers.get("x-rate-limit-remaining"),
                                    "rate_reset": r.headers.get("x-rate-limit-reset")}),
                    ),
                )
            return result
        except HTTPError as e:
            from diagnostics import safe_error
            try:
                body = json.loads(e.read(16384))
                detail = safe_error("; ".join(str(body[k]) for k in ("title", "detail", "reason") if body.get(k))) if isinstance(body, dict) else ""
            except (ValueError, OSError, TypeError):
                detail = ""
            with db() as c:
                c.execute(
                    "INSERT INTO calls(time,endpoint,status,records,details) VALUES (?,?,?,0,?)",
                    (time.time(), path, e.code, json.dumps({"error": detail})),
                )
            if e.code == 401 and attempt == 0:
                continue
            if e.code == 429:
                try:
                    reset = float(
                        e.headers.get("x-rate-limit-reset", time.time() + 900)
                    )
                except ValueError:
                    reset = time.time() + 900
                raise RetryLater(
                    "X rate limit reached; the queued job will resume automatically.",
                    max(time.time() + 5, reset + 2),
                ) from None
            if e.code >= 500:
                raise RetryLater(
                    f"X returned HTTP {e.code}; retry scheduled.", time.time() + 60
                ) from None
            raise XError(e.code, f"X returned HTTP {e.code} for {path}. {detail}".strip()) from None
    raise RuntimeError("X authorization failed; reconnect.")


def begin():
    cfg = config()
    if not cfg.get("client_id"):
        raise RuntimeError("Run plugin setup to configure the X app's public Client ID.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    atomic(
        HOME / "oauth-pending.json",
        {"state": state, "verifier": verifier, "expires": time.time() + 600},
    )
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return {
        "url": "https://x.com/i/oauth2/authorize?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": cfg["client_id"],
                "redirect_uri": REDIRECT,
                "scope": SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    }


def finish(params):
    with LOCK:
        path = HOME / "oauth-pending.json"
        if not path.exists():
            raise RuntimeError("This connection attempt is no longer active.")
        pending = json.loads(path.read_text())
        if pending["expires"] < time.time() or not secrets.compare_digest(
            params.get("state", [""])[0], pending["state"]
        ):
            raise RuntimeError("Invalid or expired connection state.")
        path.unlink()
        if "error" in params:
            raise RuntimeError("X connection was cancelled.")
        token = token_request(
            {
                "grant_type": "authorization_code",
                "client_id": config()["client_id"],
                "redirect_uri": REDIRECT,
                "code": params.get("code", [""])[0],
                "code_verifier": pending["verifier"],
            }
        )
        required = set(SCOPES.split())
        if not required.issubset(set(token.get("scope", "").split())):
            raise RuntimeError(
                "X did not grant all required read and refresh permissions."
            )
        # Identify before replacing an existing library's credential.
        with urlopen(
            Request(
                "https://api.x.com/2/users/me",
                headers={"Authorization": "Bearer " + token["access_token"]},
            ),
            timeout=30,
        ) as r:
            account = json.load(r)["data"]
        cfg = config()
        prior = cfg.get("owner_id")
        if prior and prior != account["id"]:
            raise RuntimeError(
                "This library belongs to another X account. Choose a separate library directory in setup."
            )
        keychain("set", token)
        cfg.update(account=account, owner_id=account["id"])
        save_config(cfg)


def disconnect():
    from store import set_setting

    with LOCK:
        set_setting("paused", True)
        keychain("delete")
        cfg = config()
        cfg.pop("account", None)
        save_config(cfg)
        (HOME / "oauth-pending.json").unlink(missing_ok=True)
    return {"disconnected": True, "local_library_retained": True}
