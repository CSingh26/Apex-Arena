# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

SECRET_KEY_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key)"
    r"([=:\s]+)([^,\s;&]+)"
)
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
DSN_RE = re.compile(r"\b(?:postgresql(?:\+asyncpg)?|redis|rediss)://[^\s'\"<>]+")


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    parsed = urlparse(raw)
    netloc = parsed.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"<redacted>@{host}"
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if re.search(r"(?i)(password|token|secret|key)", key):
            query_items.append((key, "<redacted>"))
        else:
            query_items.append((key, value))
    return urlunparse(parsed._replace(netloc=netloc, query=urlencode(query_items)))


def sanitize_exception_message(exc: BaseException) -> str:
    """Return a useful operator message while redacting common secret forms."""

    message = str(exc).strip()
    if not message:
        return ""
    message = DSN_RE.sub(_redact_url, message)
    message = OPENAI_KEY_RE.sub("<redacted>", message)
    message = SECRET_KEY_RE.sub(lambda item: f"{item.group(1)}{item.group(2)}<redacted>", message)
    return message


def format_safe_cli_error(prefix: str, exc: BaseException) -> str:
    detail = sanitize_exception_message(exc)
    suffix = f": {detail}" if detail else ""
    return f"{prefix}: {type(exc).__name__}{suffix}"
