import hashlib
import logging
import os
import tempfile
import urllib.request

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python.core.base_options import BaseOptions
from mediapipe.tasks.python.vision.core.vision_task_running_mode import VisionTaskRunningMode
from mediapipe.tasks.python.vision.face_detector import FaceDetector, FaceDetectorOptions

logger = logging.getLogger("clipper")

# Detector weights, fetched once and cached under the model dir. Each entry
# carries the SHA-256 of the exact artifact we expect, because "the file
# exists" is not the same as "the file is correct": an interrupted download, a
# proxy error page saved as a .caffemodel, or an upstream re-tag all leave a
# plausible-looking file that fails deep inside OpenCV with an opaque error.
# Verifying on every load turns that into one clear log line and a re-download.
_PROTOTXT_URL = "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt"
_MODEL_URL = "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"

# MediaPipe face detection model (auto-downloaded on first use)
_MEDIAPIPE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)

_MODEL_SHA256 = {
    "deploy.prototxt": "dcd661dc48fc9de0a341db1f666a2164ea63a67265c7f779bc12d6b3f2fa67e9",
    "res10_300x300_ssd.caffemodel": "2a56a11a57a4a295956b0660b4a3d76bbdca2206c4961cea8efe7d95c7cb2f2d",
    "blaze_face_short_range.tflite": "b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f",
}

_net = None
_net_loaded = False

_mediapipe_detector = None
_mediapipe_loaded = False

_yolo = None
_yolo_loaded = False


def _load_yolo():
    """Lazy-load YOLOv8n as a fallback person detector."""
    global _yolo, _yolo_loaded
    if _yolo_loaded:
        return _yolo
    _yolo_loaded = True
    try:
        from ultralytics import YOLO
        model_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "yolov8n.pt",
        )
        _yolo = YOLO(model_path)
        logger.info("YOLO person detector loaded for face fallback")
    except Exception as e:
        logger.warning(f"YOLO load failed: {e}")
    return _yolo


def _detect_persons_yolo(frame, conf_threshold=0.4):
    """Detect persons with YOLO and return bounding boxes."""
    yolo = _load_yolo()
    if yolo is None:
        return []
    try:
        results = yolo.predict(frame, classes=[0], conf=conf_threshold, verbose=False)
    except Exception as e:
        logger.warning(f"YOLO predict failed: {e}")
        return []
    persons = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            persons.append((int(x1), int(y1), int(x2), int(y2), float(box.conf[0])))
    return persons


def _faces_from_persons(persons):
    """Derive synthetic face boxes from person detections."""
    faces = []
    for px1, py1, px2, py2, conf in persons:
        pw = px2 - px1
        ph = py2 - py1
        if pw <= 0 or ph <= 0:
            continue
        fx1 = int(px1 + pw * 0.2)
        fx2 = int(px2 - pw * 0.2)
        fy1 = int(py1 + ph * 0.05)
        fy2 = int(py1 + ph * 0.35)
        faces.append((fx1, fy1, fx2, fy2, conf))
    return faces


