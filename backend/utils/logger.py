import logging
import contextvars
import json
import os
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime
from config import config

# Every log file this module writes is UTF-8. Without an explicit encoding
# Python picks the locale codepage, which on Windows is cp1252 — and the
# pipeline logs video titles, hashtags and status glyphs (▶ ✓ ✗) that cp1252
# cannot represent, turning a log line into a UnicodeEncodeError mid-job.
_LOG_ENCODING = "utf-8"


class SharedRotatingFileHandler(RotatingFileHandler):
    """A RotatingFileHandler that tolerates other processes holding the file.

    Several processes log to the same files at once: the API server, the Celery
    worker and beat, plus the orchestrator's per-clip process pool. Appending
    concurrently is fine everywhere, but *rotating* is not — Windows refuses to
    rename a file another process still has open, so whichever process trips
    maxBytes first raises and logging spews the traceback to stderr. (POSIX
    renames happily, which is why this only ever bites on Windows.)

    A failed rollover is not worth losing a log line over: another process has
    almost certainly just rotated the same file. Swallow it, reopen, and keep
    writing to whatever file the name now points at.
    """

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            if self.stream:
                try:
                    self.stream.close()
                except OSError:
                    pass
                self.stream = None
            if not self.delay:
                self.stream = self._open()


def _rotating_handler(path: str, formatter: logging.Formatter,
                      max_bytes: int = 50_000_000, backup_count: int = 5):
    """A UTF-8, multi-process-tolerant rotating handler for `path`."""
    handler = SharedRotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding=_LOG_ENCODING
    )
    handler.setFormatter(formatter)
    return handler


current_job_id = contextvars.ContextVar('current_job_id', default=None)
current_clip_id = contextvars.ContextVar('current_clip_id', default=None)
# When set, every log record emitted in this context is tagged with this stage
# (unless the call passed its own `extra={'stage': ...}`). Lets a whole phase —
# e.g. posting — tag its logs without threading `extra=` through every call.
current_stage = contextvars.ContextVar('current_stage', default=None)

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcfromtimestamp(record.created).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            "level": record.levelname,
            "job_id": current_job_id.get(),
            "clip_id": current_clip_id.get(),
            "stage": getattr(record, 'stage', None) or current_stage.get(),
            "message": record.getMessage()
        }
        if hasattr(record, 'extra_data'):
            log_data["extra"] = record.extra_data
            
        return json.dumps({k: v for k, v in log_data.items() if v is not None})

class JobFileHandler(logging.Handler):
    def __init__(self, max_bytes=50_000_000, backup_count=5):
        super().__init__()
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._handlers = {}
        self._formatter = None

    def emit(self, record):
        job_id = current_job_id.get()
        if not job_id:
            return
        handler = self._handlers.get(job_id)
        if handler is None:
            job_dir = os.path.join(config.LOG_DIR, "jobs")
            os.makedirs(job_dir, exist_ok=True)
            log_file = os.path.join(job_dir, f"{job_id}.jsonl")
            handler = _rotating_handler(
                log_file, self._formatter,
                max_bytes=self.max_bytes, backup_count=self.backup_count,
            )
            self._handlers[job_id] = handler
        handler.emit(record)

    def setFormatter(self, fmt):
        super().setFormatter(fmt)
        self._formatter = fmt
        for handler in self._handlers.values():
            handler.setFormatter(fmt)


def _log_level_from_string(level: str) -> int:
    return getattr(logging, level.upper(), logging.INFO)


def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 so non-ASCII log output can never crash a job.

    Both launchers redirect the backend's console output into logs/*.log. A
    redirected stream is not a terminal, so Windows gives it the ANSI codepage
    (cp1252) — and any video title, hashtag or status glyph outside that
    codepage would then raise UnicodeEncodeError from inside a logging call.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding=_LOG_ENCODING, errors="replace")
        except Exception:
            pass  # already detached or wrapped — nothing we can do, nothing broken


def setup_logger():
    logger = logging.getLogger("clipper")
    logger.setLevel(_log_level_from_string(config.LOG_LEVEL))

    if logger.handlers:
        return logger

    formatter = JsonFormatter()

    os.makedirs(config.LOG_DIR, exist_ok=True)

    # The launchers redirect stdout/stderr into log files; on Windows that hands
    # Python the ANSI codepage instead of UTF-8, so re-point the streams first.
    _force_utf8_streams()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    def _file_handler(filename: str):
        return _rotating_handler(os.path.join(config.LOG_DIR, filename), formatter)

    logger.addHandler(_file_handler("api.jsonl"))
    logger.addHandler(_file_handler("platform.jsonl"))

    error_handler = _file_handler("errors.jsonl")
    error_handler.setLevel(logging.ERROR)
    logger.addHandler(error_handler)

    job_handler = JobFileHandler()
    job_handler.setFormatter(formatter)
    logger.addHandler(job_handler)

    return logger

logger = setup_logger()

def get_job_logger(job_id: str):
    current_job_id.set(job_id)
    return logger
