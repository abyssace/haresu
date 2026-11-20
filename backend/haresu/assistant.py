"""The assistant: builds the harness and its evals from what the person asks for in a chat.

It works on a copy of the canvas and emits one event for everything it does, so that the
interface can replay it live. It is not the meta-agent: the assistant helps a person
build; the meta-agent improves versions without seeing held-out.

Events: say {text} · op {op, ...} · evals {added, dev, heldout, total} · test {...}
         saved {version, current} · measured {...} · done {}
"""

from __future__ import annotations

import copy
import json
import re

from . import engine, loop, providers
from . import spec as S
from .store import Workspace

ALLOWED = {
    "input": {"router", "agent"}, "router": {"agent"}, "agent": {"agent", "output"},
    "tool": {"agent"}, "context": {"agent"}, "policy": {"agent"},
}
COLUMN = {"input": 0, "router": 300, "agent": 600, "output": 920,
          "context": 300, "tool": 300, "policy": 920, "meta": 920, "note": 0}
LANE = {"input": 0, "router": 0, "agent": 0, "output": 0, "context": 1, "tool": 1, "policy": 1, "meta": 1, "note": 1}


class Cancelled(Exception):
    pass


class Session:
    """The assistant's working canvas. Each method validates, applies and emits its event."""

    def __init__(self, ws: Workspace, h: str, spec: dict, emit, cancelled=lambda: False, default_model: str = "mock"):
        self.ws, self.h, self.emit, self.cancelled = ws, h, emit, cancelled
        self.default_model = default_model or "mock"
        self.spec = copy.deepcopy(spec)
        self.spec.setdefault("nodes", [])
        self.spec.setdefault("edges", [])

    def _check(self) -> None:
        if self.cancelled():
            raise Cancelled()

    def say(self, text: str) -> None:
        self._check()
        if text.strip():
            self.emit({"type": "say", "text": text.strip()})

    # --- canvas
    def _place(self, type_: str) -> dict:
        """A free position: the task flow on top, resources and control below."""
        flow_bottom = max([n["position"]["y"] for n in self.spec["nodes"] if LANE.get(n["type"]) == 0] or [0])
        x = COLUMN.get(type_, 0)
        y = 0 if LANE.get(type_) == 0 else flow_bottom + 220
        taken = [(n["position"]["x"], n["position"]["y"]) for n in self.spec["nodes"]]
        while any(abs(px - x) < 240 and abs(py - y) < 150 for px, py in taken):
            y += 160
        return {"x": x, "y": y}

    def add_node(self, node_id: str, type_: str, label: str = "", data: dict | None = None) -> str:
        self._check()
        if type_ not in S.NODE_TYPES:
            return f"Error: the type '{type_}' does not exist. Types: {', '.join(S.NODE_TYPES)}."
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", node_id or ""):
            return "Error: the id must be lowercase letters, digits and underscores, starting with a letter."
        if S.node(self.spec, node_id):
            return f"Error: a node '{node_id}' already exists."
        if type_ == "input" and S.nodes_of(self.spec, "input"):
            return "Error: there is already an input node; there can be only one."
        node = {"id": node_id, "type": type_, "label": label or S.NODE_TYPES[type_]["label"], "position": self._place(type_),
                "data": {**copy.deepcopy(S.NODE_TYPES[type_]["defaults"]), **(data or {})}}
        if type_ in ("agent", "meta") and "model" not in (data or {}):
            node["data"]["model"] = self.default_model  # the default model the person chose
        self.spec["nodes"].append(node)
        self.emit({"type": "op", "op": "add_node", "node": node})
        return f"Added '{node_id}'."

    def edit_node(self, node_id: str, label: str | None = None, data: dict | None = None) -> str:
        self._check()
        node = S.node(self.spec, node_id)
        if not node:
            return f"Error: the node '{node_id}' does not exist."
        if label:
            node["label"] = label
        if data:
            node["data"].update(data)
        self.emit({"type": "op", "op": "set_node", "id": node_id, "label": node["label"], "data": node["data"]})
        return f"Edited '{node_id}'."

    def remove_node(self, node_id: str) -> str:
        self._check()
        if not S.node(self.spec, node_id):
            return f"Error: the node '{node_id}' does not exist."
        self.spec = S.apply_ops(self.spec, [{"op": "remove_node", "node_id": node_id}])
        self.emit({"type": "op", "op": "remove_node", "id": node_id})
        return f"Deleted '{node_id}'."

    def connect(self, source: str, target: str) -> str:
        self._check()
        s, t = S.node(self.spec, source), S.node(self.spec, target)
        if not s or not t:
            return f"Error: '{source if not s else target}' does not exist."
        if t["type"] not in ALLOWED.get(s["type"], set()):
            ok = ", ".join(sorted(ALLOWED.get(s["type"], set()))) or "nothing"
            return f"Error: a '{s['type']}' node cannot connect to a '{t['type']}' node. Only to: {ok}."
        if any(e["source"] == source and e["target"] == target for e in self.spec["edges"]):
            return "They were already connected."
        edge = {"id": f"e-{source}-{target}", "source": source, "target": target}
        self.spec["edges"].append(edge)
        self.emit({"type": "op", "op": "add_edge", "edge": edge})
        return f"Connected {source} → {target}."

    def disconnect(self, source: str, target: str) -> str:
        self._check()
        before = len(self.spec["edges"])
        self.spec["edges"] = [e for e in self.spec["edges"] if not (e["source"] == source and e["target"] == target)]
        if len(self.spec["edges"]) == before:
            return "There was no such connection."
        self.emit({"type": "op", "op": "remove_edge", "source": source, "target": target})
        return f"Disconnected {source} → {target}."

    def add_docs(self, context_id: str, docs: list[dict], size: int = 1200) -> str:
        """Loads information into a context (creates it if it does not exist). Long texts are split by paragraph."""
        self._check()
        node = S.node(self.spec, context_id)
        if node and node["type"] != "context":
            return f"Error: '{context_id}' is not a context."
        if not node:
            out = self.add_node(context_id, "context", f"Context: {context_id.replace('_', ' ')}", {"mode": "tool", "docs": []})
            if out.startswith("Error"):
                return out
            node = S.node(self.spec, context_id)
        existing = list(node["data"].get("docs") or [])
        added = []
        for d in docs:
            title, text = str(d.get("title") or d.get("title") or "Document").strip(), str(d.get("text") or d.get("text") or "").strip()
            if not text:
                continue
            parts, cur = [], ""
            for para in [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r", "")) if p.strip()]:
                for i in range(0, len(para), size):
                    piece = para[i:i + size]
                    if cur and len(cur) + len(piece) + 2 > size:
                        parts.append(cur)
                        cur = ""
                    cur = f"{cur}\n\n{piece}" if cur else piece
            if cur:
                parts.append(cur)
            for i, part in enumerate(parts):
                added.append({"id": f"{context_id}-{len(existing) + len(added) + 1}",
                              "title": f"{title} · part {i + 1} of {len(parts)}" if len(parts) > 1 else title, "text": part})
        if not added:
            return "Error: no document had any text."
        self.edit_node(context_id, None, {"docs": existing + added})
        return f"Loaded {len(added)} documents into '{context_id}'. It now has {len(existing) + len(added)}."

    def state(self) -> dict:
        ev = self.ws.get_evals(self.h)
        return {
            "nodes": [{"id": n["id"], "type": n["type"], "name": n["label"], "data": n["data"]} for n in self.spec["nodes"]],
            "connections": [[e["source"], e["target"]] for e in self.spec["edges"]],
            "missing_to_run": S.validate(self.spec),
            "warnings_by_node": S.node_issues(self.spec),
            "eval_cases": [{"id": c["id"], "split": c.get("split", "dev"), "input": c["input"]} for c in ev.get("cases", [])],
            "current_version": self.ws.current(self.h),
        }

    # --- evals
    def add_cases(self, cases: list[dict]) -> str:
        self._check()
        ev = self.ws.get_evals(self.h)
        existing = {c["id"] for c in ev.get("cases", [])}
        added = []
        for c in cases:
            text = str(c.get("input") or "").strip()
            if not text:
                continue
            split = "heldout" if c.get("split") in ("heldout", "held-out") else "dev"
            cid = str(c.get("id") or "")
            k = len(existing) + 1
            while not cid or cid in existing:
                cid = f"{'h' if split == 'heldout' else 'd'}{k}"
                k += 1
            expect = {k_: v for k_, v in (c.get("expect") or {}).items()
                      if k_ in ("contains", "not_contains", "route", "rubric") and v}
            existing.add(cid)
            added.append({"id": cid, "split": split, "input": text, "expect": expect})
        if not added:
            return "Error: no case had an 'input'."
        ev.setdefault("cases", []).extend(added)
        self.ws.save_evals(self.h, ev)
        dev = sum(1 for c in ev["cases"] if c.get("split", "dev") == "dev")
        self.emit({"type": "evals", "added": len(added), "dev": dev, "heldout": len(ev["cases"]) - dev, "total": len(ev["cases"])})
        return f"Added {len(added)} cases. There are now {dev} dev and {len(ev['cases']) - dev} held-out."

    # --- run
    def test(self, task: str) -> str:
        self._check()
        result = engine.run(self.spec, task)
        run_id = self.ws.save_run(self.h, {"kind": "manual", "version": "(assistant)", "input": task,
                                           "output": result["output"], "metrics": result["metrics"], "trace": result["trace"]})
        self.emit({"type": "test", "task": task, "run_id": run_id, **{k: result[k] for k in ("output", "trace", "metrics")}})
        m = result["metrics"]
        return json.dumps({"answer": result["output"], "error": m["error"], "route": m["agents"],
                           "tools": [e["tool"] for e in result["trace"] if e["type"] == "tool"],
                           "violations": m["violations"]}, ensure_ascii=False)

    def save(self, note: str = "", use_now: bool = False) -> str:
        self._check()
        problems = S.validate(self.spec)
        current = self.ws.current(self.h)
        first = not self.ws.get_spec(self.h, current)["nodes"]  # the current version is still empty
        make_current = use_now or first
        v = self.ws.save_version(self.h, self.spec, {"author": "assistant", "parent": current,
                                                     "note": note or "Built by the assistant", "status": "candidate"})
        if make_current:
            self.ws.update_version_meta(self.h, v, status="accepted", promoted_by="assistant (no gate)")
            self.ws.set_current(self.h, v)
        self.emit({"type": "saved", "version": v, "current": make_current})
        tail = " It has pending items: " + " ".join(problems) if problems else ""
        return f"Saved as {v}{' and it is now the current version' if make_current else ', as a candidate'}.{tail}"

    def measure(self) -> str:
        self._check()
        it = loop.measure(self.ws, self.h)
        s = it["summary"]
        self.emit({"type": "measured", "iteration": it["id"], "version": it["version"],
                   "dev": s["dev"]["score"], "heldout": s["heldout"]["score"]})
        fails = [c["case_id"] for c in s["cases"] if c["passed"] < 1 and c["split"] == "dev"]
        return json.dumps({"version": it["version"], "dev": s["dev"]["score"], "heldout": s["heldout"]["score"],
                           "failing_dev_cases": fails}, ensure_ascii=False)


# ---------------------------------------------------------------- template assistant (no key)

_LIST_INTRO = re.compile(r"^.*\b(?:for|about|of|with|topics?|teams?|areas?|para|sobre|de|con|áreas?|temas?|equipos?)\b[:\s]+", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:the|an?|el|la|los|las|un|una|del)\s+", re.IGNORECASE)


def topics_of(text: str) -> list[str]:
    """Extracts a list of topics from phrases like 'support for payments, shipping and account'."""
    best: list[str] = []
    for chunk in re.split(r"[.;\n]", text):
        parts = [p.strip(" .:¿?¡!\"'") for p in re.split(r",|\s+(?:and|y|e)\s+", chunk)]
        if len(parts) < 2:
            continue
        parts[0] = _LIST_INTRO.sub("", parts[0])  # keeps what follows the last introductory word
        parts = [_ARTICLE.sub("", p) for p in parts]
        parts = [p for p in parts if p and len(p.split()) <= 3 and S.tokens(p)]
        if len(parts) > len(best):
            best = parts
    seen, out = set(), []
    for p in best:
        if S.norm(p) not in seen:
            seen.add(S.norm(p))
            out.append(p)
    return out[:6] if len(out) >= 2 else []


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", S.norm(text)).strip("_")[:24] or "x"


def run_template(sess: Session, message: str) -> None:
    """No model: fills in the canvas with a standard structure and writes evals from the documents that exist."""
    sess.say("I'm in template mode (no model): I build a standard structure from your message. "
             "For an AI that understands what you ask and writes prompts and cases tailored to you, choose a real model above.")
    spec = lambda: sess.spec  # noqa: E731  (the canvas changes with every operation)
    topics = topics_of(message)

    if not S.nodes_of(spec(), "input"):
        sess.add_node("input", "input", "Input")
    entry = S.nodes_of(spec(), "input")[0]["id"]

    agents = [a["id"] for a in S.nodes_of(spec(), "agent")]
    if not agents:
        names = topics or [""]
        for t in names:
            aid = f"ag_{_slug(t)}" if t else "agent"
            area = f" for {t}" if t else ""
            sess.add_node(aid, "agent", f"{t[:1].upper()}{t[1:]} agent" if t else "Agent", {"system_prompt": (
                f"You are the agent{area}. Before answering, search the shared context and answer only with what "
                "you find there, in one or two sentences. If there is no information, say so.")})
            agents.append(aid)

    if len(agents) > 1 and not S.nodes_of(spec(), "router"):
        rules = [{"id": f"r{i + 1}", "keywords": S.tokens(t) or [S.norm(t)], "target": f"ag_{_slug(t)}"}
                 for i, t in enumerate(topics) if S.node(spec(), f"ag_{_slug(t)}")]
        sess.add_node("router", "router", "Router", {"rules": rules, "default": agents[-1]})
    routers = S.nodes_of(spec(), "router")

    if not S.nodes_of(spec(), "output"):
        sess.add_node("output", "output", "Output")
    out = S.nodes_of(spec(), "output")[0]["id"]

    if routers:
        sess.connect(entry, routers[0]["id"])
        for a in agents:
            sess.connect(routers[0]["id"], a)
    else:
        sess.connect(entry, agents[0])
    for a in agents:
        if not (S.targets(spec(), a, "output") or S.targets(spec(), a, "agent")):
            sess.connect(a, out)

    search = next((t["id"] for t in S.nodes_of(spec(), "tool") if t["data"].get("tool") == "search_context"), None)
    if not search:
        sess.add_node("search", "tool", "Search the context", {"tool": "search_context"})
        search = "search"
    for a in agents:
        sess.connect(search, a)
        if not S.sources(spec(), a, "context"):
            name = S.node(spec(), a)["label"]
            label = name[: -len(" agent")].lower() if name.endswith(" agent") else "general"
            cid = f"ctx_{_slug(label)}"
            if not S.node(spec(), cid):
                sess.add_node(cid, "context", f"Context: {label}", {"mode": "tool", "docs": []})
            sess.connect(cid, a)

    if not S.nodes_of(spec(), "policy"):
        sess.add_node("governance", "policy", "Global governance",
                      {"locked": True, "max_steps": 6, "max_tool_calls": 8, "max_output_chars": 1500,
                       "redact_patterns": [r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"]})
    if not S.nodes_of(spec(), "meta"):
        sess.add_node("meta", "meta", "Meta-agent")

    # Evals: one per document that already exists, alternating dev and held-out.
    known = {c["input"] for c in sess.ws.get_evals(sess.h).get("cases", [])}
    cases = []
    for c in S.nodes_of(spec(), "context"):
        owners = [a["id"] for a in S.targets(spec(), c["id"], "agent")]
        for d in c["data"].get("docs", []):
            text = str(d.get("text") or "").strip()
            if not text or not owners:
                continue
            question = f"What does the information say about {str(d.get('title') or 'this topic').lower()}?"
            if question in known:
                continue
            first = re.split(r"(?<=[.!?])\s", text)[0][:240]
            expect = {"rubric": first}
            if routers:
                expect["route"] = owners[0]
            cases.append({"split": "dev" if len(cases) % 2 == 0 else "heldout", "input": question, "expect": expect})
    if cases:
        sess.add_cases(cases)
        sess.say(f"I wrote {len(cases)} test cases, one per document. Review them in Evals: a generic question "
                 "per document is a starting point, not a good eval.")
    else:
        sess.say("I did not write test cases because the contexts do not have documents yet. Open each Context node, "
                 "load your files and ask me for the evals again.")

    if not S.validate(spec()):
        sess.test(f"I have a question about {topics[0]}" if topics else "Hello, how can you help me?")
    sess.say("Done. Review the Control section: open “Global governance” and adjust the limits and what gets redacted; "
             "that part is up to you. When you like it, press Save.")


# ---------------------------------------------------------------- assistant with a real model

SYSTEM = """You are the haresu assistant, a visual tool for building agent harnesses. \
The person asks you for what they want in a chat and you build it on the canvas with your tools. \
Every operation you do shows up live on their screen, so work in an order that is easy to follow: \
first the flow (input, router, agents, output), then each agent's resources, then control.

Nodes (type → data):
- input: where the task comes in. Only one.
- router: {"rules": [{"id", "keywords": [...], "regex": "", "target": "<agent id>"}], "default": "<agent id>"}. \
Rules are tried in order; keywords are compared ignoring accents and case.
- agent: {"model": "...", "effort": "medium", "system_prompt": "..."}. The model is set automatically to the one the person chose as default; do not change it unless asked.
- context: {"mode": "tool" | "prompt", "docs": [{"id", "title", "text"}]}. "tool" requires the agent to have search_context.
- tool: {"tool": "search_context" | "calculator" | "write_memory" | "read_memory"}.
- policy: {"locked": true, "max_steps", "max_tool_calls", "max_run_tokens", "max_output_chars", "allowed_tools", \
"blocked_tools", "redact_patterns": [regex]}. With no connections it is global.
- meta: the meta-agent that later improves the harness. One is enough.
- output: the final answer.
- note: {"text"}: a note for the person.

Valid connections: input→router|agent, router→agent, agent→agent|output, tool→agent, context→agent, policy→agent.

Eval cases: {"split": "dev" | "heldout", "input": "<task>", "expect": {"contains": [...], "not_contains": [...], \
"route": "<agent id>", "rubric": "<what the answer must achieve>"}}. The meta-agent will learn from dev and the \
gate will decide with held-out, so spread the cases between both and make held-out ask the same thing with different \
words, not a copy.

Rules:
- Do not invent business facts. Context documents carry only what the person gave you. If information is missing, \
create the context empty and tell them what to load.
- If the default model is "mock", agents can only search the context: with it, a case passes only if the \
answer is in a document. Keep that in mind when writing evals and tell the person if it matters.
- Start with view_state if the canvas is not empty. Before finishing, use view_state to check that nothing is \
missing, try a task with try_task and fix whatever fails.
- Save with save when the graph is complete, unless the person asks only for a draft.
- Governance is the person's decision: create a reasonable global policy and, when you finish, ask them to \
review the Control section.
- Answer in the person's language, briefly. Your last message summarizes what you built and what they should review."""


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_STR = {"type": "string"}
TOOLS = [
    {"name": "view_state", "description": "Returns the current canvas, what is missing to run, the warnings per node and the eval cases.",
     "input_schema": _obj({}, [])},
    {"name": "add_node", "description": "Adds a node. The canvas chooses its position.",
     "input_schema": _obj({"id": {"type": "string", "description": "lowercase_and_underscores"}, "type": _STR, "name": _STR,
                           "data_json": {"type": "string", "description": "JSON object with the node's data; \"{}\" for the defaults."}},
                          ["id", "type", "name", "data_json"])},
    {"name": "edit_node", "description": "Changes the name of a node or merges new fields into its data.",
     "input_schema": _obj({"id": _STR, "name": {"type": "string", "description": "Empty to leave it unchanged."},
                           "data_json": {"type": "string", "description": "JSON object with the fields to change; \"{}\" if none."}},
                          ["id", "name", "data_json"])},
    {"name": "delete_node", "description": "Deletes a node and its connections.", "input_schema": _obj({"id": _STR}, ["id"])},
    {"name": "connect", "description": "Connects two nodes, from the source to the target.",
     "input_schema": _obj({"source": _STR, "target": _STR}, ["source", "target"])},
    {"name": "disconnect", "description": "Removes the connection between two nodes.",
     "input_schema": _obj({"source": _STR, "target": _STR}, ["source", "target"])},
    {"name": "add_cases", "description": "Adds eval cases. They are saved right away.",
     "input_schema": _obj({"cases_json": {"type": "string", "description": "JSON list of cases."}}, ["cases_json"])},
    {"name": "try_task", "description": "Runs a task through the current canvas and returns the answer, the route and the tools used.",
     "input_schema": _obj({"task": _STR}, ["task"])},
    {"name": "save", "description": "Saves the canvas as a new version.",
     "input_schema": _obj({"note": _STR, "use_now": {"type": "boolean", "description": "true: replaces the current version without measuring it."}},
                          ["note", "use_now"])},
    {"name": "measure", "description": "Runs every eval case against the current version (save first).",
     "input_schema": _obj({}, [])},
]
for _t in TOOLS:
    _t["strict"] = True


def _json(text: str, kind):
    try:
        value = json.loads(text or ("{}" if kind is dict else "[]"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"is not valid JSON ({exc})") from exc
    if not isinstance(value, kind):
        raise ValueError("does not have the expected shape")
    return value


def dispatch(sess: Session, name: str, args: dict) -> str:
    try:
        if name == "view_state":
            return json.dumps(sess.state(), ensure_ascii=False)
        if name == "add_node":
            return sess.add_node(args["id"], args["type"], args.get("name", ""), _json(args.get("data_json", ""), dict))
        if name == "edit_node":
            return sess.edit_node(args["id"], args.get("name") or None, _json(args.get("data_json", ""), dict))
        if name == "delete_node":
            return sess.remove_node(args["id"])
        if name == "connect":
            return sess.connect(args["source"], args["target"])
        if name == "disconnect":
            return sess.disconnect(args["source"], args["target"])
        if name == "load_documents":
            return sess.add_docs(args["context"], _json(args.get("documents_json", ""), list))
        if name == "add_cases":
            return sess.add_cases(_json(args.get("cases_json", ""), list))
        if name == "try_task":
            return sess.test(args["task"])
        if name == "save":
            return sess.save(args.get("note", ""), bool(args.get("use_now")))
        if name == "measure":
            return sess.measure()
        return f"Error: the tool '{name}' does not exist."
    except (ValueError, KeyError, loop.LoopError, S.PatchError) as exc:
        return f"Error: {exc}"


def run_llm(sess: Session, model: str, message: str, history: list[dict]) -> None:
    provider = providers.for_model(model)
    messages: list[dict] = [{"role": m["role"], "content": str(m["text"])} for m in history[-8:]
                            if m.get("role") in ("user", "assistant") and str(m.get("text") or "").strip()]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    messages.append({"role": "user", "content": message})
    for _ in range(60):
        sess._check()
        reply = provider.chat(model=model, system=SYSTEM + f"\n\nDefault model for new agents: {sess.default_model}.",
                              messages=messages, tools=TOOLS, effort="medium")
        if reply.stop_reason == "refusal":
            sess.say("The model refused this request.")
            return
        sess.say(reply.text)
        messages.append({"role": "assistant", "content": reply.content})
        if reply.stop_reason == "pause_turn":
            continue
        if not reply.tool_calls:
            return
        if reply.stop_reason == "max_tokens":
            results = [{"type": "tool_result", "tool_use_id": c["id"], "is_error": True,
                        "content": "The call arrived cut off. Repeat it with less content."} for c in reply.tool_calls]
        else:
            results = [{"type": "tool_result", "tool_use_id": c["id"], "content": dispatch(sess, c["name"], c["input"])}
                       for c in reply.tool_calls]
        messages.append({"role": "user", "content": results})
    sess.say("I stopped: I reached the maximum number of steps for a single request. Tell me how to continue.")


def run(ws: Workspace, h: str, spec: dict, message: str, model: str, history: list[dict], emit, cancelled,
        default_model: str = "mock") -> dict:
    sess = Session(ws, h, spec, emit, cancelled, default_model)
    try:
        if providers.resolve(model)[0] == "mock":
            run_template(sess, message)
        else:
            run_llm(sess, model, message, history)
    except Cancelled:
        emit({"type": "say", "text": "Stopped. What I managed to do stays on the canvas."})
    emit({"type": "done"})
    return {"spec": sess.spec}
