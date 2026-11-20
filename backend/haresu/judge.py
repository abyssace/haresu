"""Judge: decides whether an answer meets a rubric written in natural language."""

from __future__ import annotations

import json
import re

from . import credentials, providers
from . import spec as S

SYSTEM = """You are the judge of an eval. You receive a task, an agent's answer and a rubric. \
Decide whether the answer meets the rubric. Judge only what the rubric asks for: do not reward or punish \
style, length or other details the rubric does not mention. If the answer does not give the \
information the rubric requires, it does not meet it, even if it sounds reasonable."""

SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "description": "One sentence that explains the verdict."},
        "passed": {"type": "boolean"},
    },
    "required": ["reason", "passed"],
    "additionalProperties": False,
}


def judge_mock(rubric: str, output: str) -> tuple[bool, str]:
    """Mock judge: counts which share of the rubric's words appears in the answer."""
    want = {S.stem(t) for t in S.tokens(rubric)}
    if not want:
        return True, "The rubric has no words to check."
    got = {S.stem(t) for t in S.tokens(output)}
    cover = len(want & got) / len(want)
    return cover >= 0.6, f"The answer covers {cover:.0%} of the rubric's words (mock judge; minimum 60%)."


def judge_claude(model: str, task: str, output: str, rubric: str) -> tuple[bool, str]:
    """Anthropic: structured output, which guarantees the format."""
    import anthropic

    client = credentials.client(providers.key_for_model("anthropic", model))
    prompt = f"<task>\n{task}\n</task>\n\n<answer>\n{output}\n</answer>\n\n<rubric>\n{rubric}\n</rubric>"
    try:
        resp = client.messages.create(
            model=providers.resolve(model)[1], max_tokens=2000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.AuthenticationError as exc:
        raise providers.ProviderError("Judge: Anthropic rejected the key or there is none. Add it in Settings.") from exc
    except anthropic.APIStatusError as exc:
        raise providers.ProviderError(f"Judge: API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise providers.ProviderError("Judge: could not connect to the Anthropic API.") from exc
    except TypeError as exc:
        raise providers.ProviderError(f"Judge: there are no Anthropic credentials: {exc}") from exc
    if resp.stop_reason == "refusal":
        return False, "The judge refused the request."
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    return bool(data["passed"]), str(data["reason"])


def judge_generic(model: str, task: str, output: str, rubric: str) -> tuple[bool, str]:
    """Other providers: the verdict is requested as JSON and read from the answer."""
    prompt = (f"<task>\n{task}\n</task>\n\n<answer>\n{output}\n</answer>\n\n<rubric>\n{rubric}\n</rubric>\n\n"
              'Reply only with a JSON object: {"reason": "one sentence", "passed": true or false}.')
    reply = providers.for_model(model).chat(model=model, system=SYSTEM, messages=[{"role": "user", "content": prompt}], tools=[])
    match = re.search(r"\{.*\}", reply.text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
        return bool(data["passed"]), str(data.get("reason", ""))
    except (ValueError, KeyError, TypeError, AttributeError):
        return False, "The judge did not return a verdict in valid JSON."


def judge(model: str, task: str, output: str, rubric: str) -> tuple[bool, str]:
    provider = providers.resolve(model)[0]
    if provider == "mock":
        return judge_mock(rubric, output)
    if provider == "anthropic":
        return judge_claude(model, task, output, rubric)
    return judge_generic(model, task, output, rubric)
