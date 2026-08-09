import sys
import json
import time
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

print("\nPOST Job:")
resp = client.post("/api/jobs", json={"youtube_url": "https://youtube.com/watch?v=dQw4w9WgXcQ"})
print(resp.status_code, resp.json())
job_id = resp.json()["id"]

print(f"\nChecking status of {job_id}...")
for _ in range(5):
    time.sleep(1)
    resp = client.get(f"/api/jobs/{job_id}")
    status = resp.json()["status"]
    print(f"Status: {status}")
    if status == "completed":
        break
