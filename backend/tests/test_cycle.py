"""The complete cycle with the mock model, with no network or keys."""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from haresu import engine, governance, loop
from haresu import spec as S
from haresu.api import create_app
from haresu.examples import EXAMPLES
from haresu.store import Workspace


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    spec, evals = EXAMPLES["support"]()
    w.create_harness("Support", spec, evals)
    return w


def test_routing_and_shared_context():
    spec, _ = EXAMPLES["support"]()
    r = engine.run(spec, "I was charged twice, a duplicate payment")
    assert r["metrics"]["route"] == "ag_payments"
    assert "72 hours" in r["output"]
    # With no rule for refunds, it falls to the general agent, which does not see the payments context.
    r = engine.run(spec, "I want a refund")
    assert r["metrics"]["route"] == "ag_general"
    assert "5 a 10" not in r["output"]


def test_governance_redacts_and_blocks_tools():
    spec, _ = EXAMPLES["support"]()
    r = engine.run(spec, "My email is ana@example.com and my package arrived damaged")
    assert "ana@example.com" not in r["output"] and "[REDACTED]" in r["output"]
    assert any(e["type"] == "redaction" for e in r["trace"])

    S.node(spec, "governance")["data"]["blocked_tools"] = ["search_context"]
    r = engine.run(spec, "I was charged twice, a duplicate payment")
    assert any(e["type"] == "tool_filtered" for e in r["trace"])
    assert "72 hours" not in r["output"]


def test_run_record_has_every_step():
    spec, _ = EXAMPLES["support"]()
    types = [e["type"] for e in engine.run(spec, "Where is my package?")["trace"]]
    assert types == ["start", "routing", "agent_start", "model", "tool", "model", "agent_end", "end"]


def test_cycle_improves_and_the_gate_accepts(ws):
    log = loop.cycle(ws, "support", rounds=2)
    rows = loop.trend(ws, "support")
    assert rows[0]["status"] == "baseline" and rows[0]["heldout"] == pytest.approx(0.6)
    assert rows[1]["status"] == "accepted" and rows[1]["heldout"] == 1.0
    assert ws.current("support") == "v001"
    # Second round: there are no more dev failures, the meta-agent proposes nothing.
    assert "no candidate" in log[-1]["step"]


def test_meta_agent_does_not_see_heldout(ws):
    loop.measure(ws, "support")
    p = loop.propose(ws, "support")
    assert p["version"] == "v001"
    assert "h1" not in p["patch"]["diagnosis"] and "iter-001-d3" in p["patch"]["diagnosis"]


def test_worse_candidate_is_rejected(ws):
    loop.measure(ws, "support")
    spec = ws.get_spec("support", "v000")
    S.node(spec, "router")["data"]["rules"] = []
    v = ws.save_version("support", spec, {"author": "human", "parent": "v000"})
    loop.measure(ws, "support", v, "candidate")
    d = loop.decide(ws, "support", v)
    assert not d["accepted"] and ws.current("support") == "v000"


def test_governance_change_needs_a_person(ws):
    loop.measure(ws, "support")
    spec = S.apply_ops(ws.get_spec("support", "v000"), [
        {"op": "add_router_rule", "node_id": "router",
         "rule": {"keywords": ["refund", "return"], "target": "ag_payments"}},
        {"op": "add_node", "node": {"id": "pol2", "type": "policy", "data": {"locked": False, "max_steps": 4}}},
    ])
    v = ws.save_version("support", spec, {"author": "human", "parent": "v000"})
    loop.measure(ws, "support", v, "candidate")
    assert loop.decide(ws, "support", v)["needs_human"]
    assert ws.current("support") == "v000"
    assert loop.decide(ws, "support", v, approved_by="Ana")["accepted"]
    assert ws.current("support") == "v001"


def test_meta_agent_cannot_touch_locked_nodes():
    spec, _ = EXAMPLES["support"]()
    check = governance.check_ops(spec, [{"op": "set_node_data", "node_id": "governance", "path": "redact_patterns", "value": []}])
    assert check["blocked"]
    check = governance.check_ops(spec, [{"op": "set_node_data", "node_id": "meta", "path": "max_ops", "value": 99}])
    assert check["blocked"]
    check = governance.check_ops(spec, [{"op": "set_node_data", "node_id": "ag_payments", "path": "system_prompt", "value": "x"}])
    assert not check["blocked"] and not check["needs_human"]


