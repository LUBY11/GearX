"""
Event hub for real-time updates between bot and web.
Uses asyncio Queue to broadcast events to multiple SSE clients.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Set
import json

log = logging.getLogger(__name__)


@dataclass
class EventHub:
    """Simple pub/sub hub for broadcasting events to SSE clients."""
    _subscribers: Set[asyncio.Queue] = field(default_factory=set)
    
    def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        log.info("SSE client subscribed. Total subscribers: %d", len(self._subscribers))
        return queue
    
    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        self._subscribers.discard(queue)
        log.info("SSE client unsubscribed. Total subscribers: %d", len(self._subscribers))
    
    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast an event to all subscribers."""
        message = json.dumps({"type": event_type, "data": data}, default=str)
        log.info("Publishing to %d subscribers: %s", len(self._subscribers), event_type)
        for queue in self._subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                log.warning("Queue full, skipping message")


# Global event hub instance
event_hub = EventHub()
