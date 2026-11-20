# haresu

A visual meta-harness: build your agents' harness on a canvas and let a meta-agent polish it version by version, with evals and a promotion gate that decides whether each change stays.

The harness is versioned data, and every new version has to earn its place against cases that whoever proposed it never saw.

## The four pieces

| Piece | On the canvas | What it does |
|---|---|---|
| **Shared context** | *Shared context* node and the `read_memory` / `write_memory` tools | Documents seen by the connected agents (pasted into the prompt or searched) and a scratchpad shared by the agents of a single run. |
| **Routing rules** | *Router* node | Ordered rules (keywords or regex) that decide which agent handles each task, with a default destination. |
| **Governance policies** | *Policy* node | Limits on steps, tool calls, tokens and answer size; allowed and blocked tools; patterns redacted from the answer. With no connections it is global; connected to an agent, it applies only to that agent. |
| **Run records** | *Runs* tab | Every run stores its step-by-step trace: routing, model calls, tools with input and output, redactions and violations. The meta-agent reads them to diagnose. |

## What the canvas shows

The canvas is not just an editor: what the harness did is painted on top of the graph.

- **Warnings per node.** Each node says what it is missing: an agent that receives no task, a context with no documents, a context nobody can search.
- **Path of a test.** When you test a task, the agent that handled it, what the router chose and the tools and contexts that were used are marked.
- **Results of the last measurement.** Each agent shows how many cases pass and which ones failed; each tool, how many times it was used.
- **What changed.** In a version proposed by the meta-agent, the touched nodes come marked, with their diagnosis, prediction and risk.
- **Two kinds of wire.** Solid for the path of the task; dotted for an agent's resources (tools, context, policies).

To load information, a context node accepts text files (`.txt`, `.md`, `.csv`, `.json` and similar) and splits them into documents. A whole harness, with its evals, is exported and imported as a JSON file from the ⋯ menu.

## Build it with AI

There are two ways to have an AI build the harness; in both you watch it being built live, with a cursor showing where it is working, and then you take over by hand.

**The canvas assistant.** Press “Build with AI” and describe what you want. With a real model (it needs the key of its provider, which you set in Settings) it builds the graph, writes prompts and test cases, and tries them. Without a key there is a template mode that builds a standard structure with the topics you mention.

**Your own agent, over MCP.** haresu ships an MCP server. Tell Claude Code (or any agent that speaks MCP) what you want and give it the information; it sends it to haresu, builds the harness, writes the evals, tests, saves, measures and runs the cycle.

```bash
npx github:abyssace/haresu setup-claude      # or, with the repo cloned: .venv/bin/python -m haresu setup-claude
```

That command registers the MCP server in Claude Code and installs the `haresu` skill. After that, in any folder, you just describe the vibe:

> Build me a support harness in haresu for my coffee shop: one agent for orders and another for complaints, warm tone. Here is the menu and the refund policy: …

Claude Code creates the harness, gives you the link, loads your information, writes the evals, tests, saves and measures. You open the link and watch it come together; when it finishes, you continue by hand on the canvas. If haresu is not running, the MCP server starts it by itself.

MCP tools: `list_harnesses`, `create_harness`, `view_state`, `add_node`, `edit_node`, `delete_node`, `connect`, `disconnect`, `load_documents`, `add_cases`, `try_task`, `save`, `measure`, `run_cycle`, `get_trend` and `reset_canvas`. To undo the installation: `claude mcp remove --scope user haresu` and delete `~/.claude/skills/haresu`. If you installed with npm, the MCP server runs with the Python in `~/.haresu/runtime`; so Claude Code does not wait for the first setup, run `npx github:abyssace/haresu --help` once beforehand (that command prepares the environment) and then `setup-claude`.

Governance is still decided by a person: the AI creates a policy with reasonable values and asks you to review the Control section.

## The cycle

```
measure the current version → the meta-agent proposes vNNN → measure vNNN → the gate decides
```