def test_validation_and_diff():
    spec, _ = EXAMPLES["support"]()
    assert S.validate(spec) == []
    bad = copy.deepcopy(spec)
    bad["edges"] = [e for e in bad["edges"] if e["source"] != "input"]
    assert S.validate(bad)
    new = S.apply_ops(spec, [{"op": "set_node_data", "node_id": "ag_payments", "path": "effort", "value": "high"}])
    assert S.diff(spec, new)["changed_nodes"] == ["ag_payments"]


def test_api(tmp_path):
    import time

    client = TestClient(create_app(tmp_path, static_dir=tmp_path / "nothing"))
    assert client.post("/api/harnesses", json={"name": "Demo", "example": "support"}).status_code == 200
    r = client.post("/api/harnesses/demo/run", json={"input": "I forgot my password"}).json()
    assert "recovery link" in r["output"]
    job = client.post("/api/harnesses/demo/loop/cycle", json={"rounds": 1}).json()
    for _ in range(100):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done", job
    t = client.get("/api/harnesses/demo/trend").json()
    assert t["current"] == "v001" and len(t["rows"]) == 2
    v = client.get("/api/harnesses/demo/versions/v001").json()
    assert v["diff"]["changed_nodes"] == ["router"]
    assert client.get("/api/harnesses/nope").status_code == 404


def _fresh(tmp_path, mutate):
    w = Workspace(tmp_path)
    spec, evals = EXAMPLES["support"]()
    mutate(spec, evals)
    w.create_harness("Support", spec, evals)
    return w


def test_meta_connects_missing_context(tmp_path):
    def mutate(spec, evals):
        spec["edges"] = [e for e in spec["edges"] if not (e["source"] == "ctx_shipping" and e["target"] == "ag_shipping")]
    w = _fresh(tmp_path, mutate)
    loop.measure(w, "support")
    p = loop.propose(w, "support")
    assert {"op": "add_edge", "source": "ctx_shipping", "target": "ag_shipping"} in p["patch"]["ops"]


def test_meta_connects_missing_tool(tmp_path):
    def mutate(spec, evals):
        spec["edges"] = [e for e in spec["edges"] if not (e["source"] == "search" and e["target"] == "ag_shipping")]
    w = _fresh(tmp_path, mutate)
    loop.cycle(w, "support", rounds=1)
    ops = w.get_version_meta("support", "v001")["patch"]["ops"]
    assert {"op": "add_edge", "source": "search", "target": "ag_shipping"} in ops
    assert loop.trend(w, "support")[-1]["heldout"] == 1.0


def test_meta_does_not_touch_governance(tmp_path):
    def mutate(spec, evals):
        S.node(spec, "governance")["data"]["blocked_tools"] = ["search_context"]
    w = _fresh(tmp_path, mutate)
    loop.measure(w, "support")
    p = loop.propose(w, "support")
    # Reports the block, but no operation touches the policy.
    assert "governance change is decided by a person" in p["patch"]["diagnosis"]
    assert "governance" not in str(p["patch"]["ops"])


def test_repetitions_and_judge(tmp_path):
    def mutate(spec, evals):
        evals["gate"]["seeds"] = 3
        evals["cases"].append({"id": "d7", "split": "dev", "input": "What are the support hours?",
                               "expect": {"rubric": "Says that the hours are Monday to Saturday, 8 am to 8 pm."}})
    w = _fresh(tmp_path, mutate)
    it = loop.measure(w, "support")
    assert it["summary"]["seeds"] == 3 and it["summary"]["dev"]["se"] == 0.0
    assert len(w.list_runs("support", iteration=it["id"])) == 12 * 3
    run = w.get_run("support", f"{it['id']}-d7")
    assert run["grade"]["passed"] and run["grade"]["checks"][0]["check"].startswith("judge")


