import redis
import json
import logging
from config import config

logger = logging.getLogger("clipper")

try:
    redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
except Exception as e:
    logger.warning(f"Could not connect to Redis: {e}")
    redis_client = None

def publish_status(job_id: str, data: dict):
    if redis_client:
        channel = f"clipper:status:{job_id}"
        try:
            redis_client.publish(channel, json.dumps(data))
        except Exception as e:
            logger.error(f"Failed to publish status to {channel}: {e}")

def publish_status_batch(job_id: str, messages: list[dict]):
    if not redis_client or not messages:
        return
    channel = f"clipper:status:{job_id}"
    try:
        pipe = redis_client.pipeline()
        for msg in messages:
            pipe.publish(channel, json.dumps(msg))
        pipe.execute()
    except Exception as e:
        logger.error(f"Failed to publish status batch to {channel}: {e}")

def subscribe(job_id: str):
    if redis_client:
        pubsub = redis_client.pubsub()
        pubsub.subscribe(f"clipper:status:{job_id}")
        return pubsub
    return None
