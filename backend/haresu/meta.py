"""The meta-agent: reads the dev run records and proposes a patch to the harness.

What it sees: the current graph, the dev cases and runs, and for each previous decision
only the accepted/rejected bit. It never sees held-out and cannot touch evals, locked
policies or its own node.
"""

from __future__ import annotations

import json

from . import governance, providers
from . import spec as S
from .store import Workspace
from .tools import search_docs

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string", "description": "What failed in dev and why, citing runs."},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["set_node_data", "set_label", "add_router_rule",
                                                       "remove_router_rule", "add_node", "remove_node",
                                                       "add_edge", "remove_edge"]},
                    "args_json": {"type": "string",
                                  "description": "The operation's arguments as a JSON object in text."},
                },
                "required": ["op", "args_json"],
                "additionalProperties": False,
            },
        },
        "prediction": {"type": "string", "description": "What should improve if the patch works."},
        "risk": {"type": "string", "description": "What could get worse."},
    },
    "required": ["diagnosis", "ops", "prediction", "risk"],
    "additionalProperties": False,
}

SYSTEM = """You are the meta-agent of an agent harness. Your job is to propose ONE small patch to the \
harness so that the next version solves tasks better.

The harness is a graph: an input, a router with rules, agents with their prompt, tools, \
shared context and governance policies. A promotion gate will decide whether your version replaces \
the current one by measuring cases you cannot see (held-out). That is why a patch that only memorizes \
the dev cases is useless: look for the general cause of each failure.

How to work:
1. Read the graph with read_spec.
2. List the dev runs with list_dev_runs and open the failing ones with read_run. The trace says which \
agent it was routed to, which tools it called, what they returned and what it answered.
3. Diagnose: was it routed wrongly? did the agent lack the context? did the prompt not ask for what was needed? \
was a tool missing?
4. Deliver the patch with submit_patch. Stop there.

Operations (args_json is a JSON object in text):
- set_node_data {"node_id", "path", "value"}: changes a field of data, e.g. path "system_prompt".
- set_label {"node_id", "value"}
- add_router_rule {"node_id", "rule": {"keywords": [...], "regex": "", "target": "<agent id>"}}
- remove_router_rule {"node_id", "rule_id"}
- add_node {"node": {"id", "type", "label", "data"}} with type agent | tool | context | router
- remove_node {"node_id"}
- add_edge {"source", "target"} / remove_edge {"source", "target"}

Limits: you cannot touch the locked nodes that read_spec lists, nor the evals. A patch that touches \
a policy needs human approval. Prefer few changes with a clear prediction."""


def _args(op: dict) -> dict:
    """Model operation ({op, args_json}) to a spec.apply_ops operation."""
    try:
        args = json.loads(op.get("args_json") or "{}")
    except json.JSONDecodeError as exc:
        raise S.PatchError(f"{op.get('op')}: args_json is not valid JSON ({exc}).") from exc
    if not isinstance(args, dict):
        raise S.PatchError(f"{op.get('op')}: args_json must be an object.")
    return {"op": op.get("op"), **args}


def _dev_runs(ws: Workspace, h: str, iteration: str) -> list[dict]:
    return ws.list_runs(h, iteration=iteration, split="dev")


