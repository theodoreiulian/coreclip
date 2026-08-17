import redis
import json
import logging
import time
from config import config

logger = logging.getLogger("clipper")

try:
    redis_client = redis.from_url(
        config.REDIS_URL,
        decode_responses=True,
        # Without these, an unreachable Redis makes every publish hang on the
        # OS-level TCP timeout (~75s). Status updates are best-effort telemetry
        # — the pipeline runs fine without them — so failing fast matters far
        # more than delivering them.
        socket_connect_timeout=5,
        socket_timeout=5,
        # Long-lived subscribers sit idle between jobs; without keepalives a NAT
        # or proxy can silently drop the connection and reads then hang until
        # socket_timeout on every message.
        socket_keepalive=True,
        health_check_interval=30,
        # No retry_on_timeout: it doubles every stall to 10s, and it buys
        # nothing here. Publishes are fire-and-forget, and the WebSocket route
        # (routes_ws.listen_to_redis) already resubscribes on its own when a read
        # fails, so redis-py retrying underneath it is redundant.
    )
except Exception as e:
    logger.warning(f"Could not connect to Redis: {e}")
    redis_client = None


# ── Circuit breaker ──────────────────────────────────────────────────────
#
# Timeouts bound a single failure; they don't stop the pipeline paying that
# bound over and over. With Redis down, every stage transition and every batch
# of clip events would each block for the full 5s connect timeout. Since these
# messages only drive live UI updates, it is strictly better to notice Redis is
# gone and stop asking for a while.
_BREAKER_THRESHOLD = 3      # consecutive failures before opening the circuit
_BREAKER_COOLDOWN_S = 30.0  # how long to skip publishing once open

_consecutive_failures = 0
_circuit_open_until = 0.0


def _circuit_is_open() -> bool:
    """True while we are deliberately skipping publishes."""
    if _circuit_open_until and time.monotonic() < _circuit_open_until:
        return True
    return False


def _record_success() -> None:
    global _consecutive_failures, _circuit_open_until
    if _consecutive_failures or _circuit_open_until:
        logger.info("Redis publishing recovered")
    _consecutive_failures = 0
    _circuit_open_until = 0.0


def _record_failure(channel: str, error: Exception) -> None:
    global _consecutive_failures, _circuit_open_until
    _consecutive_failures += 1
    if _consecutive_failures >= _BREAKER_THRESHOLD:
        _circuit_open_until = time.monotonic() + _BREAKER_COOLDOWN_S
        # Log the trip once at WARNING, not once per skipped publish.
        logger.warning(
            f"Redis unreachable after {_consecutive_failures} attempts; "
            f"pausing status publishing for {_BREAKER_COOLDOWN_S:.0f}s "
            f"(pipeline continues, live UI updates will be missing): {error}"
        )
    else:
        logger.error(f"Failed to publish status to {channel}: {error}")


def _publishing_disabled() -> bool:
    return redis_client is None or _circuit_is_open()


def publish_status(job_id: str, data: dict):
    if _publishing_disabled():
        return
    channel = f"clipper:status:{job_id}"
    try:
        redis_client.publish(channel, json.dumps(data))
        _record_success()
    except Exception as e:
        _record_failure(channel, e)


def publish_status_batch(job_id: str, messages: list[dict]):
    if not messages or _publishing_disabled():
        return
    channel = f"clipper:status:{job_id}"
    try:
        pipe = redis_client.pipeline()
        for msg in messages:
            pipe.publish(channel, json.dumps(msg))
        pipe.execute()
        _record_success()
    except Exception as e:
        _record_failure(channel, e)


def subscribe(job_id: str):
    # Deliberately not gated by the circuit breaker: the breaker exists to keep
    # the pipeline from stalling on writes, whereas a subscriber is a client
    # actively waiting for updates and routes_ws already paces its own retries.
    if redis_client:
        pubsub = redis_client.pubsub()
        pubsub.subscribe(f"clipper:status:{job_id}")
        return pubsub
    return None
