"""The engine: runs a task through the graph and leaves the complete run record."""

from __future__ import annotations

import re
import time

from . import governance, providers
from . import spec as S
from .tools import REGISTRY, RunContext, search_docs


class Trace:
    def __init__(self):
        self.events: list[dict] = []
        self._t0 = time.time()

    def add(self, type_: str, **data) -> None:
        self.events.append({"ms": int((time.time() - self._t0) * 1000), "type": type_, **data})


def route(router: dict, text: str, reachable: list[str], docs_by_agent: dict[str, list[dict]] | None = None) -> tuple[str, str]:
    """Applies the routing rules in order. Returns (agent, reason).

    If no rule matches and the router has `auto_context`, it picks the agent whose context is most similar
    to the task; only when nobody is similar does it use the default destination."""
    ntext = S.norm(text)
    for rule in router["data"].get("rules", []):
        if rule.get("target") not in reachable:
            continue
        for kw in rule.get("keywords") or []:
            if S.norm(kw) and S.norm(kw) in ntext:
                return rule["target"], f"rule '{rule.get('id')}': word '{kw}'"
        if rule.get("regex") and re.search(rule["regex"], text, re.IGNORECASE):
            return rule["target"], f"rule '{rule.get('id')}': regex"
    if router["data"].get("auto_context") and docs_by_agent:
        scored = [(search_docs(docs_by_agent.get(a, []), text, k=1), a) for a in reachable]
        scored = [(hits[0][0], a) for hits, a in scored if hits]
        if scored:
            best = max(scored, key=lambda x: x[0])
            return best[1], f"no rule matched; the context of {best[1]} is the most similar"
    default = router["data"].get("default")
    if default in reachable:
        return default, "default destination"
    if reachable:
        return reachable[0], "no rule or default destination: first connected agent"
    return "", "the router has no connected agents"


def _clip(text: str, n: int = 600) -> str:
    text = str(text)
    return text if len(text) <= n else text[:n] + "…"


def run(spec: dict, task: str) -> dict:
    """Runs a task. Returns {output, trace, metrics}; it never raises because of agent failures."""
    trace = Trace()
    m = {"tokens_in": 0, "tokens_out": 0, "tool_calls": 0, "steps": 0, "violations": 0, "redactions": 0,
         "route": "", "agents": [], "error": ""}
    memory: dict[str, str] = {}
    output = ""
    trace.add("start", input=task)

    def violation(kind: str, detail: str, agent: str = "") -> None:
        m["violations"] += 1
        trace.add("violation", kind=kind, detail=detail, agent=agent)

    try:
        problems = S.validate(spec)
        if problems:
            raise providers.ProviderError("The graph is not valid: " + " ".join(problems))
        entry = S.nodes_of(spec, "input")[0]
        routers = S.targets(spec, entry["id"], "router")
        if routers:
            reachable = [n["id"] for n in S.targets(spec, routers[0]["id"], "agent")]
            docs = {a: [d for c in S.sources(spec, a, "context") for d in c["data"].get("docs", [])] for a in reachable}
            agent_id, why = route(routers[0], task, reachable, docs)
            trace.add("routing", router=routers[0]["id"], target=agent_id, reason=why)
            if not agent_id:
                raise providers.ProviderError(why)
        else:
            agent_id = S.targets(spec, entry["id"], "agent")[0]["id"]
            trace.add("routing", router=None, target=agent_id, reason="direct connection")
        m["route"] = agent_id

        text = task
        visited: set[str] = set()
        while agent_id and agent_id not in visited:
            visited.add(agent_id)
            m["agents"].append(agent_id)
            text = _run_agent(spec, agent_id, text, memory, trace, m, violation)
            nxt = S.targets(spec, agent_id, "agent")
            agent_id = nxt[0]["id"] if nxt else ""
            if agent_id:
                trace.add("handoff", to=agent_id)
        output = text
    except providers.ProviderError as exc:
        m["error"] = str(exc)
        trace.add("error", detail=str(exc))

    trace.add("end", output=_clip(output, 2000))
    m["ms"] = trace.events[-1]["ms"]
    return {"output": output, "trace": trace.events, "metrics": m, "memory": memory}


