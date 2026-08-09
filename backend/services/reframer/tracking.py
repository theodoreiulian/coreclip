"""
PASS C — face tracking.

Detections (sparse, every detect_every frames) are linked into per-shot
identity tracks. Within a shot there are no cuts, so motion is continuous
and IoU + velocity prediction + size consistency is a reliable association
cue — no appearance embedding is needed.

Each finished Track carries a dense per-frame face bbox (linearly
interpolated across the detection cadence) plus the associated body bbox.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from services.reframer.detect import FrameDetections, VideoMeta, associate_face_to_person
from services.reframer.shots import Shot

logger = logging.getLogger("clipper")

BBox = tuple[int, int, int, int]


# ── geometry helpers ───────────────────────────────────────────────────


def iou(a: BBox, b: BBox) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _center(b: BBox) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def _size(b: BBox) -> float:
    return max(b[2] - b[0], b[3] - b[1])


def _lerp_box(a: BBox, b: BBox, f: float) -> BBox:
    return tuple(int(round(a[i] + (b[i] - a[i]) * f)) for i in range(4))  # type: ignore


# ── Track ──────────────────────────────────────────────────────────────


@dataclass
class _Obs:
    frame_idx: int
    t: float
    face: BBox
    body: BBox
    conf: float


@dataclass
class Track:
    track_id: int
    shot_id: int
    obs: list[_Obs] = field(default_factory=list)
    # filled by finalize():
    face_by_frame: dict[int, BBox] = field(default_factory=dict)
    body_by_frame: dict[int, BBox] = field(default_factory=dict)
    start_frame: int = 0
    end_frame: int = 0
    start_t: float = 0.0
    end_t: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.end_t - self.start_t

    def covers(self, frame_idx: int) -> bool:
        return self.start_frame <= frame_idx <= self.end_frame

    def face_at(self, frame_idx: int) -> BBox | None:
        return self.face_by_frame.get(frame_idx)

    def body_at(self, frame_idx: int) -> BBox | None:
        return self.body_by_frame.get(frame_idx)

    def _last_obs_boxes(self) -> tuple[BBox, BBox] | None:
        if len(self.obs) >= 2:
            return self.obs[-2].face, self.obs[-1].face
        return None

    def predict_face(self, frame_idx: int) -> BBox:
        """Extrapolate the face box to frame_idx from recent velocity."""
        last = self.obs[-1]
        prev = self._last_obs_boxes()
        if prev is None or self.obs[-1].frame_idx == self.obs[-2].frame_idx:
            return last.face
        b0, b1 = prev
        span = self.obs[-1].frame_idx - self.obs[-2].frame_idx
        step = (frame_idx - last.frame_idx) / span if span else 0.0
        return tuple(int(round(b1[i] + (b1[i] - b0[i]) * step)) for i in range(4))  # type: ignore


# ── association ────────────────────────────────────────────────────────


def _shot_for_t(t: float, shots: list[Shot]) -> int:
    for s in shots:
        if s.start <= t < s.end:
            return s.idx
    return shots[-1].idx if shots else 0


def build_tracks(
    detections: dict[int, FrameDetections],
    shots: list[Shot],
    meta: VideoMeta,
    cfg,
) -> list[Track]:
    """Link sparse detections into dense per-shot face tracks."""
    det_frames = sorted(detections.keys())
    if not det_frames:
        return []

    max_gap_frames = max(1, int(cfg.track_max_gap_s * meta.fps))
    next_id = 0
    open_tracks: list[Track] = []
    closed_tracks: list[Track] = []

    last_shot = -1
    for fidx in det_frames:
        fd = detections[fidx]
        shot_id = _shot_for_t(fd.t, shots)

        # Shot boundary — everything teleports, so close all open tracks.
        if shot_id != last_shot and last_shot != -1:
            closed_tracks.extend(open_tracks)
            open_tracks = []
        last_shot = shot_id

        faces = list(fd.faces)
        persons = fd.persons

        # Build candidate (track, face) pairs scored by IoU-with-prediction.
        pairs: list[tuple[float, int, int]] = []
        for ti, track in enumerate(open_tracks):
            pred = track.predict_face(fidx)
            psize = _size(track.obs[-1].face)
            pcx, pcy = _center(pred)
            for fi, face in enumerate(faces):
                fbox = face[:4]
                score = iou(pred, fbox)
                if score < cfg.track_iou_gate:
                    # distance fallback for fast motion (boxes may not overlap)
                    fcx, fcy = _center(fbox)
                    dist = ((fcx - pcx) ** 2 + (fcy - pcy) ** 2) ** 0.5
                    fsize = _size(fbox)
                    size_ok = psize > 0 and 0.5 <= fsize / psize <= 2.0
                    if size_ok and dist < 0.6 * max(psize, fsize):
                        score = cfg.track_iou_gate * (1.0 - dist / (0.6 * max(psize, fsize)))
                    else:
                        continue
                pairs.append((score, ti, fi))

        pairs.sort(reverse=True)
        used_tracks: set[int] = set()
        used_faces: set[int] = set()
        for score, ti, fi in pairs:
            if ti in used_tracks or fi in used_faces:
                continue
            used_tracks.add(ti)
            used_faces.add(fi)
            face = faces[fi]
            body = associate_face_to_person(face, persons)
            open_tracks[ti].obs.append(_Obs(fidx, fd.t, face[:4], body, face[4]))

        # Unmatched faces → new tracks
        for fi, face in enumerate(faces):
            if fi in used_faces:
                continue
            body = associate_face_to_person(face, persons)
            tr = Track(track_id=next_id, shot_id=shot_id)
            next_id += 1
            tr.obs.append(_Obs(fidx, fd.t, face[:4], body, face[4]))
            open_tracks.append(tr)

        # Coasting tracks that exceeded the gap → close
        still_open: list[Track] = []
        for track in open_tracks:
            if fidx - track.obs[-1].frame_idx > max_gap_frames:
                closed_tracks.append(track)
            else:
                still_open.append(track)
        open_tracks = still_open

    closed_tracks.extend(open_tracks)

    tracks = [_finalize(t) for t in closed_tracks]
    tracks = [t for t in tracks if t is not None]
    tracks.sort(key=lambda t: (t.start_frame, t.track_id))
    logger.info(
        f"build_tracks: {len(tracks)} tracks "
        f"(durations: {[round(t.duration_s, 1) for t in tracks][:8]})"
    )
    return tracks


def _finalize(track: Track) -> Track | None:
    """Densely interpolate the face/body box across every covered frame."""
    if len(track.obs) < 1:
        return None
    obs = sorted(track.obs, key=lambda o: o.frame_idx)
    track.start_frame = obs[0].frame_idx
    track.end_frame = obs[-1].frame_idx
    track.start_t = obs[0].t
    track.end_t = obs[-1].t

    if len(obs) == 1:
        track.face_by_frame[obs[0].frame_idx] = obs[0].face
        track.body_by_frame[obs[0].frame_idx] = obs[0].body
        return track

    for k in range(len(obs) - 1):
        a, b = obs[k], obs[k + 1]
        span = b.frame_idx - a.frame_idx
        for f in range(a.frame_idx, b.frame_idx + 1):
            frac = (f - a.frame_idx) / span if span else 0.0
            track.face_by_frame[f] = _lerp_box(a.face, b.face, frac)
            track.body_by_frame[f] = _lerp_box(a.body, b.body, frac)
    return track
