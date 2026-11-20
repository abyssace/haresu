"""The promotion gate: decides whether a candidate replaces the current version."""

from __future__ import annotations

from . import governance
from . import spec as S
from .store import DEFAULT_GATE


def decide(base: dict, cand: dict, base_spec: dict, cand_spec: dict, gate: dict | None, approved_by: str = "") -> dict:
    """base and cand are summaries from evaluate(). Returns {accepted, needs_human, rules}."""
    g = {**DEFAULT_GATE, **(gate or {})}
    rules = []

    def rule(name: str, ok: bool, detail: str) -> None:
        rules.append({"rule": name, "ok": bool(ok), "detail": detail})

    b_h, c_h = base["heldout"]["score"], cand["heldout"]["score"]
    if b_h is None or c_h is None:
        rule("held-out improves", False, "There are no held-out cases; without them the gate cannot decide.")
    else:
        gain = round(c_h - b_h, 4)
        # With several repetitions, the improvement also has to exceed the noise between them.
        noise = round((base["heldout"].get("se", 0) ** 2 + cand["heldout"].get("se", 0) ** 2) ** 0.5, 4)
        need = max(g["min_heldout_gain"], noise)
        rule("held-out improves", gain >= need,
             f"{b_h:.3f} → {c_h:.3f} (change {gain:+.3f}; minimum {need:+.3f}"
             + (f", which is the noise between repetitions)" if noise > g["min_heldout_gain"] else ")"))

    b_d, c_d = base["dev"]["score"], cand["dev"]["score"]
    if b_d is not None and c_d is not None:
        drop = round(b_d - c_d, 4)
        rule("dev no cae", drop <= g["max_dev_drop"],
             f"{b_d:.3f} → {c_d:.3f} (allowed drop {g['max_dev_drop']:.3f})")

    b_v = base["dev"]["violations"] + base["heldout"]["violations"]
    c_v = cand["dev"]["violations"] + cand["heldout"]["violations"]
    rule("no new violations", c_v - b_v <= g["max_new_violations"], f"{b_v} → {c_v} policy violations")

    d = S.diff(base_spec, cand_spec)
    locked = governance.locked_nodes(base_spec)
    touched_locked = sorted(locked & set(d["changed_nodes"] + d["removed_nodes"]))
    locked_edges = [e for e in d["added_edges"] + d["removed_edges"] if set(e) & locked]
    rule("locked nodes untouched", not touched_locked and not locked_edges,
         "no changes" if not touched_locked and not locked_edges
         else f"changed: {', '.join(touched_locked) or 'connections of a locked node'}")

    policy_ids = {n["id"] for n in S.nodes_of(base_spec, "policy")} | {n["id"] for n in S.nodes_of(cand_spec, "policy")}
    policy_touched = sorted(policy_ids & set(d["changed_nodes"] + d["added_nodes"] + d["removed_nodes"]))
    policy_touched += [f"{a}→{b}" for a, b in d["added_edges"] + d["removed_edges"] if {a, b} & policy_ids]
    needs_human = bool(policy_touched) and g["policy_changes_need_human"]
    if needs_human:
        rule("governance change approved by a person", bool(approved_by.strip()),
             f"touches {', '.join(policy_touched)}; " + (f"approved by {approved_by}" if approved_by.strip() else "approval missing"))

    return {
        "accepted": all(r["ok"] for r in rules),
        "needs_human": needs_human and not approved_by.strip(),
        "approved_by": approved_by.strip(),
        "rules": rules,
    }
