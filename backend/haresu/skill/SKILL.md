---
name: haresu
description: Turns the "vibe" the user describes (which agents they want, for what, with what information) into a harness built in haresu over MCP, with its evals, and returns the link to watch and manage it in the visual tool. Use it when the user asks for a harness, a team of agents, an agent flow, "build me … in haresu", or wants to improve an existing harness.
---

# haresu: from vibe to harness

The user describes what they want in their own words. You build it in haresu with the MCP tools
`mcp__haresu__*` and give them the link. They watch it being built live on the canvas and then manage it by hand.

If the `mcp__haresu__*` tools are not available, tell them to run `haresu setup-claude` (or
`npx github:abyssace/haresu setup-claude`) once and restart Claude Code. Do not try to build the harness any other way.

## What to do

1. **Understand the vibe.** From their message, work out what the harness is for, which areas or task types it
   handles, what information they gave (pasted text, files, paths) and what limits they mentioned. If they gave
   file paths, read them. Ask only if something is missing without which you cannot build anything useful; one
   question, not a questionnaire. Decide the rest yourself and say so at the end.
2. **Create the harness** with `create_harness` (or use an existing one if they asked to improve it: `list_harnesses`,
   then `reset_canvas` and `view_state`). Give them the link it returns **right then**, so they can open it and watch
   it being built.
3. **Build the flow:** input → router (only if there is more than one agent) → agents → output. One agent per area,
   with a prompt that states its role, what to do when it does not know and the tone the user asked for.
4. **Load the information** with `load_documents`, one context per area, and connect each context and the
   `search_context` tool to its agent. Use only what the user gave you: do not invent policies, prices or business
   facts. If they gave no information, leave the contexts empty and tell them.
5. **Control:** add a global policy (`policy`) with reasonable limits and email redaction, and a meta-agent
   (`meta`). Do not fine-tune it: the user reviews that.
6. **Check** with `view_state` that `missing_to_run` is empty and no warnings remain, and try two or three tasks
   with `try_task`. Fix whatever fails.
7. **Write the evals** with `add_cases`: for each area, `dev` cases and `heldout` cases that ask the same thing
   in other words. Use `contains` when the answer must carry an exact fact from the context, `route` when it
   matters which agent it reaches, and `rubric` for everything else.
8. **Save and measure:** `save`, then `measure`. If dev cases fail, run `run_cycle` with 1 or 2 rounds so the
   meta-agent proposes improvements.

## What you should know

- New agents use the `mock` model, which needs no key and can only answer with what it finds in the context.
  With `mock`, a case only passes if the answer is in a document. If the user wants agents that reason or write,
  set `"model": "claude-opus-5-5"` on each agent and tell them the haresu server needs `ANTHROPIC_API_KEY`
  (or a key added in Settings).
- The meta-agent never sees the held-out cases; that is why splitting the cases well matters.
- Node ids are lowercase with underscores (`ag_payments`, `ctx_payments`).

## How to finish

Reply briefly, in the user's language:

- the link to the harness;
- what you built, in two or three lines (agents, what information you loaded, how many cases);
- the measurement result, exactly as it came out;
- what you decided on your own and what they need to review: always the Control section (the governance
  policy) and any context that was left empty.
