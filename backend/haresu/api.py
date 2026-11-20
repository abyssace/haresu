"""HTTP API for the visual interface."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import json
import os

from . import assistant, credentials, engine, loop, model_list, prefs, providers, registry
from . import spec as S
from .examples import EXAMPLES
from .store import DEFAULT_GATE, NotFound, Workspace
from .tools import REGISTRY


def create_app(root: str | Path | None = None, static_dir: str | Path | None = None) -> FastAPI:
    ws = Workspace(root)
    app = FastAPI(title="haresu")
    jobs: dict[str, dict] = {}
    live: dict[str, dict] = {}  # per harness: the working canvas of external agents and their events
    locks: dict[str, threading.Lock] = {}

    def guard(fn):
        try:
            return fn()
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, loop.LoopError, S.PatchError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except providers.ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc

    def start_job(h: str, label: str, fn, with_job: bool = False) -> dict:
        """Cycle operations run in the background, one at a time per harness."""
        ws.info(h)
        lock = locks.setdefault(h, threading.Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "An operation is already running on this harness.")
        job = {"id": uuid.uuid4().hex[:10], "harness": h, "label": label, "status": "running",
               "progress": "", "result": None, "error": None, "started": time.time(), "events": [], "cancel": False}
        jobs[job["id"]] = job

        def work():
            try:
                job["result"] = fn(lambda msg: job.update(progress=str(msg))) if not with_job else fn(job)
                job["status"] = "done"
            except (NotFound, ValueError, loop.LoopError, S.PatchError, providers.ProviderError) as exc:
                job["status"], job["error"] = "error", str(exc)
            except Exception as exc:  # unexpected error: it is reported, not swallowed
                traceback.print_exc()
                job["status"], job["error"] = "error", f"{type(exc).__name__}: {exc}"
            finally:
                lock.release()

        threading.Thread(target=work, daemon=True).start()
        return job

    # --- catalog
    @app.get("/api/catalog")
    def catalog():
        return {
            "node_types": S.NODE_TYPES,
            "tools": [{"name": t.name, "description": t.description} for t in REGISTRY.values()],
            "examples": list(EXAMPLES),
            "models": ["mock", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"],
            "has_credentials": credentials.has_credentials("anthropic"),
            "preferences": prefs.get(),
            "providers": [{"id": pid, "name": cfg["name"], "source": credentials.source(pid), "example": cfg["example"]}
                          for pid, cfg in credentials.PROVIDERS.items()],
        }

    # --- API keys, per provider (a full key is never returned)
    def keys_guard(fn):
        try:
            return fn()
        except credentials.KeyError_ as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/settings")
    def get_settings():
        return credentials.describe()

    @app.post("/api/settings/keys")
    def add_key(body: dict = Body(...)):
        return keys_guard(lambda: (credentials.add(str(body.get("provider") or "anthropic"), str(body.get("name") or ""),
                                                   str(body.get("key") or ""), bool(body.get("activate", True))),
                                   credentials.describe())[1])

    @app.post("/api/settings/active")
    def set_active(body: dict = Body(...)):
        return keys_guard(lambda: (credentials.activate(str(body.get("provider") or "anthropic"), body.get("id") or None),
                                   credentials.describe())[1])

    @app.delete("/api/settings/keys/{key_id}")
    def delete_key(key_id: str):
        return keys_guard(lambda: (credentials.remove(key_id), credentials.describe())[1])

    @app.post("/api/settings/check")
    def check_key(body: dict = Body(default={})):
        return keys_guard(lambda: {"message": credentials.check(str(body.get("provider") or "anthropic"), body.get("id") or None)})

    @app.get("/api/models")
    def list_models(refresh: bool = False):
        """The models of every provider that has a key set."""
        mine = registry.list_()
        return {"providers": [{**p, "registered": [{"value": r["value"], "id": r["model_id"], "name": r["name"], "key_id": r["key_id"]}
                                                    for r in mine if r["provider"] == p["id"]]}
                              for p in model_list.available(refresh)]}

    # --- my models: the ones the person registered with a name and, optionally, their own key
    @app.get("/api/registry")
    def get_registry():
        return {"models": registry.list_()}

    @app.post("/api/registry")
    def add_registry(body: dict = Body(...)):
        def go():
            provider = str(body.get("provider") or "")
            key_id = body.get("key_id") or None
            new_key = body.get("new_key")
            registry.validate_new(provider, str(body.get("model") or ""))  # before saving the new key
            if isinstance(new_key, dict) and str(new_key.get("key") or "").strip():
                # A new key typed while registering the model: it is saved with the provider and this model uses it.
                key_id = credentials.add(provider, str(new_key.get("name") or ""), str(new_key["key"]), activate=False)
            registry.add(provider, str(body.get("model") or ""), str(body.get("name") or ""), key_id)
            return {"models": registry.list_()}
        try:
            return go()
        except credentials.KeyError_ as exc:
            raise HTTPException(400, str(exc)) from exc
        except registry.RegistryError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/registry/{entry_id}")
    def update_registry(entry_id: str, body: dict = Body(...)):
        try:
            registry.update(entry_id, body.get("name"), (body.get("key_id") or None) if "key_id" in body else ...)
            return {"models": registry.list_()}
        except registry.RegistryError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/registry/{entry_id}")
    def delete_registry(entry_id: str):
        try:
            registry.remove(entry_id)
            return {"models": registry.list_()}
        except registry.RegistryError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/preferences")
    def get_preferences():
        return prefs.get()

    @app.put("/api/preferences")
    def put_preferences(body: dict = Body(...)):
        def go():
            changes = {}
            for key in ("default_model", "assistant_model"):
                if body.get(key) is not None:
                    model = str(body[key]).strip()
                    try:
                        providers.resolve(model)  # make sure it is a model that can be resolved
                    except providers.ProviderError as exc:
                        raise ValueError(str(exc)) from exc
                    changes[key] = model
            return prefs.set_(**changes)
        return guard(go)

    @app.get("/api/resolve")
    def resolve_model(model: str):
        """Which provider a model belongs to and which key it will use."""
        try:
            provider, model_id = providers.resolve(model)
        except providers.ProviderError as exc:
            return {"model": model, "ok": False, "error": str(exc)}
        if provider == "mock":
            return {"model": model, "ok": True, "provider": "mock", "provider_name": "Test model", "source": "none", "needs_key": False}
        info = credentials.resolve(provider)
        own = registry.entry_for(model)
        if own and own.get("key_id") and (k := credentials.key_by_id(provider, own["key_id"])):  # the model has its own key
            info = {**info, "source": "saved", "last4": k[-4:],
                    "key_name": next((x["name"] for x in credentials.describe_provider(provider)["keys"] if x["id"] == own["key_id"]), None)}
        return {"model": model, "model_id": model_id, "ok": info["source"] != "none", "needs_key": True,
                "registered_name": own["name"] if own else None, **info,
                "error": None if info["source"] != "none" else f"There is no key for {info['provider_name']}. Add it in Settings."}

    # --- harnesses
    @app.get("/api/harnesses")
    def list_harnesses():
        return ws.list_harnesses()

    @app.post("/api/harnesses")
    def create_harness(body: dict = Body(...)):
        name = str(body.get("name") or "").strip()
        example = body.get("example") or "blank"
        if not name:
            raise HTTPException(400, "The name is missing.")
        if example not in EXAMPLES:
            raise HTTPException(400, f"The example '{example}' does not exist.")
        spec, evals = EXAMPLES[example]()
        imported = body.get("import")
        if isinstance(imported, dict):  # an exported harness: {spec, evals}
            spec, evals = imported.get("spec"), imported.get("evals") or evals
            if not isinstance(spec, dict) or not isinstance(spec.get("nodes"), list) or not isinstance(spec.get("edges"), list):
                raise HTTPException(400, "The file is not a haresu exported harness.")
            bad = [n.get("type") for n in spec["nodes"] if n.get("type") not in S.NODE_TYPES]
            if bad:
                raise HTTPException(400, f"The file has unknown node types: {', '.join(map(str, bad))}.")
        spec["name"] = name
        return guard(lambda: ws.info(ws.create_harness(name, spec, evals, spec.get("description", ""))))

    @app.patch("/api/harnesses/{h}")
    def rename_harness(h: str, body: dict = Body(...)):
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "The name is missing.")
        return guard(lambda: (ws.rename_harness(h, name), ws.info(h))[1])

    @app.delete("/api/harnesses/{h}")
    def delete_harness(h: str):
        return guard(lambda: (ws.delete_harness(h), {"ok": True})[1])

    @app.post("/api/harnesses/{h}/duplicate")
    def duplicate_harness(h: str, body: dict = Body(...)):
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "The name is missing.")
        return guard(lambda: ws.info(ws.duplicate_harness(h, name)))

    @app.get("/api/harnesses/{h}/export")
    def export_harness(h: str):
        return guard(lambda: {"haresu": 1, "name": ws.info(h)["name"],
                              "spec": ws.get_spec(h, ws.current(h)), "evals": ws.get_evals(h)})

    @app.get("/api/harnesses/{h}/overlay")
    def overlay(h: str, version: str):
        return guard(lambda: loop.overlay(ws, h, version))

    @app.get("/api/harnesses/{h}")
    def get_harness(h: str):
        return guard(lambda: {**ws.info(h), "versions": ws.list_versions(h)})

    # --- versiones
    @app.get("/api/harnesses/{h}/versions/{v}")
    def get_version(h: str, v: str):
        def go():
            spec = ws.get_spec(h, v)
            meta = ws.get_version_meta(h, v)
            parent = meta.get("parent")
            return {"version": v, "spec": spec, "meta": meta, "problems": S.validate(spec),
                    "diff": S.diff(ws.get_spec(h, parent), spec) if parent else None}
        return guard(go)

    @app.post("/api/harnesses/{h}/versions")
    def save_version(h: str, body: dict = Body(...)):
        def go():
            spec = body.get("spec")
            if not isinstance(spec, dict) or "nodes" not in spec or "edges" not in spec:
                raise ValueError("The graph is missing.")
            problems = S.validate(spec)
            v = ws.save_version(h, spec, {"author": "human", "parent": body.get("parent") or ws.current(h),
                                          "note": str(body.get("note") or "Manual edit"), "status": "candidate"})
            if body.get("make_current"):
                ws.update_version_meta(h, v, status="accepted", promoted_by="human (no gate)")
                ws.set_current(h, v)
            return {"version": v, "problems": problems}
        return guard(go)

    @app.post("/api/validate")
    def validate(body: dict = Body(...)):
        spec = body.get("spec") or {"nodes": [], "edges": []}
        by_node = S.node_issues(spec)
        for n in spec.get("nodes", []):  # a model from a provider without a key will fail when it runs: the node gets a warning
            if n.get("type") in ("agent", "meta"):
                model = (n.get("data") or {}).get("model") or "mock"
                try:
                    provider = providers.resolve(model)[0]
                except providers.ProviderError as exc:
                    by_node.setdefault(n["id"], []).append(str(exc))
                    continue
                if provider != "mock" and not credentials.has_credentials(provider):
                    by_node.setdefault(n["id"], []).append(
                        f"It uses {credentials.PROVIDERS[provider]['name']} and there is no key. Add it in Settings.")
        return {"problems": S.validate(spec), "by_node": by_node}

    # --- one-off run
    @app.post("/api/harnesses/{h}/run")
    def run_once(h: str, body: dict = Body(...)):
        def go():
            spec = body.get("spec") or ws.get_spec(h, body.get("version") or ws.current(h))
            task = str(body.get("input") or "").strip()
            if not task:
                raise ValueError("Type a task.")
            result = engine.run(spec, task)
            run_id = ws.save_run(h, {"kind": "manual", "version": body.get("version") or "(unsaved)",
                                     "input": task, "output": result["output"], "metrics": result["metrics"],
                                     "trace": result["trace"]})
            return {"run_id": run_id, **result}
        return guard(go)

    # --- run logs
    @app.get("/api/harnesses/{h}/runs")
    def list_runs(h: str, iteration: str | None = None):
        return guard(lambda: ws.list_runs(h, iteration=iteration)[:300])

    @app.get("/api/harnesses/{h}/runs/{run_id}")
    def get_run(h: str, run_id: str):
        return guard(lambda: ws.get_run(h, run_id))

    # --- evals
    @app.get("/api/harnesses/{h}/evals")
    def get_evals(h: str):
        return guard(lambda: ws.get_evals(h))

    @app.put("/api/harnesses/{h}/evals")
    def put_evals(h: str, body: dict = Body(...)):
        def go():
            cases = body.get("cases")
            if not isinstance(cases, list):
                raise ValueError("'cases' must be a list.")
            ids = [c.get("id") for c in cases]
            if len(ids) != len(set(ids)) or not all(ids):
                raise ValueError("Every case needs a unique 'id'.")
            for c in cases:
                if c.get("split", "dev") not in ("dev", "heldout"):
                    raise ValueError(f"Case {c['id']}: 'split' must be dev or heldout.")
                if not str(c.get("input") or "").strip():
                    raise ValueError(f"Case {c['id']}: 'input' is missing.")
            gate = {**DEFAULT_GATE, **(body.get("gate") or {})}
            gate["seeds"] = max(1, min(int(gate.get("seeds") or 1), 10))
            judge = {"model": str((body.get("judge") or {}).get("model") or "mock")}
            ws.save_evals(h, {"gate": gate, "judge": judge, "cases": cases})
            return {"ok": True}
        return guard(go)

    # --- cycle
    @app.get("/api/harnesses/{h}/trend")
    def trend(h: str):
        return guard(lambda: {"current": ws.current(h), "rows": loop.trend(ws, h), "versions": ws.list_versions(h)})

    @app.get("/api/harnesses/{h}/guide")
    def guide(h: str):
        return guard(lambda: loop.guide(ws, h))

    @app.post("/api/harnesses/{h}/loop/measure")
    def measure(h: str, body: dict = Body(default={})):
        version = body.get("version")

        def fn(progress):
            kind = "baseline" if not version or version == ws.current(h) else "candidate"
            return loop.measure(ws, h, version, kind, lambda i, n: progress(f"case {i} of {n}"))
        return guard(lambda: start_job(h, f"Measure {version or 'current'}", fn))

    @app.post("/api/harnesses/{h}/loop/propose")
    def propose(h: str):
        return guard(lambda: start_job(h, "Proponer", lambda progress: loop.propose(ws, h)))

    @app.post("/api/harnesses/{h}/loop/decide")
    def decide(h: str, body: dict = Body(...)):
        return guard(lambda: loop.decide(ws, h, str(body.get("candidate") or ""), str(body.get("approved_by") or "")))

    @app.post("/api/harnesses/{h}/loop/cycle")
    def cycle(h: str, body: dict = Body(default={})):
        rounds = max(1, min(int(body.get("rounds") or 1), 10))
        return guard(lambda: start_job(h, f"Cycle ×{rounds}", lambda progress: loop.cycle(ws, h, rounds, progress)))

    @app.post("/api/harnesses/{h}/assistant")
    def assistant_start(h: str, body: dict = Body(...)):
        message = str(body.get("message") or "").strip()
        spec = body.get("spec")
        if not message:
            raise HTTPException(400, "Type what you want to build.")
        if not isinstance(spec, dict) or not isinstance(spec.get("nodes"), list) or not isinstance(spec.get("edges"), list):
            raise HTTPException(400, "The canvas is missing.")
        model = str(body.get("model") or "mock")
        history = body.get("history") if isinstance(body.get("history"), list) else []

        def fn(job):
            assistant.run(ws, h, spec, message, model, history, job["events"].append, lambda: job["cancel"], prefs.get()["default_model"])
            return {"ok": True}
        job = guard(lambda: start_job(h, "Assistant", fn, with_job=True))
        return {k: v for k, v in job.items() if k != "events"}

    # --- external agents (MCP): they work on a canvas the server keeps, and the interface shows it live
    def live_of(h: str) -> dict:
        ws.info(h)
        return live.setdefault(h, {"session": None, "events": []})

    @app.get("/api/harnesses/{h}/live")
    def live_events(h: str, after: int = -1):
        def go():
            events = list(live_of(h)["events"])
            return {"events": events[after:] if after >= 0 else [], "next": len(events)}
        return guard(go)

    @app.post("/api/harnesses/{h}/agent")
    def agent_call(h: str, body: dict = Body(...)):
        tool = str(body.get("tool") or "")
        args = dict(body.get("args") or {})
        who = str(body.get("agent") or "agent")[:40]

        def go():
            state = live_of(h)
            emit = lambda e: state["events"].append({**e, "agent": who})  # noqa: E731
            lock = locks.setdefault(h, threading.Lock())
            if not lock.acquire(timeout=30):
                raise ValueError("Another operation is running on this harness; try again in a moment.")
            try:
                def fresh():
                    state["session"] = assistant.Session(ws, h, ws.get_spec(h, ws.current(h)), emit, default_model=prefs.get()["default_model"])
                    emit({"type": "reload"})
                if state["session"] is None:
                    fresh()
                sess = state["session"]
                if tool == "reset_canvas":
                    fresh()
                    return {"result": f"Working canvas reset from {ws.current(h)}."}
                if tool == "run_cycle":
                    log = loop.cycle(ws, h, max(1, min(int(args.get("rounds") or 1), 10)))
                    fresh()
                    return {"result": " · ".join(s["step"] for s in log) + f". Current version: {ws.current(h)}."}
                if tool == "get_trend":
                    rows = [{k: r[k] for k in ("iteration", "version", "status", "dev", "heldout", "gain")} for r in loop.trend(ws, h)]
                    return {"result": json.dumps(rows, ensure_ascii=False)}
                if "data" in args:
                    args["data_json"] = json.dumps(args.pop("data") or {}, ensure_ascii=False)
                if "documents" in args:
                    args["documents_json"] = json.dumps(args.pop("documents") or [], ensure_ascii=False)
                if "cases" in args:
                    args["cases_json"] = json.dumps(args.pop("cases") or [], ensure_ascii=False)
                result = assistant.dispatch(sess, tool, args)
                if tool in ("save", "measure", "add_cases"):
                    emit({"type": "changed"})
                return {"result": result}
            finally:
                lock.release()
        return guard(go)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        if job_id not in jobs:
            raise HTTPException(404, "That job does not exist.")
        jobs[job_id]["cancel"] = True
        return {"ok": True}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, after: int = 0):
        if job_id not in jobs:
            raise HTTPException(404, "That job does not exist.")
        job = jobs[job_id]
        events = list(job["events"])
        return {**{k: v for k, v in job.items() if k != "events"}, "events": events[after:], "next": len(events)}

    # --- compiled interface
    if static_dir:
        static = Path(static_dir)
    else:  # the compiled interface ships inside the package; in a working copy, the one in frontend/dist
        here = Path(__file__).resolve()
        static = next((c for c in (here.parent / "static", here.parents[2] / "frontend" / "dist") if (c / "index.html").exists()),
                      here.parent / "static")
    if static.is_dir() and (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}")
        def index(path: str):
            return FileResponse(static / "index.html")

    return app
