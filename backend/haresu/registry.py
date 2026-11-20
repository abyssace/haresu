"""My models: the models the person registered with a name and, optionally, their own key.

For providers with hundreds of models (OpenRouter, OpenCode) the selectors show only these. They are saved in
`settings.json` (they are not secrets: the key is saved separately and here we only note which one each model uses).
"""

from __future__ import annotations

import uuid

from . import credentials, prefs, providers


class RegistryError(ValueError):
    pass


def _canon(provider: str, model: str) -> tuple[str, str]:
    """(provider, model id) validating that it can be resolved."""
    if provider not in credentials.PROVIDERS:
        raise RegistryError(f"The provider “{provider}” does not exist.")
    model = (model or "").strip()
    if not model or any(c.isspace() for c in model) or len(model) > 200:
        raise RegistryError("Type the model id exactly as the provider gives it, without spaces.")
    if model.startswith(f"{provider}:"):
        model = model[len(provider) + 1:]
    try:
        return providers.resolve(f"{provider}:{model}")
    except providers.ProviderError as exc:
        raise RegistryError(str(exc)) from exc


def _check_key(provider: str, key_id: str | None) -> None:
    if key_id and credentials.key_by_id(provider, key_id) is None:
        raise RegistryError("That key does not exist or belongs to another provider.")


def raw() -> list[dict]:
    return prefs.get_models()


def list_() -> list[dict]:
    """The registered models, with the name of the key each one uses."""
    out = []
    for e in raw():
        key = credentials.key_by_id(e["provider"], e["key_id"]) if e.get("key_id") else None
        name = next((k["name"] for k in credentials.describe_provider(e["provider"])["keys"] if k["id"] == e.get("key_id")), None)
        out.append({**e, "value": f"{e['provider']}:{e['model_id']}", "key_name": name if key else None,
                    "key_last4": key[-4:] if key else None, "key_missing": bool(e.get("key_id")) and key is None})
    return out


def validate_new(provider: str, model: str) -> None:
    """Fails if the model cannot be registered (invalid or repeated), before saving anything else."""
    provider, model_id = _canon(provider, model)
    if any(e["provider"] == provider and e["model_id"] == model_id for e in raw()):
        raise RegistryError("That model is already registered.")


def add(provider: str, model: str, name: str = "", key_id: str | None = None) -> dict:
    provider, model_id = _canon(provider, model)
    _check_key(provider, key_id)
    validate_new(provider, model_id)
    entries = raw()
    entry = {"id": uuid.uuid4().hex[:8], "name": (name or "").strip()[:60] or model_id, "provider": provider,
             "model_id": model_id, "key_id": key_id or None}
    prefs.save_models(entries + [entry])
    return entry


def update(entry_id: str, name: str | None = None, key_id: str | None = ..., ) -> dict:  # type: ignore[assignment]
    entries = raw()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if not entry:
        raise RegistryError("That registered model does not exist.")
    if name is not None:
        entry["name"] = name.strip()[:60] or entry["model_id"]
    if key_id is not ...:
        _check_key(entry["provider"], key_id)
        entry["key_id"] = key_id or None
    prefs.save_models(entries)
    return entry


def remove(entry_id: str) -> None:
    entries = raw()
    if not any(e["id"] == entry_id for e in entries):
        raise RegistryError("That registered model does not exist.")
    prefs.save_models([e for e in entries if e["id"] != entry_id])


def entry_for(model: str) -> dict | None:
    """The registered entry of a model, if there is one."""
    try:
        provider, model_id = providers.resolve(model)
    except providers.ProviderError:
        return None
    return next((e for e in raw() if e["provider"] == provider and e["model_id"] == model_id), None)


def key_for(model: str) -> str | None:
    """A registered model's own key; None if it uses the provider's."""
    e = entry_for(model)
    return credentials.key_by_id(e["provider"], e["key_id"]) if e and e.get("key_id") else None