def test_guide(ws):
    g = loop.guide(ws, "support")
    assert g["next"] == "measure"
    loop.measure(ws, "support")
    assert loop.guide(ws, "support")["next"] == "propose"
    loop.propose(ws, "support")
    g = loop.guide(ws, "support")
    assert g["next"] == "decide" and g["candidate"] == "v001"


def _mini(tools: list[str], docs=None, chain=False) -> dict:
    """A minimal harness built from scratch, as someone would on the canvas."""
    spec = S.apply_ops({"name": "m", "description": "", "nodes": [], "edges": []}, [
        {"op": "add_node", "node": {"id": "in", "type": "input"}},
        {"op": "add_node", "node": {"id": "a1", "type": "agent"}},
        {"op": "add_node", "node": {"id": "out", "type": "output"}},
        {"op": "add_edge", "source": "in", "target": "a1"},
        {"op": "add_edge", "source": "a1" if not chain else "a2", "target": "out"} if not chain else
        {"op": "add_node", "node": {"id": "a2", "type": "agent"}},
    ])
    if chain:
        spec = S.apply_ops(spec, [{"op": "add_edge", "source": "a1", "target": "a2"},
                                  {"op": "add_edge", "source": "a2", "target": "out"}])
    for t in tools:
        spec = S.apply_ops(spec, [{"op": "add_node", "node": {"id": f"t_{t}", "type": "tool", "data": {"tool": t}}},
                                  {"op": "add_edge", "source": f"t_{t}", "target": "a1"}])
    if docs:
        spec = S.apply_ops(spec, [{"op": "add_node", "node": {"id": "ctx", "type": "context", "data": {"docs": docs}}},
                                  {"op": "add_edge", "source": "ctx", "target": "a1"}])
    return spec


def test_empty_canvas_lists_what_is_missing():
    spec, evals = EXAMPLES["blank"]()
    assert spec["nodes"] == [] and evals["cases"] == []
    assert S.validate(spec)[0].startswith("Add an Input node")
    assert S.validate(_mini([])) == []


def test_every_tool_works():
    docs = [{"id": "p", "title": "Price", "text": "Each box costs 12 dollars."}]
    r = engine.run(_mini(["search_context", "calculator"], docs), "What is the price of 3 * 12 boxes?")
    used = {e["tool"]: e["result"] for e in r["trace"] if e["type"] == "tool"}
    assert used["calculator"] == "36" and "12 dollars" in used["search_context"]
    assert "12 dollars" in r["output"] and "36" in r["output"]

    # The shared scratchpad passes information from one agent to the next.
    spec = _mini(["search_context", "write_memory"], docs, chain=True)
    spec = S.apply_ops(spec, [{"op": "add_node", "node": {"id": "t_read", "type": "tool", "data": {"tool": "read_memory"}}},
                              {"op": "add_edge", "source": "t_read", "target": "a2"}])
    r = engine.run(spec, "What is the price of the box?")
    assert r["metrics"]["agents"] == ["a1", "a2"] and r["memory"]["last_answer"].startswith("Each box")
    assert [e["tool"] for e in r["trace"] if e["type"] == "tool"] == ["search_context", "write_memory", "read_memory"]


def test_an_incomplete_graph_is_not_measured(tmp_path):
    w = Workspace(tmp_path)
    spec, evals = EXAMPLES["blank"]()
    evals["cases"] = [{"id": "d1", "split": "dev", "input": "hello", "expect": {}}]
    w.create_harness("Empty", spec, evals)
    with pytest.raises(loop.LoopError, match="Input"):
        loop.measure(w, "empty")
    assert loop.guide(w, "empty")["next"] == "graph"


def test_warnings_per_node():
    spec = _mini([], [{"id": "p", "title": "Price", "text": "12 dollars"}])
    issues = S.node_issues(spec)
    assert any("Search the context" in t for t in issues["ctx"])
    spec = S.apply_ops(spec, [{"op": "add_node", "node": {"id": "loose", "type": "tool"}},
                              {"op": "add_node", "node": {"id": "n1", "type": "note", "data": {"text": "hello"}}}])
    assert S.node_issues(spec)["loose"] == ["It is not connected to any agent."]
    assert S.validate(spec) == [] and engine.run(spec, "hello")["metrics"]["error"] == ""
    assert S.node_issues(EXAMPLES["support"]()[0]) == {}


