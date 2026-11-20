"""MCP server: connects any agent (Claude Code, Cursor, your own) to haresu.

The agent builds and manages harnesses with these tools; every change goes through the
haresu server, so whoever has the canvas open sees it happen live.

    python -m haresu serve                      # in one terminal
    python -m haresu mcp --url http://127.0.0.1:8000   # launched by the MCP client
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer

from . import __version__, assistant

INSTRUCTIONS = """Tools to build and improve agent harnesses in haresu, a visual tool. \
Whatever you do shows up live on the person's canvas.

Usual flow: list_harnesses or create_harness → view_state → add_node and connect → load_documents with \
the information the person gave you → view_state until it reports nothing in missing_to_run → try_task → \
add_cases (dev and held-out) → save → measure → run_cycle.

""" + assistant.SYSTEM.split("Nodes (type → data):", 1)[1].split("Rules:", 1)[0].join(
    ["Nodes (type → data):", ""]) + """Rules:
- Do not invent business facts in the context documents: use only what the person gave you.
- Agents with the "mock" model can only search the context.
- Governance (policy nodes) is the person's decision: propose reasonable values and ask them to review them.
- After run_cycle, or when you start working on a harness another person may have changed, call reset_canvas \
to start from the current version."""


def ensure_running(url: str, home: str | None) -> str:
    """If haresu does not respond and the URL is local, it starts it in the background. Returns '' or the reason it failed."""
    def alive() -> bool:
        try:
            with urllib.request.urlopen(f"{url}/api/catalog", timeout=2):
                return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            return False

    if alive():
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        return f"haresu does not respond at {url} and it is not a local address, so I cannot start it."
    cmd = [sys.executable, "-m", "haresu"] + (["--home", home] if home else []) + ["serve", "--port", str(parsed.port or 80)]
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.Popen(cmd, cwd=backend, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(40):
        time.sleep(0.25)
        if alive():
            return ""
    return f"I tried to start haresu at {url} and it did not respond within 10 seconds."


def build(url: str, agent: str = "agent", home: str | None = None) -> MCPServer:
    url = url.rstrip("/")
    server = MCPServer("haresu", instructions=INSTRUCTIONS, version=__version__)
    link = lambda h: f"{url}/#{h}"  # noqa: E731

    def http(method: str, path: str, body: dict | None = None, retry: bool = True) -> dict | list | str:
        req = urllib.request.Request(f"{url}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode()).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if exc.code == 405 and path.endswith("/agent"):
                return ("Error: the haresu server that is running is an older version and does not have the routes "
                        "for agents. Ask the person to stop it and start it again with: python -m haresu serve")
            return f"Error: {detail or exc.reason}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if retry:
                problem = ensure_running(url, home)
                if not problem:
                    return http(method, path, body, retry=False)
                return f"Error: {problem} Ask the person to start it with: python -m haresu serve"
            return (f"Error: I could not connect to haresu at {url} ({getattr(exc, 'reason', exc)}). "
                    "Ask the person to start it with: python -m haresu serve")

    def call(harness: str, tool: str, **args) -> str:
        out = http("POST", f"/api/harnesses/{urllib.parse.quote(harness)}/agent", {"tool": tool, "args": args, "agent": agent})
        return out if isinstance(out, str) else str(out.get("result", out))

    @server.tool()
    def list_harnesses() -> str:
        """Lists the harnesses that exist, with their id and their current version."""
        out = http("GET", "/api/harnesses")
        return out if isinstance(out, str) else json.dumps(
            [{"id": x["id"], "name": x["name"], "current_version": x["current"], "link": link(x["id"])} for x in out],
            ensure_ascii=False)

    @server.tool()
    def create_harness(name: str) -> str:
        """Creates a new harness with an empty canvas. Returns its id, which the other tools use."""
        out = http("POST", "/api/harnesses", {"name": name})
        if isinstance(out, str):
            return out
        return (f"Created. id: {out['id']}. Link to see it in the visual tool: {link(out['id'])} "
                "(give it to the person right away, so they can watch it being built live).")

    @server.tool()
    def view_state(harness: str) -> str:
        """Returns the working canvas: nodes, connections, what is missing to run, warnings per node and eval cases."""
        return call(harness, "view_state")

    @server.tool()
    def add_node(harness: str, id: str, type: str, name: str = "", data: dict | None = None) -> str:
        """Adds a node. type: input, router, agent, context, tool, policy, meta, output or note. The canvas chooses the position."""
        return call(harness, "add_node", id=id, type=type, name=name, data=data or {})

    @server.tool()
    def edit_node(harness: str, id: str, name: str = "", data: dict | None = None) -> str:
        """Changes the name of a node or merges new fields into its data (for example system_prompt, rules, docs)."""
        return call(harness, "edit_node", id=id, name=name, data=data or {})

    @server.tool()
    def delete_node(harness: str, id: str) -> str:
        """Deletes a node and its connections."""
        return call(harness, "delete_node", id=id)

    @server.tool()
    def connect(harness: str, source: str, target: str) -> str:
        """Connects two nodes, from the source to the target. Only connections that make sense are accepted."""
        return call(harness, "connect", source=source, target=target)

    @server.tool()
    def disconnect(harness: str, source: str, target: str) -> str:
        """Removes the connection between two nodes."""
        return call(harness, "disconnect", source=source, target=target)

    @server.tool()
    def load_documents(harness: str, context: str, documents: list[dict]) -> str:
        """Loads information into a context node: [{"title": "...", "text": "..."}]. Creates the context if it does not exist \
(it still has to be connected to an agent) and splits long texts into several documents. Use it to pass the harness whatever the person gave you."""
        return call(harness, "load_documents", context=context, documents=documents)

    @server.tool()
    def add_cases(harness: str, cases: list[dict]) -> str:
        """Adds eval cases: [{"split": "dev"|"heldout", "input": "...", "expect": {"contains", "not_contains", "route", "rubric"}}]."""
        return call(harness, "add_cases", cases=cases)

    @server.tool()
    def try_task(harness: str, task: str) -> str:
        """Runs a task through the working canvas and returns the answer, the route and the tools used."""
        return call(harness, "try_task", task=task)

    @server.tool()
    def save(harness: str, note: str = "", use_now: bool = False) -> str:
        """Saves the canvas as a new version. use_now=true makes it the current version without measuring it; otherwise it stays a candidate."""
        return call(harness, "save", note=note, use_now=use_now)

    @server.tool()
    def measure(harness: str) -> str:
        """Runs every eval case against the current version. You have to save first."""
        return call(harness, "measure")

    @server.tool()
    def run_cycle(harness: str, rounds: int = 1) -> str:
        """Complete improvement rounds: the meta-agent proposes, the candidate is measured and the gate decides."""
        return call(harness, "run_cycle", rounds=rounds)

    @server.tool()
    def get_trend(harness: str) -> str:
        """Dev and held-out scores per iteration, with the gate's decision."""
        return call(harness, "get_trend")

    @server.tool()
    def reset_canvas(harness: str) -> str:
        """Discards the unsaved changes of the working canvas and goes back to the current version."""
        return call(harness, "reset_canvas")

    return server


def main(url: str, agent: str = "agent", home: str | None = None) -> None:
    build(url, agent, home).run("stdio")