def _get_model_dir():
    """Get/create the model cache directory.

    Defaults to `backend/.models`, which is where the weights are vendored.
    `CORECLIP_MODEL_DIR` points it at a shared location instead, so several
    checkouts, containers or CI runs reuse one cache rather than each paying
    the download again.
    """
    override = os.getenv("CORECLIP_MODEL_DIR")
    if override:
        model_dir = os.path.abspath(os.path.expanduser(override))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        model_dir = os.path.join(base, ".models")
    os.makedirs(model_dir, exist_ok=True)
    return model_dir


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_model(path: str, url: str, description: str) -> bool:
    """Make `path` hold the expected artifact, downloading it if needed.

    Returns True if the file is present and trustworthy. A cached file is
    re-used only when its checksum matches, so a truncated or corrupted
    download is repaired on the next run instead of failing forever. The
    download itself lands on a temp file in the same directory and is moved
    into place atomically, so an interrupted run never leaves a half-written
    model behind for the next one to trust.
    """
    expected = _MODEL_SHA256.get(os.path.basename(path))

    if os.path.exists(path):
        if expected is None or _sha256(path) == expected:
            return True
        logger.warning(f"{description} failed its checksum - re-downloading")
        try:
            os.remove(path)
        except OSError:
            pass

    logger.info(f"Downloading {description} (one-time)...")
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".part")
    os.close(tmp_fd)
    try:
        urllib.request.urlretrieve(url, tmp_path)
        if expected is not None:
            actual = _sha256(tmp_path)
            if actual != expected:
                raise ValueError(
                    f"checksum mismatch (expected {expected[:12]}..., got {actual[:12]}...)"
                )
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        logger.warning(f"Could not fetch {description}: {e}")
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _load_mediapipe_detector():
    """Lazy-load the MediaPipe BlazeFace short-range face detector."""
    global _mediapipe_detector, _mediapipe_loaded
    if _mediapipe_loaded:
        return _mediapipe_detector
    _mediapipe_loaded = True
    try:
        model_dir = _get_model_dir()
        model_path = os.path.join(model_dir, "blaze_face_short_range.tflite")
        if not _ensure_model(model_path, _MEDIAPIPE_MODEL_URL,
                             "MediaPipe face detection model (~1MB)"):
            _mediapipe_detector = None
            return _mediapipe_detector

        options = FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=VisionTaskRunningMode.IMAGE,
            min_detection_confidence=0.5,
        )
        _mediapipe_detector = FaceDetector.create_from_options(options)
        logger.info("MediaPipe face detector loaded successfully")
    except Exception as e:
        logger.warning(f"Failed to load MediaPipe face detector: {e}")
        _mediapipe_detector = None
    return _mediapipe_detector


def detect_faces_mediapipe(frame: np.ndarray, conf_threshold: float = 0.5) -> list:
    """
    Detect faces in a BGR frame using MediaPipe.
    Returns list of (x1, y1, x2, y2, confidence) in pixel coordinates.
    """
    detector = _load_mediapipe_detector()
    if detector is None:
        return []

    try:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = detector.detect(mp_image)
    except Exception as e:
        logger.warning(f"MediaPipe face detection failed: {e}")
        return []

    h, w = frame.shape[:2]
    faces = []
    for detection in result.detections:
        conf = float(detection.categories[0].score)
        if conf < conf_threshold:
            continue
        bbox = detection.bounding_box
        x1 = max(0, int(bbox.origin_x))
        y1 = max(0, int(bbox.origin_y))
        x2 = min(w, int(bbox.origin_x + bbox.width))
        y2 = min(h, int(bbox.origin_y + bbox.height))
        if x2 > x1 and y2 > y1:
            faces.append((x1, y1, x2, y2, conf))
    return faces


def _load_dnn_net():
    """Load the DNN face detector, downloading model files if needed."""
    global _net, _net_loaded
    if _net_loaded:
        return _net

    model_dir = _get_model_dir()
    prototxt_path = os.path.join(model_dir, "deploy.prototxt")
    model_path = os.path.join(model_dir, "res10_300x300_ssd.caffemodel")

    try:
        ok = _ensure_model(prototxt_path, _PROTOTXT_URL, "face detection prototxt")
        ok = _ensure_model(model_path, _MODEL_URL, "face detection model (~10MB)") and ok
        if not ok:
            # Fall through to the handler below so there is one exit path and
            # the caller still gets None (MediaPipe/YOLO/Haar cover for us).
            raise RuntimeError("model files unavailable")

        _net = cv2.dnn.readNetFromCaffe(prototxt_path, model_path)
        logger.info("DNN face detector loaded successfully")
    except Exception as e:
        logger.warning(f"Failed to load DNN face detector: {e}")
        _net = None

    _net_loaded = True
    return _net


