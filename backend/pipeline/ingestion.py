import os
import yt_dlp
import json
import logging
import shutil
from sqlalchemy.orm import Session
from models.job import Job
from services import whisper_client
from config import config
from utils.platform_compat import link_or_copy

logger = logging.getLogger("clipper")

class IngestionError(Exception):
    pass

def download_video(job: Job, db: Session):
    logger.info(f"Starting download for job {job.id} (URL: {job.youtube_url})")
    
    cache_dir = os.path.join(config.OUTPUT_DIR, '.cache')
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{job.youtube_video_id}.mp4")
    
    job_output_dir = os.path.join(config.OUTPUT_DIR, job.id)
    os.makedirs(job_output_dir, exist_ok=True)
    job.source_video_path = os.path.join(job_output_dir, 'source.mp4')
    
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        logger.info(f"Found cached video for {job.youtube_video_id}")
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(job.youtube_url, download=False)
            job.video_title = info.get('title')
            job.video_duration_seconds = float(info.get('duration', 0))
        
        link_or_copy(cache_path, job.source_video_path)
        db.commit()
        return

    out_tmpl = os.path.join(cache_dir, f"{job.youtube_video_id}.%(ext)s")
    
    ydl_opts = {
        'format': f'bestvideo[height<={config.YTDLP_MAX_HEIGHT}][ext=mp4]+bestaudio[ext=m4a][abr<={config.YTDLP_MAX_AUDIO_BITRATE}]/best[height<={config.YTDLP_MAX_HEIGHT}][ext=mp4]/best',
        'outtmpl': out_tmpl,
        'merge_output_format': 'mp4',
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 3,
        'remote_components': ['ejs:github'],
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(job.youtube_url, download=True)
            
            job.video_title = info.get('title')
            job.video_duration_seconds = float(info.get('duration', 0))
            expected_filename = ydl.prepare_filename(info)
            if expected_filename.endswith('.webm') or expected_filename.endswith('.mkv'):
                actual_downloaded_path = os.path.splitext(expected_filename)[0] + '.mp4'
            else:
                actual_downloaded_path = expected_filename
            
            if not os.path.exists(actual_downloaded_path) or os.path.getsize(actual_downloaded_path) == 0:
                raise IngestionError(f"Downloaded video file not found or is empty: {actual_downloaded_path}")
            
            if actual_downloaded_path != cache_path:
                # os.replace, not os.rename: Windows raises FileExistsError when
                # the destination is already there (a stale cache entry).
                os.replace(actual_downloaded_path, cache_path)

            strategy = link_or_copy(cache_path, job.source_video_path)

            db.commit()
            logger.info(
                f"Downloaded video: {job.video_title} ({job.video_duration_seconds}s) "
                f"to {cache_path} and linked ({strategy}) to {job.source_video_path}"
            )
            
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download failed: {e}")
        raise IngestionError(f"Download failed: {e}")

def transcribe(job: Job, db: Session):
    logger.info(f"Transcribing audio for job {job.id}")
    if not job.source_video_path or not os.path.exists(job.source_video_path):
        raise IngestionError("Source video not found for transcription")
        
    try:
        transcript_data = whisper_client.transcribe(job.source_video_path)
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise IngestionError(f"Transcription failed: {e}")
        
    job_output_dir = os.path.join(config.OUTPUT_DIR, job.id)
    transcript_path = os.path.join(job_output_dir, 'transcript.json')
    
    with open(transcript_path, 'w', encoding='utf-8') as f:
        json.dump(transcript_data, f, ensure_ascii=False, indent=2)
        
    job.transcript_path = transcript_path
    db.commit()
    logger.info(f"Transcription saved to {transcript_path}")
