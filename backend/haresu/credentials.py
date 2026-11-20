"""API keys per provider, chosen from the interface.

Each model is written as `provider:model` (for example `anthropic:claude-opus-5-5`) and haresu uses the
key of that provider. Keys are saved in `secrets.json` inside the workspace (0600 permissions, outside
git). The interface only receives the last 4 characters. Per provider, the chosen key takes priority over
the environment variable; if none is chosen, the environment variable is used.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Anthropic uses its own API; the rest use the OpenAI-compatible API each one offers.
PROVIDERS: dict[str, dict] = {
    "anthropic": {"name": "Anthropic (Claude)", "env": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"], "prefix": "sk-ant-",
                  "example": "anthropic:claude-opus-5-5"},
    "openai": {"name": "OpenAI", "env": ["OPENAI_API_KEY"], "prefix": "sk-", "base": "https://api.openai.com/v1",
               "example": "openai:<model>"},
    "google": {"name": "Google (Gemini)", "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"], "prefix": "AIza",
               "base": "https://generativelanguage.googleapis.com/v1beta/openai", "example": "google:<model>"},
    "openrouter": {"name": "OpenRouter", "env": ["OPENROUTER_API_KEY"], "prefix": "sk-or-", "base": "https://openrouter.ai/api/v1",
                   "example": "openrouter:<model>"},
    "groq": {"name": "Groq", "env": ["GROQ_API_KEY"], "prefix": "gsk_", "base": "https://api.groq.com/openai/v1",
             "example": "groq:<model>"},
    "mistral": {"name": "Mistral", "env": ["MISTRAL_API_KEY"], "prefix": "", "base": "https://api.mistral.ai/v1",
                "example": "mistral:<model>"},
    "deepseek": {"name": "DeepSeek", "env": ["DEEPSEEK_API_KEY"], "prefix": "sk-", "base": "https://api.deepseek.com/v1",
                 "example": "deepseek:<model>"},
    # One key for both plans (Go and Console); each model brings its own call format. See opencode.py.
    "opencode": {"name": "OpenCode (Go and Console)", "env": ["OPENCODE_API_KEY"], "prefix": "", "custom": True,
                 "example": "opencode:go/<model> or opencode:console/<model>",
                 "help": "One key works for both plans: Go (subscription) and Console (pay as you go). Create it at opencode.ai/console. "
                         "The models of each plan appear grouped in the selector."},
}
# Model names without a provider that are recognized by how they start.
INFER = {"claude": "anthropic", "gpt-": "openai", "gemini": "google", "mistral-": "mistral", "deepseek": "deepseek"}

_path: Path | None = None


def configure(path: Path | str | None) -> None:
    global _path
    _path = Path(path) if path else None


class KeyError_(ValueError):
    pass


def _provider(provider: str) -> dict:
    if provider not in PROVIDERS:
        raise KeyError_(f"The provider '{provider}' does not exist. Options: {', '.join(PROVIDERS)}.")
    return PROVIDERS[provider]


def _read() -> dict:
    data = {"keys": [], "active": {}}
    if _path and _path.exists():
        try:
            raw = json.loads(_path.read_text(encoding="utf-8"))
            if isinstance(raw.get("keys"), list):
                data["keys"] = [{"provider": "anthropic", **k} for k in raw["keys"]]  # versiones anteriores: solo Anthropic
                active = raw.get("active")
                data["active"] = active if isinstance(active, dict) else ({"anthropic": active} if active else {})
        except (OSError, ValueError):
            pass
    return data


def _write(data: dict) -> None:
    if not _path:
        raise KeyError_("There is no workspace to save the key in.")
    _path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(_path)


def base_url(provider: str) -> str:
    """API address of the provider. It can be changed with HARESU_<PROVIDER>_BASE_URL (proxies, gateways)."""
    return os.environ.get(f"HARESU_{provider.upper()}_BASE_URL") or _provider(provider)["base"]


def env_key(provider: str) -> str | None:
    for var in _provider(provider)["env"]:
        if os.environ.get(var):
            return os.environ[var]
    return None


def active_key(provider: str) -> str | None:
    data = _read()
    wanted = data["active"].get(provider)
    for k in data["keys"]:
        if k["id"] == wanted and k["provider"] == provider:
            return k["key"]
    return None


def key_for(provider: str) -> str | None:
    """The key that will be used with this provider: the chosen one or, if there is none, the one from the environment."""
    return active_key(provider) or env_key(provider)


def source(provider: str) -> str:
    """Where the provider's key will come from: 'saved', 'env' or 'none'."""
    if active_key(provider):
        return "saved"
    return "env" if env_key(provider) else "none"


def has_credentials(provider: str = "anthropic") -> bool:
    return source(provider) != "none"


def key_by_id(provider: str, key_id: str) -> str | None:
    """A saved key, by its id (only if it belongs to that provider)."""
    return next((k["key"] for k in _read()["keys"] if k["id"] == key_id and k["provider"] == provider), None)


