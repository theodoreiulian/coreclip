import sys
from services.face_detector import detect_face_position, compute_crop_region

print("Testing face detector...")
try:
    res = detect_face_position("test.mp4")
    print(res)
    crop = compute_crop_region(1280, 720, res)
    print(crop)
except Exception as e:
    print(f"Error: {e}")