def test_results_per_node(ws):
    it = loop.measure(ws, "support")
    o = loop.overlay(ws, "support", "v000")
    assert o["iteration"] == it["id"]
    assert o["nodes"]["ag_general"]["failed_cases"] == ["d3", "d4", "h1", "h2"]
    assert o["nodes"]["ag_payments"] == {"cases": 2, "passed": 2, "calls": 0, "violations": 0, "failed_cases": []}
    assert o["nodes"]["search"]["calls"] == 11 and o["nodes"]["router"]["cases"] == 11
    assert loop.overlay(ws, "support", "v000")["nodes"] and not ws.list_iterations("support")[0].get("decision")


def test_api_export_import_duplicate_delete(tmp_path):
    c = TestClient(create_app(tmp_path, static_dir=tmp_path / "nothing"))
    c.post("/api/harnesses", json={"name": "Demo", "example": "support"})
    exported = c.get("/api/harnesses/demo/export").json()
    assert c.post("/api/harnesses", json={"name": "Imported copy", "import": exported}).status_code == 200
    assert len(c.get("/api/harnesses/imported-copy/evals").json()["cases"]) == 11
    assert c.post("/api/harnesses", json={"name": "Malo", "import": {"spec": {"nodes": "x"}}}).status_code == 400
    assert c.post("/api/harnesses/demo/duplicate", json={"name": "Demo two"}).json()["id"] == "demo-two"
    assert c.patch("/api/harnesses/demo", json={"name": "Other name"}).json()["name"] == "Other name"
    assert c.delete("/api/harnesses/demo-two").status_code == 200
    assert [x["id"] for x in c.get("/api/harnesses").json()] == ["demo", "imported-copy"]
    v = c.post("/api/validate", json={"spec": {"nodes": [{"id": "t", "type": "tool", "data": {}}], "edges": []}}).json()
    assert "t" in v["by_node"]


from haresu import assistant  # noqa: E402


def _assist(tmp_path, message, mutate=None):
    w = Workspace(tmp_path)
    spec, evals = EXAMPLES["blank"]()
    if mutate:
        mutate(spec)
    w.create_harness("New", spec, evals)
    events = []
    out = assistant.run(w, "new", spec, message, "mock", [], events.append, lambda: False)
    return w, out["spec"], events


def test_assistant_builds_from_scratch(tmp_path):
    w, spec, events = _assist(tmp_path, "I want a support harness for payments, shipping and account")
    assert assistant.topics_of("I want a support harness for payments, shipping and account") == ["payments", "shipping", "account"]
    assert S.validate(spec) == []
    assert [a["id"] for a in S.nodes_of(spec, "agent")] == ["ag_payments", "ag_shipping", "ag_account"]
    router = S.nodes_of(spec, "router")[0]
    assert router["data"]["rules"][0] == {"id": "r1", "keywords": ["payments"], "target": "ag_payments"}
    assert S.nodes_of(spec, "policy") and S.nodes_of(spec, "meta")
    # Every operation came out as an event, in an order that can be replayed on an empty canvas.
    replay = {"nodes": [], "edges": []}
    for e in events:
        if e["type"] == "op" and e["op"] == "add_node":
            replay["nodes"].append(e["node"])
        elif e["type"] == "op" and e["op"] == "add_edge":
            assert {e["edge"]["source"], e["edge"]["target"]} <= {n["id"] for n in replay["nodes"]}
            replay["edges"].append(e["edge"])
    assert replay["nodes"] == spec["nodes"] and replay["edges"] == spec["edges"]
    assert len({(n["position"]["x"], n["position"]["y"]) for n in spec["nodes"]}) == len(spec["nodes"])
    assert events[-1] == {"type": "done"} and any(e["type"] == "test" for e in events)
    # Without documents it does not invent evals.
    assert w.get_evals("new")["cases"] == []
    assert engine.run(spec, "I have a problem with my payments")["metrics"]["route"] == "ag_payments"


