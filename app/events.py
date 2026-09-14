"""Event-Bus: verteilt Job-Fortschritt und Zustandsaenderungen per SSE an die Web-UI."""
from __future__ import annotations

import asyncio
import json
from typing import Any

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Wird beim App-Start gesetzt, damit Worker-Threads publizieren koennen."""
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _subscribers.discard(queue)


def _deliver(payload: str) -> None:
    for queue in list(_subscribers):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Langsamer Client: aeltestes Event verwerfen statt zu blockieren.
            try:
                queue.get_nowait()
                queue.put_nowait(payload)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


def publish(event_type: str, data: dict[str, Any]) -> None:
    """Threadsicher: darf aus Worker-Threads heraus aufgerufen werden."""
    payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False, default=str)
    if _loop is None:
        _deliver(payload)
        return
    try:
        _loop.call_soon_threadsafe(_deliver, payload)
    except RuntimeError:
        pass