def detect_face_position(video_path: str, sample_interval: int = 15) -> dict:
    """
    Detect the median face position in a video.
    Uses OpenCV DNN (SSD ResNet-10) for high accuracy, falls back to Haar cascade.
    Returns normalized coordinates (0-1) for the detected face center.
    """
    try:
        return _detect_face_impl(video_path, sample_interval)
    except Exception as e:
        logger.warning(f"Face detection failed (falling back to center crop): {e}")
        return {"detected": False}


def _detect_face_impl(video_path: str, sample_interval: int) -> dict:
    logger.info(f"Running face detection on {video_path}")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Failed to open video: {video_path}")
        return {"detected": False}

    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Cap at 200 sampled frames to avoid slow processing on long videos
    max_samples = 200
    if total_frames > 0:
        adjusted_interval = max(sample_interval, total_frames // max_samples)
    else:
        adjusted_interval = sample_interval

    # Primary: MediaPipe BlazeFace
    mp_detector = _load_mediapipe_detector()
    use_mediapipe = mp_detector is not None

    # Legacy fallbacks (mostly unavailable in OpenCV 5 headless builds)
    net = None
    use_dnn = False
    haar_cascade = None
    use_yolo_fallback = False

    if not use_mediapipe:
        net = _load_dnn_net()
        use_dnn = net is not None
        if not use_dnn:
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            if os.path.exists(cascade_path):
                haar_cascade = cv2.CascadeClassifier(cascade_path)
            else:
                logger.warning("No native face detector available; attempting YOLO person fallback")
                use_yolo_fallback = _load_yolo() is not None
                if not use_yolo_fallback:
                    logger.warning("No face detector available")
                    cap.release()
                    return {"detected": False}

    face_centers_x = []
    face_centers_y = []
    face_sizes = []
    frame_count = 0
    face_count = 0
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % adjusted_interval == 0:
            frame_count += 1
            detection = None

            if use_mediapipe:
                detection = _detect_with_mediapipe(frame, frame_w, frame_h)
            elif use_dnn:
                detection = _detect_with_dnn(net, frame, frame_w, frame_h)
            elif haar_cascade is not None:
                detection = _detect_with_haar(haar_cascade, frame, frame_w, frame_h)
            else:
                detection = _detect_with_yolo_fallback(frame, frame_w, frame_h)

            if detection:
                face_count += 1
                face_centers_x.append(detection["cx"])
                face_centers_y.append(detection["cy"])
                face_sizes.append(detection["size"])

        frame_idx += 1

    cap.release()

    if not face_centers_x:
        logger.info(f"No faces detected across {frame_count} sampled frames")
        return {"detected": False}

    import statistics

    result = {
        "detected": True,
        "median_x": statistics.median(face_centers_x),
        "median_y": statistics.median(face_centers_y),
        "median_size": statistics.median(face_sizes),
        "frame_count": frame_count,
        "face_count": face_count,
        "confidence": face_count / frame_count if frame_count > 0 else 0,
    }
    logger.info(
        f"Face detected: center=({result['median_x']:.2f}, {result['median_y']:.2f}), "
        f"confidence={result['confidence']:.0%} ({face_count}/{frame_count} frames)"
    )
    return result


def _detect_with_mediapipe(frame, frame_w: int, frame_h: int) -> dict | None:
    """Run MediaPipe BlazeFace. Returns best (largest) detection or None."""
    faces = detect_faces_mediapipe(frame, conf_threshold=0.5)
    if not faces:
        return None

    largest = max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))
    x1, y1, x2, y2, conf = largest
    return {
        "cx": (x1 + x2) / 2 / frame_w,
        "cy": (y1 + y2) / 2 / frame_h,
        "size": max((x2 - x1) / frame_w, (y2 - y1) / frame_h),
        "confidence": conf,
    }


