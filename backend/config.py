import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    # Try project root .env first, then CWD
    _env_path = os.path.join(_PROJECT_ROOT, ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
    else:
        load_dotenv()
except ImportError:
    pass

def _get_default_whisper_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"

class Config:
    # Base directory (parent of backend/)
    BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_DEFAULT_MODEL: str = os.getenv("GEMINI_DEFAULT_MODEL", "gemini-2.5-flash")
    # Cheaper/faster model used for metadata generation when no per-job override is provided.
    GEMINI_METADATA_MODEL: str = os.getenv("GEMINI_METADATA_MODEL", "gemini-2.5-flash")
    # Multimodal model used to locate a TikTok uploader element from a screenshot
    # when the hardcoded CSS selectors drift. Env-overridable in case the
    # published preview id differs slightly.
    GEMINI_VISION_MODEL: str = os.getenv("GEMINI_VISION_MODEL", "gemini-3-flash-preview")
    # Cache Gemini JSON responses on disk keyed by prompt hash to avoid repeating
    # identical API calls on retries/re-runs.
    GEMINI_CACHE_RESPONSES: bool = os.getenv("GEMINI_CACHE_RESPONSES", "true").lower() == "true"
    GEMINI_CACHE_DIR: str = os.getenv("GEMINI_CACHE_DIR", "./output/.gemini_cache")
    # Enable Google Search Grounding for trending hashtag fetch.
    TRENDING_HASHTAG: bool = os.getenv("TRENDING_HASHTAG", "false").lower() == "true"

    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", _get_default_whisper_device())
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    WHISPER_CPU_THREADS: int = int(os.getenv("WHISPER_CPU_THREADS", "4"))
    WHISPER_BATCH_SIZE: int = int(os.getenv("WHISPER_BATCH_SIZE", "4"))
    WHISPER_BEAM_SIZE: int = int(os.getenv("WHISPER_BEAM_SIZE", "1"))
    WHISPER_VAD_FILTER: bool = os.getenv("WHISPER_VAD_FILTER", "true").lower() == "true"
    # Default to smaller English model on CPU; use multilingual `small` on GPU/MPS.
    WHISPER_MODEL_SIZE: str = os.getenv(
        "WHISPER_MODEL_SIZE",
        "small" if WHISPER_DEVICE in ("cuda", "mps") else "small.en",
    )

    # Phase 6 orchestrator tuning
    CLIP_PROCESSING_MAX_WORKERS: int = int(os.getenv("CLIP_PROCESSING_MAX_WORKERS", "0"))

    # Phase 9 Celery worker tuning
    WORKER_MAX_TASKS_PER_CHILD: int = int(os.getenv("WORKER_MAX_TASKS_PER_CHILD", "50"))
    PIPELINE_SOFT_TIME_LIMIT: int = int(os.getenv("PIPELINE_SOFT_TIME_LIMIT", "1800"))
    PIPELINE_TIME_LIMIT: int = int(os.getenv("PIPELINE_TIME_LIMIT", "2100"))
    POSTING_SOFT_TIME_LIMIT: int = int(os.getenv("POSTING_SOFT_TIME_LIMIT", "600"))
    POSTING_TIME_LIMIT: int = int(os.getenv("POSTING_TIME_LIMIT", "900"))

    # Enable librosa-based audio feature summary passed to moment detection.
    ENABLE_AUDIO_ANALYSIS: bool = os.getenv("ENABLE_AUDIO_ANALYSIS", "true").lower() == "true"

    FFMPEG_INTERMEDIATE_PRESET: str = os.getenv("FFMPEG_INTERMEDIATE_PRESET", "veryfast")
    FFMPEG_INTERMEDIATE_CRF: int = int(os.getenv("FFMPEG_INTERMEDIATE_CRF", "23"))
    FFMPEG_FINAL_PRESET: str = os.getenv("FFMPEG_FINAL_PRESET", "medium")
    FFMPEG_FINAL_CRF: int = int(os.getenv("FFMPEG_FINAL_CRF", "20"))
    FFMPEG_THREADS: int = int(os.getenv("FFMPEG_THREADS", "0"))
    FFMPEG_PREFERRED_ENCODER: str = os.getenv("FFMPEG_PREFERRED_ENCODER", "")

    REFRAMER_MODE: str = os.getenv("REFRAMER_MODE", "auto")
    REFRAMER_RENDER: str = os.getenv("REFRAMER_RENDER", "ffmpeg")

    BURN_CAPTIONS: bool = os.getenv("BURN_CAPTIONS", "true").lower() == "true"

    YTDLP_MAX_HEIGHT: int = int(os.getenv("YTDLP_MAX_HEIGHT", "720"))
    YTDLP_MAX_AUDIO_BITRATE: int = int(os.getenv("YTDLP_MAX_AUDIO_BITRATE", "128"))

    # TikTok posting via local browser automation (Playwright driving the real
    # TikTok Studio web uploader with a logged-in session). No API, no dev app.
    # Each connected account keeps a persistent browser profile under this dir.
    TIKTOK_SESSION_DIR: str = os.getenv("TIKTOK_SESSION_DIR", "./output/.tiktok_sessions")
    # Posting runs headless; set false to watch/debug the automation in a window.
    TIKTOK_HEADLESS: bool = os.getenv("TIKTOK_HEADLESS", "true").lower() == "true"
    # When the hardcoded uploader selectors fail to find an element, fall back to
    # asking GEMINI_VISION_MODEL to locate it from a screenshot. Set false to
    # disable (then a selector miss is a hard failure, as before).
    TIKTOK_VISION_FALLBACK: bool = os.getenv("TIKTOK_VISION_FALLBACK", "true").lower() == "true"
    INSTAGRAM_APP_ID: str = os.getenv("INSTAGRAM_APP_ID", "")
    INSTAGRAM_APP_SECRET: str = os.getenv("INSTAGRAM_APP_SECRET", "")
    INSTAGRAM_BUSINESS_ACCOUNT_ID: str = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
    YOUTUBE_CLIENT_ID: str = os.getenv("YOUTUBE_CLIENT_ID", "")
    YOUTUBE_CLIENT_SECRET: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    SNAPCHAT_CLIENT_ID: str = os.getenv("SNAPCHAT_CLIENT_ID", "")
    SNAPCHAT_CLIENT_SECRET: str = os.getenv("SNAPCHAT_CLIENT_SECRET", "")

    TOKEN_STORE_PATH: str = os.getenv("TOKEN_STORE_PATH", "./token_store.json")

    CLOUD_STORAGE_PROVIDER: str = os.getenv("CLOUD_STORAGE_PROVIDER", "s3")
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_S3_BUCKET: str = os.getenv("AWS_S3_BUCKET", "clipper-temp")
    GCS_BUCKET: str = os.getenv("GCS_BUCKET", "")
    GCS_SERVICE_ACCOUNT_JSON: str = os.getenv("GCS_SERVICE_ACCOUNT_JSON", "")

    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "./output")
    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")
    FONT_DIR: str = os.getenv("FONT_DIR", "./fonts")

    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    MIN_DISK_SPACE_GB: int = int(os.getenv("MIN_DISK_SPACE_GB", "10"))
    CLEANUP_SOURCE_AFTER_CLIPPING: bool = os.getenv("CLEANUP_SOURCE_AFTER_CLIPPING", "true").lower() == "true"
    CLEANUP_INTERMEDIATES: bool = os.getenv("CLEANUP_INTERMEDIATES", "true").lower() == "true"
    CLEANUP_TRANSCRIPTS: bool = os.getenv("CLEANUP_TRANSCRIPTS", "true").lower() == "true"

    def validate(self) -> None:
        """Validate required configuration at application startup."""
        if not self.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is required. Set it in your .env file or environment."
            )

config = Config()
