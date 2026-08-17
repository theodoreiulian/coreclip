import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch):
    defaults = {
        "GEMINI_API_KEY": "",
        "FFMPEG_INTERMEDIATE_PRESET": "veryfast",
        "FFMPEG_INTERMEDIATE_CRF": "23",
        "FFMPEG_FINAL_PRESET": "medium",
        "FFMPEG_FINAL_CRF": "20",
        "FFMPEG_THREADS": "0",
        "FFMPEG_PREFERRED_ENCODER": "",
        "WHISPER_MODEL_SIZE": "medium",
        "WHISPER_DEVICE": "cpu",
        "WHISPER_COMPUTE_TYPE": "int8",
        "WHISPER_CPU_THREADS": "4",
        "WHISPER_BATCH_SIZE": "4",
        "WHISPER_BEAM_SIZE": "1",
        "WHISPER_VAD_FILTER": "true",
        "REFRAMER_MODE": "auto",
        "REFRAMER_RENDER": "ffmpeg",
        "CLEANUP_SOURCE_AFTER_CLIPPING": "true",
        "CLEANUP_INTERMEDIATES": "true",
        "CLEANUP_TRANSCRIPTS": "true",
        "LOG_LEVEL": "INFO",
    }
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)


def _reload_config():
    import config

    return importlib.reload(config)


def test_ffmpeg_config_defaults():
    cfg = _reload_config().config
    assert cfg.FFMPEG_INTERMEDIATE_PRESET == "veryfast"
    assert cfg.FFMPEG_INTERMEDIATE_CRF == 23
    assert cfg.FFMPEG_FINAL_PRESET == "medium"
    assert cfg.FFMPEG_FINAL_CRF == 20
    assert cfg.FFMPEG_THREADS == 0
    assert cfg.FFMPEG_PREFERRED_ENCODER == ""


def test_whisper_config_defaults():
    cfg = _reload_config().config
    assert cfg.WHISPER_MODEL_SIZE == "medium"
    assert cfg.WHISPER_DEVICE == "cpu"
    assert cfg.WHISPER_COMPUTE_TYPE == "int8"
    assert cfg.WHISPER_CPU_THREADS == 4
    assert cfg.WHISPER_BATCH_SIZE == 4
    assert cfg.WHISPER_BEAM_SIZE == 1
    assert cfg.WHISPER_VAD_FILTER is True


def test_reframer_config_defaults():
    cfg = _reload_config().config
    assert cfg.REFRAMER_MODE == "auto"
    assert cfg.REFRAMER_RENDER == "ffmpeg"


def test_cleanup_defaults_are_true():
    cfg = _reload_config().config
    assert cfg.CLEANUP_SOURCE_AFTER_CLIPPING is True
    assert cfg.CLEANUP_INTERMEDIATES is True
    assert cfg.CLEANUP_TRANSCRIPTS is True


def test_log_level_default():
    cfg = _reload_config().config
    assert cfg.LOG_LEVEL == "INFO"


# The API key used to be an env-only setting that `validate()` hard-failed on.
# The Settings UI superseded that: the key is now supplied at runtime and
# persisted to user_settings.json, so a missing key at startup is a warning
# rather than a fatal error — the server has to boot for the user to be able to
# enter one. These tests assert that current contract.


def test_validate_warns_but_does_not_raise_without_a_key(monkeypatch):
    cfg = _reload_config().config
    monkeypatch.setattr(cfg, "gemini_api_key", lambda: "")
    cfg.validate()  # must not raise — startup has to survive a missing key


def test_gemini_api_key_resolves_from_user_settings(monkeypatch):
    cfg = _reload_config().config
    monkeypatch.setattr(cfg, "_GEMINI_API_KEY", "", raising=False)

    import services.settings_manager as sm
    monkeypatch.setattr(
        sm.user_settings, "effective_gemini_api_key", lambda: "key-from-settings"
    )

    assert cfg.gemini_api_key() == "key-from-settings"
    cfg.validate()


def test_env_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("FFMPEG_INTERMEDIATE_PRESET", "ultrafast")
    monkeypatch.setenv("FFMPEG_INTERMEDIATE_CRF", "28")
    monkeypatch.setenv("WHISPER_BATCH_SIZE", "8")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("CLEANUP_TRANSCRIPTS", "false")

    cfg = _reload_config().config
    assert cfg.FFMPEG_INTERMEDIATE_PRESET == "ultrafast"
    assert cfg.FFMPEG_INTERMEDIATE_CRF == 28
    assert cfg.WHISPER_BATCH_SIZE == 8
    assert cfg.LOG_LEVEL == "DEBUG"
    assert cfg.CLEANUP_TRANSCRIPTS is False
