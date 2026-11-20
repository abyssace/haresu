"""Model providers. 'mock' runs without a key; everything else uses each provider's API."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from . import credentials
from . import spec as S


@dataclass
class Reply:
    text: str
    tool_calls: list[dict] = field(default_factory=list)  # [{id, name, input}]
    content: object = None  # what is sent back again as the assistant's turn
    tokens_in: int = 0
    tokens_out: int = 0
    stop_reason: str = "end_turn"
    model: str = ""


class ProviderError(Exception):
    pass


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif isinstance(b, dict) and b.get("type") == "tool_result":
            parts.append(str(b.get("content", "")))
    return "\n".join(parts)


_ARITH = re.compile(r"\d+(?:[.,]\d+)?(?:\s*(?:\*\*|[-+*/x×%])\s*\d+(?:[.,]\d+)?)+")


class MockProvider:
    """Mock model, deterministic. It is used to test the whole cycle without a key.

    It behaves like a simple agent that uses every tool it has connected:
      search_context  searches the question in the shared context
      calculator      if the question contains an arithmetic operation
      read_memory     reads the note a previous agent left on the scratchpad
      write_memory    leaves its answer on the scratchpad for the next agent
    Without tools it answers with the prompt paragraph that most resembles the question.
    """

    MEMORY_KEY = "last_answer"

    def chat(self, *, model: str, system: str, messages: list[dict], tools: list[dict], effort: str = "medium") -> Reply:
        question = _text_of(messages[0]["content"])
        names = {t["name"] for t in tools}
        size_in = (len(system) + sum(len(_text_of(m["content"])) for m in messages)) // 4

        # Tool results by name, matching each tool_result with its tool_use.
        called: dict[str, str] = {}
        ids: dict[str, str] = {}
        for m in messages:
            if not isinstance(m["content"], list):
                continue
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    ids[b["id"]] = b["name"]
                elif isinstance(b, dict) and b.get("type") == "tool_result":
                    called[ids.get(b.get("tool_use_id"), "?")] = str(b.get("content", ""))

        def use(calls: list[dict]) -> Reply:
            for c in calls:
                c["id"] = f"mock_{uuid.uuid4().hex[:8]}"
            return Reply("", calls, [{"type": "tool_use", **c} for c in calls], size_in, 12 * len(calls), "tool_use", "mock")

        # First turn: gather information with what is available.
        if not called:
            calls = []
            if "search_context" in names:
                calls.append({"name": "search_context", "input": {"query": question}})
            expr = _ARITH.search(question)
            if "calculator" in names and expr:
                clean = expr.group(0).replace("x", "*").replace("×", "*").replace(",", ".")
                calls.append({"name": "calculator", "input": {"expression": clean}})
            if "read_memory" in names:
                calls.append({"name": "read_memory", "input": {"key": self.MEMORY_KEY}})
            if calls:
                return use(calls)

        parts = []
        found = called.get("search_context", "")
        first = found.splitlines()[0] if found else ""
        if first and not first.startswith(("No results", "The tool")):
            parts.append(re.sub(r"^\[[^\]]*\]\s*[^:]*:\s*", "", first))
        elif "## Shared context" in system:
            q = {S.stem(t) for t in S.tokens(question)}
            best, best_score = "", 0
            for para in re.split(r"\n\s*\n", system.split("## Shared context", 1)[1]):
                score = len(q & {S.stem(t) for t in S.tokens(para)})
                if score > best_score:
                    best, best_score = para.strip(), score
            if best:
                parts.append(best)
        calc = called.get("calculator", "")
        if calc and not calc.startswith(("Error", "The tool")):
            parts.append(f"The result is {calc}.")
        note = called.get("read_memory", "")
        if not parts and note and not note.startswith(("Nothing is stored", "Claves:", "The tool")):
            parts.append(f"According to the shared scratchpad: {note}")
        answer = " ".join(parts) or "I have no information about that."

        # Last turn: leave the answer on the scratchpad for the next agent.
        if "write_memory" in names and "write_memory" not in called:
            return use([{"name": "write_memory", "input": {"key": self.MEMORY_KEY, "value": answer}}])

        email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", question)
        if email:
            answer = f"Thanks, we will write to {email.group(0)}. {answer}"
        return Reply(answer, [], [{"type": "text", "text": answer}], size_in, len(answer) // 4, "end_turn", "mock")


def key_for_model(provider: str, model: str) -> str | None:
    """The key for this model: its own if you registered it with one, and otherwise the provider's."""
    from . import registry  # imported here to avoid creating a cycle

    return registry.key_for(model) or credentials.key_for(provider)