def propose_mock(ws: Workspace, h: str, base_version: str, iteration: str) -> dict:
    """Mock meta-agent: heuristics over the dev runs.

    For every failed case it locates the document that answers the question and checks,
    in this order, why the agent that handled it did not use it:

      1. the task was routed to an agent that cannot see that document  -> routing rule
      2. no reachable agent sees the document                           -> connect the context to the agent
      3. the agent sees it but has no way to search it                  -> connect search_context
      4. a policy removed its tool                                      -> reports it; a person decides that
      5. nobody has a document that answers                             -> reports it; it does not invent content
    """
    spec = ws.get_spec(h, base_version)
    cases = {c["id"]: c for c in ws.get_evals(h).get("cases", []) if c.get("split", "dev") == "dev"}
    routers = S.nodes_of(spec, "router")
    router = routers[0] if routers else None
    reachable = {a["id"] for a in S.targets(spec, router["id"], "agent")} if router else set()
    known = {S.norm(k) for rule in (router["data"].get("rules", []) if router else []) for k in rule.get("keywords") or []}

    failed, seen = [], set()
    for r in sorted(_dev_runs(ws, h, iteration), key=lambda r: r["id"]):
        if not r["grade"]["passed"] and r["case_id"] not in seen:
            seen.add(r["case_id"])
            failed.append(r)

    notes: list[str] = []
    rules: dict[str, list[str]] = {}
    edges: list[tuple[str, str]] = []
    new_nodes: list[dict] = []

    def want_edge(source: str, target: str) -> None:
        if (source, target) not in edges and not any(e["source"] == source and e["target"] == target for e in spec["edges"]):
            edges.append((source, target))

    for r in failed:
        case = cases.get(r["case_id"])
        if not case:
            continue
        exp = case.get("expect") or {}
        query = " ".join(exp.get("contains") or []) + f" {exp.get('rubric') or ''} {case['input']}"
        went = r["metrics"].get("route") or ""
        best_ctx, best_doc, best_score = None, None, 0
        for c in S.nodes_of(spec, "context"):
            hits = search_docs(c["data"].get("docs", []), query, k=1)
            if hits and hits[0][0] > best_score:
                best_ctx, best_doc, best_score = c, hits[0][1], hits[0][0]
        if not best_ctx:
            notes.append(f"{r['id']}: no context has a document that answers this task. "
                         "Content is missing; a person adds that.")
            continue
        title = best_doc.get("title") or best_doc.get("id")
        owners = {a["id"] for a in S.targets(spec, best_ctx["id"], "agent")}

        if went not in owners:
            candidates = sorted(owners & reachable) if router else []
            doc_stems = {S.stem(t) for t in S.tokens(f"{best_doc.get('title', '')} {best_doc.get('text', '')}")}
            kws = [t for t in S.tokens(case["input"]) if S.stem(t) in doc_stems and t not in known]
            if candidates and kws:
                target = candidates[0]
                rules.setdefault(target, [])
                rules[target] += [k for k in kws if k not in rules[target]]
                notes.append(f"{r['id']}: it was routed to {went}, but the document that answers (“{title}”) "
                             f"is only seen by {target}. I am adding a routing rule.")
            elif went:
                want_edge(best_ctx["id"], went)
                notes.append(f"{r['id']}: {went} handled the task but does not see the context “{best_ctx.get('label')}”, "
                             f"where “{title}” is. I am connecting that context to the agent.")
            continue

        # The agent sees the document. Could it use it?
        trace = ws.get_run(h, r["id"])["trace"]
        if any(e["type"] == "tool_filtered" and e.get("tool") == "search_context" for e in trace):
            notes.append(f"{r['id']}: {went} sees “{title}”, but a policy blocks search_context for it. "
                         "I am not touching it: a governance change is decided by a person.")
            continue
        has_search = any(t["data"].get("tool") == "search_context" for t in S.sources(spec, went, "tool"))
        if best_ctx["data"].get("mode", "tool") == "tool" and not has_search:
            existing = [t for t in S.nodes_of(spec, "tool") if t["data"].get("tool") == "search_context"]
            if existing:
                want_edge(existing[0]["id"], went)
            else:
                if not new_nodes:
                    pos = S.node(spec, went).get("position", {"x": 0, "y": 0})
                    new_nodes.append({"id": "search_ctx", "type": "tool", "label": "search_context",
                                      "position": {"x": pos["x"], "y": pos["y"] - 140},
                                      "data": {"tool": "search_context"}})
                want_edge("search_ctx", went)
            notes.append(f"{r['id']}: {went} sees “{title}” but it has no search_context to search it. I am connecting it.")
            continue
        notes.append(f"{r['id']}: routing and context look correct and it still failed. "
                     "I have no rule for this case; review the agent's prompt or the case.")

    ops: list[dict] = [{"op": "add_router_rule", "node_id": router["id"], "rule": {"keywords": kws, "target": target}}
                       for target, kws in rules.items()]
    ops += [{"op": "add_node", "node": n} for n in new_nodes]
    ops += [{"op": "add_edge", "source": s_, "target": t_} for s_, t_ in edges]
    predictions = []
    if rules:
        predictions.append("tasks with those words will reach the agent that has the context")
    if edges:
        predictions.append("agents will be able to find the documents they cannot reach today")
    risks = []
    if rules:
        risks.append("a keyword that is too general can divert tasks from other topics")
    if edges:
        risks.append("more context per agent can bring less precise search results")
    return {
        "diagnosis": "\n".join(notes) or "No dev case failed; there is nothing to propose.",
        "ops": ops,
        "prediction": ("The dev cases that were failing, and similar ones, should pass: " + "; ".join(predictions) + "."
                       if ops else "No changes."),
        "risk": (("; ".join(risks)).capitalize() + "." if ops else "None."),
        "author": "meta (mock)",
    }