- Eval cases are split into **dev** and **held-out**. The meta-agent can only read dev records.
- The **gate** accepts a candidate if held-out rises at least by the configured amount, dev does not drop, there are no new violations and locked nodes are intact.
- A patch that touches a policy needs a person's approval.
- The meta-agent cannot edit the evals, locked policies or its own node.

## Try it

You need **Python 3.10 or newer** and **Node 18 or newer**.

### With npm

```bash
npx github:abyssace/haresu
```

The first time, it prepares a Python environment in `~/.haresu/runtime` (a minute or two); then it starts the interface at http://127.0.0.1:8000 and opens it. Your data stays in `~/.haresu/workspace`. Other commands: `npx github:abyssace/haresu demo`, `... setup-claude`, `... --help`.

Once the package is published to npm, `npx haresu` will be enough.

### Cloning the repo

```bash
git clone https://github.com/abyssace/haresu.git
cd haresu/backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m haresu serve --open      # http://127.0.0.1:8000
```

The interface comes already compiled in `backend/haresu/static`. If you change something in `frontend/`, run `npm run build:ui` from the root to rebuild it. With a copy of the repo, data stays in `backend/workspace`. For another folder, set `HARESU_HOME`.

When you open the interface you name your harness and start with an **empty canvas**:

1. Press **+** and add an Input, an Agent and an Output. Connect them by dragging from one dot to the other; the canvas only allows connections that mean something.
2. Click a node to configure it. A label at the bottom says what is missing to be able to run.
3. Press **Test** and type a task. Press **Save**.
4. In **Evals** write test cases; in **Cycle**, measure and let the meta-agent propose improvements.

The **Help** section explains every step, every connection and every word, and tells you which step you are on. From there you can also create an already built example harness (store support), which runs with a fake model (`mock`) and needs no key.

The whole cycle of the example, from the terminal:

```bash
.venv/bin/python -m haresu demo      # or: npx github:abyssace/haresu demo
```

```
Round 1: measured v000
Round 1: the meta-agent proposed v001
Round 1: measured v001
Round 1: v001 accepted
Round 2: no candidate — The meta-agent proposed no changes.

iter     version      dev  held-out     gain  status
iter-001 v000       0.667     0.600           baseline
iter-002 v001       1.000     1.000   +0.400  accepted
```

The example's initial version lacks a rule for refunds: those tasks fall to the general agent, which cannot see the payments context. The meta-agent finds this in the dev records and adds the rule.

To develop the interface with hot reload, keep the backend running and use `npm run dev` in `frontend/` (the proxy sends `/api` to port 8000).

### Choosing a model and key

Models are **chosen from a list** built from the providers that have a key set: haresu asks each provider for the models your key can use. The list appears in the agents, the meta-agent, the canvas assistant and the evals judge, and always includes the test model `mock`, which needs no key. “Write another model…” lets you type one by hand.

- **My models** (Settings): for providers with hundreds of models (OpenRouter, OpenCode), you register the ones you use with the name you want and, optionally, their own key (one you already saved or a new one). While a provider has registered models, the selectors show only those, with a link to see them all. Each model's key is used on every call; if you remove it, the model goes back to the provider's key. Registered models live in `workspace/settings.json`; keys, always in `secrets.json`.
- **Default model** (Settings): new agents and meta-agents receive it, whether created by hand, with the assistant or from Claude Code. It does not change existing ones.
- **Assistant model**: it remembers what you choose; until you choose another, it follows the default model.
- **The key** of each model is its provider's. Internally a model is `provider:model` (for example `anthropic:claude-opus-5-5`); names that start with `claude`, `gpt-`, `gemini`, `mistral-` or `deepseek` are recognized without a prefix.

Providers: Anthropic (its own API), OpenAI, Google (Gemini), OpenRouter, Groq, Mistral and DeepSeek (through their OpenAI-compatible API), and OpenCode.

