"""Models from several providers: each one gets its own provider's key.

A local server that speaks the OpenAI-compatible chat API is used and it records what it receives.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from haresu import assistant, credentials, engine, judge, loop, providers
from haresu import spec as S
from haresu.examples import EXAMPLES
from haresu.store import Workspace

ANTHROPIC = "sk-ant-api03-" + "A" * 40
OPENAI = "sk-proj-" + "O" * 40
GROQ = "gsk_" + "G" * 40


class Fake:
    def __init__(self):
        self.requests, self.script, self.status = [], [], 200
        self.models = ["gpt-fake-b", "text-embedding-fake", "whisper-1", "models/gpt-fake-a", "dall-e-9"]
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": None})
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"id": m} for m in outer.models]} if outer.status == 200 else {"error": {}}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": "nope"}}).encode())
                    return
                msg = outer.script.pop(0) if outer.script else {"content": "no script"}
                finish = "tool_calls" if msg.get("tool_calls") else "stop"
                data = {"model": "fake-model-1", "choices": [{"message": {"role": "assistant", **msg}, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def calls(*specs):
    """A model response asking for tools: (name, arguments)."""
    return {"content": None, "tool_calls": [{"id": f"call_{i}", "type": "function",
                                             "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(specs)]}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    f = Fake()
    ws = Workspace(tmp_path)
    for provider in ("openai", "groq"):
        monkeypatch.setitem(credentials.PROVIDERS[provider], "base", f.base)
    providers._cache.clear()
    yield f, ws
    f.close()
    providers._cache.clear()


def agent_spec(model):
    spec = S.apply_ops({"name": "m", "description": "", "nodes": [], "edges": []}, [
        {"op": "add_node", "node": {"id": "in", "type": "input"}},
        {"op": "add_node", "node": {"id": "a1", "type": "agent", "data": {"model": model, "system_prompt": "Answer with the context."}}},
        {"op": "add_node", "node": {"id": "out", "type": "output"}},
        {"op": "add_node", "node": {"id": "t", "type": "tool", "data": {"tool": "search_context"}}},
        {"op": "add_node", "node": {"id": "ctx", "type": "context", "data": {"docs": [{"id": "h", "title": "Opening hours", "text": "We are open from 7 am to 7 pm."}]}}},
        {"op": "add_edge", "source": "in", "target": "a1"}, {"op": "add_edge", "source": "a1", "target": "out"},
        {"op": "add_edge", "source": "t", "target": "a1"}, {"op": "add_edge", "source": "ctx", "target": "a1"},
    ])
    return spec


def test_each_model_uses_its_providers_key(fake):
    f, ws = fake
    credentials.add("anthropic", "A", ANTHROPIC)
    credentials.add("openai", "O", OPENAI)
    credentials.add("groq", "G", GROQ)
    f.script = [calls(("search_context", {"query": "hours"})), {"content": "We are open from 7 am to 7 pm."}]
    r = engine.run(agent_spec("openai:model-x"), "What are the hours?")
    assert r["metrics"]["error"] == "" and r["output"] == "We are open from 7 am to 7 pm."
    # Both requests carry the OpenAI key and never another provider's.
    assert [q["auth"] for q in f.requests] == [f"Bearer {OPENAI}"] * 2
    first, second = f.requests[0]["body"], f.requests[1]["body"]
    assert first["model"] == "model-x" and first["messages"][0]["role"] == "system"
    assert first["tools"][0]["function"]["name"] == "search_context" and "parameters" in first["tools"][0]["function"]
    # The tool result comes back in OpenAI format, tied to the call.
    assistant_msg, tool_msg = second["messages"][-2], second["messages"][-1]
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "search_context"
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_0" and "7 am to 7 pm" in tool_msg["content"]
    # Another model, another provider, another key.
    f.requests.clear()
    f.script = [{"content": "ok"}]
    engine.run(agent_spec("groq:other"), "hello")
    assert f.requests[0]["auth"] == f"Bearer {GROQ}" and f.requests[0]["body"]["model"] == "other"


def test_the_environment_key_works_if_none_is_chosen(fake, monkeypatch):
    f, ws = fake
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI)
    f.script = [{"content": "hello"}]
    assert engine.run(agent_spec("gpt-something"), "hello")["output"] == "hello"  # “gpt-” is recognized as OpenAI
    assert f.requests[0]["auth"] == f"Bearer {OPENAI}"


def test_clear_errors_per_provider(fake):
    f, ws = fake
    r = engine.run(agent_spec("groq:x"), "hello")
    assert r["metrics"]["error"] == "There is no Groq key. Add it in Settings." and f.requests == []
    credentials.add("groq", "G", GROQ)
    for status, expected in [(401, "Groq rejected the key"), (404, "does not exist on Groq"), (429, "rate limit"), (500, "Groq error (500)")]:
        f.status = status
        assert expected in engine.run(agent_spec("groq:x"), "hello")["metrics"]["error"], status
    assert "I do not know which provider".lower() in engine.run(agent_spec("zzz"), "hello")["metrics"]["error"].lower()


def test_the_assistant_builds_the_canvas_with_another_provider(fake):
    f, ws = fake
    credentials.add("openai", "O", OPENAI)
    spec, evals = EXAMPLES["blank"]()
    ws.create_harness("New", spec, evals)
    f.script = [
        calls(("add_node", {"id": "input", "type": "input", "name": "Input", "data_json": "{}"}),
              ("add_node", {"id": "ag", "type": "agent", "name": "Agent", "data_json": json.dumps({"system_prompt": "Help."})}),
              ("add_node", {"id": "output", "type": "output", "name": "Output", "data_json": "{}"})),
        calls(("connect", {"source": "input", "target": "ag"}), ("connect", {"source": "ag", "target": "output"}),
              ("connect", {"source": "output", "target": "ag"})),
        {"content": "Done: I built one agent."},
    ]
    events = []
    out = assistant.run(ws, "new", spec, "a simple agent", "openai:m", [], events.append, lambda: False)
    assert [n["id"] for n in out["spec"]["nodes"]] == ["input", "ag", "output"] and S.validate(out["spec"]) == []
    assert events[-2] == {"type": "say", "text": "Done: I built one agent."}
    # The invalid connection (output → agent) was returned to the model as an error, without breaking anything.
    results = [m for m in f.requests[2]["body"]["messages"] if m["role"] == "tool"]
    assert any("cannot connect" in m["content"] for m in results) and len(results) == 6


def test_the_meta_agent_proposes_with_another_provider(fake):
    f, ws = fake
    credentials.add("openai", "O", OPENAI)
    spec, evals = EXAMPLES["support"]()
    S.node(spec, "meta")["data"]["model"] = "openai:meta-x"
    ws.create_harness("Support", spec, evals)
    loop.measure(ws, "support")
    patch = {"diagnosis": "d3 and d4 fall to the general agent.", "prediction": "Dev goes up.", "risk": "None.",
             "ops": [{"op": "add_router_rule", "args_json": json.dumps({"node_id": "router", "rule": {"keywords": ["refund", "return"], "target": "ag_payments"}})}]}
    f.script = [calls(("read_spec", {}), ("list_dev_runs", {})), calls(("submit_patch", patch))]
    p = loop.propose(ws, "support")
    assert p["version"] == "v001" and p["problems"] == []
    meta = ws.get_version_meta("support", "v001")
    assert meta["author"] == "meta (fake-model-1)"
    assert all(q["auth"] == f"Bearer {OPENAI}" for q in f.requests)
    rules = S.node(ws.get_spec("support", "v001"), "router")["data"]["rules"]
    assert rules[-1]["keywords"] == ["refund", "return"]


def test_the_judge_with_another_provider_reads_the_verdict(fake):
    f, ws = fake
    credentials.add("openai", "O", OPENAI)
    f.script = [{"content": 'My verdict: {"reason": "says the hours", "passed": true}'}, {"content": "I don't know"}]
    assert judge.judge("openai:j", "hours?", "from 7 am to 7 pm", "says the hours") == (True, "says the hours")
    ok, why = judge.judge("openai:j", "hours?", "from 7 am to 7 pm", "says the hours")
    assert ok is False and "JSON" in why
    assert judge.judge("mock", "t", "says the hours of Monday", "says the hours")[0] is True


# ---------------------------------------------------------------- choosing the model from a list

from fastapi.testclient import TestClient  # noqa: E402

from haresu import model_list, prefs  # noqa: E402
from haresu.api import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_model_cache():
    model_list._cache.clear()
    yield
    model_list._cache.clear()


def test_only_offers_models_from_providers_with_a_key(fake, monkeypatch):
    f, ws = fake
    assert model_list.available() == []
    credentials.add("openai", "O", OPENAI)
    out = model_list.available()
    assert [p["id"] for p in out] == ["openai"]
    # Those that do not chat (embeddings, audio, images) are removed, and the “models/” prefix.
    assert [m["value"] for m in out[0]["models"]] == ["openai:gpt-fake-a", "openai:gpt-fake-b"]
    assert f.requests[0]["auth"] == f"Bearer {OPENAI}" and f.requests[0]["path"].endswith("/models")
    # A second provider with a key appears separately, asking with its own key.
    credentials.add("groq", "G", GROQ)
    out = model_list.available()
    assert [p["id"] for p in out] == ["openai", "groq"]
    assert {q["auth"] for q in f.requests} == {f"Bearer {OPENAI}", f"Bearer {GROQ}"}


def test_the_list_is_cached_and_renewed_when_the_key_changes(fake):
    f, ws = fake
    credentials.add("openai", "O", OPENAI)
    model_list.available()
    model_list.available()
    assert len(f.requests) == 1  # the second time it came from memory
    model_list.available(refresh=True)
    assert len(f.requests) == 2
    credentials.add("openai", "Other", "sk-proj-" + "P" * 40)  # another key: new list
    model_list.available()
    assert len(f.requests) == 3 and f.requests[-1]["auth"].endswith("P" * 40)


def test_if_a_provider_fails_it_is_explained_and_the_rest_continue(fake):
    f, ws = fake
    credentials.add("openai", "O", OPENAI)
    f.status = 401
    out = model_list.available()
    assert out[0]["models"] == [] and out[0]["error"] == "OpenAI rejected the key."
    f.status = 200
    assert model_list.available(refresh=True)[0]["error"] is None  # a failure is not saved


def test_anthropic_models_with_its_key(fake, monkeypatch):
    import anthropic

    seen = []

    class Model:
        def __init__(self, id, name): self.id, self.display_name = id, name

    class C:
        def __init__(self, **kw): seen.append(kw.get("api_key"))
        class models:  # noqa: N801
            @staticmethod
            def list(limit=100): return iter([Model("claude-a", "Claude A"), Model("claude-b", "Claude B")])
    monkeypatch.setattr(anthropic, "Anthropic", C)
    credentials.add("anthropic", "A", ANTHROPIC)
    out = model_list.available()
    assert out[0]["models"] == [{"id": "claude-a", "name": "Claude A", "value": "anthropic:claude-a"},
                                {"id": "claude-b", "name": "Claude B", "value": "anthropic:claude-b"}]
    assert seen == [ANTHROPIC]


def test_api_models_and_preferences(fake, tmp_path):
    f, ws = fake
    c = TestClient(create_app(ws.root, static_dir=tmp_path / "nothing"))
    assert c.get("/api/models").json() == {"providers": []}
    assert c.get("/api/preferences").json() == {"default_model": "mock", "assistant_model": "mock"}
    c.post("/api/settings/keys", json={"provider": "openai", "name": "O", "key": OPENAI})
    models = c.get("/api/models").json()["providers"][0]["models"]
    assert [m["id"] for m in models] == ["gpt-fake-a", "gpt-fake-b"]
    # What was chosen is remembered, and a model that cannot be resolved is rejected.
    assert c.put("/api/preferences", json={"default_model": "openai:gpt-fake-a"}).json() == {"default_model": "openai:gpt-fake-a", "assistant_model": "openai:gpt-fake-a"}
    assert c.put("/api/preferences", json={"assistant_model": "mock"}).json()["assistant_model"] == "mock"
    # Once chosen, the assistant's model no longer follows the default; empty makes it follow it again.
    assert c.put("/api/preferences", json={"default_model": "openai:gpt-fake-b"}).json() == {"default_model": "openai:gpt-fake-b", "assistant_model": "mock"}
    assert c.put("/api/preferences", json={"assistant_model": "", "default_model": "openai:gpt-fake-a"}).json()["assistant_model"] == "openai:gpt-fake-a"
    assert c.put("/api/preferences", json={"default_model": "zzz:x"}).status_code == 400
    assert c.get("/api/catalog").json()["preferences"]["default_model"] == "openai:gpt-fake-a"
    assert Workspace(ws.root) and prefs.get()["default_model"] == "openai:gpt-fake-a"  # survives a restart


def test_new_agents_use_the_default_model(fake, tmp_path):
    f, ws = fake
    spec, evals = EXAMPLES["blank"]()
    ws.create_harness("New", spec, evals)
    events = []
    out = assistant.run(ws, "new", spec, "support for payments and shipping", "mock", [], events.append, lambda: False, "openai:gpt-fake-a")
    agents = [n for n in out["spec"]["nodes"] if n["type"] in ("agent", "meta")]
    assert len(agents) == 3 and {n["data"]["model"] for n in agents} == {"openai:gpt-fake-a"}
    # An external agent (MCP) also gets the default model from the preferences.
    c = TestClient(create_app(ws.root, static_dir=tmp_path / "nothing"))
    c.put("/api/preferences", json={"default_model": "openai:gpt-fake-b"})
    c.post("/api/harnesses", json={"name": "Another"})
    c.post("/api/harnesses/another/agent", json={"tool": "add_node", "args": {"id": "ag", "type": "agent", "name": "A", "data": {}}})
    c.post("/api/harnesses/another/agent", json={"tool": "add_node", "args": {"id": "ag2", "type": "agent", "name": "B", "data": {"model": "mock"}}})
    state = json.loads(c.post("/api/harnesses/another/agent", json={"tool": "view_state", "args": {}}).json()["result"])
    assert {n["id"]: n["data"]["model"] for n in state["nodes"]} == {"ag": "openai:gpt-fake-b", "ag2": "mock"}