def test_assistant_single_agent_and_evals_from_documents(tmp_path):
    def mutate(spec):
        spec["nodes"].append({"id": "ctx", "type": "context", "label": "Manual", "position": {"x": 300, "y": 220},
                              "data": {"mode": "tool", "docs": [
                                  {"id": "a", "title": "Opening hours", "text": "We are open Monday to Saturday, from 8 am to 8 pm. Not on Sundays."},
                                  {"id": "b", "title": "Returns", "text": "Returns are accepted within 30 days with the receipt."}]}})
        spec["nodes"].append({"id": "my_agent", "type": "agent", "label": "My agent", "position": {"x": 600, "y": 0},
                              "data": {"model": "mock", "system_prompt": "Answer with the context."}})
        spec["edges"].append({"id": "e-ctx-my_agent", "source": "ctx", "target": "my_agent"})
    w, spec, events = _assist(tmp_path, "Complete it and write the evals", mutate)
    assert S.validate(spec) == [] and S.node_issues(spec) == {}
    assert [a["id"] for a in S.nodes_of(spec, "agent")] == ["my_agent"] and not S.nodes_of(spec, "router")
    cases = w.get_evals("new")["cases"]
    assert [c["split"] for c in cases] == ["dev", "heldout"] and cases[0]["expect"]["rubric"].startswith("We are open")
    # The cases it wrote pass with the harness it built.
    w.save_version("new", spec, {"author": "human"})
    w.set_current("new", "v001")
    assert loop.measure(w, "new")["summary"]["dev"]["score"] == 1.0


def test_assistant_validates_and_saves(tmp_path):
    w = Workspace(tmp_path)
    spec, evals = EXAMPLES["blank"]()
    w.create_harness("New", spec, evals)
    events = []
    sess = assistant.Session(w, "new", spec, events.append)
    assert assistant.dispatch(sess, "add_node", {"id": "t", "type": "tool", "name": "", "data_json": "{}"}).startswith("Added")
    assert assistant.dispatch(sess, "add_node", {"id": "r", "type": "router", "name": "", "data_json": "{}"}).startswith("Added")
    assert "cannot connect" in assistant.dispatch(sess, "connect", {"source": "t", "target": "r"})
    assert "JSON" in assistant.dispatch(sess, "add_node", {"id": "x", "type": "agent", "name": "", "data_json": "{mal"})
    assert "id" in assistant.dispatch(sess, "add_node", {"id": "Mal Id", "type": "agent", "name": "", "data_json": "{}"})
    assert "Saved as v001 and it is now the current version" in assistant.dispatch(sess, "save", {"note": "", "use_now": False})
    assert "candidate" in assistant.dispatch(sess, "save", {"note": "", "use_now": False})
    assert w.current("new") == "v001"
    out = assistant.dispatch(sess, "add_cases", {"cases_json": json.dumps([{"input": "hello", "split": "heldout", "expect": {"rubric": "greets", "x": 1}}])})
    assert "0 dev and 1 held-out" in out and w.get_evals("new")["cases"][0] == {"id": "h1", "split": "heldout", "input": "hello", "expect": {"rubric": "greets"}}


def test_assistant_can_be_stopped(tmp_path):
    w = Workspace(tmp_path)
    spec, evals = EXAMPLES["blank"]()
    w.create_harness("New", spec, evals)
    events = []
    out = assistant.run(w, "new", spec, "support for payments and shipping", "mock", [], events.append,
                        lambda: sum(e["type"] == "op" for e in events) >= 2)
    assert len(out["spec"]["nodes"]) == 2 and events[-2]["text"].startswith("Stopped") and events[-1] == {"type": "done"}


