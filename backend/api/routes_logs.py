from fastapi import APIRouter, Query, HTTPException
import os
import json
from typing import List, Optional
from config import config

router = APIRouter()

def read_log_file(filepath: str, tail: int = 100, level: str = None, stage: str = None):
    if not os.path.exists(filepath):
        return []
    
    lines = []
    with open(filepath, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
        
    for line in all_lines:
        try:
            data = json.loads(line.strip())
            if level and data.get("level") != level:
                continue
            if stage and data.get("stage") != stage:
                continue
            lines.append(data)
        except:
            continue
            
    return lines[-tail:] if tail > 0 else lines

@router.get("/jobs/{job_id}")
def get_job_logs(
    job_id: str,
    tail: int = Query(100, ge=1, le=1000),
    level: Optional[str] = None,
    stage: Optional[str] = None
):
    filepath = os.path.join(config.LOG_DIR, "jobs", f"{job_id}.jsonl")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Log file not found")
        
    logs = read_log_file(filepath, tail=tail, level=level, stage=stage)
    return {"logs": logs}

@router.get("/errors")
def get_error_logs(tail: int = Query(100, ge=1, le=1000)):
    filepath = os.path.join(config.LOG_DIR, "errors.jsonl")
    return {"logs": read_log_file(filepath, tail=tail)}

@router.get("/api")
def get_api_logs(tail: int = Query(100, ge=1, le=1000)):
    filepath = os.path.join(config.LOG_DIR, "api.jsonl")
    return {"logs": read_log_file(filepath, tail=tail)}
