"""Evals: grades each run and summarizes a version per split (dev / heldout).

A case:
    {"id": "d1", "split": "dev" | "heldout", "input": "...",
     "expect": {"contains": [...], "not_contains": [...], "route": "agent",
                "rubric": "what the answer must achieve, for the judge"}}

Each measurement repeats the cases `gate.seeds` times; the summary has the mean across
repetitions and its standard error. The meta-agent only sees dev cases and runs.
"""

from __future__ import annotations

import statistics

from . import engine
from . import judge as J
from . import spec as S
from .store import Workspace


def grade(case: dict, result: dict, judge_model: str = "mock") -> dict:
    exp = case.get("expect") or {}
    out = S.norm(result["output"])
    checks = []
    for s in exp.get("contains") or []:
        checks.append({"check": f"contains “{s}”", "ok": S.norm(s) in out})
    for s in exp.get("not_contains") or []:
        checks.append({"check": f"does not contain “{s}”", "ok": S.norm(s) not in out})
    if exp.get("route"):
        got = result["metrics"].get("route")
        checks.append({"check": f"route = {exp['route']}", "ok": got == exp["route"], "detail": f"went to {got}"})
    if str(exp.get("rubric") or "").strip():
        ok, reason = J.judge(judge_model, case["input"], result["output"], exp["rubric"])
        checks.append({"check": f"judge: {exp['rubric']}", "ok": ok, "detail": reason, "judge": judge_model})
    if result["metrics"].get("error"):
        checks.append({"check": "no error", "ok": False, "detail": result["metrics"]["error"]})
    score = sum(c["ok"] for c in checks) / len(checks) if checks else 1.0
    return {"checks": checks, "score": round(score, 4), "passed": all(c["ok"] for c in checks)}


def _mean_se(values: list[float]) -> tuple[float | None, float]:
    """Mean and standard error across repetitions."""
    if not values:
        return None, 0.0
    se = statistics.stdev(values) / len(values) ** 0.5 if len(values) > 1 else 0.0
    return round(statistics.fmean(values), 4), round(se, 4)


def evaluate(ws: Workspace, h: str, version: str, iteration: str, on_progress=None) -> dict:
    """Runs every case against a version. Saves each run and returns the summary."""
    spec = ws.get_spec(h, version)
    ev = ws.get_evals(h)
    cases = ev.get("cases", [])
    seeds = max(1, min(int((ev.get("gate") or {}).get("seeds") or 1), 10))
    judge_model = (ev.get("judge") or {}).get("model") or "mock"
    total, done = len(cases) * seeds, 0
    runs: list[dict] = []
    for seed in range(1, seeds + 1):
        for case in cases:
            result = engine.run(spec, case["input"])
            g = grade(case, result, judge_model)
            split = case.get("split", "dev")
            run_id = ws.save_run(h, {
                "id": f"{iteration}-{case['id']}" + (f"-s{seed}" if seed > 1 else ""),
                "kind": "eval", "version": version, "iteration": iteration, "seed": seed,
                "case_id": case["id"], "split": split, "input": case["input"],
                "output": result["output"], "metrics": result["metrics"], "grade": g, "trace": result["trace"],
            })
            runs.append({"case_id": case["id"], "split": split, "seed": seed, "run_id": run_id,
                         "score": g["score"], "passed": g["passed"],
                         "violations": result["metrics"]["violations"],
                         "tokens": result["metrics"]["tokens_in"] + result["metrics"]["tokens_out"]})
            done += 1
            if on_progress:
                on_progress(done, total)

    def summary(split: str) -> dict:
        items = [r for r in runs if r["split"] == split]
        per_seed = [statistics.fmean(r["score"] for r in items if r["seed"] == s)
                    for s in range(1, seeds + 1) if any(r["seed"] == s for r in items)]
        score, se = _mean_se(per_seed)
        n = len({r["case_id"] for r in items})
        return {
            "n": n, "score": score, "se": se,
            "pass_rate": round(sum(r["passed"] for r in items) / len(items), 4) if items else None,
            "violations": sum(r["violations"] for r in items),
            "tokens": sum(r["tokens"] for r in items),
        }

    rows = []
    for case in cases:
        items = [r for r in runs if r["case_id"] == case["id"]]
        rows.append({"case_id": case["id"], "split": case.get("split", "dev"),
                     "score": round(statistics.fmean(r["score"] for r in items), 4),
                     "passed": sum(r["passed"] for r in items) / len(items),
                     "run_ids": [r["run_id"] for r in items]})
    return {"seeds": seeds, "judge": judge_model, "dev": summary("dev"), "heldout": summary("heldout"), "cases": rows}