def client(key: str | None = None):
    """Anthropic client with the given key or, if none, the one the person chose (or the one from the environment)."""
    import anthropic

    key = key or active_key("anthropic")
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def describe() -> dict:
    """What the interface sees: never a full key."""
    data = _read()
    out = []
    for pid, cfg in PROVIDERS.items():
        env = env_key(pid)
        out.append({
            "id": pid, "name": cfg["name"], "example": cfg["example"], "prefix": cfg["prefix"], "help": cfg.get("help"),
            "source": source(pid), "active": data["active"].get(pid),
            "env": {"present": bool(env), "last4": env[-4:] if env else None, "var": cfg["env"][0]},
            "keys": [{"id": k["id"], "name": k["name"], "last4": k["key"][-4:], "created": k.get("created")}
                     for k in data["keys"] if k["provider"] == pid],
        })
    return {"providers": out}


def describe_provider(provider: str) -> dict:
    return next(p for p in describe()["providers"] if p["id"] == provider)


def resolve(provider: str) -> dict:
    """To show next to a model: which key it will use."""
    cfg = _provider(provider)
    data = _read()
    src = source(provider)
    saved = next((k for k in data["keys"] if k["id"] == data["active"].get(provider) and k["provider"] == provider), None)
    env = env_key(provider)
    return {"provider": provider, "provider_name": cfg["name"], "source": src,
            "key_name": saved["name"] if saved else None,
            "last4": (saved["key"][-4:] if saved else env[-4:] if env else None)}


def add(provider: str, name: str, key: str, activate: bool = True) -> str:
    cfg = _provider(provider)
    key = (key or "").strip()
    name = (name or "").strip()[:60]
    if provider != "anthropic" and key.startswith("sk-ant-"):
        raise KeyError_(f"That key belongs to Anthropic, not to {cfg['name']}. Add it in the Anthropic section.")
    if provider != "openrouter" and key.startswith("sk-or-"):
        raise KeyError_(f"That key belongs to OpenRouter, not to {cfg['name']}. Add it in the OpenRouter section.")
    if cfg["prefix"] and not key.startswith(cfg["prefix"]):
        raise KeyError_(f"A {cfg['name']} key starts with “{cfg['prefix']}”. Check that you copied the whole key.")
    if provider == "anthropic" and key.startswith("sk-ant-admin"):
        raise KeyError_("That is an admin key and it cannot generate text. Create a regular API key in the Anthropic console.")
    if len(key) < (30 if provider == "anthropic" else 20) or any(c.isspace() for c in key):
        raise KeyError_("The key looks incomplete or has spaces. Copy it again in full.")
    data = _read()
    if any(k["key"] == key for k in data["keys"]):
        raise KeyError_("That key is already saved.")
    kid = uuid.uuid4().hex[:8]
    mine = [k for k in data["keys"] if k["provider"] == provider]
    data["keys"].append({"id": kid, "provider": provider, "name": name or f"Key {len(mine) + 1}", "key": key,
                         "created": time.time()})
    if activate or not data["active"].get(provider):
        data["active"][provider] = kid
    _write(data)
    return kid


def activate(provider: str, key_id: str | None) -> None:
    """key_id=None: stop using the saved keys of this provider and go back to the one from the environment."""
    _provider(provider)
    data = _read()
    if key_id is None:
        data["active"].pop(provider, None)
    elif any(k["id"] == key_id and k["provider"] == provider for k in data["keys"]):
        data["active"][provider] = key_id
    else:
        raise KeyError_("That key does not exist.")
    _write(data)


def remove(key_id: str) -> None:
    data = _read()
    if not any(k["id"] == key_id for k in data["keys"]):
        raise KeyError_("That key does not exist.")
    data["keys"] = [k for k in data["keys"] if k["id"] != key_id]
    data["active"] = {p: i for p, i in data["active"].items() if i != key_id}
    _write(data)


def _get(url: str, key: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "haresu/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace")


def check(provider: str, key_id: str | None = None) -> str:
    """Tests a key (the one used with the provider, if none is given) without spending tokens. Returns a message."""
    cfg = _provider(provider)
    if key_id:
        key = next((k["key"] for k in _read()["keys"] if k["id"] == key_id and k["provider"] == provider), None)
        if not key:
            raise KeyError_("That key does not exist.")
    else:
        key = key_for(provider)
        if not key:
            raise KeyError_(f"There is no {cfg['name']} key.")
    name = cfg["name"]
    if provider == "opencode":
        from . import opencode

        return opencode.check(key)
    if provider == "anthropic":
        import anthropic

        try:
            anthropic.Anthropic(api_key=key).models.list(limit=1)
        except anthropic.AuthenticationError:
            return f"{name} rejected this key: it is invalid or was revoked."
        except anthropic.PermissionDeniedError:
            return "The key is valid but it is not allowed to list models."
        except anthropic.RateLimitError:
            return "The key looks valid, but Anthropic reported a rate limit. Try again in a moment."
        except anthropic.APIConnectionError:
            return f"I could not connect to {name}. Check your connection."
        except anthropic.APIStatusError as exc:
            return f"{name} responded with an error ({exc.status_code})."
        return f"It works: {name} accepted the key."
    try:
        status, body = _get(f"{base_url(provider).rstrip('/')}/models", key)
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return f"I could not connect to {name}. Check your connection."
    if status == 200:
        return f"It works: {name} accepted the key."
    if status in (401, 403) or (status == 400 and "api key" in body.lower()):
        return f"{name} rejected this key: it is invalid, was revoked or has no permission."
    if status == 429:
        return f"The key looks valid, but {name} reported a rate limit. Try again in a moment."
    return (f"{name} responded {status} and with that I cannot confirm the key without spending tokens. "
            "Test it by running an agent with a model from this provider.")