def _friendly(detail: str | None, name: str) -> str | None:
    """Clear messages for known failures that providers report as text."""
    low = (detail or "").lower()
    if "can't call models" in low or "cannot call models" in low:
        return (f"{name}: the key exists but it has no inference permission. Check that the subscription or the balance are "
                "active in the same workspace and create a new key.")
    if "insufficient" in low or "credit balance" in low or "quota" in low:
        return f"{name} has no balance or the account limit was reached."
    return None


def _anthropic_chat(client, *, model_id: str, system: str, messages: list[dict], tools: list[dict], effort: str,
                    name: str, native: bool) -> Reply:
    """A call to the Messages API. `native` means Anthropic's own; otherwise it is a compatible service."""
    import anthropic

    kwargs: dict = {"model": model_id, "max_tokens": 16000, "system": system, "messages": messages}
    if native:
        kwargs["output_config"] = {"effort": effort or "medium"}
    if tools:
        kwargs["tools"] = tools if native else [{k: v for k, v in t.items() if k != "strict"} for t in tools]
    try:
        resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise ProviderError(f"{name} rejected the key or there is none. Add it in Settings or use the 'mock' model.") from exc
    except anthropic.NotFoundError as exc:
        raise ProviderError(f"The model '{model_id}' does not exist on {name}.") from exc
    except anthropic.RateLimitError as exc:
        raise ProviderError("Rate limit reached; try again later.") from exc
    except anthropic.APIStatusError as exc:
        raise ProviderError(_friendly(str(exc.message), name) or f"API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ProviderError(f"Could not connect to {name}.") from exc
    except TypeError as exc:
        # The SDK raises TypeError when it finds no credentials at all.
        raise ProviderError(f"There are no credentials for {name}: {exc}") from exc

    if resp.stop_reason == "refusal":
        return Reply("[The model refused the request]", [], resp.content, resp.usage.input_tokens, resp.usage.output_tokens, "refusal", resp.model)
    text = "\n".join(b.text for b in resp.content if b.type == "text")
    calls = [{"id": b.id, "name": b.name, "input": dict(b.input)} for b in resp.content if b.type == "tool_use"]
    return Reply(text, calls, resp.content, resp.usage.input_tokens, resp.usage.output_tokens, resp.stop_reason or "end_turn", resp.model)


class AnthropicProvider:
    """Claude through the Messages API. The engine runs the tool loop."""

    def __init__(self):
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("The 'anthropic' package is missing.") from exc

    def chat(self, *, model: str, system: str, messages: list[dict], tools: list[dict], effort: str = "medium") -> Reply:
        return _anthropic_chat(credentials.client(key_for_model("anthropic", model)), model_id=resolve(model)[1], system=system, messages=messages,
                               tools=tools, effort=effort, name="Anthropic", native=True)


def _block(b, key, default=None):
    """A content block can be a dict or an SDK object."""
    return b.get(key, default) if isinstance(b, dict) else getattr(b, key, default)


def to_openai(system: str, messages: list[dict]) -> list[dict]:
    """Converts the history (Anthropic block format) to the OpenAI chat format."""
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "".join(_block(b, "text", "") for b in content if _block(b, "type") == "text")
            calls = [{"id": _block(b, "id"), "type": "function",
                      "function": {"name": _block(b, "name"), "arguments": json.dumps(dict(_block(b, "input", {})), ensure_ascii=False)}}
                     for b in content if _block(b, "type") == "tool_use"]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in content:
                if _block(b, "type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": _block(b, "tool_use_id"), "content": str(_block(b, "content", ""))})
            text = "".join(_block(b, "text", "") for b in content if _block(b, "type") == "text")
            if text:
                out.append({"role": "user", "content": text})
    return out


