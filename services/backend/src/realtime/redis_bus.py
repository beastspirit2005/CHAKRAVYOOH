import os
import json
import logging
import asyncio
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL")
_redis_client = None

try:
    if REDIS_URL:
        import redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        logger.info("Connected to Redis Event Bus.")
except Exception as e:
    logger.warning(f"Redis connection unavailable ({e}). Using async in-memory fallback queue.")
    _redis_client = None

# In-memory queue fallback for event-driven processing if Redis URL is absent
_in_memory_subscribers: Dict[str, List[Callable]] = {}

def publish_event(stream_name: str, event_data: dict):
    """
    Publishes an event to Redis Stream or in-memory fallback bus.
    """
    event_payload = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in event_data.items()}
    
    if _redis_client:
        try:
            _redis_client.xadd(stream_name, event_payload)
            logger.info(f"Published event to Redis stream {stream_name}")
            return
        except Exception as e:
            logger.error(f"Failed publishing to Redis: {e}")

    # Fallback in-memory event dispatching
    if stream_name in _in_memory_subscribers:
        for handler in _in_memory_subscribers[stream_name]:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(event_data))
                else:
                    handler(event_data)
            except Exception as err:
                logger.error(f"Error handling event {stream_name}: {err}")

def subscribe_event(stream_name: str, handler: Callable):
    """
    Subscribes a handler to a specific stream topic.
    """
    if stream_name not in _in_memory_subscribers:
        _in_memory_subscribers[stream_name] = []
    _in_memory_subscribers[stream_name].append(handler)