def _detect_with_dnn(net, frame, frame_w: int, frame_h: int) -> dict | None:
    """Run SSD ResNet-10 face detector. Returns best detection or None."""
    blob = cv2.dnn.blobFromImage(frame, 1.0, (300, 300), (104.0, 177.0, 123.0))
    net.setInput(blob)
    detections = net.forward()

    best = None
    best_conf = 0.0

    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence < 0.5:
            continue

        x1 = max(0.0, detections[0, 0, i, 3])
        y1 = max(0.0, detections[0, 0, i, 4])
        x2 = min(1.0, detections[0, 0, i, 5])
        y2 = min(1.0, detections[0, 0, i, 6])

        w = x2 - x1
        h = y2 - y1
        area = w * h

        if confidence > best_conf and area > 0.001:
            best_conf = confidence
            best = {
                "cx": (x1 + x2) / 2,
                "cy": (y1 + y2) / 2,
                "size": max(w, h),
            }

    return best


def _detect_with_haar(cascade, frame, frame_w: int, frame_h: int) -> dict | None:
    """Fallback Haar cascade detection. Returns best detection or None."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))

    if len(faces) == 0:
        return None

    largest = max(faces, key=lambda f: f[2] * f[3])
    x, y, w, h = largest

    return {
        "cx": (x + w / 2) / frame_w,
        "cy": (y + h / 2) / frame_h,
        "size": max(w / frame_w, h / frame_h),
    }


def _detect_with_yolo_fallback(frame, frame_w: int, frame_h: int) -> dict | None:
    """Use YOLO person boxes to estimate a face center. Returns best or None."""
    persons = _detect_persons_yolo(frame)
    if not persons:
        return None
    faces = _faces_from_persons(persons)
    if not faces:
        return None

    best = max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))
    fx1, fy1, fx2, fy2, _ = best
    return {
        "cx": (fx1 + fx2) / 2 / frame_w,
        "cy": (fy1 + fy2) / 2 / frame_h,
        "size": max((fx2 - fx1) / frame_w, (fy2 - fy1) / frame_h),
    }


def compute_crop_region(
    video_width: int,
    video_height: int,
    face_info: dict,
    target_aspect_ratio: float = 9 / 16,
) -> dict:
    """
    Compute a crop region that keeps the detected face centered.
    Ensures the face is always fully visible within the crop.
    """
    # Calculate crop dimensions
    source_ar = video_width / video_height

    if source_ar > target_aspect_ratio:
        # Source is wider than target — crop width
        crop_h = video_height
        crop_w = int(crop_h * target_aspect_ratio)
    else:
        # Source is taller than target — crop height
        crop_w = video_width
        crop_h = int(crop_w / target_aspect_ratio)

    if face_info.get("detected"):
        face_cx = face_info["median_x"] * video_width
        face_cy = face_info["median_y"] * video_height

        # Add padding around the face so it's not pinned to the exact center
        # but is comfortably within the top third (more natural framing)
        face_size_px = face_info.get("median_size", 0.15) * video_width
        padding = face_size_px * 0.5

        # Place the face in the upper-center area of the crop (rule of thirds)
        # Shift crop so face_cy lands at ~40% from the top of the crop
        target_y_ratio = 0.38
        crop_x = int(face_cx - crop_w / 2)
        crop_y = int(face_cy - crop_h * target_y_ratio)
    else:
        # No face: center crop
        crop_x = (video_width - crop_w) // 2
        crop_y = (video_height - crop_h) // 2

    # Clamp to frame bounds
    crop_x = max(0, min(crop_x, video_width - crop_w))
    crop_y = max(0, min(crop_y, video_height - crop_h))

    # Ensure even dimensions (required by h264)
    crop_w = crop_w - (crop_w % 2)
    crop_h = crop_h - (crop_h % 2)
    crop_x = crop_x - (crop_x % 2)
    crop_y = crop_y - (crop_y % 2)

    return {"x": crop_x, "y": crop_y, "w": crop_w, "h": crop_h}
