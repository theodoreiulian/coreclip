import os
import tempfile

import pytest

import utils.file_manager as fm
from config import config


def test_check_disk_space_returns_true(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    assert fm.check_disk_space(0) is True


def test_ensure_disk_space_returns_free_gb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    free_gb = fm.ensure_disk_space(0)
    assert free_gb > 0


def test_ensure_disk_space_raises_when_below_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="Insufficient disk space"):
        fm.ensure_disk_space(100_000_000)


def test_cleanup_transcripts_removes_transcript_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))

    job_dir = tmp_path / "job-abc"
    clip_dir = job_dir / "clip_0"
    clip_dir.mkdir(parents=True)
    (clip_dir / "transcript.json").write_text("{}")
    (clip_dir / "captioned.mp4").write_text("video")

    fm.cleanup_transcripts("job-abc")

    assert not (clip_dir / "transcript.json").exists()
    assert (clip_dir / "captioned.mp4").exists()


def test_cleanup_transcripts_is_noop_for_missing_job(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    fm.cleanup_transcripts("missing-job")


def test_cleanup_source_files_deletes_source_and_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))

    job_dir = tmp_path / "job-abc"
    job_dir.mkdir()
    (job_dir / "source.mp4").write_text("video")
    (job_dir / "audio.wav").write_text("audio")
    (job_dir / "transcript.json").write_text("{}")

    fm.cleanup_source_files("job-abc")

    assert not (job_dir / "source.mp4").exists()
    assert not (job_dir / "audio.wav").exists()
    assert (job_dir / "transcript.json").exists()


def test_cleanup_intermediates_deletes_raw_and_vertical(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))

    job_dir = tmp_path / "job-abc"
    clip_dir = job_dir / "clip_0"
    clip_dir.mkdir(parents=True)
    (clip_dir / "raw.mp4").write_text("video")
    (clip_dir / "vertical.mp4").write_text("video")
    (clip_dir / "captioned.mp4").write_text("video")

    fm.cleanup_intermediates("job-abc")

    assert not (clip_dir / "raw.mp4").exists()
    assert not (clip_dir / "vertical.mp4").exists()
    assert (clip_dir / "captioned.mp4").exists()