def to_responses(messages: list[dict]) -> list[dict]:
    """Converts the history to the input format of the Responses API.

    If an assistant turn saved the original elements of the response (`raw_responses`, which include
    the reasoning), they are sent back as they are; that way models that reason between tools do not lose the thread."""
    items: list[dict] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            items.append({"role": m["role"], "content": content})
        elif m["role"] == "assistant":
            raw = [b for b in content if _block(b, "type") == "raw_responses"]
            if raw:
                items.extend(_block(raw[0], "items", []))
                continue
            text = "".join(_block(b, "text", "") for b in content if _block(b, "type") == "text")
            if text:
                items.append({"role": "assistant", "content": [{"type": "output_text", "text": text}]})
            for b in content:
                if _block(b, "type") == "tool_use":
                    items.append({"type": "function_call", "call_id": _block(b, "id"), "name": _block(b, "name"),
                                  "arguments": json.dumps(dict(_block(b, "input", {})), ensure_ascii=False)})
        else:
            for b in content:
                if _block(b, "type") == "tool_result":
                    items.append({"type": "function_call_output", "call_id": _block(b, "tool_use_id"),
                                  "output": str(_block(b, "content", ""))})
            text = "".join(_block(b, "text", "") for b in content if _block(b, "type") == "text")
            if text:
                items.append({"role": "user", "content": text})
    return items


