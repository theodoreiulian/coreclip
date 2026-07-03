import os
import sys

# Ensure backend/ is on sys.path so all modules are discoverable
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery import Celery
from celery.signals import worker_process_init
from config import config
import logging

logger = logging.getLogger("clipper")

celery_app = Celery('clipper', broker=config.REDIS_URL, include=['tasks.celery_tasks'])

celery_app.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='UTC',
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=1,
    # Keep the Whisper/CV worker alive long enough to amortize model load cost,
    # but restart periodically to cap memory growth from native libraries.
    worker_max_tasks_per_child=int(os.getenv('WORKER_MAX_TASKS_PER_CHILD', '50')),
)

celery_app.conf.beat_schedule = {
    'check-scheduled-posts': {
        'task': 'tasks.celery_tasks.check_scheduled_posts',
        'schedule': 60.0,
    },
}

whisper_model = None

@worker_process_init.connect
def load_whisper_on_startup(**kwargs):
    global whisper_model
    logger.info("Initializing worker process: attempting to load Whisper model")
    try:
        from faster_whisper import WhisperModel
        whisper_model = WhisperModel(
            config.WHISPER_MODEL_SIZE,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
            cpu_threads=config.WHISPER_CPU_THREADS,
        )
        logger.info(f"Successfully loaded Whisper model ({config.WHISPER_MODEL_SIZE}) on {config.WHISPER_DEVICE}")
    except ImportError:
        logger.warning("faster-whisper is not installed. Whisper model will not be loaded.")
        whisper_model = None
    except Exception as e:
        logger.warning(f"Failed to load Whisper model: {e}")
        whisper_model = None
