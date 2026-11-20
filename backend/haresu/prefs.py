"""The person's preferences that are not secret: the default model and the assistant's model.

They are saved in `settings.json` inside the workspace.
"""

from __future__ import annotations

import json
from pathlib import Path

_path: Path | None = None


def configure(path: Path | str | None) -> None:
    global _path
    _path = Path(path) if path else None


def _read() -> dict:
    if _path and _path.exists():
        try:
            data = json.loads(_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def get() -> dict:
    """default_model: the one for new agents. assistant_model: the one for the assistant chat (by default, the same)."""
    data = _read()
    default = str(data.get("default_model") or "mock")
    return {"default_model": default, "assistant_model": str(data.get("assistant_model") or default)}


def get_models() -> list[dict]:
    models = _read().get("models")
    return [m for m in models if isinstance(m, dict) and m.get("id") and m.get("provider") and m.get("model_id")] if isinstance(models, list) else []


def save_models(models: list[dict]) -> None:
    if not _path:
        raise ValueError("There is no workspace to save the models in.")
    data = _read()
    data["models"] = models
    _path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path)


def set_(default_model: str | None = None, assistant_model: str | None = None) -> dict:
    if not _path:
        raise ValueError("There is no workspace to save the preferences in.")
    data = _read()
    if default_model is not None:
        data["default_model"] = default_model
    if assistant_model is not None:
        data["assistant_model"] = assistant_model
    _path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path)
    return get()
