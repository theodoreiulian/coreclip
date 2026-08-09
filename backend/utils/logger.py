import logging
import contextvars
import json
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
from config import config

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
            handler = RotatingFileHandler(log_file, maxBytes=self.max_bytes, backupCount=self.backup_count)
            handler.setFormatter(self._formatter)
            self._handlers[job_id] = handler
        handler.emit(record)

    def setFormatter(self, fmt):
        super().setFormatter(fmt)
        self._formatter = fmt
        for handler in self._handlers.values():
            handler.setFormatter(fmt)


def _log_level_from_string(level: str) -> int:
    return getattr(logging, level.upper(), logging.INFO)


def setup_logger():
    logger = logging.getLogger("clipper")
    logger.setLevel(_log_level_from_string(config.LOG_LEVEL))

    if logger.handlers:
        return logger

    formatter = JsonFormatter()

    os.makedirs(config.LOG_DIR, exist_ok=True)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    api_handler = RotatingFileHandler(os.path.join(config.LOG_DIR, "api.jsonl"), maxBytes=50_000_000, backupCount=5)
    api_handler.setFormatter(formatter)
    logger.addHandler(api_handler)

    platform_handler = RotatingFileHandler(os.path.join(config.LOG_DIR, "platform.jsonl"), maxBytes=50_000_000, backupCount=5)
    platform_handler.setFormatter(formatter)
    logger.addHandler(platform_handler)

    error_handler = RotatingFileHandler(os.path.join(config.LOG_DIR, "errors.jsonl"), maxBytes=50_000_000, backupCount=5)
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    job_handler = JobFileHandler()
    job_handler.setFormatter(formatter)
    logger.addHandler(job_handler)

    return logger

logger = setup_logger()

def get_job_logger(job_id: str):
    current_job_id.set(job_id)
    return logger
