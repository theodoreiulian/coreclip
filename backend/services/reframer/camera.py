"""
PASS E — virtual camera planner.

Converts the per-frame speaker decisions into a smooth crop trajectory.
A global forward pass (state is deterministic and order-defined):

  • dead-zone   — ignore sub-pixel jitter; the camera only moves once the
                  subject drifts out of a slack box
  • smooth-damp — critically-damped easing toward the target (no overshoot,
                  framerate-independent)
  • eased pan   — a faster smooth-damp on a speaker switch within a shot
  • snap        — the camera teleports at every shot cut

Crop size is fixed (punch-in disabled): never tighter than the base
aspect-preserving window, so the output is never upscaled beyond it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from services.reframer.detect import VideoMeta
from services.reframer.speaker_timeline import FrameDecision
from services.reframer.tracking import Track

logger = logging.getLogger("clipper")


@dataclass
class CropFrame:
    frame_idx: int
    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int
    mode: str
    target_track_id: int | None


def _smooth_damp(cur: float, target: float, vel: float, smooth_time: float,
                 dt: float) -> tuple[float, float]:
    """Unity-style critically-damped follow. Returns (new_pos, new_vel)."""
    smooth_time = max(1e-4, smooth_time)
    omega = 2.0 / smooth_time
    x = omega * dt
    exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
    change = cur - target
    temp = (vel + omega * change) * dt
    new_vel = (vel - omega * temp) * exp
    new_pos = target + (change + temp) * exp
    # kill residual overshoot
    if (target - cur > 0.0) == (new_pos > target):
        new_pos = target
        new_vel = 0.0
    return new_pos, new_vel


def _crop_size(meta: VideoMeta, target_w: int, target_h: int) -> tuple[int, int]:
    target_aspect = target_w / target_h
    aw, ah = meta.active_w, meta.active_h
    if aw / ah > target_aspect:
        crop_h = ah
        crop_w = int(round(crop_h * target_aspect))
    else:
        crop_w = aw
        crop_h = int(round(crop_w / target_aspect))
    crop_w = min(aw, crop_w - (crop_w % 2))
    crop_h = min(ah, crop_h - (crop_h % 2))
    return crop_w, crop_h


def _desired_center(
    d: FrameDecision, by_id: dict[int, Track], meta: VideoMeta,
    crop_w: int, crop_h: int, cfg,
) -> tuple[float, float] | None:
    """Crop centre for this frame, or None when there is no on-screen
    subject to frame (the camera then holds its current position rather
    than drifting toward the geometric centre)."""
    face_offset_y = crop_h * (0.5 - cfg.face_top_frac)

    if d.target_track_id is None:        # held / center / no subject → hold
        return None

    track = by_id.get(d.target_track_id)
    if track is None:
        return None
    box = track.face_at(d.frame_idx)
    if box is None:
        return None
    fcx = (box[0] + box[2]) / 2.0
    fcy = (box[1] + box[3]) / 2.0

    # group framing — centre between two speakers if they both fit
    if d.mode == "speaker" and d.secondary_track_id is not None and cfg.allow_group_framing:
        sec = by_id.get(d.secondary_track_id)
        sbox = sec.face_at(d.frame_idx) if sec else None
        if sbox is not None:
            x1 = min(box[0], sbox[0])
            x2 = max(box[2], sbox[2])
            if (x2 - x1) <= crop_w * (1.0 - cfg.group_pad_frac):
                fcx = (x1 + x2) / 2.0
                fcy = (fcy + (sbox[1] + sbox[3]) / 2.0) / 2.0

    return (fcx, fcy + face_offset_y)


def _shot_subject_target(
    decisions: list[FrameDecision], i: int, by_id: dict[int, Track],
    meta: VideoMeta, crop_w: int, crop_h: int, cfg,
) -> tuple[float, float] | None:
    """Crop centre of the first frame in this shot that targets an actual
    tracked face. A shot cut snaps straight onto that face — even if the
    very first frames of the shot haven't resolved a subject yet (detection
    runs on a cadence, tracks take a frame or two to start)."""
    shot = decisions[i].shot_id
    for j in range(i, len(decisions)):
        if decisions[j].shot_id != shot:
            break
        if decisions[j].target_track_id is None:
            continue
        c = _desired_center(decisions[j], by_id, meta, crop_w, crop_h, cfg)
        if c is not None:
            return c
    return None


def _clamp(cx: float, cy: float, meta: VideoMeta, crop_w: int, crop_h: int) -> tuple[int, int]:
    min_x = meta.active_x0
    max_x = meta.active_x0 + meta.active_w - crop_w
    min_y = meta.active_y0
    max_y = meta.active_y0 + meta.active_h - crop_h
    crop_x = int(round(cx - crop_w / 2.0))
    crop_y = int(round(cy - crop_h / 2.0))
    crop_x = max(min_x, min(crop_x, max_x))
    crop_y = max(min_y, min(crop_y, max_y))
    crop_x -= crop_x % 2
    crop_y -= crop_y % 2
    return crop_x, crop_y


def plan_camera(
    decisions: list[FrameDecision],
    tracks: list[Track],
    meta: VideoMeta,
    target_w: int,
    target_h: int,
    cfg,
) -> list[CropFrame]:
    by_id = {t.track_id: t for t in tracks}
    crop_w, crop_h = _crop_size(meta, target_w, target_h)
    logger.info(f"plan_camera: crop window {crop_w}x{crop_h} from "
                f"{meta.active_w}x{meta.active_h} active region")

    out: list[CropFrame] = []
    if not decisions:
        return out

    # camera state
    cam_x = meta.active_x0 + meta.active_w / 2.0
    cam_y = meta.active_y0 + meta.active_h / 2.0
    vel_x = vel_y = 0.0
    prev_shot: int | None = None
    prev_target: int | None = None
    panning = False          # mid-shot glide to a new speaker, until it arrives

    dz_x = cfg.deadzone_frac * crop_w / 2.0
    dz_y = cfg.deadzone_frac * crop_h / 2.0

    for i, d in enumerate(decisions):
        dt = 1.0 / meta.fps
        if i > 0:
            dt = max(1e-3, d.t - decisions[i - 1].t)

        desired = _desired_center(d, by_id, meta, crop_w, crop_h, cfg)
        cross_shot = (prev_shot is not None and d.shot_id != prev_shot)

        # current "active" subject id (speaker or broll target)
        cur_target = d.target_track_id if d.mode in ("speaker", "broll") else None
        switched = (
            not cross_shot
            and cur_target is not None
            and prev_target is not None
            and cur_target != prev_target
        )
        if cross_shot or prev_shot is None:
            # hard cut — instantly lock onto the new shot's face, synced to
            # the cut. Snap to the shot's actual subject (not the geometric
            # centre) so the camera never slides onto the new face.
            snap = _shot_subject_target(decisions, i, by_id, meta, crop_w, crop_h, cfg)
            if snap is None:
                snap = desired   # no detectable face anywhere in the shot
            if snap is not None:
                cam_x, cam_y = snap
            vel_x = vel_y = 0.0
            panning = False
        elif switched and desired is not None and cfg.speaker_switch_snap:
            # optional hard-cut onto a new speaker mid-shot (off by default)
            cam_x, cam_y = desired
            vel_x = vel_y = 0.0
            panning = False
        elif desired is None:
            # held — freeze the camera
            vel_x = vel_y = 0.0
            panning = False
        else:
            if switched:
                panning = True               # glide to the new speaker

            if panning:
                # glide straight toward the speaker until the camera arrives
                smooth_time = cfg.pan_ease_s
                leash_x, leash_y = desired
            else:
                # dead-zone: only chase once the subject leaves the slack box
                smooth_time = cfg.settle_time_s
                leash_x = cam_x if abs(desired[0] - cam_x) <= dz_x else \
                    desired[0] - (dz_x if desired[0] > cam_x else -dz_x)
                leash_y = cam_y if abs(desired[1] - cam_y) <= dz_y else \
                    desired[1] - (dz_y if desired[1] > cam_y else -dz_y)

            cam_x, vel_x = _smooth_damp(cam_x, leash_x, vel_x, smooth_time, dt)
            cam_y, vel_y = _smooth_damp(cam_y, leash_y, vel_y, smooth_time, dt)

            # the glide ends once the camera has reached the speaker
            if panning and abs(desired[0] - cam_x) <= dz_x \
                    and abs(desired[1] - cam_y) <= dz_y:
                panning = False

        crop_x, crop_y = _clamp(cam_x, cam_y, meta, crop_w, crop_h)
        out.append(CropFrame(d.frame_idx, crop_x, crop_y, crop_w, crop_h,
                             d.mode, d.target_track_id))

        prev_shot = d.shot_id
        if cur_target is not None:
            prev_target = cur_target
        elif cross_shot:
            prev_target = None

    return out
