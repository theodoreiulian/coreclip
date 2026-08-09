import asyncio
import threading
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.redis_pubsub import subscribe

logger = logging.getLogger("clipper")
router = APIRouter()


@router.websocket("/jobs/{job_id}")
async def job_status_websocket(websocket: WebSocket, job_id: str):
    await websocket.accept()

    loop = asyncio.get_running_loop()
    stop_event = threading.Event()

    def listen_to_redis():
        """Stream Redis pub/sub messages to the socket. If Redis is unavailable
        we keep the socket open and retry subscribing — closing the socket here
        is what made the client thrash in a connect/disconnect loop."""
        pubsub = None
        try:
            while not stop_event.is_set():
                if pubsub is None:
                    try:
                        pubsub = subscribe(job_id)
                    except Exception as e:
                        logger.debug(f"WS {job_id}: Redis subscribe failed, retrying: {e}")
                        pubsub = None
                    if pubsub is None:
                        # Redis down/not configured — wait and retry, don't drop the socket.
                        stop_event.wait(2.0)
                        continue

                try:
                    message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                except Exception as e:
                    # Connection dropped mid-stream — reset and resubscribe.
                    logger.debug(f"WS {job_id}: Redis read failed, resubscribing: {e}")
                    try:
                        pubsub.close()
                    except Exception:
                        pass
                    pubsub = None
                    stop_event.wait(2.0)
                    continue

                if message and message.get('type') == 'message':
                    asyncio.run_coroutine_threadsafe(
                        websocket.send_text(message['data']),
                        loop,
                    )
        finally:
            if pubsub is not None:
                try:
                    pubsub.unsubscribe()
                    pubsub.close()
                except Exception:
                    pass

    listener = loop.run_in_executor(None, listen_to_redis)

    try:
        # Hold the connection open until the client disconnects.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        try:
            await listener
        except Exception:
            pass
