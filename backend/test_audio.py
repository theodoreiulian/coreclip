from database import SessionLocal, init_db
from models.job import Job
from pipeline.audio_analysis import compute_features
import os

db = SessionLocal()
job = Job(id="test-job", source_video_path="test.mp4")

os.makedirs(f"../output/{job.id}", exist_ok=True)

try:
    summary = compute_features(job, db)
    print("SUMMARY:\n", summary)
    
    features_path = f"../output/{job.id}/audio_features.json"
    if os.path.exists(features_path):
        print(f"Success! {features_path} exists.")
    else:
        print("Failed to find audio_features.json")
except Exception as e:
    print(f"Error: {e}")
finally:
    db.close()