def propose_llm(ws: Workspace, h: str, base_version: str, iteration: str, meta_node: dict) -> dict:
    """Meta-agent with a real model (from any provider) and read access to the dev runs."""
    spec = ws.get_spec(h, base_version)
    data = meta_node["data"]
    model = data.get("model") or "claude-opus-5-5"
    provider = providers.for_model(model)
    allowed_ids = {r["id"] for r in _dev_runs(ws, h, iteration)}
    history = [{"version": v["version"], "status": v.get("status")} for v in ws.list_versions(h)]

    def obj(props: dict, required: list[str]) -> dict:
        return {"type": "object", "properties": props, "required": required, "additionalProperties": False}

    tools = [
        {"name": "read_spec", "description": "Returns the harness's current graph and the list of locked nodes.",
         "input_schema": obj({}, [])},
        {"name": "list_dev_runs",
         "description": "Lists the dev run records of the latest evaluation: case, task, answer, route, grade.",
         "input_schema": obj({}, [])},
        {"name": "read_run", "description": "Returns a complete dev run, with its step-by-step trace.",
         "input_schema": obj({"run_id": {"type": "string"}}, ["run_id"])},
        {"name": "submit_patch", "description": "Delivers the final patch. Call it only once, at the end.",
         "input_schema": PATCH_SCHEMA, "strict": True},
    ]

    def call(name: str, args: dict) -> str:
        if name == "read_spec":
            return json.dumps({"spec": spec, "locked_nodes": sorted(governance.locked_nodes(spec)),
                               "version_history": history}, ensure_ascii=False)
        if name == "list_dev_runs":
            return json.dumps([{k: r.get(k) for k in ("id", "case_id", "input", "output", "grade")}
                               | {"route": r["metrics"].get("route"), "violations": r["metrics"].get("violations")}
                               for r in _dev_runs(ws, h, iteration)], ensure_ascii=False)
        if name == "read_run":
            if args.get("run_id") not in allowed_ids:
                return "Access denied: you can only read dev runs of the latest evaluation."
            return json.dumps(ws.get_run(h, args["run_id"]), ensure_ascii=False)
        return f"Unknown tool: {name}"

    system = SYSTEM + (f"\n\nTeam instructions:\n{data['instructions']}" if data.get("instructions") else "")
    messages: list[dict] = [{"role": "user", "content": "Propose the patch for the next version of the harness."}]
    for _ in range(30):
        reply = provider.chat(model=model, system=system, messages=messages, tools=tools, effort=data.get("effort") or "high")
        if reply.stop_reason == "refusal":
            raise providers.ProviderError("The model refused the meta-agent's request.")
        messages.append({"role": "assistant", "content": reply.content})
        if reply.stop_reason == "pause_turn":
            continue
        if not reply.tool_calls:
            messages.append({"role": "user", "content": "Deliver the patch with submit_patch."})
            continue
        results = []
        for c in reply.tool_calls:
            if c["name"] == "submit_patch":
                patch = dict(c["input"])
                try:
                    patch["ops"] = [_args(op) for op in patch.get("ops", [])]
                except (S.PatchError, AttributeError, TypeError) as exc:
                    results.append({"type": "tool_result", "tool_use_id": c["id"], "is_error": True,
                                    "content": f"The patch does not have the expected shape: {exc}"})
                    continue
                patch["author"] = f"meta ({reply.model or model})"
                return patch
            results.append({"type": "tool_result", "tool_use_id": c["id"], "content": call(c["name"], c["input"])})
        messages.append({"role": "user", "content": results})
    raise providers.ProviderError("The meta-agent did not deliver a patch in 30 steps.")


def propose(ws: Workspace, h: str, base_version: str, iteration: str) -> dict:
    spec = ws.get_spec(h, base_version)
    metas = S.nodes_of(spec, "meta")
    model = metas[0]["data"].get("model", "mock") if metas else "mock"
    if providers.resolve(model)[0] == "mock":
        return propose_mock(ws, h, base_version, iteration)
    return propose_llm(ws, h, base_version, iteration, metas[0])
