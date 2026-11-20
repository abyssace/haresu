import { useEffect, useState } from "react";
import { api } from "../api";

const WORDS: [string, string][] = [
  ["Harness", "Everything around the model that makes it do a job: which agent each task goes to, what context it sees, which tools it has and which limits it respects. Here you draw it as a graph."],
  ["Version", "A snapshot of the graph. Every time you save, another one is created (v000, v001…). The “current” version is the one that counts."],
  ["Candidate", "A saved version that does not replace the current one yet. It is measured first."],
  ["Dev cases", "Test tasks the meta-agent can see. It learns from the ones that fail."],
  ["Held-out cases", "Test tasks hidden from the meta-agent. They check that a change really improves things and does not just memorize the dev cases."],
  ["Meta-agent", "An agent that reads the records of the runs that failed and proposes a new version of the harness."],
  ["Gate", "The rules that decide whether a candidate replaces the current version: it has to go up on held-out, not drop on dev and not break governance."],
  ["Run record", "What happened in a run, step by step: who it was routed to, what the model asked for, what each tool returned."],
];

const LINKS: [string, string][] = [
  ["Input → Router or Agent", "where the task comes in."],
  ["Router → Agent", "the agents the router chooses from."],
  ["Agent → Agent", "a chain: the first one's answer is the second one's task."],
  ["Agent → Output", "the final answer."],
  ["Context → Agent", "that agent sees those documents."],
  ["Tool → Agent", "that agent can use it."],
  ["Policy → Agent", "applies only to that agent. Unconnected, it applies to all of them."],
];

const TOOLS: [string, string][] = [
  ["Search the context", "Searches the documents of the contexts connected to the agent. Without it, the agent only sees contexts that are in “pasted into the prompt” mode."],
  ["Calculator", "Does exact arithmetic."],
  ["Write to the scratchpad", "Leaves a note that other agents in the same run can read."],
  ["Read the scratchpad", "Reads the notes other agents left. Useful in Agent → Agent chains."],
];