def _post_json(url: str, key: str, body: dict, name: str, model_id: str, headers: dict | None = None) -> dict:
    """POST with the provider's key. Failures become clear messages."""
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "haresu/0.1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode())
            detail = (detail.get("error") or detail)
            detail = detail.get("message") if isinstance(detail, dict) else str(detail)
        except (ValueError, AttributeError):
            detail = None
        friendly = _friendly(detail, name)
        if friendly:
            raise ProviderError(friendly) from exc
        if exc.code in (401, 403):
            raise ProviderError(f"{name} rejected the key. Check it in Settings.") from exc
        if exc.code == 404:
            raise ProviderError(f"The model '{model_id}' does not exist on {name}, or your key has no access to it.") from exc
        if exc.code == 429:
            raise ProviderError(f"{name} reported a rate limit; try again later.") from exc
        raise ProviderError(f"{name} error ({exc.code}): {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ProviderError(f"Could not connect to {name}.") from exc
    except ValueError as exc:
        raise ProviderError(f"{name} returned a response I could not read.") from exc


def _tool_args(raw) -> dict:
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return {"_unparsed_text": raw}
    return args if isinstance(args, dict) else {}


def chat_completions(base: str, key: str, model_id: str, system: str, messages: list[dict], tools: list[dict], name: str,
                     headers: dict | None = None) -> Reply:
    """The OpenAI-compatible chat API (POST {base}/chat/completions)."""
    body: dict = {"model": model_id, "messages": to_openai(system, messages)}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                            "parameters": t["input_schema"]}} for t in tools]
    data = _post_json(f"{base.rstrip('/')}/chat/completions", key, body, name, model_id, headers)
    try:
        choice = data["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"{name} returned a response with no message.") from exc
    text = msg.get("content") or ""
    calls = [{"id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}", "name": c["function"]["name"],
              "input": _tool_args(c["function"].get("arguments"))} for c in msg.get("tool_calls") or []]
    blocks = ([{"type": "text", "text": text}] if text else []) + [{"type": "tool_use", **c} for c in calls]
    finish = choice.get("finish_reason")
    stop = "tool_use" if calls else "max_tokens" if finish == "length" else "refusal" if finish == "content_filter" else "end_turn"
    usage = data.get("usage") or {}
    return Reply(text, calls, blocks, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
                 stop, data.get("model") or model_id)


def responses_api(base: str, key: str, model_id: str, system: str, messages: list[dict], tools: list[dict], name: str,
                  headers: dict | None = None) -> Reply:
    """The OpenAI Responses API (POST {base}/responses)."""
    body: dict = {"model": model_id, "input": to_responses(messages)}
    if system:
        body["instructions"] = system
    if tools:
        body["tools"] = [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                          "parameters": t["input_schema"]} for t in tools]
    data = _post_json(f"{base.rstrip('/')}/responses", key, body, name, model_id, headers)
    output = data.get("output")
    if not isinstance(output, list):
        raise ProviderError(f"{name} returned a response with no content.")
    text, calls = "", []
    for item in output:
        kind = item.get("type")
        if kind == "message":
            text += "".join(p.get("text", "") for p in item.get("content") or [] if p.get("type") == "output_text")
        elif kind == "function_call":
            calls.append({"id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                          "name": item.get("name"), "input": _tool_args(item.get("arguments"))})
    blocks = ([{"type": "text", "text": text}] if text else []) + [{"type": "tool_use", **c} for c in calls]
    blocks.append({"type": "raw_responses", "items": output})
    incomplete = (data.get("incomplete_details") or {}).get("reason")
    stop = "tool_use" if calls else "max_tokens" if incomplete == "max_output_tokens" else "refusal" if incomplete == "content_filter" else "end_turn"
    usage = data.get("usage") or {}
    return Reply(text, calls, blocks, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
                 stop, data.get("model") or model_id)


class OpenAICompatProvider:
    """Any provider with the OpenAI-compatible chat API. It uses that provider's key."""

    def __init__(self, provider: str):
        self.provider = provider

    def chat(self, *, model: str, system: str, messages: list[dict], tools: list[dict], effort: str = "medium") -> Reply:
        name = credentials.PROVIDERS[self.provider]["name"]
        key = key_for_model(self.provider, model)
        if not key:
            raise ProviderError(f"There is no {name} key. Add it in Settings.")
        return chat_completions(credentials.base_url(self.provider), key, resolve(model)[1], system, messages, tools, name)


def resolve(model: str) -> tuple[str, str]:
    """Which provider a model belongs to and its id without a prefix. 'mock', 'provider:model' or a recognizable name."""
    m = (model or "mock").strip()
    if m.startswith("mock"):
        return "mock", m
    if ":" in m:
        provider, _, model_id = m.partition(":")
        if provider in credentials.PROVIDERS and model_id:
            return provider, model_id
        if provider in credentials.PROVIDERS:
            raise ProviderError(f"The model name is missing after “{provider}:”.")
        raise ProviderError(f"The provider “{provider}” does not exist. The ones I know: {', '.join(credentials.PROVIDERS)}.")
    for prefix, provider in credentials.INFER.items():
        if m.startswith(prefix):
            return provider, m
    raise ProviderError(f"I do not know which provider “{m}” belongs to. Write it as provider:model, for example "
                        "anthropic:claude-opus-5-5 or openai:<model>.")


_cache: dict[str, object] = {}


def for_model(model: str):
    """The provider that serves this model. 'mock' needs no key; the others use their provider's key."""
    provider, _ = resolve(model)
    if provider not in _cache:
        if provider == "opencode":
            from .opencode import OpenCodeProvider  # imported here to avoid creating a cycle

            _cache[provider] = OpenCodeProvider()
        else:
            _cache[provider] = (MockProvider() if provider == "mock" else AnthropicProvider() if provider == "anthropic"
                                else OpenAICompatProvider(provider))
    return _cache[provider]
