"""The cycle: measure the current version -> propose -> measure the candidate -> the gate decides."""

from __future__ import annotations

import time

from . import evals, gate, governance, meta
from . import spec as S
from .store import Workspace


class LoopError(Exception):
    pass


def measure(ws: Workspace, h: str, version: str | None = None, kind: str = "baseline", on_progress=None) -> dict:
    """Evaluates a version and saves the iteration."""
    version = version or ws.current(h)
    problems = S.validate(ws.get_spec(h, version))
    if problems:
        raise LoopError(f"{version} is missing: " + " ".join(problems))
    if not ws.get_evals(h).get("cases"):
        raise LoopError("There are no eval cases. Add cases in the Evals tab.")
    it = {"id": ws.next_iteration_id(h), "kind": kind, "version": version, "created": time.time(),
          "base_version": ws.current(h) if kind == "candidate" else None, "decision": None}
    ws.save_iteration(h, it)  # reserves the id
    it["summary"] = evals.evaluate(ws, h, version, it["id"], on_progress)
    ws.save_iteration(h, it)
    return it


def last_iteration_of(ws: Workspace, h: str, version: str) -> dict | None:
    its = [i for i in ws.list_iterations(h) if i["version"] == version and i.get("summary")]
    return its[-1] if its else None


def propose(ws: Workspace, h: str) -> dict:
    """The meta-agent proposes the next version from the latest measurement of the current one."""
    base_version = ws.current(h)
    base_it = last_iteration_of(ws, h, base_version)
    if not base_it:
        raise LoopError(f"Measure the current version first ({base_version}).")
    spec = ws.get_spec(h, base_version)
    patch = meta.propose(ws, h, base_version, base_it["id"])
    if not patch["ops"]:
        return {"version": None, "patch": patch, "problems": ["The meta-agent proposed no changes."]}
    check = governance.check_ops(spec, patch["ops"])
    if check["blocked"]:
        return {"version": None, "patch": patch, "problems": check["blocked"]}
    try:
        new_spec = S.apply_ops(spec, patch["ops"])
    except S.PatchError as exc:
        return {"version": None, "patch": patch, "problems": [str(exc)]}
    problems = S.validate(new_spec)
    if problems:
        return {"version": None, "patch": patch, "problems": problems}
    v = ws.save_version(h, new_spec, {
        "author": patch.pop("author", "meta"), "parent": base_version, "status": "candidate",
        "note": patch["diagnosis"].splitlines()[0][:200] if patch["diagnosis"] else "",
        "patch": patch, "needs_human": check["needs_human"], "from_iteration": base_it["id"],
    })
    return {"version": v, "patch": patch, "problems": []}


def decide(ws: Workspace, h: str, candidate: str, approved_by: str = "") -> dict:
    """Runs the candidate through the gate. If it passes, it becomes the current version."""
    base_version = ws.current(h)
    if candidate == base_version:
        raise LoopError("That version is already the current one.")
    base_it = last_iteration_of(ws, h, base_version)
    cand_it = last_iteration_of(ws, h, candidate)
    if not base_it or not cand_it:
        raise LoopError("The current version and the candidate must be measured before deciding.")
    decision = gate.decide(base_it["summary"], cand_it["summary"], ws.get_spec(h, base_version),
                           ws.get_spec(h, candidate), ws.get_evals(h).get("gate"), approved_by)
    decision["base_version"] = base_version
    decision["base_iteration"] = base_it["id"]
    cand_it["decision"] = decision
    ws.save_iteration(h, cand_it)
    if decision["accepted"]:
        ws.update_version_meta(h, candidate, status="accepted")
        ws.set_current(h, candidate)
    elif not decision["needs_human"]:
        ws.update_version_meta(h, candidate, status="rejected")
    else:
        ws.update_version_meta(h, candidate, status="pending_human")
    return {"iteration": cand_it["id"], **decision}


def cycle(ws: Workspace, h: str, rounds: int = 1, on_step=None) -> list[dict]:
    """Complete rounds without an operator. It stops if there is no proposal or human approval is missing."""
    log = []

    def step(msg: str, **data) -> None:
        log.append({"step": msg, **data})
        if on_step:
            on_step(msg)

    for n in range(1, rounds + 1):
        current = ws.current(h)
        if not last_iteration_of(ws, h, current):
            it = measure(ws, h, current, "baseline")
            step(f"Round {n}: measured {current}", iteration=it["id"])
        p = propose(ws, h)
        if not p["version"]:
            step(f"Round {n}: no candidate — {'; '.join(p['problems'])}", patch=p["patch"])
            break
        step(f"Round {n}: the meta-agent proposed {p['version']}", version=p["version"])
        it = measure(ws, h, p["version"], "candidate")
        step(f"Round {n}: measured {p['version']}", iteration=it["id"])
        d = decide(ws, h, p["version"])
        verdict = "accepted" if d["accepted"] else ("awaiting human approval" if d["needs_human"] else "rejected")
        step(f"Round {n}: {p['version']} {verdict}", decision=d)
        if d["needs_human"]:
            break
    return log


