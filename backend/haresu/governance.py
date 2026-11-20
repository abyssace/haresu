"""Governance policies: they are combined per agent and the engine enforces them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import spec as S


@dataclass
class Policy:
    max_steps: int = 8
    max_tool_calls: int = 16
    max_run_tokens: int = 0  # 0 = no limit
    allowed_tools: set[str] | None = None  # None = todas
    blocked_tools: set[str] = field(default_factory=set)
    redact_patterns: list[str] = field(default_factory=list)
    max_output_chars: int = 0  # 0 = no limit
    sources: list[str] = field(default_factory=list)

    def tool_allowed(self, name: str) -> tuple[bool, str]:
        if name in self.blocked_tools:
            return False, f"The tool '{name}' is blocked by policy."
        if self.allowed_tools is not None and name not in self.allowed_tools:
            return False, f"The tool '{name}' is not on the allowed list."
        return True, ""

    def redact(self, text: str) -> tuple[str, int]:
        total = 0
        for pat in self.redact_patterns:
            text, n = re.subn(pat, "[REDACTED]", text)
            total += n
        return text, total

    def describe(self) -> dict:
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_run_tokens": self.max_run_tokens,
            "allowed_tools": sorted(self.allowed_tools) if self.allowed_tools is not None else None,
            "blocked_tools": sorted(self.blocked_tools),
            "redact_patterns": self.redact_patterns,
            "max_output_chars": self.max_output_chars,
            "sources": self.sources,
        }


def _min_positive(a: int, b: int) -> int:
    """The strictest limit; 0 means no limit."""
    if not a:
        return b
    if not b:
        return a
    return min(a, b)


def for_agent(spec: dict, agent_id: str) -> Policy:
    """Combines the global policies and the ones connected to the agent. The strictest wins."""
    connected = {p["id"] for p in S.sources(spec, agent_id, "policy")}
    has_edges = {e["source"] for e in spec["edges"]}
    pol = Policy()
    for p in S.nodes_of(spec, "policy"):
        is_global = p["id"] not in has_edges
        if not (is_global or p["id"] in connected):
            continue
        d = p["data"]
        pol.sources.append(p["id"])
        if d.get("max_steps"):
            pol.max_steps = min(pol.max_steps, int(d["max_steps"]))
        if d.get("max_tool_calls"):
            pol.max_tool_calls = min(pol.max_tool_calls, int(d["max_tool_calls"]))
        pol.max_run_tokens = _min_positive(pol.max_run_tokens, int(d.get("max_run_tokens") or 0))
        pol.max_output_chars = _min_positive(pol.max_output_chars, int(d.get("max_output_chars") or 0))
        if d.get("allowed_tools"):
            allowed = set(d["allowed_tools"])
            pol.allowed_tools = allowed if pol.allowed_tools is None else pol.allowed_tools & allowed
        pol.blocked_tools |= set(d.get("blocked_tools") or [])
        for pat in d.get("redact_patterns") or []:
            if pat not in pol.redact_patterns:
                pol.redact_patterns.append(pat)
    return pol


def locked_nodes(spec: dict) -> set[str]:
    """Nodes the meta-agent cannot touch: policies with locked and the meta-agent itself."""
    out = {n["id"] for n in S.nodes_of(spec, "meta")}
    out |= {n["id"] for n in S.nodes_of(spec, "policy") if n["data"].get("locked", True)}
    return out


def check_ops(spec: dict, ops: list[dict]) -> dict:
    """Checks a meta-agent patch against governance.

    Returns {"blocked": [reasons], "needs_human": bool}.
    """
    locked = locked_nodes(spec)
    meta = (S.nodes_of(spec, "meta") or [{"data": {}}])[0]["data"]
    editable = set(meta.get("editable_types") or ["router", "agent", "context", "tool"])
    max_ops = int(meta.get("max_ops") or 8)
    blocked: list[str] = []
    needs_human = False
    if len(ops) > max_ops:
        blocked.append(f"The patch has {len(ops)} operations; the maximum is {max_ops}.")
    for op in ops:
        kind = op.get("op")
        touched: list[tuple[str, str]] = []  # (id, type)
        if kind == "add_node":
            n = op.get("node") or {}
            touched.append((n.get("id", ""), n.get("type", "")))
        elif kind in ("add_edge", "remove_edge"):
            for nid in (op.get("source"), op.get("target")):
                n = S.node(spec, nid or "")
                if n and n["type"] in ("policy", "meta"):
                    touched.append((n["id"], n["type"]))
        else:
            n = S.node(spec, op.get("node_id", ""))
            if n:
                touched.append((n["id"], n["type"]))
        for nid, type_ in touched:
            if nid in locked or type_ == "meta":
                blocked.append(f"{kind}: '{nid}' is locked for the meta-agent.")
            elif type_ == "policy":
                needs_human = True
            elif type_ not in editable and type_ not in ("policy",):
                blocked.append(f"{kind}: the meta-agent cannot edit nodes of type '{type_}'.")
    return {"blocked": blocked, "needs_human": needs_human}
