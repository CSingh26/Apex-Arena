# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import re
from copy import copy
from datetime import UTC, datetime

from app.core.settings import Settings

_CREDENTIAL = re.compile(
    r"(?i)(\b(?:[\w-]*[_-])?(?:authorization|password|secret|token|api[_-]?key)\b[\"']?\s*[:=]\s*)"
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:bearer\s+|basic\s+)?[^\s,;"'}]+)"""
)
_URL_CREDENTIAL = re.compile(r"(://)[^/@\s]+:[^/@\s]+@")
_AUTHORIZATION = re.compile(r"(?i)(\bauthorization\b[\"']?\s*[:=]\s*)[^\r\n]+")


def _safe_text(value: object, limit: int = 4096) -> str:
    text = str(value)
    text = _AUTHORIZATION.sub(r"\1[REDACTED]", text)
    text = _CREDENTIAL.sub(r"\1[REDACTED]", text)
    text = _URL_CREDENTIAL.sub(r"\1[REDACTED]@", text)
    text = text.replace("\r", r"\r").replace("\n", r"\n")
    return text[:limit]


class CredentialFilter(logging.Filter):
    """Sanitize a handler-local copy, including handlers supplied by the host."""

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        safe = copy(record)
        safe.msg = _safe_text(record.getMessage())
        safe.args = ()
        if record.exc_info and record.exc_info[0]:
            safe.safe_exception_type = record.exc_info[0].__name__
        safe.exc_info = None
        safe.exc_text = None
        safe.stack_info = None
        for key, value in record.__dict__.items():
            if re.search(r"(?i)(authorization|password|secret|token|api[_-]?key)", key):
                setattr(safe, key, "[REDACTED]")
            elif isinstance(value, str) and key not in {"msg", "exc_text", "stack_info"}:
                setattr(safe, key, _safe_text(value))
        return safe


class RuntimeFormatter(logging.Formatter):
    def __init__(self, *, structured: bool) -> None:
        super().__init__()
        self.structured = structured

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": _safe_text(record.name, 128),
            "message": _safe_text(record.getMessage()),
        }
        # Explicit allowlist: never serialize arbitrary extras, Settings,
        # provider payloads, headers or exception messages/traceback locals.
        for key in ("request_id", "session_key", "room_id", "run_id", "provider", "event_id"):
            value = getattr(record, key, None)
            if isinstance(value, (str, int)):
                payload[key] = _safe_text(value, 128)
        if record.exc_info and record.exc_info[0]:
            payload["exception_type"] = record.exc_info[0].__name__
        elif getattr(record, "safe_exception_type", None):
            payload["exception_type"] = record.safe_exception_type
        if self.structured:
            return json.dumps(payload, ensure_ascii=True)
        return " ".join(
            (payload["timestamp"], payload["level"], payload["logger"], payload["message"])
        )


def configure_logging(settings: Settings) -> None:
    """Configure application logging without serializing settings or secrets."""

    root = logging.getLogger()
    handler = next((item for item in root.handlers if getattr(item, "apex_owned", False)), None)
    if handler is None:
        handler = logging.StreamHandler()
        handler.apex_owned = True
        root.addHandler(handler)
    handler.setFormatter(RuntimeFormatter(structured=settings.log_format == "json"))
    output_handlers = set(root.handlers)
    # Uvicorn access formatting consumes raw positional args itself. Give its
    # known runtime handlers the safe formatter as well as the shared filter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for runtime_handler in logging.getLogger(name).handlers:
            runtime_handler.setFormatter(RuntimeFormatter(structured=settings.log_format == "json"))
            output_handlers.add(runtime_handler)
    for configured_handler in output_handlers:
        if not any(isinstance(item, CredentialFilter) for item in configured_handler.filters):
            configured_handler.addFilter(CredentialFilter())
    root.setLevel(settings.log_level.upper())
    logging.getLogger("app").info(
        "Starting %s with %s", settings.app_name, settings.safe_runtime_metadata
    )
