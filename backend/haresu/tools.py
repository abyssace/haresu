"""Built-in tools. Each one receives the run's context and its arguments."""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from typing import Callable

from . import spec as S


@dataclass
class RunContext:
    """What the agents share during a run."""

    docs: list[dict] = field(default_factory=list)  # documents visible to the current agent
    memory: dict[str, str] = field(default_factory=dict)  # scratchpad shared between agents


def search_docs(docs: list[dict], query: str, k: int = 3) -> list[tuple[int, dict]]:
    """Search by words in common. Returns (score, doc) from highest to lowest."""
    q = {S.stem(t) for t in S.tokens(query)}
    scored = []
    for d in docs:
        title = {S.stem(t) for t in S.tokens(d.get("title", ""))}
        body = {S.stem(t) for t in S.tokens(d.get("text", ""))}
        score = 2 * len(q & title) + len(q & body)  # the title weighs double
        if score:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def _search_context(ctx: RunContext, args: dict) -> str:
    hits = search_docs(ctx.docs, str(args.get("query", "")))
    if not hits:
        return "No results."
    return "\n".join(f"[{d.get('id', '?')}] {d.get('title', '')}: {d.get('text', '')}" for _, d in hits)


def _read_memory(ctx: RunContext, args: dict) -> str:
    key = str(args.get("key", ""))
    if not key:
        return "Claves: " + (", ".join(sorted(ctx.memory)) or "(empty)")
    return ctx.memory.get(key, f"Nothing is stored under '{key}'.")


def _write_memory(ctx: RunContext, args: dict) -> str:
    ctx.memory[str(args.get("key", ""))] = str(args.get("value", ""))
    return "Saved."


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


def _calc(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _calc(node.left), _calc(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc(node.operand))
    raise ValueError("expression not allowed")


def _calculator(ctx: RunContext, args: dict) -> str:
    try:
        return str(_calc(ast.parse(str(args.get("expression", "")), mode="eval").body))
    except Exception as exc:  # the tool returns the error to the model
        return f"Error: {exc}"


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[[RunContext, dict], str]

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


REGISTRY: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "search_context",
            "Searches the shared context (the team's documents). Use it before answering any question about policies, processes or business data.",
            _schema({"query": {"type": "string", "description": "What you want to find."}}, ["query"]),
            _search_context,
        ),
        Tool(
            "read_memory",
            "Reads a key from the scratchpad shared between agents. Without a key, it lists the keys that exist.",
            _schema({"key": {"type": "string"}}, ["key"]),
            _read_memory,
        ),
        Tool(
            "write_memory",
            "Saves a value on the shared scratchpad so other agents in the same run can read it.",
            _schema({"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
            _write_memory,
        ),
        Tool(
            "calculator",
            "Evaluates an arithmetic expression (+ - * / ** %).",
            _schema({"expression": {"type": "string"}}, ["expression"]),
            _calculator,
        ),
    ]
}
