"""Bound subprocess/provider errors before exposing them through library status."""

import re


def safe_error(value, limit=1500):
    text = str(value)
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)(access_token|refresh_token|client_secret|code_verifier|authorization|token)([\"'\s:=]+)[^\s,\"'}]+",
                  r"\1\2[redacted]", text)
    text = re.sub(r"https?://[^\s<>\"']+", lambda m: m[0].split("?", 1)[0], text)
    return text[-limit:]
