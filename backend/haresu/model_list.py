"""The models each provider offers with a key set, to choose them from a list."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import credentials

TTL = 300  # seconds
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
# Models that are not useful to chat with agents.
_NOT_CHAT = ("embedding", "embed-", "whisper", "tts", "dall-e", "moderation", "rerank", "transcribe", "realtime", "-image", "guard")


def _fingerprint(provider: str) -> str:
    key = credentials.key_for(provider) or ""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _fetch_openai_compat(provider: str, key: str) -> list[dict]:
    url = f"{credentials.base_url(provider).rstrip('/')}/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "haresu/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read(5_000_000).decode("utf-8", "replace"))
    ids = []
    for m in data.get("data") or data.get("models") or []:
        mid = str(m.get("id") or m.get("name") or "").removeprefix("models/")
        if mid and not any(bad in mid.lower() for bad in _NOT_CHAT):
            ids.append(mid)
    return [{"id": i, "name": i, "value": f"{provider}:{i}"} for i in sorted(set(ids))]


def _fetch_anthropic(key: str) -> list[dict]:
    import anthropic

    out = []
    for m in anthropic.Anthropic(api_key=key).models.list(limit=100):
        out.append({"id": m.id, "name": getattr(m, "display_name", None) or m.id, "value": f"anthropic:{m.id}"})
    return out


def _error(provider: str, exc: Exception) -> str:
    name = credentials.PROVIDERS[provider]["name"]
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f"{name} rejected the key."
        if exc.code == 429:
            return f"{name} reported a rate limit."
        return f"{name} responded {exc.code} when asked for the list."
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return f"I could not connect to {name}."
    if isinstance(exc, ValueError):
        return f"{name} returned a list I could not read."
    try:
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            return f"{name} rejected the key."
        if isinstance(exc, anthropic.RateLimitError):
            return f"{name} reported a rate limit."
        if isinstance(exc, anthropic.APIConnectionError):
            return f"I could not connect to {name}."
        if isinstance(exc, anthropic.APIStatusError):
            return f"{name} responded {exc.status_code} when asked for the list."
    except ImportError:  # pragma: no cover
        pass
    return f"I could not ask {name} for the list."


def for_provider(provider: str, refresh: bool = False) -> dict:
    """{"id", "name", "source", "models": [...], "error": str | None} of a provider that has a key."""
    cfg = credentials.PROVIDERS[provider]
    key = credentials.key_for(provider)
    base = {"id": provider, "name": cfg["name"], "source": credentials.source(provider)}
    if not key:
        return {**base, "models": [], "error": None}
    ck = (provider, _fingerprint(provider))
    hit = _cache.get(ck)
    if hit and not refresh and time.time() - hit[0] < TTL:
        return {**base, **hit[1]}
    try:
        if provider == "anthropic":
            models = _fetch_anthropic(key)
        elif provider == "opencode":
            from . import opencode

            models = opencode.listing(refresh)
        else:
            models = _fetch_openai_compat(provider, key)
        result = {"models": models, "error": None}
        _cache[ck] = (time.time(), result)
    except Exception as exc:  # each provider fails on its own and the failure is explained
        result = {"models": [], "error": _error(provider, exc)}
    return {**base, **result}


def available(refresh: bool = False) -> list[dict]:
    """The providers with a key, with their models. They are requested in parallel."""
    ready = [p for p in credentials.PROVIDERS if credentials.has_credentials(p)]
    if not ready:
        return []
    with ThreadPoolExecutor(max_workers=len(ready)) as pool:
        return list(pool.map(lambda p: for_provider(p, refresh), ready))
