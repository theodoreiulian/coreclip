"""
Smoke + logic tests for the active-speaker reframer.

Run from backend/:  python test_reframer.py

Covers:
  1. end-to-end reframe of a real clip -> valid 1080x1920 + audio
  2. face tracking: sparse detections -> dense per-shot tracks
  3. THE GUARANTEE: a confidently-speaking on-screen track always wins the crop
  4. hysteresis: a challenger needs a margin to steal the camera
  5. short-segment merge: sub-second speaker blips are erased
  6. fallback ladder: speech+no-speaker -> held, silence -> broll/center
  7. group framing: two close speakers are framed together
  8. camera: snaps at shot cuts, stays inside the frame
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.reframer.config import load_reframer_config
from services.reframer.detect import FrameDetections, VideoMeta
from services.reframer.shots import Shot
from services.reframer.tracking import build_tracks, Track
from services.reframer.speaker_timeline import resolve_speakers
from services.reframer.camera import plan_camera

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


class FakeAudio:
    def __init__(self, speech_segments, envelope=None):
        self.speech_segments = speech_segments
        import numpy as np
        self.envelope = envelope if envelope is not None else np.zeros(0)

    def vad_speech_at(self, t):
        return any(s <= t < e for s, e in self.speech_segments)


def _meta(frames=60, fps=30.0, w=1280, h=720):
    return VideoMeta(fps=fps, width=w, height=h, frame_count=frames,
                     timestamps=[i / fps for i in range(frames)],
                     letterbox=(0, 0, 0, 0))


def _track(tid, frames, cx, fps=30.0, shot=0, w=200, h=200):
    """A synthetic track with a centred face box at every frame."""
    t = Track(track_id=tid, shot_id=shot)
    for f in frames:
        box = (int(cx - w / 2), 200, int(cx + w / 2), 200 + h)
        t.face_by_frame[f] = box
        t.body_by_frame[f] = box
    t.start_frame, t.end_frame = frames[0], frames[-1]
    t.start_t, t.end_t = frames[0] / fps, frames[-1] / fps
    return t


# ── Test 1: end-to-end ─────────────────────────────────────────────────


def test_end_to_end():
    print("\n[1] end-to-end smoke")
    if not os.path.exists("test.mp4"):
        print("  SKIP  test.mp4 not present")
        return
    from services.reframer import reframe_video
    out = "/tmp/reframe_test_out.mp4"
    if os.path.exists(out):
        os.remove(out)
    ok = reframe_video("test.mp4", out, 1080, 1920)
    check("reframe_video returns True", ok)
    check("output file exists", os.path.exists(out))
    if os.path.exists(out):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", out],
            capture_output=True, text=True)
        check("output is 1080x1920", probe.stdout.strip() == "1080,1920",
              probe.stdout.strip())
        aud = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=codec_type", "-of", "csv=p=0", out],
            capture_output=True, text=True)
        check("output has audio", "audio" in aud.stdout)


# ── Test 2: tracking ───────────────────────────────────────────────────


def test_tracking():
    print("\n[2] face tracking")
    cfg = load_reframer_config()
    meta = _meta(frames=60)
    shots = [Shot(0, 0.0, 2.0)]
    detections = {}
    for f in range(0, 60, cfg.detect_every):
        # face A drifts right, face B drifts left — they never collide
        ax = 200 + f * 2
        bx = 1000 - f * 2
        detections[f] = FrameDetections(
            f, f / 30.0,
            faces=[(ax - 80, 150, ax + 80, 320, 0.9),
                   (bx - 80, 150, bx + 80, 320, 0.9)],
            persons=[])
    tracks = build_tracks(detections, shots, meta, cfg)
    check("two tracks found", len(tracks) == 2, f"got {len(tracks)}")
    if len(tracks) == 2:
        spans = sorted(t.end_frame - t.start_frame for t in tracks)
        check("tracks span most of the clip", spans[0] >= 50, spans)
        t0 = tracks[0]
        check("interpolation fills every frame",
              all(f in t0.face_by_frame for f in range(t0.start_frame, t0.end_frame + 1)))


# ── Test 3: THE GUARANTEE ──────────────────────────────────────────────


def test_speaker_guarantee():
    print("[3] THE GUARANTEE — speaker always wins the crop")
    cfg = load_reframer_config()
    meta = _meta(frames=60)
    shots = [Shot(0, 0.0, 2.0)]
    frames = list(range(60))
    a = _track(0, frames, cx=300)
    b = _track(1, frames, cx=950)
    # A speaks first half, B speaks second half
    asd = {0: {f: (0.95 if f < 30 else 0.02) for f in frames},
           1: {f: (0.02 if f < 30 else 0.95) for f in frames}}
    audio = FakeAudio([(0.0, 2.0)])
    decisions = resolve_speakers([a, b], asd, audio, shots, meta, cfg)

    violations = []
    for d in decisions:
        speaking = [tid for tid in (0, 1) if asd[tid][d.frame_idx] >= cfg.asd_confidence]
        if speaking:
            # someone is visibly speaking -> the crop MUST be on a speaker
            if d.mode != "speaker" or d.target_track_id not in speaking:
                violations.append((d.frame_idx, d.mode, d.target_track_id, speaking))
    check("crop is on the speaker in EVERY speaking frame",
          not violations, f"{len(violations)} violations e.g. {violations[:3]}")
    check("crop follows A in first half",
          all(d.target_track_id == 0 for d in decisions[5:28]))
    check("crop follows B in second half",
          all(d.target_track_id == 1 for d in decisions[33:58]))


# ── Test 4: hysteresis ─────────────────────────────────────────────────


def test_hysteresis():
    print("[4] hysteresis — challenger needs a margin to steal the camera")
    cfg = load_reframer_config()
    meta = _meta(frames=60)
    shots = [Shot(0, 0.0, 2.0)]
    frames = list(range(60))
    a = _track(0, frames, cx=300)
    b = _track(1, frames, cx=950)
    # A is the speaker; B hovers just above confidence but never beats A by margin
    asd = {0: {f: 0.80 for f in frames},
           1: {f: 0.62 for f in frames}}
    audio = FakeAudio([(0.0, 2.0)])
    decisions = resolve_speakers([a, b], asd, audio, shots, meta, cfg)
    check("incumbent A keeps the camera (no flicker)",
          all(d.target_track_id == 0 for d in decisions),
          set(d.target_track_id for d in decisions))


# ── Test 5: short-segment merge ────────────────────────────────────────


def test_short_segment_merge():
    print("[5] short-segment merge — sub-second blips erased")
    cfg = load_reframer_config()
    meta = _meta(frames=90)
    shots = [Shot(0, 0.0, 3.0)]
    frames = list(range(90))
    a = _track(0, frames, cx=300)
    b = _track(1, frames, cx=950)
    # B steals the camera for only 5 frames (~0.17s) — below min_speaker_segment_s
    asd = {0: {f: 0.9 for f in frames}, 1: {f: 0.0 for f in frames}}
    for f in range(40, 45):
        asd[0][f] = 0.1
        asd[1][f] = 0.95
    audio = FakeAudio([(0.0, 3.0)])
    decisions = resolve_speakers([a, b], asd, audio, shots, meta, cfg)
    check("5-frame blip on B was merged away",
          all(d.target_track_id == 0 for d in decisions),
          f"blip frames -> {[decisions[f].target_track_id for f in range(40, 45)]}")


# ── Test 6: fallback ladder ────────────────────────────────────────────


def test_fallback_ladder():
    print("[6] fallback ladder — held / broll / center")
    cfg = load_reframer_config()
    meta = _meta(frames=90)
    shots = [Shot(0, 0.0, 3.0)]
    frames = list(range(90))
    a = _track(0, frames, cx=640)
    asd = {0: {f: 0.0 for f in frames}}
    # frames 0-29 A speaks; 30-59 speech continues but nobody speaks on screen;
    # 60-89 silence
    for f in range(30):
        asd[0][f] = 0.9
    audio = FakeAudio([(0.0, 2.0)])  # speech only first 2s
    decisions = resolve_speakers([a], asd, audio, shots, meta, cfg)
    check("speaking frames -> speaker", all(decisions[f].mode == "speaker" for f in range(5, 28)))
    grace = int(cfg.off_screen_grace_s * 30)
    check("off-screen speaker -> held during grace",
          decisions[30 + 2].mode == "held", decisions[32].mode)
    check("after grace -> broll (face present, no speech)",
          decisions[30 + grace + 8].mode in ("broll", "center"),
          decisions[30 + grace + 8].mode)
    check("silence with a face present -> broll",
          decisions[80].mode == "broll", decisions[80].mode)


# ── Test 7: group framing ──────────────────────────────────────────────


def test_group_framing():
    print("[7] group framing — two close speakers framed together")
    cfg = load_reframer_config()
    meta = _meta(frames=30)
    shots = [Shot(0, 0.0, 1.0)]
    frames = list(range(30))
    # two faces close enough to both fit in a 404-wide crop
    a = _track(0, frames, cx=560, w=140)
    b = _track(1, frames, cx=720, w=140)
    asd = {0: {f: 0.9 for f in frames}, 1: {f: 0.88 for f in frames}}
    audio = FakeAudio([(0.0, 1.0)])
    decisions = resolve_speakers([a, b], asd, audio, shots, meta, cfg)
    has_secondary = any(d.secondary_track_id is not None for d in decisions)
    check("a second simultaneous speaker is recorded", has_secondary)
    traj = plan_camera(decisions, [a, b], meta, 1080, 1920, cfg)
    # crop centre should sit between the two faces (~640), not on one of them
    mid_cx = [cf.crop_x + cf.crop_w / 2 for cf in traj[15:]]
    ok = all(600 <= c <= 680 for c in mid_cx)
    check("crop is centred between the two speakers", ok,
          f"centres ~ {mid_cx[0]:.0f}")


# ── Test 8: camera snap + bounds ───────────────────────────────────────


def test_camera():
    print("[8] camera — snaps at cuts onto the new face, stays in bounds")
    cfg = load_reframer_config()
    meta = _meta(frames=60)
    shots = [Shot(0, 0.0, 1.0), Shot(1, 1.0, 2.0)]
    # shot 0: speaker on the left. shot 1: a different face on the right, but
    # its track only STARTS at frame 33 (detection cadence) — frames 30-32 of
    # the new shot have no detected face yet.
    a = _track(0, list(range(30)), cx=200, shot=0)
    b = _track(1, list(range(33, 60)), cx=1080, shot=1)
    asd = {0: {f: 0.9 for f in range(30)}, 1: {f: 0.9 for f in range(33, 60)}}
    audio = FakeAudio([(0.0, 2.0)])
    decisions = resolve_speakers([a, b], asd, audio, shots, meta, cfg)
    traj = plan_camera(decisions, [a, b], meta, 1080, 1920, cfg)
    check("trajectory length matches frames", len(traj) == 60)
    in_bounds = all(0 <= cf.crop_x and cf.crop_x + cf.crop_w <= meta.width
                    and 0 <= cf.crop_y and cf.crop_y + cf.crop_h <= meta.height
                    for cf in traj)
    check("every crop window is inside the frame", in_bounds)
    # the cut at frame 30 should produce a large instant jump (snap)
    jump = abs(traj[30].crop_x - traj[29].crop_x)
    check("camera snaps at the shot cut", jump > 200, f"jump={jump}")
    # the snap must land on the NEW face immediately, even though the face is
    # not detected until frame 33 — no slow slide onto it.
    cut_offsets = [abs((traj[f].crop_x + traj[f].crop_w / 2) - 1080) for f in (30, 31, 32, 33)]
    check("cut snaps straight onto the new face (no slide)",
          all(o < 60 for o in cut_offsets), f"offsets={[round(o) for o in cut_offsets]}")
    # within shot 0 the camera should not teleport frame-to-frame
    max_step = max(abs(traj[i].crop_x - traj[i - 1].crop_x) for i in range(1, 30))
    check("camera is smooth within a shot", max_step < 120, f"max_step={max_step}")


def test_switch_pan():
    print("[9] mid-shot speaker switch — smooth glide, never an abrupt cut")
    cfg = load_reframer_config()
    meta = _meta(frames=60)
    shots = [Shot(0, 0.0, 2.0)]            # ONE continuous shot — no video cut
    frames = list(range(60))
    a = _track(0, frames, cx=250)
    b = _track(1, frames, cx=1030)
    asd = {0: {f: (0.95 if f < 30 else 0.02) for f in frames},
           1: {f: (0.02 if f < 30 else 0.95) for f in frames}}
    audio = FakeAudio([(0.0, 2.0)])
    decisions = resolve_speakers([a, b], asd, audio, shots, meta, cfg)
    traj = plan_camera(decisions, [a, b], meta, 1080, 1920, cfg)
    centers = [cf.crop_x + cf.crop_w / 2 for cf in traj]
    # no single-frame teleport anywhere inside a continuous shot
    max_step = max(abs(centers[i] - centers[i - 1]) for i in range(1, 60))
    check("crop never teleports within a continuous shot", max_step < 260,
          f"max_step={max_step:.0f}")
    # it is genuinely in transit a few frames after the switch
    check("crop is mid-glide shortly after the switch",
          290 < centers[33] < 1000, f"center@33={centers[33]:.0f}")
    # the transition spans multiple frames (a visible pan, not a cut)
    moving = sum(1 for i in range(30, 46) if abs(centers[i] - centers[i - 1]) > 2)
    check("transition is a visible multi-frame glide", moving >= 4,
          f"moving frames={moving}")
    # but it still arrives promptly (~0.5s), not a slow drift
    check("crop reaches the new speaker within ~0.5s",
          abs(centers[45] - 1030) < 60, f"offset={abs(centers[45] - 1030):.0f}")


if __name__ == "__main__":
    test_end_to_end()
    test_tracking()
    test_speaker_guarantee()
    test_hysteresis()
    test_short_segment_merge()
    test_fallback_ladder()
    test_group_framing()
    test_camera()
    test_switch_pan()
    print(f"\n{'=' * 48}\n  {PASS} passed, {FAIL} failed\n{'=' * 48}")
    sys.exit(1 if FAIL else 0)
