import time

import pytest

import pipeline.orchestrator as orch
from config import config


def test_stages_no_metadata_stage():
    # Metadata generation is lazy (POST /jobs/{id}/generate-metadata), not a pipeline stage.
    assert orch.STAGES == ["ingesting", "analyzing", "clipping", "processing", "captioning"]
    assert "generating_metadata" not in orch.STAGES


def test_batch_publisher_flushes_on_max_size(monkeypatch):
    published = []
    monkeypatch.setattr(orch, "publish_status_batch", lambda job_id, msgs: published.append((job_id, list(msgs))))

    o = orch.PipelineOrchestrator("job-1")
    pub = orch._BatchPublisher(o, max_size=3, flush_interval=9999)
    pub.publish("processing", "in_progress", clip_id="c1")
    pub.publish("processing", "in_progress", clip_id="c2")
    assert published == []
    pub.publish("processing", "in_progress", clip_id="c3")
    assert len(published) == 1
    assert published[0][0] == "job-1"
    assert [m["clip_id"] for m in published[0][1]] == ["c1", "c2", "c3"]


def test_batch_publisher_flushes_on_exit(monkeypatch):
    published = []
    monkeypatch.setattr(orch, "publish_status_batch", lambda job_id, msgs: published.append(list(msgs)))

    o = orch.PipelineOrchestrator("job-2")
    with orch._BatchPublisher(o, max_size=100, flush_interval=9999) as pub:
        pub.publish("captioning", "in_progress", clip_id="c1")
        assert published == []
    assert len(published) == 1 and published[0][0]["clip_id"] == "c1"


def test_batch_publisher_flushes_on_interval(monkeypatch):
    published = []
    monkeypatch.setattr(orch, "publish_status_batch", lambda job_id, msgs: published.append(list(msgs)))

    o = orch.PipelineOrchestrator("job-3")
    pub = orch._BatchPublisher(o, max_size=100, flush_interval=0.0)
    pub.publish("processing", "in_progress", clip_id="c1")
    assert len(published) == 1


def test_clip_workers_respects_config_override(monkeypatch):
    o = orch.PipelineOrchestrator("job-4")
    monkeypatch.setattr(config, "CLIP_PROCESSING_MAX_WORKERS", 2)
    assert o._clip_workers() == 2
    monkeypatch.setattr(config, "CLIP_PROCESSING_MAX_WORKERS", 0)
    assert 1 <= o._clip_workers() <= 4


def test_generate_metadata_route_registered():
    from api.routes_jobs import router
    paths = {route.path for route in router.routes}
    assert "/jobs/{job_id}/generate-metadata" in paths
