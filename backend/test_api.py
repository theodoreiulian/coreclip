import sys
import json
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

print("Health Check:")
resp = client.get("/api/health")
print(resp.status_code, resp.json())

print("\nPOST Job:")
resp = client.post("/api/jobs", json={"youtube_url": "https://youtube.com/watch?v=dQw4w9WgXcQ"})
print(resp.status_code, resp.json())
job_id = resp.json()["id"]

print("\nPOST Job (duplicate):")
resp = client.post("/api/jobs", json={"youtube_url": "https://youtube.com/watch?v=dQw4w9WgXcQ"})
print(resp.status_code, resp.json())

print(f"\nGET Job {job_id}:")
resp = client.get(f"/api/jobs/{job_id}")
print(resp.status_code, resp.json())

print("\nGET Jobs:")
resp = client.get("/api/jobs")
print(resp.status_code, resp.json())
