import json
import logging
import os
import tempfile

import pytest

from utils.logger import JobFileHandler, JsonFormatter, _log_level_from_string


def test_log_level_from_string():
    assert _log_level_from_string("DEBUG") == logging.DEBUG
    assert _log_level_from_string("INFO") == logging.INFO
    assert _log_level_from_string("WARNING") == logging.WARNING
    assert _log_level_from_string("ERROR") == logging.ERROR
    assert _log_level_from_string("unknown") == logging.INFO


def test_json_formatter_outputs_expected_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="clipper",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="test message",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)
    assert data["level"] == "INFO"
    assert data["message"] == "test message"
    assert "timestamp" in data


def test_job_file_handler_writes_rotating_log(monkeypatch):
    import utils.logger as logger_module

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(logger_module.config, "LOG_DIR", tmpdir)

        handler = JobFileHandler(max_bytes=1024, backup_count=2)
        handler.setFormatter(JsonFormatter())

        logger_module.current_job_id.set("job-123")
        record = logging.LogRecord(
            name="clipper",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="job log entry",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        log_path = os.path.join(tmpdir, "jobs", "job-123.jsonl")
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            line = f.readline()
            data = json.loads(line)
            assert data["message"] == "job log entry"


def test_setup_logger_uses_configured_log_level(monkeypatch):
    import utils.logger as logger_module

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(logger_module.config, "LOG_LEVEL", "ERROR")
        monkeypatch.setattr(logger_module.config, "LOG_DIR", tmpdir)

        logger_module.logging.getLogger("clipper").handlers.clear()
        logger = logger_module.setup_logger()

        assert logger.level == logging.ERROR
