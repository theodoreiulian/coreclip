"""
PASS D — Active Speaker Detection.

For every face track, Light-ASD scores each frame with a speaking
probability by correlating the cropped face video against the audio. This
is the heart of the rework: the crop target is decided by *who is actually
speaking on screen*, frame by frame — not by a one-shot heuristic.

If the vendored weight is missing/corrupt, a per-track lip-motion fallback
scorer runs instead so the pipeline degrades rather than fails.
"""

from __future__ import annotations

import hashlib
import logging
import os

import cv2
import numpy as np

from services.reframer.detect import VideoMeta
from services.reframer.tracking import Track

logger = logging.getLogger("clipper")

_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".models", "asd", "light_asd.pth",
)
_MODEL_SHA256 = "efc375833887eefa9d209dc92810e18519b04c3c73ea35a549f2a7f40b7d94d5"

_CROP_SCALE = 0.40   # Light-ASD face crop padding (matches training preprocessing)
_ASD_FPS = 25.0


# ── Detector singleton ─────────────────────────────────────────────────


class ActiveSpeakerDetector:
    def __init__(self, device: str = "cpu"):
        self.device = device
        self._net = None
        self._available = False
        self._load()

    def _load(self):
        if not os.path.exists(_MODEL_PATH):
            logger.warning(f"ASD weight missing at {_MODEL_PATH} — fallback scorer will be used")
            return
        try:
            with open(_MODEL_PATH, "rb") as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()
            if sha != _MODEL_SHA256:
                logger.warning(f"ASD weight SHA mismatch ({sha[:12]}…) — fallback scorer will be used")
                return
            from services.reframer._light_asd_net import LightASDNet
            net = LightASDNet()
            net.load_checkpoint(_MODEL_PATH, self.device)
            self._net = net
            self._available = True
            logger.info(f"Light-ASD loaded on {self.device}")
        except Exception as e:
            logger.warning(f"Light-ASD load failed ({e}) — fallback scorer will be used")

    @property
    def available(self) -> bool:
        return self._available

    def score(self, video_feature: np.ndarray, audio_mfcc: np.ndarray,
              chunk_durations) -> np.ndarray:
        """
        video_feature: (T_v, 112, 112) uint8/float, 25 fps
        audio_mfcc   : (T_a, 13), 100 fps
        returns      : (T_v,) speaking probability in [0, 1]
        """
        import torch

        t_v = video_feature.shape[0]
        if t_v == 0:
            return np.zeros(0, dtype=np.float32)
        # length sync — audio carries 4 mfcc rows per video frame
        usable = min(t_v, audio_mfcc.shape[0] // 4)
        if usable < 3:
            return np.zeros(t_v, dtype=np.float32)

        vf = torch.from_numpy(video_feature[:usable].astype(np.float32))
        af = torch.from_numpy(audio_mfcc[: usable * 4].astype(np.float32))

        # Overlapping short windows: reacts quickly to a speech onset (no long
        # averaging tail) while the 50% overlap removes chunk-boundary dips.
        sums = np.zeros(usable, dtype=np.float32)
        counts = np.zeros(usable, dtype=np.float32)
        for dur in chunk_durations:
            win_v = max(3, int(round(dur * _ASD_FPS)))
            stride = max(1, win_v // 2)
            pos = 0
            while True:
                end = min(pos + win_v, usable)
                if end - pos < 3:                 # pad a tiny trailing chunk
                    pos, end = max(0, usable - 3), usable
                v = vf[pos:end].unsqueeze(0).to(self.device)
                a = af[pos * 4:end * 4].unsqueeze(0).to(self.device)
                try:
                    p = self._net.speaking_prob(a, v).cpu().numpy()
                except Exception as e:
                    logger.warning(f"ASD forward failed: {e}")
                    p = np.zeros(end - pos, dtype=np.float32)
                sums[pos:end] += p[: end - pos]
                counts[pos:end] += 1.0
                if end >= usable:
                    break
                pos += stride

        avg = sums / np.maximum(counts, 1.0)
        out = np.zeros(t_v, dtype=np.float32)
        out[:usable] = avg
        if usable < t_v:                          # tail with no audio coverage
            out[usable:] = avg[-1] if usable else 0.0
        return out


_detector: ActiveSpeakerDetector | None = None


def get_asd(cfg) -> ActiveSpeakerDetector:
    """Process-global Light-ASD singleton (loaded once per worker)."""
    global _detector
    if _detector is None:
        _detector = ActiveSpeakerDetector(device=cfg.asd_device)
    return _detector


# ── Crop geometry + extraction ─────────────────────────────────────────


def _smooth_geometry(track: Track) -> dict[int, tuple[float, float, float]]:
    """Median-filtered (cx, cy, s) per source frame — stabilises the ASD crop."""
    from scipy.signal import medfilt

    frames = sorted(track.face_by_frame.keys())
    cx = np.array([(track.face_by_frame[f][0] + track.face_by_frame[f][2]) / 2 for f in frames])
    cy = np.array([(track.face_by_frame[f][1] + track.face_by_frame[f][3]) / 2 for f in frames])
    s = np.array([max(track.face_by_frame[f][2] - track.face_by_frame[f][0],
                      track.face_by_frame[f][3] - track.face_by_frame[f][1]) / 2.0
                  for f in frames])
    if len(frames) >= 13:
        k = 13
        cx = medfilt(cx, k); cy = medfilt(cy, k); s = medfilt(s, k)
    return {f: (float(cx[i]), float(cy[i]), float(s[i])) for i, f in enumerate(frames)}


def _crop_face(frame: np.ndarray, cx: float, cy: float, s: float) -> np.ndarray:
    """Light-ASD face crop → 112x112 grayscale."""
    cs = _CROP_SCALE
    bsi = int(max(1.0, s * (1 + 2 * cs)))
    padded = np.pad(frame, ((bsi, bsi), (bsi, bsi), (0, 0)),
                    "constant", constant_values=110)
    my, mx = int(cy) + bsi, int(cx) + bsi
    y1, y2 = int(my - s), int(my + s * (1 + 2 * cs))
    x1, x2 = int(mx - s * (1 + cs)), int(mx + s * (1 + cs))
    face = padded[max(0, y1):y2, max(0, x1):x2]
    if face.size == 0:
        return np.full((112, 112), 110, dtype=np.uint8)
    face = cv2.resize(face, (224, 224))
    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    return gray[56:168, 56:168]


def _extract_all_crops(video_path: str, tracks: list[Track]) -> dict[int, dict[int, np.ndarray]]:
    """One decode pass: 112x112 grayscale face crop per (track, source frame)."""
    geom = {t.track_id: _smooth_geometry(t) for t in tracks}
    crops: dict[int, dict[int, np.ndarray]] = {t.track_id: {} for t in tracks}

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return crops
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        for t in tracks:
            g = geom[t.track_id]
            if frame_idx in g:
                cx, cy, s = g[frame_idx]
                crops[t.track_id][frame_idx] = _crop_face(frame, cx, cy, s)
        frame_idx += 1
    cap.release()
    return crops


# ── Fallback lip-motion scorer ─────────────────────────────────────────


def _fallback_score(video_feature: np.ndarray, envelope_slice: np.ndarray) -> np.ndarray:
    """Sliding-window correlation of mouth-region motion vs audio energy."""
    t_v = video_feature.shape[0]
    if t_v < 3:
        return np.zeros(t_v, dtype=np.float32)
    mouth = video_feature[:, 72:104, 32:80].astype(np.float32)   # lower-centre region
    motion = np.zeros(t_v, dtype=np.float32)
    for i in range(1, t_v):
        motion[i] = float(np.mean(np.abs(mouth[i] - mouth[i - 1])))
    if motion.max() > 1e-6:
        motion = motion / motion.max()
    aud = envelope_slice
    if len(aud) < t_v:
        aud = np.pad(aud, (0, t_v - len(aud)), constant_values=0.0)
    aud = aud[:t_v]
    win = 12
    out = np.zeros(t_v, dtype=np.float32)
    for i in range(t_v):
        a, b = max(0, i - win), min(t_v, i + win)
        m = motion[a:b]; e = aud[a:b]
        if m.std() < 1e-4 or e.std() < 1e-4:
            out[i] = 0.0
            continue
        c = float(np.corrcoef(m, e)[0, 1])
        out[i] = max(0.0, c) * min(1.0, motion[i] * 2.0)
    return out


# ── Main entry point ───────────────────────────────────────────────────


def run_asd(video_path: str, tracks: list[Track], audio, meta: VideoMeta,
            cfg) -> dict[int, dict[int, float]]:
    """Return {track_id: {source_frame_idx: speaking_probability}}."""
    if not tracks:
        return {}

    detector = get_asd(cfg) if cfg.asd_model != "off" else None
    use_model = detector is not None and detector.available
    crops = _extract_all_crops(video_path, tracks)

    result: dict[int, dict[int, float]] = {}
    for track in tracks:
        tcrops = crops.get(track.track_id, {})
        frames = sorted(tcrops.keys())
        if len(frames) < 3:
            result[track.track_id] = {f: 0.0 for f in frames}
            continue

        track_ts = np.array([meta.timestamps[f] if f < len(meta.timestamps)
                              else f / meta.fps for f in frames])
        t0, t1 = track_ts[0], track_ts[-1]
        n_slots = max(3, int(round((t1 - t0) * _ASD_FPS)))

        # resample crops onto a uniform 25 fps grid by timestamp
        slot_ts = t0 + np.arange(n_slots) / _ASD_FPS
        nn = np.clip(np.searchsorted(track_ts, slot_ts), 0, len(frames) - 1)
        for j in range(len(nn)):
            i = nn[j]
            if i > 0 and abs(track_ts[i - 1] - slot_ts[j]) < abs(track_ts[i] - slot_ts[j]):
                nn[j] = i - 1
        video_feature = np.stack([tcrops[frames[i]] for i in nn], axis=0)

        if use_model:
            mfcc = audio.mfcc_window(t0, t0 + n_slots / _ASD_FPS)
            slot_prob = detector.score(video_feature, mfcc, cfg.asd_chunk_durations)
        else:
            i0 = int(t0 * 100)
            env = audio.envelope[i0:i0 + n_slots * 4:4] if audio.envelope.size else np.zeros(n_slots)
            slot_prob = _fallback_score(video_feature, env)

        # map 25 fps slot probabilities back onto source frames
        per_frame: dict[int, float] = {}
        for f in range(track.start_frame, track.end_frame + 1):
            tf = meta.timestamps[f] if f < len(meta.timestamps) else f / meta.fps
            slot = int(round((tf - t0) * _ASD_FPS))
            slot = max(0, min(n_slots - 1, slot))
            per_frame[f] = float(slot_prob[slot]) if slot < len(slot_prob) else 0.0
        result[track.track_id] = per_frame

    speaking = sum(1 for pf in result.values() if pf and max(pf.values()) > cfg.asd_confidence)
    logger.info(f"run_asd: scored {len(tracks)} tracks, {speaking} ever-speaking "
                f"({'Light-ASD' if use_model else 'fallback scorer'})")
    return result
