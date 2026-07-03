import logging
import os
import shutil
from sqlalchemy.orm import Session
from models.clip import Clip
from services import ffmpeg_client
from services.video_reframer import reframe_video
from config import config

logger = logging.getLogger("clipper")

def _is_simple_clip(clip_path: str) -> tuple[bool, dict]:
    """
    Determine if a clip is simple enough to bypass ML reframing.
    A clip is "simple" if there is a consistently detected face.
    Returns (is_simple, face_info).
    """
    from services.face_detector import detect_face_position
    face_info = detect_face_position(clip_path, sample_interval=30)
    if face_info.get("detected") and face_info.get("confidence", 0) > 0.7:
        return True, face_info
    return False, face_info

def to_vertical(clip: Clip, db: Session):
    logger.info(f"Reformatting clip {clip.clip_index} to vertical")
    
    if not clip.raw_clip_path or not os.path.exists(clip.raw_clip_path):
        raise FileNotFoundError(f"Raw clip not found for clip {clip.clip_index}")
        
    clip_dir = os.path.dirname(clip.raw_clip_path)
    vertical_path = os.path.join(clip_dir, "vertical.mp4")
    
    info = ffmpeg_client.get_video_info(clip.raw_clip_path)
    width = info['width']
    height = info['height']

    # Only pass through when the source is already at (essentially) the target
    # 9:16 aspect. A 4:5 or 1:1 source still gets speaker-reframed.
    target_aspect = 1080 / 1920
    source_aspect = (width / height) if height else target_aspect
    if abs(source_aspect - target_aspect) / target_aspect < 0.01:
        logger.info(f"Clip {clip.clip_index} is already 9:16. Copying.")
        shutil.copy(clip.raw_clip_path, vertical_path)
        clip.vertical_clip_path = vertical_path
        db.commit()
        return
        
    is_simple = False
    face_info = None
    
    if config.REFRAMER_MODE == 'simple':
        is_simple = True
    elif config.REFRAMER_MODE == 'auto':
        is_simple, face_info = _is_simple_clip(clip.raw_clip_path)

    if is_simple:
        logger.info(f"Using simple reframer for clip {clip.clip_index}")
        from services.face_detector import detect_face_position, compute_crop_region
        if face_info is None:
            face_info = detect_face_position(clip.raw_clip_path, sample_interval=30)
            
        crop_region = compute_crop_region(width, height, face_info)
        
        crop_vf = f"crop={crop_region['w']}:{crop_region['h']}:{crop_region['x']}:{crop_region['y']},scale=1080:1920"
        
        encode_args = ffmpeg_client.build_encode_args()
        args = [
            "-i", clip.raw_clip_path,
            "-vf", crop_vf,
        ] + encode_args + [
            vertical_path
        ]
        ffmpeg_client.run_ffmpeg(args, "Simple Reframe")
        
        clip.vertical_clip_path = vertical_path
        db.commit()
        logger.info(f"Successfully reformatted clip {clip.clip_index} (simple)")
        return
    
    # Find audio for speaker diarization
    job_dir = os.path.join(config.OUTPUT_DIR, clip.job_id)
    audio_path = os.path.join(job_dir, "audio.wav")
    if not os.path.exists(audio_path):
        audio_path = None
    
    # Get pre-computed speaker mapping from the job
    speaker_mapping = getattr(clip.job, 'speaker_mapping', None) if clip.job else None

    # Frame-by-frame reframe with face tracking
    success = reframe_video(
        input_path=clip.raw_clip_path,
        output_path=vertical_path,
        target_w=1080,
        target_h=1920,
        audio_path=audio_path,
        speaker_mapping=speaker_mapping,
    )
    
    if not success:
        raise RuntimeError(f"Reframing failed for clip {clip.clip_index}")
    
    clip.vertical_clip_path = vertical_path
    db.commit()
    logger.info(f"Successfully reformatted clip {clip.clip_index}")
