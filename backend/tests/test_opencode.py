"""OpenCode: one key, two plans, three call formats. Tested against a local server that imitates them."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from haresu import credentials, engine, model_list, opencode, providers
from haresu import spec as S
from haresu.api import create_app
from haresu.store import Workspace

KEY = "oc-" + "K" * 40


class OC:
    def __init__(self):
        self.requests, self.scripts, self.status, self.body = [], {"chat": [], "responses": [], "messages": []}, 200, None
        outer = self
        self.root = ""  # filled in at startup

        def entry(id_, api, name=None, type_="chat", tools=True, rec=None, plan="go"):
            base = {"go": f"{outer.root}/go", "console": outer.root}[plan]
            urls = {"chat-completions": f"{base}/openai/v1", "responses": f"{base}/openai/v1", "messages": f"{base}/anthropic/v1",
                    "generate-content": f"{base}/google/v1beta", "embeddings": f"{base}/openai/v1"}
            e = {"id": id_, "name": name or id_.upper(), "type": type_, "capabilities": {"tools": {"supported": tools}},
                 "api": {a: {"base_url": urls[a]} for a in api}}
            if rec:
                e["recommended_api"] = rec
            return e

        self.entry = entry

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _send(self, status, data):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
                go = [entry("kimi-go", ["chat-completions"]), entry("gpt-go", ["responses"], "GPT Go"),
                      entry("qwen-go", ["messages"], "Qwen Go"), entry("emb-go", ["embeddings"], type_="embedding"),
                      entry("gem-go", ["generate-content"]), entry("no-tools", ["chat-completions"], tools=False),
                      entry("shared", ["chat-completions"]),
                      entry("multi", ["chat-completions", "messages"], rec="messages")]
                console = [entry("claude-con", ["messages"], plan="console"), entry("hy-free", ["chat-completions"], plan="console"),
                           entry("shared", ["chat-completions"], plan="console")]
                if self.path == "/go/v1/models":
                    self._send(200, {"object": "list", "data": go})
                elif self.path == "/v1/models":
                    self._send(200, {"object": "list", "data": console})
                else:
                    self._send(404, {"error": "no"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"method": "POST", "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if outer.status != 200:
                    self._send(outer.status, outer.body or {"error": {"message": "nope"}})
                    return
                kind = "responses" if self.path.endswith("/responses") else "messages" if self.path.endswith("/messages") else "chat"
                script = outer.scripts[kind]
                self._send(200, script.pop(0) if script else {"error": "no script"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.root = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    def posts(self):
        return [r for r in self.requests if r["method"] == "POST"]


def chat_reply(content=None, calls=()):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    return {"model": "m", "choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}


@pytest.fixture
def oc(tmp_path, monkeypatch):
    for var in ("OPENCODE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    f = OC()
    monkeypatch.setenv("HARESU_OPENCODE_BASE_URL", f.root)
    Workspace(tmp_path)
    opencode._catalog.clear()
    model_list._cache.clear()
    providers._cache.clear()
    credentials.add("opencode", "Mine", KEY)
    yield f
    f.close()
    opencode._catalog.clear()
    model_list._cache.clear()
    providers._cache.clear()


def agent(model):
    return S.apply_ops({"name": "m", "description": "", "nodes": [], "edges": []}, [
        {"op": "add_node", "node": {"id": "in", "type": "input"}},
        {"op": "add_node", "node": {"id": "a1", "type": "agent", "data": {"model": model, "system_prompt": "Answer with the context."}}},
        {"op": "add_node", "node": {"id": "out", "type": "output"}},
        {"op": "add_node", "node": {"id": "t", "type": "tool", "data": {"tool": "search_context"}}},
        {"op": "add_node", "node": {"id": "ctx", "type": "context", "data": {"docs": [{"id": "h", "title": "Opening hours", "text": "We are open from 7 am to 7 pm."}]}}},
        {"op": "add_edge", "source": "in", "target": "a1"}, {"op": "add_edge", "source": "a1", "target": "out"},
        {"op": "add_edge", "source": "t", "target": "a1"}, {"op": "add_edge", "source": "ctx", "target": "a1"},
    ])


def test_the_list_brings_both_plans_and_only_usable_models(oc):
    models = model_list.available()[0]["models"]
    by_group = {}
    for m in models:
        by_group.setdefault(m["group"], []).append(m["value"])
    assert set(by_group) == {"OpenCode Go (subscription)", "OpenCode Console (pay as you go)"}
    assert sorted(by_group["OpenCode Go (subscription)"]) == ["opencode:go/gpt-go", "opencode:go/kimi-go", "opencode:go/multi",
                                                           "opencode:go/qwen-go", "opencode:go/shared"]
    assert sorted(by_group["OpenCode Console (pay as you go)"]) == ["opencode:console/claude-con", "opencode:console/hy-free", "opencode:console/shared"]
    # Out: embeddings, Google-only models and models without tools.
    assert not any(x in m["value"] for m in models for x in ("emb-go", "gem-go", "no-tools"))
    assert [p["id"] for p in model_list.available()] == ["opencode"]


def test_chat_completions_with_session_on_go_and_without_it_on_console(oc):
    oc.scripts["chat"] = [chat_reply(calls=[("search_context", {"query": "hours"})]), chat_reply("We are open from 7 am to 7 pm."),
                          chat_reply("hello")]
    r = engine.run(agent("opencode:go/kimi-go"), "What are the hours?")
    assert r["metrics"]["error"] == "" and "7 am to 7 pm" in r["output"]
    p1, p2 = oc.posts()
    assert p1["path"] == "/go/openai/v1/chat/completions" and p1["headers"]["authorization"] == f"Bearer {KEY}"
    assert p1["headers"]["user-agent"] == "haresu/0.1"
    # The same conversation carries the same session identifier; another conversation, another one.
    assert p1["headers"]["x-opencode-session"] == p2["headers"]["x-opencode-session"]
    oc.requests.clear()
    oc.scripts["chat"] = [chat_reply("a")]
    engine.run(agent("opencode:go/kimi-go"), "another different task")
    assert oc.posts()[0]["headers"]["x-opencode-session"] != p1["headers"]["x-opencode-session"]
    # Console sends no session.
    oc.requests.clear()
    oc.scripts["chat"] = [chat_reply("ready")]
    assert engine.run(agent("opencode:console/hy-free"), "hello")["output"] == "ready"
    assert oc.posts()[0]["path"] == "/openai/v1/chat/completions" and "x-opencode-session" not in oc.posts()[0]["headers"]


def test_responses_format_resends_the_reasoning(oc):
    oc.scripts["responses"] = [
        {"model": "gpt-go", "output": [{"type": "reasoning", "id": "rs_1", "summary": []},
                                       {"type": "function_call", "id": "fc_1", "call_id": "call_9", "name": "search_context",
                                        "arguments": json.dumps({"query": "hours"})}],
         "usage": {"input_tokens": 9, "output_tokens": 4}},
        {"model": "gpt-go", "output": [{"type": "message", "content": [{"type": "output_text", "text": "We are open from 7 am to 7 pm."}]}],
         "usage": {"input_tokens": 12, "output_tokens": 6}},
    ]
    r = engine.run(agent("opencode:go/gpt-go"), "What are the hours?")
    assert r["metrics"]["error"] == "" and r["output"] == "We are open from 7 am to 7 pm." and r["metrics"]["tokens_in"] == 21
    first, second = oc.posts()
    assert first["path"] == "/go/openai/v1/responses" and first["body"]["instructions"].startswith("Answer with the context")
    assert first["body"]["tools"][0] == {"type": "function", "name": "search_context", "description": first["body"]["tools"][0]["description"],
                                         "parameters": first["body"]["tools"][0]["parameters"]}
    kinds = [i.get("type") or i.get("role") for i in second["body"]["input"]]
    # What the model returned (with its reasoning) is sent back, and the tool result tied to its call.
    assert kinds == ["user", "reasoning", "function_call", "function_call_output"]
    assert second["body"]["input"][3]["call_id"] == "call_9" and "7 am to 7 pm" in second["body"]["input"][3]["output"]


def test_messages_format_with_the_anthropic_sdk(oc):
    def msg(content, stop):
        return {"id": "msg_1", "type": "message", "role": "assistant", "model": "qwen-go", "content": content, "stop_reason": stop,
                "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 3}}
    oc.scripts["messages"] = [msg([{"type": "tool_use", "id": "tu_1", "name": "search_context", "input": {"query": "hours"}}], "tool_use"),
                              msg([{"type": "text", "text": "We are open from 7 am to 7 pm."}], "end_turn")]
    r = engine.run(agent("opencode:go/qwen-go"), "What are the hours?")
    assert r["metrics"]["error"] == "" and r["output"] == "We are open from 7 am to 7 pm."
    first, second = oc.posts()
    assert first["path"] == "/go/anthropic/v1/messages" and first["headers"]["authorization"] == f"Bearer {KEY}"
    assert first["headers"]["user-agent"] == "haresu/0.1" and first["headers"]["x-opencode-session"]
    assert "output_config" not in first["body"] and all("strict" not in t for t in first["body"]["tools"])
    assert second["body"]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_the_recommended_format_wins_and_without_a_plan_go_is_searched_first(oc):
    oc.scripts["messages"] = [{"id": "m", "type": "message", "role": "assistant", "model": "multi", "stop_sequence": None,
                               "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}]
    assert engine.run(agent("opencode:multi"), "hello")["output"] == "ok"  # recommended: messages, although it also speaks chat
    assert oc.posts()[0]["path"] == "/go/anthropic/v1/messages"
    oc.requests.clear()
    oc.scripts["chat"] = [chat_reply("de go")]
    assert engine.run(agent("opencode:shared"), "hello")["output"] == "de go"  # it is in both plans: Go wins
    assert oc.posts()[0]["path"] == "/go/openai/v1/chat/completions"
    oc.requests.clear()
    oc.scripts["chat"] = [chat_reply("de console")]
    assert engine.run(agent("opencode:console/shared"), "hello")["output"] == "de console"
    assert oc.posts()[0]["path"] == "/openai/v1/chat/completions"


def test_clear_errors(oc):
    assert "does not offer the model “nothing”" in engine.run(agent("opencode:nothing"), "hello")["metrics"]["error"]
    assert "OpenCode Console (pay as you go) plan" in engine.run(agent("opencode:console/kimi-go"), "hello")["metrics"]["error"]
    for status, body, expected in [(401, None, "OpenCode rejected the key"),
                                   (403, {"error": {"message": "This API key can't call models"}}, "has no inference permission"),
                                   (402, {"error": {"message": "Insufficient balance"}}, "has no balance")]:
        oc.status, oc.body = status, body
        assert expected in engine.run(agent("opencode:go/kimi-go"), "hello")["metrics"]["error"], status
    oc.status = 200
    credentials.activate("opencode", None)
    assert engine.run(agent("opencode:go/kimi-go"), "hello")["metrics"]["error"] == "There is no OpenCode key. Add it in Settings."


def test_testing_the_key_uses_a_free_model(oc):
    oc.scripts["chat"] = [chat_reply("ready")]
    assert credentials.check("opencode").startswith("It works: OpenCode accepted the key (tested with the free model “hy-free”")
    assert oc.posts()[0]["path"] == "/openai/v1/chat/completions" and oc.posts()[0]["body"]["model"] == "hy-free"
    oc.status = 401
    assert "rejected the key" in credentials.check("opencode")


def test_api_with_the_opencode_key(oc, tmp_path):
    c = TestClient(create_app(tmp_path, static_dir=tmp_path / "nothing"))
    prov = {p["id"]: p for p in c.get("/api/settings").json()["providers"]}
    assert prov["opencode"]["name"] == "OpenCode (Go and Console)" and prov["opencode"]["keys"][0]["last4"] == "KKKK"
    assert KEY not in json.dumps(c.get("/api/settings").json())
    groups = {m["group"] for m in c.get("/api/models").json()["providers"][0]["models"]}
    assert groups == {"OpenCode Go (subscription)", "OpenCode Console (pay as you go)"}
    r = c.get("/api/resolve", params={"model": "opencode:go/kimi-go"}).json()
    assert r["ok"] and r["provider_name"] == "OpenCode (Go and Console)" and r["key_name"] == "Mine"
    # An Anthropic key does not go in as an OpenCode key.
    assert c.post("/api/settings/keys", json={"provider": "opencode", "key": "sk-ant-api03-" + "A" * 40}).status_code == 400
