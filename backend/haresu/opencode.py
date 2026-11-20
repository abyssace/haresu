"""OpenCode: one key, two plans and each model with its own call format.

OpenCode publishes the list of its models (without a key) and for each one says which API it uses (`chat-completions`,
`messages` or `responses`) and its address. There are two plans with the same key:

  go       OpenCode Go (subscription)        <root>/go/v1/models
  console  OpenCode Console (pay as you go)  <root>/v1/models

A model is written `opencode:go/<id>` or `opencode:console/<id>`. Without a plan (`opencode:<id>`) it is looked up first in
Go and, if it is not there, in Console.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request

from . import credentials, providers
from .providers import ProviderError, Reply

PLANS = {"go": "OpenCode Go (subscription)", "console": "OpenCode Console (pay as you go)"}
SUPPORTED = ("chat-completions", "messages", "responses")  # the formats haresu knows how to speak
TTL = 300
_catalog: dict[str, tuple[float, list[dict]]] = {}


def root() -> str:
    """API root. It can be changed with HARESU_OPENCODE_BASE_URL."""
    return (os.environ.get("HARESU_OPENCODE_BASE_URL") or "https://opencode.ai/inference").rstrip("/")


def plan_root(plan: str) -> str:
    return root() + ("/go" if plan == "go" else "")


def split(model_id: str) -> tuple[str | None, str]:
    """'go/kimi-k3' -> ('go', 'kimi-k3'); 'kimi-k3' -> (None, 'kimi-k3')."""
    head, sep, rest = model_id.partition("/")
    return (head, rest) if sep and head in PLANS else (None, model_id)


def pick_api(entry: dict) -> tuple[str, str] | None:
    """(format, address) used to call this model, or None if none is supported."""
    apis = entry.get("api") or {}
    order = [entry.get("recommended_api")] + list(SUPPORTED)
    for name in order:
        if name in SUPPORTED and isinstance(apis.get(name), dict) and apis[name].get("base_url"):
            return name, apis[name]["base_url"]
    return None


def usable(entry: dict) -> bool:
    """Chat models with tools, in a supported format: the ones that are useful for agents."""
    tools = ((entry.get("capabilities") or {}).get("tools") or {}).get("supported")
    return entry.get("type") == "chat" and bool(tools) and pick_api(entry) is not None


def catalog(plan: str, refresh: bool = False) -> list[dict]:
    """The model list of a plan, kept for 5 minutes."""
    hit = _catalog.get(plan)
    if hit and not refresh and time.time() - hit[0] < TTL:
        return hit[1]
    req = urllib.request.Request(f"{plan_root(plan)}/v1/models", headers={"User-Agent": "haresu/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        entries = json.loads(resp.read(10_000_000).decode("utf-8", "replace")).get("data") or []
    _catalog[plan] = (time.time(), entries)
    return entries


def listing(refresh: bool = False) -> list[dict]:
    """The models for the selector: from both plans, in separate groups."""
    out, errors = [], []
    for plan, label in PLANS.items():
        try:
            entries = catalog(plan, refresh)
        except Exception as exc:  # one plan being down must not hide the other
            errors.append(exc)
            continue
        for e in entries:
            if usable(e):
                out.append({"id": e["id"], "name": e.get("name") or e["id"], "value": f"opencode:{plan}/{e['id']}", "group": label})
    if not out and errors:
        raise errors[0]
    return sorted(out, key=lambda m: (m["group"], m["name"].lower()))


def entry_for(plan: str | None, model_id: str) -> tuple[str, dict] | None:
    """(plan, entry) of a model. Without a plan: Go first, then Console."""
    for p in ([plan] if plan else ["go", "console"]):
        try:
            for e in catalog(p):
                if e.get("id") == model_id:
                    return p, e
        except Exception:  # noqa: BLE001 - if the list does not respond, the next one is tried
            continue
    return None


def _session(system: str, messages: list[dict]) -> str:
    """OpenCode Go asks for a stable identifier per conversation: it is taken from the start of the conversation."""
    first = messages[0]["content"] if messages else ""
    if not isinstance(first, str):
        first = "".join(providers._block(b, "text", "") for b in first)
    return hashlib.sha256(f"{system}\n{first}".encode()).hexdigest()[:24]


class OpenCodeProvider:
    def chat(self, *, model: str, system: str, messages: list[dict], tools: list[dict], effort: str = "medium") -> Reply:
        name = "OpenCode"
        key = providers.key_for_model("opencode", model)
        if not key:
            raise ProviderError("There is no OpenCode key. Add it in Settings.")
        plan, model_id = split(providers.resolve(model)[1])
        found = entry_for(plan, model_id)
        if not found:
            where = f" in the {PLANS[plan]} plan" if plan else ""
            raise ProviderError(f"OpenCode does not offer the model “{model_id}”{where}. Choose it from the list in Settings or on the node.")
        plan, entry = found
        choice = pick_api(entry)
        if not choice:
            raise ProviderError(f"The model “{model_id}” is only offered in formats haresu does not speak yet.")
        api, base = choice
        headers = {"User-Agent": "haresu/0.1"}
        if plan == "go":
            headers["x-opencode-session"] = _session(system, messages)
        if api == "chat-completions":
            return providers.chat_completions(base, key, model_id, system, messages, tools, name, headers)
        if api == "responses":
            return providers.responses_api(base, key, model_id, system, messages, tools, name, headers)
        import anthropic

        sdk_base = base[:-3] if base.rstrip("/").endswith("/v1") else base  # the SDK adds /v1/messages
        client = anthropic.Anthropic(api_key=key, base_url=sdk_base, default_headers=headers, max_retries=0, timeout=300)
        return providers._anthropic_chat(client, model_id=model_id, system=system, messages=messages, tools=tools,
                                         effort=effort, name=name, native=False)


def check(key: str) -> str:
    """Tests an OpenCode key. The public model list does not validate it, so a minimal call is made
    with a free model."""
    free = None
    for plan in ("console", "go"):
        try:
            for e in catalog(plan):
                if e.get("id", "").endswith("-free") and usable(e) and pick_api(e)[0] == "chat-completions":
                    free = (plan, e)
                    break
        except Exception:  # noqa: BLE001
            continue
        if free:
            break
    if not free:
        return "I did not find a free model to test the key without spending balance. Test it by running an agent."
    plan, entry = free
    try:
        providers.chat_completions(pick_api(entry)[1], key, entry["id"], "", [{"role": "user", "content": "Reply with just the word: ready"}],
                                   [], "OpenCode", {"x-opencode-session": "haresu-test"} if plan == "go" else None)
    except ProviderError as exc:
        return str(exc)
    return f"It works: OpenCode accepted the key (tested with the free model “{entry['id']}”)."
