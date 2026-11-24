"""Final handler output stays content-free across production logging configurations."""

from __future__ import annotations

import logging
import subprocess
import sys

import pytest

from relaydeck.host.observability.exceptions import ExceptionFilter, install_exception_safety

CANARY = "PRIVATE_BODY_/synthetic/private.txt?token=secret"


@pytest.mark.parametrize("level", ["INFO", "WARNING"])
@pytest.mark.parametrize("configuration", ["custom", "reconfigured", "direct"])
def test_framework_color_extras_are_sanitized_after_record_creation(
    level: str, configuration: str
) -> None:
    code = """
import io, logging, logging.config
from uvicorn.logging import DefaultFormatter
from relaydeck.host.observability.exceptions import ExceptionFilter, install_exception_safety
out = io.StringIO()
config = {'version': 1, 'formatters': {'color': {
    '()': DefaultFormatter, 'fmt': '%(levelprefix)s %(message)s', 'use_colors': True}},
    'handlers': {'out': {'class': 'logging.StreamHandler', 'stream': out,
                       'formatter': 'color'}},
    'root': {'level': 'DEBUG', 'handlers': ['out']}}
if CONFIGURATION != 'direct':
    logging.config.dictConfig(config)
install_exception_safety()
install_exception_safety()
if CONFIGURATION == 'reconfigured':
    logging.config.dictConfig(config)
log = logging.getLogger('uvicorn.error')
log.disabled = False
log.setLevel(logging.DEBUG)
if CONFIGURATION == 'direct':
    log.propagate = False
    handler = logging.StreamHandler(out)
    handler.setFormatter(DefaultFormatter(fmt='%(levelprefix)s %(message)s', use_colors=True))
    log.addHandler(handler)
for index in range(2):
    if index and CONFIGURATION == 'reconfigured':
        logging.config.dictConfig(config)
        log.filters.clear()
        log.disabled = False
        log.setLevel(logging.DEBUG)
    log.log(LEVEL, 'Started server process [%d]', 982736591,
            extra={'color_message': '/synthetic/private-color?token=private-token pid=982736591'})
assert sum(isinstance(filter_, ExceptionFilter) for filter_ in log.filters) == 1
assert '/synthetic/' not in out.getvalue()
assert 'private-token' not in out.getvalue() and '982736591' not in out.getvalue()
assert out.getvalue().count('server_started') == 2
assert LEVEL_NAME in out.getvalue()
print(out.getvalue(), end='')
"""
    prefix = (
        f"import logging\nCONFIGURATION={configuration!r}\n"
        f"LEVEL=logging.{level}\nLEVEL_NAME={level!r}\n"
    )
    done = subprocess.run(
        [sys.executable, "-B", "-c", prefix + code],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "--- Logging error ---" not in done.stderr and "Traceback" not in done.stderr


@pytest.mark.parametrize("mode", ["default", "connector", "custom"])
@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_framework_records_have_no_request_or_process_content_at_any_level(
    mode: str, level: str
) -> None:
    code = """
import logging, logging.config
from relaydeck.host.observability.logging import logging_config
from relaydeck.host.observability.exceptions import install_exception_safety
config = logging_config()
if MODE == 'connector':
    config['loggers'] = {}
    config['root'] = {'level': 'DEBUG', 'handlers': ['default']}
elif MODE == 'custom':
    config = {'version': 1, 'handlers': {'out': {'class': 'logging.StreamHandler',
              'formatter': 'plain'}}, 'formatters': {'plain': {
              'format': '%(levelname)s %(message)s'}},
              'root': {'level': 'DEBUG', 'handlers': ['out']}}
logging.config.dictConfig(config)
install_exception_safety()
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
for name in ('uvicorn.error', 'uvicorn.access', 'flet', 'flet_object_patch',
             'asyncio', 'starlette'):
    log = logging.getLogger(name)
    log.disabled = False
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    log.log(LEVEL, 'Started server process [%d]', 982736591)
    log.log(LEVEL, '%s - "WebSocket %s" [accepted]', '127.0.0.1:55123',
            '/synthetic/private-path?token=private-query-payload')
    log.log(LEVEL, '%s - "WebSocket %s" 403', '127.0.0.1:55123',
            '/synthetic/private-rejected?token=private-query-payload')
    log.log(LEVEL, 'Uvicorn running on unix socket %s (Press CTRL+C to quit)',
            '/synthetic/private-socket')
    log.log(LEVEL, 'preformatted /synthetic/private-path?token=private-query-payload')
safe = logging.getLogger('relaydeck.synthetic')
safe.disabled = False
safe.setLevel(logging.DEBUG)
safe.addHandler(handler)
safe.log(LEVEL, 'safe_event count=%d', 7)
"""
    done = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            f"import logging\nMODE={mode!r}\nLEVEL=logging.{level}\n" + code,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    output = done.stdout + done.stderr
    for value in ("/synthetic/", "private-query-payload", "127.0.0.1:55123", "982736591"):
        assert value not in output
    assert "Traceback" not in output and "--- Logging error ---" not in output
    assert f"{level} safe_event count=7" in output


@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING])
def test_configured_framework_filter_cleans_all_final_sinks_without_the_factory(
    level: int,
) -> None:
    from relaydeck.host.observability.logging import logging_config

    config = logging_config()
    record = logging.LogRecord(
        "uvicorn.error", level, "", 0, "WebSocket /synthetic/private-path?token=secret", (), None
    )
    record.message = record.getMessage()
    record.color_message = "private-colored-path"
    for name in config["loggers"]["uvicorn.error"]["filters"]:
        assert config["filters"][name]["()"]().filter(record)
    for formatter in (
        logging.Formatter("%(levelname)s %(message)s"),
        logging.Formatter("%(name)s %(message)s"),
    ):
        output = formatter.format(record)
        assert "/synthetic/" not in output and "secret" not in output
        assert record.levelno == level
        assert not hasattr(record, "color_message") and record.args == ()


