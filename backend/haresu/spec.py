"""The harness as data: a graph of nodes and edges.

A harness is a dict:

    {"name": str, "description": str,
     "nodes": [{"id", "type", "label", "position": {"x", "y"}, "data": {...}}],
     "edges": [{"id", "source", "target"}]}

Edges get their meaning from the types they join:

    input   -> router | agent     where the task comes in
    router  -> agent              possible routing destinations
    agent   -> agent              chain: one's output is the other's input
    agent   -> output             the final answer
    tool    -> agent              the agent can use that tool
    context -> agent              the agent sees that shared context
    policy  -> agent              the policy applies to that agent
    policy  (no edges)            global policy: applies to everyone
"""

from __future__ import annotations

import copy
import re
import unicodedata

# Node types, with what the UI palette needs to create them.
NODE_TYPES: dict[str, dict] = {
    "input": {
        "label": "Input",
        "help": "Where the task comes in. Connect it to a router or to an agent.",
        "defaults": {},
    },
    "router": {
        "label": "Router",
        "help": "Routing rules: decide which agent handles the task.",
        "defaults": {"rules": [], "default": "", "auto_context": False},
    },
    "agent": {
        "label": "Agent",
        "help": "A model with its prompt. 'mock' runs without a key; any other id uses that provider's API.",
        "defaults": {
            "model": "mock",
            "effort": "medium",
            "system_prompt": "You are an assistant. Answer with what the shared context says.",
        },
    },
    "tool": {
        "label": "Tool",
        "help": "A tool that the connected agents can call.",
        "defaults": {"tool": "search_context"},
    },
    "context": {
        "label": "Shared context",
        "help": "Documents shared by the connected agents. 'prompt' pastes them into the prompt; 'tool' leaves them for search_context.",
        "defaults": {"mode": "tool", "docs": []},
    },
    "policy": {
        "label": "Governance policy",
        "help": "Limits and rules. With no connections it is global. 'locked' stops the meta-agent from touching it.",
        "defaults": {
            "locked": True,
            "max_steps": 6,
            "max_tool_calls": 8,
            "max_run_tokens": 0,
            "allowed_tools": [],
            "blocked_tools": [],
            "redact_patterns": [],
            "max_output_chars": 0,
        },
    },
    "meta": {
        "label": "Meta-agent",
        "help": "Reads the dev run records and proposes the next version of the harness.",
        "defaults": {
            "model": "mock",
            "effort": "high",
            "instructions": "",
            "editable_types": ["router", "agent", "context", "tool"],
            "max_ops": 8,
        },
    },
    "note": {
        "label": "Note",
        "help": "A note for people. It does not affect execution.",
        "defaults": {"text": ""},
    },
    "output": {
        "label": "Output",
        "help": "The final answer.",
        "defaults": {},
    },
}


def norm(text: str) -> str:
    """Lowercase and without accents, to compare text."""
    text = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


STOPWORDS = set(
    """a al algo como con cual cuando de del donde el ella en es esa ese eso esta este esto
    favor hay la las le lo los me mi mis muy necesito no nos o para pero pido por que
    quiero se si sin su sus te tu un una uno y ya yo cuanto cuanta fue ser son the of to and
    entre sobre desde hasta durante segun tras ante bajo contra hacia
    about after again also been before being between both could does doing down during each from
    further have having here into itself just more most other over same should some such than that
    their them then there these they this those through under until very what when where which
    while will with would your""".split()
)


def tokens(text: str) -> list[str]:
    """Meaningful words, normalized."""
    return [t for t in re.findall(r"[a-z0-9ñ]+", norm(text)) if len(t) >= 4 and t not in STOPWORDS]


def stem(token: str) -> str:
    """Rough stem: the first 4 characters."""
    return token[:4]


def node(spec: dict, node_id: str) -> dict | None:
    return next((n for n in spec["nodes"] if n["id"] == node_id), None)


def nodes_of(spec: dict, type_: str) -> list[dict]:
    return [n for n in spec["nodes"] if n["type"] == type_]


def sources(spec: dict, target_id: str, type_: str | None = None) -> list[dict]:
    """Nodes with an edge into target_id."""
    out = []
    for e in spec["edges"]:
        if e["target"] == target_id:
            n = node(spec, e["source"])
            if n and (type_ is None or n["type"] == type_):
                out.append(n)
    return out


def targets(spec: dict, source_id: str, type_: str | None = None) -> list[dict]:
    """Nodes that source_id points to."""
    out = []
    for e in spec["edges"]:
        if e["source"] == source_id:
            n = node(spec, e["target"])
            if n and (type_ is None or n["type"] == type_):
                out.append(n)
    return out