export default function HelpView({ h, tick, onGo, onExample }: {
  h: string; tick: number; onGo: (tab: string) => void; onExample: () => void;
}) {
  const [guide, setGuide] = useState<any>(null);

  useEffect(() => {
    if (!h) { setGuide(null); return; }
    api.get(`/harnesses/${h}/guide`).then(setGuide).catch(() => setGuide(null));
  }, [h, tick]);

  return (
    <div className="page help">
      <h1>Help</h1>

      {guide && (
        <section>
          <h2>Where you are</h2>
          <ol className="steps">
            {guide.steps.map((s: any, i: number) => (
              <li key={s.id} className={s.id === guide.next ? "step now" : s.done ? "step done" : "step"}>
                <span className="step-n">{s.done ? "✓" : i + 1}</span>
                <span className="step-body">
                  <span className="step-title">{s.title}</span>
                  <span className="step-text">{s.text}</span>
                </span>
                {s.id === guide.next && <button className="primary" onClick={() => onGo(s.tab)}>{s.action}</button>}
              </li>
            ))}
          </ol>
          <p className="muted">Steps 3 to 5 repeat: each round can leave a better version. In Cycle, “Automatic cycle” runs them by itself.</p>
        </section>
      )}

      <section>
        <h2>Build it with AI</h2>
        <p>
          On the Canvas, press <b>Build with AI</b> and describe what you want. The assistant adds the nodes, connects them, writes test cases and
          tries a task; a cursor labeled “AI” shows live where it is working. While it builds, the canvas is paused; when it finishes you can move,
          edit or delete anything by hand.
        </p>
        <dl className="defs">
          <div><dt>Template (no key)</dt><dd>Builds a standard structure with the topics you mention (“support for payments, shipping and account”). It only writes test cases if the contexts already have documents. It does not understand free-form instructions.</dd></div>
          <div><dt>A real model</dt><dd>Understands what you ask, writes prompts and cases tailored to you, tests them and fixes what fails. It needs a key for the model's provider, which you add in Settings.</dd></div>
          <div><dt>What is up to you</dt><dd>Review the Control section: the assistant creates the governance policy with reasonable values, but the limits and what gets redacted are your decision.</dd></div>
        </dl>
      </section>

      <section>
        <h2>Connect your own agent (MCP)</h2>
        <p>
          haresu ships an MCP server: any agent that speaks MCP (Claude Code, Cursor, your own) can create harnesses, build the graph, write evals,
          test, save, measure and run the cycle. Whatever it does shows up live on this canvas, with its cursor.
        </p>
        <ol className="plain">
          <li>Once: run <code>haresu setup-claude</code> (or <code>npx github:abyssace/haresu setup-claude</code>). It registers the MCP server in Claude Code and installs the skill.</li>
          <li>In Claude Code, describe the vibe: “build me a support harness for my coffee shop in haresu, with this menu…”. It gives you a link; open it to watch it being built.</li>
        </ol>
        <p className="muted">While the agent builds, the canvas is paused. Its changes live in a working canvas until it saves; save yours first if you have unsaved changes.</p>
      </section>

      <section>
        <h2>Build the harness on the Canvas</h2>
        <ol className="plain">
          <li>Press <b>+</b> (top left) and add an <b>Input</b>, an <b>Agent</b> and an <b>Output</b>.</li>
          <li>Connect them: drag from the right dot of one node to the left dot of the next. The canvas only allows connections that mean something.</li>
          <li>Click a node to configure it. The panel opens on the right and closes with ×.</li>
          <li>Below, a label says what is missing to be able to run. When it says “Ready to run”, press <b>Test</b> and type a task.</li>
          <li>Press <b>Save</b>. To delete a node or a connection, select it and press Delete.</li>
        </ol>
        <h3>What the canvas shows you</h3>
        <dl className="defs">
          <div><dt>Solid and dotted wires</dt><dd>Solid ones are the path of the task. Dotted ones are resources an agent has at hand: tools, context and policies.</dd></div>
          <div><dt>Warnings</dt><dd>A node with “1 warning” has something pending. Hover over it or open it to read it.</dd></div>
          <div><dt>Path</dt><dd>After testing a task, the canvas marks where it went: what the router chose, which agent handled it and which tools it used.</dd></div>
          <div><dt>Results</dt><dd>If the version was already measured in Cycle, the “Results” button shows on each agent how many cases pass and which ones failed.</dd></div>
          <div><dt>What changed</dt><dd>In a version proposed by the meta-agent, the nodes that changed come marked and the button explains the diagnosis.</dd></div>
        </dl>
        <h3>Load information</h3>
        <p>
          Add a <b>Shared context</b> node, open it and press “Load text files”, or type the documents by hand. Connect it to the agents that should see it.
          In the ⋯ menu you can export the whole harness to a file and import it on another machine.
        </p>
        <h3>What each connection means</h3>
        <dl className="defs">
          {LINKS.map(([a, b]) => <div key={a}><dt>{a}</dt><dd>{b}</dd></div>)}
        </dl>
        <h3>Tools</h3>
        <dl className="defs">
          {TOOLS.map(([a, b]) => <div key={a}><dt>{a}</dt><dd>{b}</dd></div>)}
        </dl>
      </section>

      <section>
        <h2>Improve it with the Cycle</h2>
        <ol className="plain">
          <li>In <b>Evals</b>, write test tasks and how you know the answer is good. Mark some as dev and others as held-out.</li>
          <li>In <b>Cycle</b>, measure the current version.</li>
          <li>Ask the meta-agent for a proposal, or edit the Canvas yourself and save a candidate.</li>
          <li>Measure the candidate and press Decide. If it passes the gate, it becomes the current version.</li>
        </ol>
        <p className="muted">In <b>Runs</b> you can open any run and see what happened step by step.</p>
      </section>

      <section>
        <h2>Words</h2>
        <dl className="defs">
          {WORDS.map(([a, b]) => <div key={a}><dt>{a}</dt><dd>{b}</dd></div>)}
        </dl>
      </section>

      <section>
        <h2>See a built example</h2>
        <p>Creates a separate, already built harness, with three support agents, their context, a policy and test cases. It runs without a key and is useful to see the whole Cycle. It does not touch your harnesses.</p>
        <button onClick={onExample}>Create the example harness</button>
      </section>

      <section>
        <h2>Use real models instead of the test model</h2>
        <p>New nodes use the <code>mock</code> model, which needs no key and is useful to try the flow. To use real models:</p>
        <ol className="plain">
          <li>Open <button className="link" onClick={() => onGo("settings")}>Settings</button> and add the key of the provider you want to use: Anthropic, OpenAI, Google, OpenRouter, Groq, Mistral, DeepSeek or OpenCode (one key for its Go and Console plans). You can save several per provider and choose which one is used.</li>
          <li>In Settings, choose the <b>default model</b>: it is what new agents and new meta-agents receive, whether you create them by hand, with the assistant or from Claude Code.</li>
          <li>To change the model of an agent that already exists, open it and choose from the list. Below it you will see which key it will use.</li>
        </ol>
        <p>
          <b>My models.</b> Providers such as OpenRouter or OpenCode have hundreds of models. In Settings, “My models”, register the ones you really use:
          the model id, the name you want and, optionally, its own key (for example one “Work” and another “Personal”). From then on, that
          provider's selectors show only your models, and a link lets you see all of them when needed.
        </p>
        <p>
          The model list comes from the providers that have a key set and is requested from the provider itself, so it always brings the models your key
          can use. “Write another model…” lets you type one by hand as <code>provider:model</code>. The canvas assistant and the evals judge choose
          their model from the same list.
        </p>
        <p>haresu uses the key of each model's provider, so one harness can mix providers: one agent with Claude, another with OpenAI and a judge with yet another model.</p>
        <p className="muted">If you prefer not to save the key in haresu, define the provider's environment variable (for example <code>OPENAI_API_KEY</code>) when starting the server.</p>
      </section>
    </div>
  );
}