@pytest.mark.parametrize("mode", ["default", "connector", "custom"])
def test_production_outputs_and_relogging(mode: str) -> None:
    code = """
import io, logging, logging.config
from relaydeck.host.observability.logging import logging_config
from relaydeck.host.observability.exceptions import install_exception_safety
from relaydeck.host.observability.request_context import current
config = logging_config()
if MODE == 'connector':
    config['loggers'] = {}
    config['root'] = {'level': 'INFO', 'handlers': ['default']}
if MODE == 'custom':
    config = {'version': 1, 'handlers': {'out': {'class': 'logging.StreamHandler'}},
              'root': {'level': 'INFO', 'handlers': ['out']}}
logging.config.dictConfig(config)
install_exception_safety()
install_exception_safety()
root = logging.StreamHandler()
root.setFormatter(logging.Formatter('rid=%(request_id)s %(message)s'))
logging.getLogger().addHandler(root)
current.set('req-safe0001')
class HostileError(Exception):
    def __str__(self): raise AssertionError('exception string was evaluated')
for name in [
    'relaydeck.gateway.errors', 'relaydeck.companion.app.runtime', 'uvicorn.error', 'asyncio',
]:
    log = logging.getLogger(name)
    log.disabled = False
    log.setLevel(logging.INFO)
    try:
        try: raise ValueError(CANARY)
        except ValueError as cause:
            error = HostileError(CANARY)
            error.add_note(CANARY)
            raise ExceptionGroup(CANARY, [error]) from cause
    except Exception:
        log.exception('unsafe %s', CANARY)
        log.error('already interpolated ' + CANARY, exc_info=True)
    log.error('mapping %(error)s', {'error': HostileError(CANARY)})
    log.error('nested %s', {'errors': [HostileError(CANARY)]})
    if name in ('uvicorn.error', 'asyncio'):
        log.error('framework preformatted ' + CANARY)
    log.error('stack ' + CANARY, stack_info=True)
"""
    done = subprocess.run(
        [sys.executable, "-c", f"MODE={mode!r}\nCANARY={CANARY!r}\n" + code],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    output = done.stdout + done.stderr
    assert CANARY not in output
    assert "Traceback" not in output and "ExceptionGroup" not in output
    assert "category=group" in output and "rid=req-safe0001" in output


def test_cached_exception_and_stack_text_are_removed_before_formatting() -> None:
    record = logging.LogRecord("uvicorn.error", logging.ERROR, "synthetic", 1, CANARY, (), None)
    record.exc_text = CANARY
    record.stack_info = CANARY
    record.message = CANARY
    assert ExceptionFilter().filter(record)
    assert CANARY not in logging.Formatter("%(message)s").format(record)


def test_exception_factory_is_idempotent_and_preserves_peer_close() -> None:
    from starlette.websockets import WebSocketDisconnect

    before = logging.getLogRecordFactory()
    try:
        install_exception_safety()
        once = logging.getLogRecordFactory()
        install_exception_safety()
        assert logging.getLogRecordFactory() is once
        exc = WebSocketDisconnect(1001, CANARY)
        record = logging.getLogger("uvicorn.error").makeRecord(
            "uvicorn.error",
            logging.ERROR,
            "synthetic",
            1,
            "Exception in ASGI application",
            (),
            (type(exc), exc, None),
        )
        assert record.levelno == logging.INFO
        assert record.getMessage() == "flet_ws_closed code=1001"
        assert record.exc_info is None
    finally:
        logging.setLogRecordFactory(before)


def test_ordinary_records_keep_their_level_and_primitive_arguments() -> None:
    code = """
import io, logging
from pathlib import Path
from enum import StrEnum
from relaydeck.host.observability.exceptions import install_exception_safety
class Kind(StrEnum):
    A = 'private_enum_value'
    def __str__(self):
        raise AssertionError('custom formatting must not run')
install_exception_safety()
out = io.StringIO()
handler = logging.StreamHandler(out)
handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
log = logging.getLogger('synthetic.ordinary')
log.setLevel(logging.DEBUG)
log.propagate = False
log.addHandler(handler)
log.info('count=%d host=%s', 3, 'x')
log.info('path=%s', Path('/synthetic/PRIVATE_PATH'))
log.warning('outcome=%s', Kind.A)
try:
    raise RuntimeError('PRIVATE_EXCEPTION')
except RuntimeError:
    log.warning('fixed_name id=%d', 42, exc_info=True)
assert out.getvalue().splitlines() == [
    'INFO count=3 host=x', 'INFO log_arguments_dropped', 'WARNING log_arguments_dropped',
    'WARNING fixed_name id=42 category=unexpected'
]
print(out.getvalue(), end='')
"""
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10, check=False
    )
    assert done.returncode == 0, done.stderr
    output = done.stdout + done.stderr
    assert "INFO count=3 host=x" in output
    assert "INFO log_arguments_dropped" in output
    assert "WARNING log_arguments_dropped" in output
    assert "exception" not in output and "PRIVATE_PATH" not in output
    assert "private_enum_value" not in output and "Traceback" not in output