**OpenCode** uses a single key (`OPENCODE_API_KEY`) for both its plans, Go (subscription) and Console (pay as you go). Its model list is public and says, for each model, which call format it uses (`chat-completions`, `messages` or `responses`) and its address, so haresu calls each one its own way. The selector shows only chat models with tools, grouped by plan; a model is written `opencode:go/<model>` or `opencode:console/<model>`. “Test” on the OpenCode key makes a minimal call with a free model, because the public list does not validate the key. Keys are added in **Settings** (several per provider, choosing which one is used) and stored in `workspace/secrets.json` with permissions for your user only; preferences go in `workspace/settings.json`. These variables are also read: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY` and `DEEPSEEK_API_KEY`. To point a provider at a proxy or gateway, set `HARESU_<PROVIDER>_BASE_URL` (for example `HARESU_OPENAI_BASE_URL`).

## How it is built

```
backend/haresu/
  spec.py         the harness as a graph: validation, diff and patch operations
  engine.py       runs a task through the graph and builds the trace
  governance.py   merges policies per agent and checks the meta-agent's patches
  tools.py        bundled tools
  providers.py    fake model and the real providers
  evals.py        grades runs, repeats measurements and summarizes by split
  judge.py        the judge that grades rubrics
  gate.py         the promotion gate
  meta.py         the meta-agent (heuristic and LLM)
  assistant.py    the assistant that builds the harness and emits every step live
  mcp_server.py   MCP server for external agents
  loop.py         measure, propose, decide, cycle and trend
  store.py        everything on disk, as JSON
  api.py          HTTP API (FastAPI)
frontend/src/     canvas, evals, cycle with chart, runs and help (React + React Flow)
backend/haresu/static/   the compiled interface (regenerate with `npm run build:ui`)
backend/haresu/skill/    the Claude Code skill that `haresu setup-claude` installs
bin/haresu.js     npm launcher: prepares a Python environment and runs `python -m haresu`
scripts/          build-ui.mjs (builds and copies the interface) and test-ui.mjs (smoke test)
```

Data lives in `$HARESU_HOME` or, if it is not set, in `backend/workspace` (repo copy) or `~/.haresu/workspace` (installed with npm or pip):

```
workspace/harnesses/<harness>/
  CURRENT                  current version
  evals.json               cases and gate
  versions/v000/spec.json  the graph
  versions/v000/meta.json  parent, author, patch with diagnosis, prediction and risk
  iterations/iter-001.json summary of a measurement and the gate's decision
  runs/<id>.json           run record with its trace
```

## Tests

```bash
cd backend && .venv/bin/python -m pytest      # backend

npm --prefix frontend install                 # once
npm run test:ui                               # interface: starts a test backend and runs the tests in frontend/
```

## Evals

Each case combines whichever checks you want:

- **Must contain / must not contain:** exact text in the answer.
- **Expected route:** which agent the task must reach.
- **Rubric:** a natural-language sentence graded by a **judge**. With the `mock` model the judge only compares words; with a real model it understands meaning.

In the Evals tab you also choose the **repetitions per measurement**. With more than one, the summary reports the mean and standard error, and the gate requires the held-out improvement to exceed that noise in addition to the configured minimum.

## The fake meta-agent

Without a key, the meta-agent is a set of heuristics over the dev records. For each failed case it looks for the document that answers the task and proposes, depending on what it finds:

1. a routing rule, if the task reached an agent that cannot see that document;
2. connecting the context to the agent, if no reachable agent sees it;
3. connecting `search_context`, if the agent sees the document but cannot search it.

If the cause is a policy that blocks the tool, or no context has the answer, it reports it in the diagnosis and changes nothing: that is for a person to decide.

## Status

This is an MVP. What you should know:

- **The path with real models is not tested end to end.** The full cycle is verified with the fake model; real models from every provider were tested against fake servers, but never with a real key.
- **The fake model and meta-agent are for testing the plumbing, not for measuring quality.** They do not rewrite prompts: only the LLM meta-agent does.
- **The shared scratchpad lasts one run.** There is no memory between runs, on purpose: it would make measurements depend on order.
- **No authentication.** The server is meant for local use.
- On macOS with Python 3.13, the `haresu` command may fail if the system flags the editable install's `.pth` file as hidden. `python -m haresu` always works.

## License

Apache-2.0. See [LICENSE](LICENSE).
