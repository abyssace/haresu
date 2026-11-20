"""Command line: `haresu serve` and `haresu demo`."""

from __future__ import annotations

import argparse
import sys

from . import loop
from .examples import EXAMPLES
from .store import Workspace


def _fmt(x) -> str:
    return "  —  " if x is None else f"{x:.3f}"


def print_trend(ws: Workspace, h: str) -> None:
    print(f"\nTrend of '{h}' (current: {ws.current(h)})")
    print(f"{'iter':<9}{'version':<9}{'dev':>7}{'held-out':>10}{'gain':>9}  status")
    for r in loop.trend(ws, h):
        gain = "" if r["gain"] is None else f"{r['gain']:+.3f}"
        print(f"{r['iteration']:<9}{r['version']:<9}{_fmt(r['dev']):>7}{_fmt(r['heldout']):>10}{gain:>9}  {r['status']}")


def setup_claude(home: str | None, url: str, scope: str) -> int:
    """Registers the MCP server in Claude Code and installs the skill. With absolute paths, so it works from any folder."""
    import shutil
    import subprocess
    from pathlib import Path

    from .store import default_root

    workspace = str(Path(home).expanduser().resolve()) if home else str(default_root())
    skill_src = Path(__file__).resolve().parent / "skill" / "SKILL.md"
    claude = shutil.which("claude")
    if not claude:
        print("I did not find the `claude` command. Install Claude Code and run this again.", file=sys.stderr)
        return 1

    subprocess.run([claude, "mcp", "remove", "--scope", scope, "haresu"], capture_output=True)
    add = subprocess.run([claude, "mcp", "add", "--scope", scope, "haresu", "--",
                          sys.executable, "-m", "haresu", "--home", workspace, "mcp", "--url", url, "--agent", "Claude Code"],
                         capture_output=True, text=True)
    if add.returncode != 0:
        print(f"I could not register the MCP server: {(add.stderr or add.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"MCP server registered in Claude Code (scope: {scope}).")

    # The skill goes to your Claude folder (user scope) or to the folder you are in (project/local).
    base = Path.home() / ".claude" if scope == "user" else Path.cwd() / ".claude"
    target = base / "skills" / "haresu" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skill_src, target)
    print(f"Skill installed at {target}.")
    print(f"Data in {workspace}. Restart Claude Code and ask, for example: “build me a support harness for my shop in haresu”.")
    print(f"To undo: claude mcp remove --scope {scope} haresu && rm -r {target.parent}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="haresu", description="Visual meta-harness for agents.")
    parser.add_argument("--home", help="Workspace folder (default ./workspace or $HARESU_HOME).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="Starts the API and the interface.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--open", action="store_true", help="Opens the interface in the browser.")

    p = sub.add_parser("mcp", help="MCP server (stdio) to connect an external agent to haresu.")
    p.add_argument("--url", default="http://127.0.0.1:8000", help="Where `haresu serve` is running.")
    p.add_argument("--agent", default="agent", help="Name its cursor shows on the canvas.")

    p = sub.add_parser("setup-claude", help="Connects haresu to Claude Code: registers the MCP server and installs the skill.")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--scope", default="user", choices=["user", "project", "local"],
                   help="Where the MCP server and the skill are registered: 'user' makes them available in any folder.")

    p = sub.add_parser("demo", help="Creates the example harness and runs the cycle with the mock model.")
    p.add_argument("--name", default="Store support")
    p.add_argument("--rounds", type=int, default=2)

    p = sub.add_parser("cycle", help="Runs cycle rounds on an existing harness.")
    p.add_argument("harness")
    p.add_argument("--rounds", type=int, default=1)

    p = sub.add_parser("trend", help="Shows the trend of a harness.")
    p.add_argument("harness")

    args = parser.parse_args(argv)
    if args.cmd == "mcp":  # does not touch the workspace: it talks to the server over HTTP
        from .mcp_server import main as mcp_main

        mcp_main(args.url, args.agent, args.home)
        return 0
    if args.cmd == "setup-claude":
        return setup_claude(args.home, args.url, args.scope)
    ws = Workspace(args.home)

    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        if args.open:
            import threading
            import webbrowser

            threading.Timer(1.2, webbrowser.open, [f"http://{args.host}:{args.port}"]).start()
        uvicorn.run(create_app(args.home), host=args.host, port=args.port)
        return 0

    if args.cmd == "demo":
        spec, evals = EXAMPLES["support"]()
        try:
            h = ws.create_harness(args.name, spec, evals, spec["description"])
        except ValueError as exc:
            print(f"{exc} Use --name with a different name.", file=sys.stderr)
            return 1
        for step in loop.cycle(ws, h, args.rounds, on_step=print):
            pass
        print_trend(ws, h)
        return 0

    if args.cmd == "cycle":
        loop.cycle(ws, args.harness, args.rounds, on_step=print)
        print_trend(ws, args.harness)
        return 0

    if args.cmd == "trend":
        print_trend(ws, args.harness)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