def _run_agent(spec, agent_id, task, memory, trace, m, violation) -> str:
    agent = S.node(spec, agent_id)
    data = agent["data"]
    policy = governance.for_agent(spec, agent_id)

    # Shared context
    docs: list[dict] = []
    prompt_docs: list[dict] = []
    for c in S.sources(spec, agent_id, "context"):
        docs += c["data"].get("docs", [])
        if c["data"].get("mode") == "prompt":
            prompt_docs += c["data"].get("docs", [])
    ctx = RunContext(docs=docs, memory=memory)

    # Connected tools, filtered by policy
    tools = []
    for t in S.sources(spec, agent_id, "tool"):
        name = t["data"].get("tool")
        if name not in REGISTRY:
            trace.add("unknown_tool", agent=agent_id, tool=name)
            continue
        ok, reason = policy.tool_allowed(name)
        if not ok:
            trace.add("tool_filtered", agent=agent_id, tool=name, reason=reason)
            continue
        if name not in [x["name"] for x in tools]:
            tools.append(REGISTRY[name].definition())

    system = data.get("system_prompt", "")
    if prompt_docs:
        system += "\n\n## Shared context\n\n" + "\n\n".join(
            f"{d.get('title', '')}: {d.get('text', '')}" for d in prompt_docs
        )
    trace.add("agent_start", agent=agent_id, model=data.get("model", "mock"),
              tools=[t["name"] for t in tools], docs=len(docs), policy=policy.describe())

    provider = providers.for_model(data.get("model", "mock"))
    messages: list[dict] = [{"role": "user", "content": task}]
    answer = ""
    finished = False
    for _ in range(policy.max_steps):
        if policy.max_run_tokens and m["tokens_in"] + m["tokens_out"] > policy.max_run_tokens:
            violation("budget", f"The limit of {policy.max_run_tokens} tokens per run was exceeded.", agent_id)
            break
        reply = provider.chat(model=data.get("model", "mock"), system=system, messages=messages,
                              tools=tools, effort=data.get("effort", "medium"))
        m["steps"] += 1
        m["tokens_in"] += reply.tokens_in
        m["tokens_out"] += reply.tokens_out
        trace.add("model", agent=agent_id, stop_reason=reply.stop_reason, text=_clip(reply.text),
                  tool_calls=[c["name"] for c in reply.tool_calls],
                  tokens_in=reply.tokens_in, tokens_out=reply.tokens_out)
        messages.append({"role": "assistant", "content": reply.content})
        if reply.stop_reason == "pause_turn":
            continue
        if not reply.tool_calls:
            answer = reply.text
            finished = True
            break
        results = []
        for call in reply.tool_calls:
            ok, reason = policy.tool_allowed(call["name"])
            if ok and call["name"] not in [t["name"] for t in tools]:
                ok, reason = False, f"The tool '{call['name']}' is not connected to this agent."
            if ok and m["tool_calls"] >= policy.max_tool_calls:
                ok, reason = False, f"The limit of {policy.max_tool_calls} tool calls was reached."
            if not ok:
                violation("tool", reason, agent_id)
                result, is_error = f"The tool did not run: {reason}", True
            else:
                m["tool_calls"] += 1
                result, is_error = REGISTRY[call["name"]].fn(ctx, call["input"]), False
            trace.add("tool", agent=agent_id, tool=call["name"], input=call["input"],
                      result=_clip(result), allowed=ok)
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": result,
                            **({"is_error": True} if is_error else {})})
        messages.append({"role": "user", "content": results})
    if not finished and not answer:
        violation("steps", f"The agent did not finish in {policy.max_steps} steps.", agent_id)

    redacted, n = policy.redact(answer)
    if n:
        m["redactions"] += n
        trace.add("redaction", agent=agent_id, count=n)
        answer = redacted
    if policy.max_output_chars and len(answer) > policy.max_output_chars:
        violation("output", f"The answer went over {policy.max_output_chars} characters and was cut.", agent_id)
        answer = answer[: policy.max_output_chars]
    trace.add("agent_end", agent=agent_id, output=_clip(answer, 2000))
    return answer