def validate(spec: dict) -> list[str]:
    """Problems of the graph. Empty list if it is fine."""
    problems: list[str] = []
    ids = [n.get("id") for n in spec.get("nodes", [])]
    if len(ids) != len(set(ids)):
        problems.append("There are repeated node ids.")
    for n in spec.get("nodes", []):
        if n.get("type") not in NODE_TYPES:
            problems.append(f"Node {n.get('id')}: unknown type '{n.get('type')}'.")
    for e in spec.get("edges", []):
        if e["source"] not in ids or e["target"] not in ids:
            problems.append(f"Edge {e.get('id')}: it points to a node that does not exist.")
    inputs = nodes_of(spec, "input")
    agents = nodes_of(spec, "agent")
    outputs = nodes_of(spec, "output")
    if not inputs:
        problems.append("Add an Input node: that is where the task comes in.")
    elif len(inputs) > 1:
        problems.append("There can be only one Input node.")
    if not agents:
        problems.append("Add at least one Agent.")
    if len(inputs) == 1 and agents and not (
        targets(spec, inputs[0]["id"], "router") or targets(spec, inputs[0]["id"], "agent")
    ):
        problems.append("Connect the Input to a Router or to an Agent.")
    if not outputs:
        problems.append("Add an Output node.")
    elif agents and not any(targets(spec, a["id"], "output") for a in agents):
        problems.append("Connect at least one Agent to the Output.")
    if len(inputs) == 1:
        for r in targets(spec, inputs[0]["id"], "router"):
            if not targets(spec, r["id"], "agent"):
                problems.append(f"Connect the router “{r.get('label')}” to the agents it chooses from.")
    for r in nodes_of(spec, "router"):
        reachable = {n["id"] for n in targets(spec, r["id"], "agent")}
        default = r["data"].get("default")
        if default and default not in reachable:
            problems.append(f"Router {r['id']}: the default destination '{default}' is not connected.")
        for rule in r["data"].get("rules", []):
            if rule.get("target") not in reachable:
                problems.append(
                    f"Router {r['id']}: rule '{rule.get('id')}' points to '{rule.get('target')}', which is not connected."
                )
            if rule.get("regex"):
                try:
                    re.compile(rule["regex"])
                except re.error as exc:
                    problems.append(f"Router {r['id']}: invalid regex in '{rule.get('id')}': {exc}")
    for p in nodes_of(spec, "policy"):
        for pat in p["data"].get("redact_patterns", []):
            try:
                re.compile(pat)
            except re.error as exc:
                problems.append(f"Policy {p['id']}: invalid pattern: {exc}")
    return problems


def node_issues(spec: dict) -> dict[str, list[str]]:
    """Warnings per node: what each one is missing to be of any use. The interface paints them on the node."""
    out: dict[str, list[str]] = {}

    def warn(node_id: str, text: str) -> None:
        out.setdefault(node_id, []).append(text)

    incoming = {n["id"]: sources(spec, n["id"]) for n in spec.get("nodes", [])}
    for n in spec.get("nodes", []):
        t, d, nid = n.get("type"), n.get("data") or {}, n["id"]
        if t == "input" and not (targets(spec, nid, "router") or targets(spec, nid, "agent")):
            warn(nid, "Connect it to a Router or to an Agent.")
        elif t == "output" and not incoming[nid]:
            warn(nid, "No agent delivers its answer here.")
        elif t == "router":
            reachable = {a["id"] for a in targets(spec, nid, "agent")}
            if not incoming[nid]:
                warn(nid, "Connect the Input to this router.")
            if not reachable:
                warn(nid, "Connect it to the agents it chooses from.")
            elif not d.get("rules") and len(reachable) > 1:
                warn(nid, "With no rules, every task goes to the same agent.")
            if reachable and not d.get("default"):
                warn(nid, "Choose a default destination for tasks that match no rule.")
            for rule in d.get("rules") or []:
                if rule.get("target") not in reachable:
                    warn(nid, f"Rule “{rule.get('id')}” points to an agent that is not connected.")
                elif not (rule.get("keywords") or rule.get("regex")):
                    warn(nid, f"Rule “{rule.get('id')}” has no keywords or regex.")
        elif t == "agent":
            if not any(s["type"] in ("input", "router", "agent") for s in incoming[nid]):
                warn(nid, "No task reaches this agent.")
            if not (targets(spec, nid, "output") or targets(spec, nid, "agent")):
                warn(nid, "Its answer does not reach the Output.")
            if not str(d.get("system_prompt") or "").strip():
                warn(nid, "It has no prompt.")
        elif t in ("tool", "context"):
            users = targets(spec, nid, "agent")
            if not users:
                warn(nid, "It is not connected to any agent.")
            if t == "context":
                if not d.get("docs"):
                    warn(nid, "It has no documents.")
                elif d.get("mode", "tool") == "tool":
                    for a in users:
                        if not any(x["data"].get("tool") == "search_context" for x in sources(spec, a["id"], "tool")):
                            warn(nid, f"“{a.get('label')}” does not have “Search the context” to query these documents.")
    return out