def test_api_assistant(tmp_path):
    import time

    c = TestClient(create_app(tmp_path, static_dir=tmp_path / "nothing"))
    c.post("/api/harnesses", json={"name": "New"})
    job = c.post("/api/harnesses/new/assistant", json={"message": "sales and support", "spec": {"nodes": [], "edges": []}}).json()
    seen = []
    for _ in range(100):
        r = c.get(f"/api/jobs/{job['id']}?after={len(seen)}").json()
        seen += r["events"]
        if r["status"] != "running":
            break
        time.sleep(0.02)
    assert r["status"] == "done" and seen[-1] == {"type": "done"} and r["next"] == len(seen)
    assert sum(e["type"] == "op" and e["op"] == "add_node" for e in seen) == 10
    assert c.post("/api/harnesses/new/assistant", json={"message": "", "spec": {"nodes": [], "edges": []}}).status_code == 400


# ---------------------------------------------------------------- keys and routing by context

from haresu import credentials  # noqa: E402

FAKE = "sk-ant-api03-" + "A" * 40
OPENAI = "sk-proj-" + "O" * 40


def _no_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                "OPENROUTER_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_keys_per_provider_with_permissions_and_never_in_full(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    Workspace(tmp_path)
    assert credentials.source("anthropic") == "none" and not credentials.has_credentials("openai")
    for provider, bad, msg in [("anthropic", "hello", "sk-ant-"), ("anthropic", "sk-ant-admin01-" + "B" * 40, "admin"),
                               ("anthropic", "sk-ant-corta", "incomplete"), ("openai", FAKE, "belongs to Anthropic"),
                               ("openai", "sk-or-" + "Q" * 40, "belongs to OpenRouter"), ("groq", OPENAI, "gsk_"), ("nope", FAKE, "does not exist")]:
        with pytest.raises(credentials.KeyError_, match=msg):
            credentials.add(provider, "x", bad)
    a1 = credentials.add("anthropic", "Personal", FAKE)
    a2 = credentials.add("anthropic", "Work", "sk-ant-api03-" + "C" * 40)
    o1 = credentials.add("openai", "Mine", OPENAI)
    with pytest.raises(credentials.KeyError_, match="already"):
        credentials.add("openai", "other", OPENAI)
    d = {p["id"]: p for p in credentials.describe()["providers"]}
    assert d["anthropic"]["active"] == a2 and [k["last4"] for k in d["anthropic"]["keys"]] == ["AAAA", "CCCC"]
    assert d["openai"]["source"] == "saved" and d["groq"]["source"] == "none"
    dump = json.dumps(credentials.describe())
    assert FAKE not in dump and OPENAI not in dump and "C" * 40 not in dump and "O" * 40 not in dump
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600
    # Each provider uses its own key.
    assert credentials.key_for("anthropic").endswith("CCCC") and credentials.key_for("openai") == OPENAI
    credentials.activate("anthropic", a1)
    assert credentials.key_for("anthropic") == FAKE and credentials.key_for("openai") == OPENAI
    with pytest.raises(credentials.KeyError_, match="does not exist"):
        credentials.activate("anthropic", o1)  # an OpenAI key cannot be activated for Anthropic
    credentials.remove(a1)
    assert credentials.source("anthropic") == "none" and credentials.source("openai") == "saved"


def test_secrets_from_an_earlier_version_are_read_as_anthropic(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    Workspace(tmp_path)
    (tmp_path / "secrets.json").write_text(json.dumps({"keys": [{"id": "k1", "name": "Old", "key": FAKE}], "active": "k1"}))
    assert credentials.key_for("anthropic") == FAKE and credentials.source("openai") == "none"


def test_the_chosen_key_beats_the_environment_one(tmp_path, monkeypatch):
    import anthropic

    _no_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "E" * 40)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "G" * 30)
    Workspace(tmp_path)
    assert credentials.source("anthropic") == "env" and credentials.key_for("google").endswith("GGGG")
    seen = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: seen.append(kw.get("api_key")) or object())
    credentials.client()
    credentials.add("anthropic", "Chosen", FAKE)
    credentials.client()
    credentials.activate("anthropic", None)  # go back to the environment one
    credentials.client()
    assert seen == [None, FAKE, None]


