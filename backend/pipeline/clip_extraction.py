import os
import logging
from sqlalchemy.orm import Session
from models.job import Job
from models.clip import Clip
from services import ffmpeg_client, whisper_client
from utils.file_manager import get_intermediate_dir
from config import config
import json

logger = logging.getLogger("clipper")

def extract_all(job: Job, db: Session):
    logger.info(f"Starting clip extraction for job {job.id}")
    clips = db.query(Clip).filter(Clip.job_id == job.id).order_by(Clip.clip_index.asc()).all()
    
    if not clips:
        logger.warning("No clips to extract")
        return
        
    job_output_dir = os.path.join(config.OUTPUT_DIR, job.id)
    
    with open(job.transcript_path, 'r', encoding='utf-8') as f:
        full_transcript = json.load(f)
    
    for clip in clips:
        clip_dir = os.path.join(job_output_dir, f"clip_{clip.clip_index}")
        os.makedirs(clip_dir, exist_ok=True)
        
        intermediate_clip_dir = get_intermediate_dir(job.id, clip.clip_index)
        raw_clip_path = os.path.join(intermediate_clip_dir, "raw.mp4")
        
        try:
            logger.info(f"Extracting clip {clip.clip_index} to {raw_clip_path}")
            # Short clips dominate short-form output; accurate seek avoids the
            # duration-mismatch retry loop with negligible speed cost.
            if clip.duration is not None and clip.duration <= 30.0:
                ffmpeg_client.extract_clip_accurate(
                    job.source_video_path,
                    raw_clip_path,
                    clip.start_time,
                    clip.end_time,
                )
            else:
                ffmpeg_client.extract_clip(
                    job.source_video_path,
                    raw_clip_path,
                    clip.start_time,
                    clip.duration,
                )
                info = ffmpeg_client.get_video_info(raw_clip_path)
                if abs(info['duration'] - clip.duration) > 1.0:
                    logger.warning(f"Clip {clip.clip_index} duration mismatch. Retrying with accurate extraction.")
                    ffmpeg_client.extract_clip_accurate(
                        job.source_video_path,
                        raw_clip_path,
                        clip.start_time,
                        clip.end_time,
                    )
                
            sliced = whisper_client.slice_transcript(full_transcript, clip.start_time, clip.end_time)
            transcript_path = os.path.join(clip_dir, "transcript.json")
            with open(transcript_path, 'w', encoding='utf-8') as f:
                json.dump(sliced, f, ensure_ascii=False, indent=2)
                
            clip.raw_clip_path = raw_clip_path
            clip.status = "processing"
            db.commit()
            
        except Exception as e:
            logger.error(f"Failed to extract clip {clip.clip_index}: {e}")
            clip.status = "failed"
            clip.error_message = str(e)
            db.commit()
            continue
