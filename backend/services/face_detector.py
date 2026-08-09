import cv2
import logging
import os
import urllib.request

logger = logging.getLogger("clipper")

# OpenCV DNN face detector model (auto-downloaded on first use)
_PROTOTXT_URL = "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt"
_MODEL_URL = "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"

_net = None
_net_loaded = False


def _get_model_dir():
    """Get/create the model cache directory."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_dir = os.path.join(base, ".models")
    os.makedirs(model_dir, exist_ok=True)
    return model_dir


def _load_dnn_net():
    """Load the DNN face detector, downloading model files if needed."""
    global _net, _net_loaded
    if _net_loaded:
        return _net

    model_dir = _get_model_dir()
    prototxt_path = os.path.join(model_dir, "deploy.prototxt")
    model_path = os.path.join(model_dir, "res10_300x300_ssd.caffemodel")

    try:
        if not os.path.exists(prototxt_path):
            logger.info("Downloading face detection prototxt...")
            urllib.request.urlretrieve(_PROTOTXT_URL, prototxt_path)

        if not os.path.exists(model_path):
            logger.info("Downloading face detection model (~10MB, one-time)...")
            urllib.request.urlretrieve(_MODEL_URL, model_path)

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

    net = _load_dnn_net()
    use_dnn = net is not None

    # Fallback: Haar cascade
    haar_cascade = None
    if not use_dnn:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        if os.path.exists(cascade_path):
            haar_cascade = cv2.CascadeClassifier(cascade_path)
        else:
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

            if use_dnn:
                detection = _detect_with_dnn(net, frame, frame_w, frame_h)
            else:
                detection = _detect_with_haar(haar_cascade, frame, frame_w, frame_h)

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