def test_api_keys_and_model_resolution(tmp_path, monkeypatch):
    _no_env(monkeypatch)
    c = TestClient(create_app(tmp_path, static_dir=tmp_path / "nothing"))
    cat = c.get("/api/catalog").json()
    assert cat["has_credentials"] is False and {p["id"] for p in cat["providers"]} >= {"anthropic", "openai", "google", "openrouter"}
    r = c.post("/api/settings/keys", json={"provider": "openai", "name": "Mine", "key": OPENAI})
    assert r.status_code == 200 and OPENAI not in r.text
    assert c.post("/api/settings/keys", json={"provider": "openai", "key": FAKE}).status_code == 400
    assert c.post("/api/settings/active", json={"provider": "anthropic", "id": "xx"}).status_code == 400
    # Each model gets its own provider's key.
    a = c.get("/api/resolve", params={"model": "openai:something"}).json()
    assert a["ok"] and a["provider"] == "openai" and a["source"] == "saved" and a["last4"] == "OOOO" and a["key_name"] == "Mine"
    b = c.get("/api/resolve", params={"model": "claude-opus-5-5"}).json()
    assert not b["ok"] and b["provider"] == "anthropic" and "There is no key for Anthropic" in b["error"]
    assert c.get("/api/resolve", params={"model": "mock"}).json()["ok"]
    assert "does not exist" in c.get("/api/resolve", params={"model": "zzz:m"}).json()["error"]
    # The warning appears on the node that uses a provider without a key.
    spec = {"nodes": [{"id": "a1", "type": "agent", "label": "A", "data": {"model": "groq:x", "system_prompt": "p"}},
                      {"id": "a2", "type": "agent", "label": "B", "data": {"model": "openai:x", "system_prompt": "p"}}], "edges": []}
    by_node = c.post("/api/validate", json={"spec": spec}).json()["by_node"]
    assert any("Groq and there is no key" in t for t in by_node["a1"]) and not any("key" in t for t in by_node.get("a2", []))
    assert c.post("/api/settings/check", json={"provider": "anthropic"}).status_code == 400
    assert c.post("/api/settings/active", json={"provider": "openai", "id": None}).json()["providers"][1]["source"] == "none"


def test_testing_a_key_distinguishes_the_errors(tmp_path, monkeypatch):
    import anthropic
    import httpx

    _no_env(monkeypatch)
    Workspace(tmp_path)
    credentials.add("anthropic", "Mine", FAKE)
    credentials.add("openai", "Mine", OPENAI)

    def fake(exc):
        class C:
            def __init__(self, **kw): pass
            class models:  # noqa: N801
                @staticmethod
                def list(limit=1):
                    if exc: raise exc
        return C
    req = httpx.Request("GET", "https://api.anthropic.com/v1/models")
    resp = httpx.Response(401, request=req)
    monkeypatch.setattr(anthropic, "Anthropic", fake(None))
    assert credentials.check("anthropic").startswith("It works")
    monkeypatch.setattr(anthropic, "Anthropic", fake(anthropic.AuthenticationError("x", response=resp, body=None)))
    assert "rejected" in credentials.check("anthropic")
    monkeypatch.setattr(anthropic, "Anthropic", fake(anthropic.APIConnectionError(request=req)))
    assert "connect" in credentials.check("anthropic")
    for status, body, expected in [(200, "{}", "It works"), (401, "{}", "rejected"), (403, "{}", "rejected"), (429, "{}", "rate limit"),
                                   (400, "API key not valid", "rejected"), (404, "{}", "cannot confirm")]:
        monkeypatch.setattr(credentials, "_get", lambda url, key, s=status, b=body: (s, b))
        assert expected in credentials.check("openai"), status


def test_router_chooses_by_context_when_no_rule_matches():
    spec, _ = EXAMPLES["support"]()
    s = S.node(spec, "router")
    s["data"]["rules"] = []
    task = "I want my money back for a product"
    assert engine.run(spec, task)["metrics"]["route"] == "ag_general"  # with no rule it goes to the default destination
    s["data"]["auto_context"] = True
    r = engine.run(spec, task)
    assert r["metrics"]["route"] == "ag_payments"
    assert any("most similar" in e["reason"] for e in r["trace"] if e["type"] == "routing")
    # If nobody is similar, the default destination still applies.
    assert engine.run(spec, "xyzzy qwerty")["metrics"]["route"] == "ag_general"