def diff(a: dict, b: dict) -> dict:
    """What changed from graph a to graph b."""
    na = {n["id"]: n for n in a["nodes"]}
    nb = {n["id"]: n for n in b["nodes"]}
    ea = {(e["source"], e["target"]) for e in a["edges"]}
    eb = {(e["source"], e["target"]) for e in b["edges"]}

    def content(n: dict) -> dict:
        return {"type": n["type"], "label": n.get("label"), "data": n.get("data")}

    return {
        "added_nodes": sorted(set(nb) - set(na)),
        "removed_nodes": sorted(set(na) - set(nb)),
        "changed_nodes": sorted(i for i in set(na) & set(nb) if content(na[i]) != content(nb[i])),
        "added_edges": sorted([list(e) for e in eb - ea]),
        "removed_edges": sorted([list(e) for e in ea - eb]),
    }


class PatchError(Exception):
    pass


def apply_ops(spec: dict, ops: list[dict]) -> dict:
    """Applies patch operations and returns a new graph.

    Operations:
      set_node_data   {node_id, path, value}     data[path] = value
      set_label       {node_id, value}
      add_router_rule {node_id, rule}            rule = {id?, keywords?, regex?, target}
      remove_router_rule {node_id, rule_id}
      add_node        {node}
      remove_node     {node_id}                  also deletes its edges
      add_edge        {source, target}
      remove_edge     {source, target}
    """
    out = copy.deepcopy(spec)
    for op in ops:
        kind = op.get("op")
        if kind in ("set_node_data", "set_label", "add_router_rule", "remove_router_rule", "remove_node"):
            n = node(out, op.get("node_id", ""))
            if n is None:
                raise PatchError(f"{kind}: the node '{op.get('node_id')}' does not exist.")
        if kind == "set_node_data":
            if not op.get("path"):
                raise PatchError("set_node_data: 'path' is missing.")
            n["data"][op["path"]] = op.get("value")
        elif kind == "set_label":
            n["label"] = str(op.get("value", ""))
        elif kind == "add_router_rule":
            if n["type"] != "router":
                raise PatchError(f"add_router_rule: '{n['id']}' is not a router.")
            rule = dict(op.get("rule") or {})
            rules = n["data"].setdefault("rules", [])
            if not rule.get("id"):
                rule["id"] = f"r{len(rules) + 1}"
                taken = {r.get("id") for r in rules}
                k = len(rules) + 1
                while rule["id"] in taken:
                    k += 1
                    rule["id"] = f"r{k}"
            rules.append(rule)
        elif kind == "remove_router_rule":
            n["data"]["rules"] = [r for r in n["data"].get("rules", []) if r.get("id") != op.get("rule_id")]
        elif kind == "add_node":
            new = copy.deepcopy(op.get("node") or {})
            if not new.get("id") or new.get("type") not in NODE_TYPES:
                raise PatchError("add_node: the node needs an 'id' and a valid 'type'.")
            if node(out, new["id"]):
                raise PatchError(f"add_node: '{new['id']}' already exists.")
            new.setdefault("label", NODE_TYPES[new["type"]]["label"])
            new.setdefault("position", {"x": 80, "y": 80})
            new["data"] = {**copy.deepcopy(NODE_TYPES[new["type"]]["defaults"]), **(new.get("data") or {})}
            out["nodes"].append(new)
        elif kind == "remove_node":
            out["nodes"] = [x for x in out["nodes"] if x["id"] != n["id"]]
            out["edges"] = [e for e in out["edges"] if n["id"] not in (e["source"], e["target"])]
        elif kind == "add_edge":
            s, t = op.get("source"), op.get("target")
            if not node(out, s or "") or not node(out, t or ""):
                raise PatchError(f"add_edge: '{s}' or '{t}' does not exist.")
            if not any(e["source"] == s and e["target"] == t for e in out["edges"]):
                out["edges"].append({"id": f"e-{s}-{t}", "source": s, "target": t})
        elif kind == "remove_edge":
            out["edges"] = [
                e for e in out["edges"] if not (e["source"] == op.get("source") and e["target"] == op.get("target"))
            ]
        else:
            raise PatchError(f"Unknown operation: '{kind}'.")
    return out
