"""Workspace on disk: harnesses, versions, evals, iterations and run records.

    <root>/harnesses/<h>/
        harness.json                 name and description
        CURRENT                      current version
        evals.json                   cases and gate (the meta-agent cannot touch them)
        versions/v000/spec.json      the graph
        versions/v000/meta.json      parent, author, patch, status
        iterations/iter-001.json     one evaluation of a version
        runs/<run_id>.json           record of a run, with its trace
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from . import credentials, prefs


def default_root() -> Path:
    """Where the data lives: $HARESU_HOME; if run from a repo copy, backend/workspace; otherwise ~/.haresu/workspace."""
    if os.environ.get("HARESU_HOME"):
        return Path(os.environ["HARESU_HOME"]).expanduser()
    backend = Path(__file__).resolve().parents[1]
    if (backend / "pyproject.toml").exists():  # working copy (git clone or pip install -e)
        return backend / "workspace"
    return Path.home() / ".haresu" / "workspace"


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "harness"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# Runs stored by earlier versions used Spanish event names.
_LEGACY_EVENTS = {"inicio": "start", "enrutamiento": "routing", "agente_inicio": "agent_start", "modelo": "model",
                  "herramienta": "tool", "herramienta_filtrada": "tool_filtered", "herramienta_desconocida": "unknown_tool",
                  "redaccion": "redaction", "violacion": "violation", "relevo": "handoff", "agente_fin": "agent_end", "fin": "end"}
_LEGACY_KINDS = {"presupuesto": "budget", "herramienta": "tool", "pasos": "steps", "salida": "output"}


def _modernize(run: dict) -> dict:
    for e in run.get("trace") or []:
        e["type"] = _LEGACY_EVENTS.get(e.get("type"), e.get("type"))
        if e["type"] == "violation":
            e["kind"] = _LEGACY_KINDS.get(e.get("kind"), e.get("kind"))
    return run


class NotFound(Exception):
    pass


class Workspace:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else default_root()
        (self.root / "harnesses").mkdir(parents=True, exist_ok=True)
        credentials.configure(self.root / "secrets.json")
        prefs.configure(self.root / "settings.json")

    def _dir(self, h: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", h):
            raise NotFound(f"Invalid harness name: {h}")
        d = self.root / "harnesses" / h
        if not d.is_dir():
            raise NotFound(f"The harness '{h}' does not exist.")
        return d

    # --- harnesses
    def list_harnesses(self) -> list[dict]:
        out = []
        for d in sorted((self.root / "harnesses").iterdir()):
            if (d / "harness.json").exists():
                info = _read(d / "harness.json")
                out.append({**info, "id": d.name, "current": self.current(d.name)})
        return out

    def create_harness(self, name: str, spec: dict, evals: dict | None = None, description: str = "") -> str:
        h = slug(name)
        d = self.root / "harnesses" / h
        if d.exists():
            raise ValueError(f"A harness named '{h}' already exists.")
        d.mkdir(parents=True)
        _write(d / "harness.json", {"name": name, "description": description, "created": time.time()})
        _write(d / "evals.json", evals or {"gate": dict(DEFAULT_GATE), "judge": {"model": "mock"}, "cases": []})
        v = self.save_version(h, spec, {"author": "human", "note": "Initial version", "status": "accepted"})
        self.set_current(h, v)
        return h

    def rename_harness(self, h: str, name: str) -> None:
        info = _read(self._dir(h) / "harness.json")
        info["name"] = name
        _write(self._dir(h) / "harness.json", info)

    def duplicate_harness(self, h: str, name: str) -> str:
        new = slug(name)
        target = self.root / "harnesses" / new
        if target.exists():
            raise ValueError(f"A harness named '{new}' already exists.")
        shutil.copytree(self._dir(h), target)
        _write(target / "harness.json", {**_read(target / "harness.json"), "name": name, "created": time.time()})
        return new

    def delete_harness(self, h: str) -> None:
        shutil.rmtree(self._dir(h))

    def info(self, h: str) -> dict:
        return {**_read(self._dir(h) / "harness.json"), "id": h, "current": self.current(h)}

    # --- versiones
    def list_versions(self, h: str) -> list[dict]:
        vdir = self._dir(h) / "versions"
        out = []
        for d in sorted(vdir.iterdir()) if vdir.exists() else []:
            if (d / "meta.json").exists():
                out.append({"version": d.name, **_read(d / "meta.json")})
        return out

    def save_version(self, h: str, spec: dict, meta: dict) -> str:
        vdir = self._dir(h) / "versions"
        vdir.mkdir(exist_ok=True)
        nums = [int(d.name[1:]) for d in vdir.iterdir() if re.fullmatch(r"v\d{3,}", d.name)]
        v = f"v{(max(nums) + 1 if nums else 0):03d}"
        _write(vdir / v / "spec.json", spec)
        _write(vdir / v / "meta.json", {"created": time.time(), "status": "candidate", **meta})
        return v

    def get_spec(self, h: str, v: str) -> dict:
        p = self._dir(h) / "versions" / v / "spec.json"
        if not re.fullmatch(r"v\d{3,}", v) or not p.exists():
            raise NotFound(f"The version '{v}' does not exist.")
        return _read(p)

    def get_version_meta(self, h: str, v: str) -> dict:
        self.get_spec(h, v)
        return _read(self._dir(h) / "versions" / v / "meta.json")

    def update_version_meta(self, h: str, v: str, **changes) -> None:
        meta = self.get_version_meta(h, v)
        meta.update(changes)
        _write(self._dir(h) / "versions" / v / "meta.json", meta)

    def current(self, h: str) -> str:
        base = self.root / "harnesses" / h
        for name in ("CURRENT", "ACTUAL"):  # earlier versions wrote "ACTUAL"
            if (base / name).exists():
                return (base / name).read_text().strip()
        return ""

    def set_current(self, h: str, v: str) -> None:
        self.get_spec(h, v)
        (self._dir(h) / "CURRENT").write_text(v)
        (self._dir(h) / "ACTUAL").unlink(missing_ok=True)

    # --- evals
    def get_evals(self, h: str) -> dict:
        return _read(self._dir(h) / "evals.json")

    def save_evals(self, h: str, evals: dict) -> None:
        _write(self._dir(h) / "evals.json", evals)

    # --- run records
    def save_run(self, h: str, run: dict) -> str:
        run_id = run.get("id") or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        run["id"] = run_id
        run.setdefault("created", time.time())
        _write(self._dir(h) / "runs" / f"{run_id}.json", run)
        return run_id

    def get_run(self, h: str, run_id: str) -> dict:
        p = self._dir(h) / "runs" / f"{run_id}.json"
        if not re.fullmatch(r"[A-Za-z0-9-]+", run_id) or not p.exists():
            raise NotFound(f"The record '{run_id}' does not exist.")
        return _modernize(_read(p))

    def list_runs(self, h: str, iteration: str | None = None, split: str | None = None) -> list[dict]:
        rdir = self._dir(h) / "runs"
        out = []
        for p in sorted(rdir.glob("*.json"), reverse=True) if rdir.exists() else []:
            r = _read(p)
            if iteration and r.get("iteration") != iteration:
                continue
            if split and r.get("split") != split:
                continue
            out.append({k: v for k, v in r.items() if k != "trace"})
        return out

    # --- iterations
    def list_iterations(self, h: str) -> list[dict]:
        idir = self._dir(h) / "iterations"
        return [_read(p) for p in sorted(idir.glob("iter-*.json"))] if idir.exists() else []

    def next_iteration_id(self, h: str) -> str:
        return f"iter-{len(self.list_iterations(h)) + 1:03d}"

    def save_iteration(self, h: str, it: dict) -> None:
        _write(self._dir(h) / "iterations" / f"{it['id']}.json", it)

    def get_iteration(self, h: str, iter_id: str) -> dict:
        p = self._dir(h) / "iterations" / f"{iter_id}.json"
        if not re.fullmatch(r"iter-\d{3,}", iter_id) or not p.exists():
            raise NotFound(f"The iteration '{iter_id}' does not exist.")
        return _read(p)


DEFAULT_GATE = {
    "min_heldout_gain": 0.05,
    "max_dev_drop": 0.02,
    "max_new_violations": 0,
    "policy_changes_need_human": True,
    "seeds": 1,
}
