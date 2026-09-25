"""Sync access to the LangGraph Platform Store for app state shared across replicas
(e.g. account groups). Callers run in worker threads;
from async code wrap calls in asyncio.to_thread — the Store is served by this same
server, so a blocking call on the event loop would deadlock it.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict

import httpx

_client = None
_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


def _store():
    global _client
    if _client is None:
        from langgraph_sdk import get_sync_client
        _client = get_sync_client(url=os.environ.get("LANGGRAPH_API_URL") or "http://localhost:8000")
    return _client.store


def lock(namespace: tuple[str, ...], key: str) -> threading.Lock:
    """Per-item lock for read-modify-write within this process (other replicas can still race)."""
    return _locks[f"{'/'.join(namespace)}:{key}"]


def get(namespace: tuple[str, ...], key: str) -> dict | None:
    try:
        item = _store().get_item(namespace, key)
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        return None
    return (item or {}).get("value")


def put(namespace: tuple[str, ...], key: str, value: dict) -> None:
    _store().put_item(namespace, key, value, index=False)


def delete(namespace: tuple[str, ...], key: str) -> None:
    try:
        _store().delete_item(namespace, key)
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise


def search(namespace: tuple[str, ...], limit: int = 1000) -> list[dict]:
    return [i["value"] for i in _store().search_items(namespace, limit=limit).get("items", []) if isinstance(i.get("value"), dict)]