def test_config_builder_does_not_install_a_record_factory() -> None:
    from relaydeck.host.observability.logging import logging_config

    before = logging.getLogRecordFactory()
    assert logging_config()["version"] == 1
    assert logging.getLogRecordFactory() is before


@pytest.mark.parametrize(
    ("template", "arguments"),
    [
        ("unsafe %s", (CANARY,)),
        ("already interpolated " + CANARY, ()),
        ("event value=%s", (CANARY,)),
        ("event value=%s", {"value": CANARY}),
        ("event id=%d", ("token",)),
        ("event id=%d", (float("inf"),)),
        ("event id=%d", ()),
        ("event", (42,)),
    ],
)
def test_unstructured_exception_records_never_keep_their_message(
    template: str, arguments: object
) -> None:
    error = RuntimeError(CANARY)
    record = logging.LogRecord(
        "relaydeck.synthetic",
        logging.WARNING,
        "",
        0,
        template,
        (),
        (type(error), error, None),
    )
    record.args = arguments  # type: ignore[assignment]
    ExceptionFilter().filter(record)
    output = logging.Formatter("%(levelname)s %(message)s").format(record)
    assert output == "WARNING exception category=unexpected"
    assert record.exc_info is None and record.exc_text is None


@pytest.mark.parametrize(
    "template,arguments",
    [
        ("ui_resume_failed", ()),
        ("flet_stale_connection_cleanup_failed", ()),
        ("fixed_name id=%d", (42,)),
        ("event kind=%s count=%d active=%r", ("synthetic.kind", 3, True)),
    ],
)
def test_structured_exception_templates_keep_names_once(
    template: str, arguments: tuple[object, ...]
) -> None:
    error = RuntimeError(CANARY)
    record = logging.LogRecord(
        "relaydeck.synthetic",
        logging.WARNING,
        "",
        0,
        template,
        arguments,
        (type(error), error, None),
    )
    expected = template % arguments + " category=unexpected"
    ExceptionFilter().filter(record)
    ExceptionFilter().filter(record)
    assert logging.Formatter("%(message)s").format(record) == expected
    assert record.exc_info is None and record.exc_text is None


@pytest.mark.parametrize("name", ["uvicorn.error", "flet", "asyncio", "starlette"])
@pytest.mark.parametrize("level", [logging.WARNING, logging.ERROR])
def test_framework_exceptions_do_not_keep_even_structured_templates(name: str, level: int) -> None:
    error = RuntimeError(CANARY)
    record = logging.LogRecord(
        name, level, "", 0, "fixed_name id=%d", (42,), (type(error), error, None)
    )
    ExceptionFilter().filter(record)
    assert logging.Formatter("%(message)s").format(record) == "exception category=unexpected"
    assert record.exc_info is None and record.levelno == level
