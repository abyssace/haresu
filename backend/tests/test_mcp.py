"""A real MCP client, over stdio, against a haresu server started for the test."""

import asyncio
import json
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from haresu.api import create_app

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def server_url(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path, static_dir=tmp_path / "nothing"), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def get(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read().decode())


async def drive(url):
    params = StdioServerParameters(command=sys.executable, args=["-m", "haresu", "mcp", "--url", url, "--agent", "test"],
                                   cwd=str(BACKEND))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}

            async def call(tool, **args):
                r = await s.call_tool(tool, args)
                return "".join(c.text for c in r.content if hasattr(c, "text"))

            out = {"tools": names}
            out["create"] = await call("create_harness", name="From MCP")
            h = "from-mcp"
            await call("add_node", harness=h, id="input", type="input")
            await call("add_node", harness=h, id="ag", type="agent", name="Agent", data={"system_prompt": "Answer with the context."})
            await call("add_node", harness=h, id="output", type="output")
            await call("add_node", harness=h, id="ctx", type="context", name="Manual",
                       data={"docs": [{"id": "h", "title": "Opening hours", "text": "We are open Monday to Saturday, from 8 am to 8 pm."}]})
            await call("add_node", harness=h, id="search", type="tool", data={"tool": "search_context"})
            out["docs"] = await call("load_documents", harness=h, context="ctx", documents=[
                {"title": "Shipping policy", "text": "\n\n".join(f"Rule {i}: " + "x" * 500 for i in range(5))}])
            out["bad"] = await call("connect", harness=h, source="search", target="output")
            for a, b in [("input", "ag"), ("ag", "output"), ("ctx", "ag"), ("search", "ag")]:
                await call("connect", harness=h, source=a, target=b)
            out["state"] = json.loads(await call("view_state", harness=h))
            out["try_task"] = json.loads(await call("try_task", harness=h, task="What are the hours?"))
            out["cases"] = await call("add_cases", harness=h, cases=[
                {"split": "dev", "input": "What are the hours?", "expect": {"contains": ["8 am to 8 pm"]}},
                {"split": "heldout", "input": "What time are you open?", "expect": {"contains": ["8 am to 8 pm"]}}])
            out["save"] = await call("save", harness=h, note="built via MCP")
            out["measure"] = json.loads(await call("measure", harness=h))
            out["get_trend"] = json.loads(await call("get_trend", harness=h))
            out["list"] = json.loads(await call("list_harnesses"))
            out["not_found"] = await call("view_state", harness="nope")
            return out


def test_an_agent_builds_and_measures_a_harness_over_mcp(server_url):
    before = get(f"{server_url}/api/harnesses")
    out = asyncio.run(asyncio.wait_for(drive(server_url), 60))
    assert before == [] and {"create_harness", "add_node", "connect", "add_cases", "try_task", "save", "measure", "run_cycle"} <= out["tools"]
    assert "cannot connect" in out["bad"]
    assert out["docs"] == "Loaded 3 documents into 'ctx'. It now has 4."
    docs = next(n for n in out["state"]["nodes"] if n["id"] == "ctx")["data"]["docs"]
    assert docs[1]["title"] == "Shipping policy · part 1 of 3" and all(len(d["text"]) <= 1200 for d in docs)
    assert out["state"]["missing_to_run"] == [] and out["state"]["warnings_by_node"] == {}
    assert "8 am to 8 pm" in out["try_task"]["answer"] and out["try_task"]["tools"] == ["search_context"]
    assert "v001 and it is now the current version" in out["save"]
    assert out["measure"]["dev"] == 1.0 and out["measure"]["heldout"] == 1.0
    assert out["get_trend"][0]["version"] == "v001"
    assert out["list"] == [{"id": "from-mcp", "name": "From MCP", "current_version": "v001", "link": f"{server_url}/#from-mcp"}]
    assert f"{server_url}/#from-mcp" in out["create"]
    assert out["not_found"].startswith("Error")
    # The interface can replay live what the agent did.
    live = get(f"{server_url}/api/harnesses/from-mcp/live?after=0")["events"]
    assert [e["op"] for e in live if e["type"] == "op"] == ["add_node"] * 5 + ["set_node"] + ["add_edge"] * 4
    assert {e["agent"] for e in live} == {"test"} and any(e["type"] == "saved" for e in live)
    assert get(f"{server_url}/api/harnesses/from-mcp/live")["events"] == []


def test_mcp_starts_haresu_if_it_is_not_running(tmp_path):
    import subprocess

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"

    async def go():
        params = StdioServerParameters(command=sys.executable, cwd=str(BACKEND),
                                       args=["-m", "haresu", "--home", str(tmp_path), "mcp", "--url", url])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                r = await s.call_tool("create_harness", {"name": "Auto"})
                return "".join(c.text for c in r.content if hasattr(c, "text"))
    try:
        out = asyncio.run(asyncio.wait_for(go(), 40))
        assert f"{url}/#auto" in out
        assert (tmp_path / "harnesses" / "auto").is_dir() and get(f"{url}/api/harnesses")[0]["id"] == "auto"
    finally:
        subprocess.run(["pkill", "-f", f"haresu --home {tmp_path} serve --port {port}"])


def test_mcp_reports_if_haresu_is_not_running():
    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "haresu", "mcp", "--url", "http://haresu.invalid:9"], cwd=str(BACKEND))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                r = await s.call_tool("list_harnesses", {})
                return "".join(c.text for c in r.content if hasattr(c, "text"))
    assert "haresu serve" in asyncio.run(asyncio.wait_for(go(), 30))
