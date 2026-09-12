# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
from io import StringIO

import pytest

from app.core.logging import RuntimeFormatter, configure_logging


@pytest.fixture(autouse=True)
def isolated_logging(capsys):
    # Earlier app-startup tests may own a handler bound to an older stderr
    # capture. Give this test its own output lifetime, preserving host handlers.
    loggers = [
        logging.getLogger(name) for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    ]
    states = [(logger, logger.handlers[:], logger.level) for logger in loggers]
    handlers = {handler for _, items, _ in states for handler in items}
    configurations = [(handler, handler.formatter, handler.filters[:]) for handler in handlers]
    root = logging.getLogger()
    root.handlers = [
        handler for handler in root.handlers if not getattr(handler, "apex_owned", False)
    ]
    try:
        yield
    finally:
        for logger, previous, level in states:
            for handler in logger.handlers:
                if handler not in handlers:
                    handler.close()
            logger.handlers = previous
            logger.setLevel(level)
        for handler, formatter, filters in configurations:
            handler.setFormatter(formatter)
            handler.filters = filters


@pytest.mark.parametrize(
    "message",
    [
        'password="two word secret"',
        "{'token': 'two word secret'}",
        "postgresql://user:two-word-secret@example.test/db",
        "Authorization: Basic two-word-secret",
        "access_token=two-word-secret",
        "openai_api_key=two-word-secret",
        "https://example.test/path?access_token=two-word-secret",
        'Authorization: Digest username="user", response="two-word-secret"',
    ],
)
def test_formatter_redacts_quoted_and_url_credentials(message):
    record = logging.LogRecord("app.synthetic", logging.WARNING, __file__, 1, message, (), None)
    rendered = RuntimeFormatter(structured=True).format(record)
    assert "secret" not in json.loads(rendered)["message"]


def test_formatter_does_not_render_exception_values_or_inject_pretty_lines():
    error = ValueError("never-log-exception-secret")
    record = logging.LogRecord(
        "app.synthetic",
        logging.ERROR,
        __file__,
        1,
        "line one\nforged line",
        (),
        (ValueError, error, None),
    )
    rendered = RuntimeFormatter(structured=True).format(record)
    assert json.loads(rendered)["exception_type"] == "ValueError"
    assert "never-log-exception-secret" not in rendered
    assert "\n" not in RuntimeFormatter(structured=False).format(record)


def test_json_logging_emits_bounded_context_without_credentials(settings, capsys):
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    try:
        configure_logging(settings.model_copy(update={"log_format": "json"}))
        record = logging.LogRecord(
            "app.synthetic",
            logging.WARNING,
            __file__,
            1,
            "provider failed Authorization: Bearer synthetic-secret",
            (),
            None,
        )
        record.session_key = "race-1"
        record.request_id = "request-1"
        record.password = "never-log-this"
        logging.getLogger("app.synthetic").handle(record)
        output = capsys.readouterr().err.splitlines()
        assert output, "configured JSON logging must emit a record"
        payload = json.loads(output[-1])
        assert payload["level"] == "WARNING"
        assert payload["session_key"] == "race-1"
        assert payload["request_id"] == "request-1"
        assert "synthetic-secret" not in json.dumps(payload)
        assert "never-log-this" not in json.dumps(payload)
        record.msg = "x" * 100_000
        logging.getLogger("app.synthetic").handle(record)
        assert len(json.loads(capsys.readouterr().err.splitlines()[-1])["message"]) <= 4096
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)


def test_logging_configuration_is_idempotent_and_preserves_external_handlers(settings, capsys):
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    external = logging.NullHandler()
    root.addHandler(external)
    try:
        configure_logging(settings)
        configure_logging(settings.model_copy(update={"log_format": "json"}))
        assert external in root.handlers
        capsys.readouterr()
        logging.getLogger("app.synthetic").warning("one record")
        assert len(capsys.readouterr().err.splitlines()) == 1
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)


def test_existing_log_handler_cannot_bypass_credential_redaction(settings):
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    output = StringIO()
    external = logging.StreamHandler(output)
    root.addHandler(external)
    try:
        configure_logging(settings)
        logging.getLogger("app.synthetic").warning("password=synthetic-secret")
        assert external in root.handlers
        assert "synthetic-secret" not in output.getvalue()
        assert "[REDACTED]" in output.getvalue()
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)


def test_external_formatter_cannot_emit_authorization_extra(settings):
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    output = StringIO()
    external = logging.StreamHandler(output)
    external.setFormatter(
        logging.Formatter("%(message)s %(authorization)s", defaults={"authorization": "-"})
    )
    root.addHandler(external)
    try:
        configure_logging(settings)
        logging.getLogger("app.synthetic").warning(
            "request rejected", extra={"authorization": "Bearer synthetic-auth-secret"}
        )
        assert "synthetic-auth-secret" not in output.getvalue()
        assert "request rejected [REDACTED]" in output.getvalue()
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)


def test_uvicorn_access_handler_emits_safe_json_without_breaking_access_args(settings):
    from uvicorn.logging import AccessFormatter

    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    prior = root.handlers[:], root.level, access.handlers[:], access.level, access.propagate
    output = StringIO()
    external = logging.StreamHandler(output)
    external.setFormatter(AccessFormatter("%(request_line)s %(status_code)s"))
    access.handlers = [external]
    access.propagate = False
    access.setLevel(logging.INFO)
    try:
        configure_logging(settings.model_copy(update={"log_format": "json"}))
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1",
            "GET",
            "/?access_token=synthetic-secret",
            "1.1",
            200,
        )
        assert "synthetic-secret" not in output.getvalue()
        payload = json.loads(output.getvalue())
        assert "200" in payload["message"]
        assert "[REDACTED]" in payload["message"]
        assert external in access.handlers
    finally:
        root.handlers, root.level, access.handlers, access.level, access.propagate = prior
