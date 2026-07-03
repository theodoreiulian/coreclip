import sys
import os
import logging
from services.ffmpeg_client import get_video_info, extract_audio, _get_ffmpeg_binary, _get_ffprobe_binary, _detect_hardware_encoder, build_encode_args

logging.basicConfig(level=logging.INFO)

print("Testing FFmpeg binary caching...")
bin1 = _get_ffmpeg_binary()
bin2 = _get_ffmpeg_binary()
assert bin1 == bin2, "FFmpeg binary path is not cached!"
print(f"FFmpeg binary cached: {bin1}")

print("\nTesting FFprobe binary caching...")
probe1 = _get_ffprobe_binary()
probe2 = _get_ffprobe_binary()
assert probe1 == probe2, "FFprobe binary path is not cached!"
print(f"FFprobe binary cached: {probe1}")

print("\nTesting hardware encoder detection and caching...")
enc1 = _detect_hardware_encoder()
enc2 = _detect_hardware_encoder()
assert enc1 == enc2, "Hardware encoder is not cached!"
print(f"Hardware encoder selected and cached: {enc1}")

print("\nTesting encode args generation...")
args = build_encode_args(crf=23)
print(f"Encode args: {args}")
if enc1 in ["h264_videotoolbox", "hevc_videotoolbox", "h264_qsv"]:
    assert "-q:v" in args, f"Hardware encoder {enc1} should use -q:v"
elif enc1 == "h264_nvenc":
    assert "-b:v" in args, f"Hardware encoder {enc1} should use -b:v"
else:
    assert "-crf" in args, f"Software encoder {enc1} should use -crf"

print("\nGetting video info for test.mp4...")
try:
    if not os.path.exists("test.mp4"):
        print("test.mp4 not found, skipping extraction tests.")
        sys.exit(0)
        
    info = get_video_info("test.mp4")
    print(f"Info: {info}")
    
    print("Extracting audio to test.wav...")
    extract_audio("test.mp4", "test.wav")
    
    if os.path.exists("test.wav"):
        print("Success! test.wav created.")
    else:
        print("Failed to create test.wav")
        sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