def trend(ws: Workspace, h: str) -> list[dict]:
    """One row per iteration, with the improvement over the last accepted version."""
    rows, best = [], None
    for it in ws.list_iterations(h):
        s = it.get("summary")
        if not s:
            continue
        d = it.get("decision")
        status = "baseline" if it["kind"] == "baseline" else (
            "undecided" if not d else "accepted" if d["accepted"] else
            "awaiting approval" if d["needs_human"] else "rejected")
        held = s["heldout"]["score"]
        rows.append({
            "iteration": it["id"], "version": it["version"], "kind": it["kind"], "status": status,
            "dev": s["dev"]["score"], "heldout": held, "seeds": s.get("seeds", 1),
            "dev_se": s["dev"].get("se", 0), "heldout_se": s["heldout"].get("se", 0),
            "violations": s["dev"]["violations"] + s["heldout"]["violations"],
            "tokens": s["dev"]["tokens"] + s["heldout"]["tokens"],
            "gain": round(held - best, 4) if (best is not None and held is not None) else None,
            "decision": d,
        })
        if it["kind"] == "baseline" or (d and d["accepted"]):
            best = held
    return rows


def guide(ws: Workspace, h: str) -> dict:
    """Which step of the cycle the harness is in and what comes next. The interface shows it as a guide."""
    current = ws.current(h)
    cases = ws.get_evals(h).get("cases", [])
    n_dev = sum(1 for c in cases if c.get("split", "dev") == "dev")
    n_held = sum(1 for c in cases if c.get("split") == "heldout")
    problems = S.validate(ws.get_spec(h, current))
    measured = bool(last_iteration_of(ws, h, current))
    pending = [v for v in ws.list_versions(h)
               if v["version"] != current and v.get("status") in ("candidate", "pending_human")]
    cand = pending[-1] if pending else None
    cand_measured = bool(cand and last_iteration_of(ws, h, cand["version"]))
    accepted = sum(1 for v in ws.list_versions(h) if v.get("status") == "accepted") - 1

    steps = [
        {"id": "graph", "tab": "canvas", "title": "Build the harness", "done": not problems,
         "text": ("Missing: " + " ".join(problems)) if problems else
                 "On the Canvas you connect the input, the router, the agents, their context and their policies.",
         "action": "Go to Canvas"},
        {"id": "evals", "tab": "evals", "title": "Write test cases", "done": n_dev > 0 and n_held > 0,
         "text": f"You have {n_dev} dev cases and {n_held} held-out. You need at least one of each kind: "
                 "the meta-agent learns from dev and the gate decides with held-out.",
         "action": "Ir a Evals"},
        {"id": "measure", "tab": "loop", "title": f"Measure the current version ({current})", "done": measured,
         "text": "Run every case against the current version to know where you start from.",
         "action": "Go to Cycle"},
        {"id": "propose", "tab": "loop", "title": "Ask the meta-agent for a proposal", "done": bool(cand),
         "text": "The meta-agent reads the dev runs that failed and proposes a new version. "
                 "You can also edit the Canvas yourself and save a candidate.",
         "action": "Go to Cycle"},
        {"id": "decide", "tab": "loop", "done": False,
         "title": f"Measure {cand['version']} and decide" if cand else "Measure the candidate and decide",
         "text": ("It is already measured: press Decide and the gate will say whether it replaces the current version."
                  if cand_measured else "Measure the candidate with the same cases; then the gate decides."),
         "action": "Go to Cycle"},
    ]
    nxt = next((s["id"] for s in steps if not s["done"]), "decide")
    return {"current": current, "next": nxt, "steps": steps, "accepted_versions": max(accepted, 0),
            "candidate": cand["version"] if cand else None}


def overlay(ws: Workspace, h: str, version: str) -> dict:
    """Results of the latest measurement of a version, split by node, to paint them on the canvas."""
    it = last_iteration_of(ws, h, version)
    if not it:
        return {"iteration": None, "nodes": {}}
    spec = ws.get_spec(h, version)
    nodes: dict[str, dict] = {}

    def stat(node_id: str) -> dict:
        return nodes.setdefault(node_id, {"cases": 0, "passed": 0, "calls": 0, "violations": 0, "failed_cases": []})

    for summary in ws.list_runs(h, iteration=it["id"]):
        run = ws.get_run(h, summary["id"])
        passed = bool(run["grade"]["passed"])
        for agent in run["metrics"].get("agents") or []:
            s = stat(agent)
            s["cases"] += 1
            s["passed"] += passed
            if not passed and run["case_id"] not in s["failed_cases"]:
                s["failed_cases"].append(run["case_id"])
        for e in run["trace"]:
            if e["type"] == "routing" and e.get("router"):
                r = stat(e["router"])
                r["cases"] += 1
                r["passed"] += passed
            elif e["type"] == "tool" and e.get("allowed"):
                for t in S.sources(spec, e["agent"], "tool"):
                    if t["data"].get("tool") == e["tool"]:
                        stat(t["id"])["calls"] += 1
            elif e["type"] == "violation" and e.get("agent"):
                stat(e["agent"])["violations"] += 1
    for s_ in nodes.values():
        s_["failed_cases"].sort()
    return {"iteration": it["id"], "version": version, "nodes": nodes,
            "dev": it["summary"]["dev"]["score"], "heldout": it["summary"]["heldout"]["score"]}
