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


# ── Disk-usage guardrails ────────────────────────────────────────────────
#
# The threshold is expressed in GB, so "plenty of space" is min_gb=0 and
# "impossible to satisfy" is an absurdly large number. That keeps these tests
# honest without needing to fill a real disk.


def test_reclaim_is_noop_when_space_is_sufficient(tmp_path, monkeypatch):
    """A healthy install must never lose data to the guardrail."""
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "video.mp4").write_text("cached source")

    fm.reclaim_disk_space(0)

    assert (cache / "video.mp4").exists()


def test_reclaim_prunes_the_source_cache_under_pressure(tmp_path, monkeypatch):
    """Cached downloads go first — they cost bandwidth to replace, not data."""
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "INTERMEDIATE_DIR", str(tmp_path / "inter"))
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "old.mp4").write_text("cached source")

    fm.reclaim_disk_space(100_000_000)

    assert not (cache / "old.mp4").exists()


def test_reclaim_spares_jobs_awaiting_review(tmp_path, monkeypatch):
    """ready_for_review output is the user's unreviewed work — never reclaim it.

    Only terminal statuses are eligible; anything mid-pipeline or awaiting a
    human is off limits, which is also what makes reclamation safe to call from
    inside a running job.
    """
    assert "ready_for_review" not in fm._RECLAIMABLE_STATUSES
    assert "posting" not in fm._RECLAIMABLE_STATUSES
    for stage in ("clipping", "processing", "captioning"):
        assert stage not in fm._RECLAIMABLE_STATUSES


def test_ensure_disk_space_reclaims_before_raising(tmp_path, monkeypatch):
    """The guard must attempt a reclaim, then still fail loudly if it can't."""
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(fm, "reclaim_disk_space", lambda min_gb: calls.append(min_gb) or 0.0)

    with pytest.raises(RuntimeError, match="Insufficient disk space"):
        fm.ensure_disk_space(100_000_000)

    assert calls == [100_000_000]


def test_check_disk_space_reclaims_before_refusing(tmp_path, monkeypatch):
    """A queue full of finished jobs shouldn't start rejecting new ones."""
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fm, "reclaim_disk_space", lambda min_gb: float(min_gb))

    assert fm.check_disk_space(100_000_000) is True
