// Smoke test of the interface against a real backend.
// Usage: start the backend with an empty workspace and run
//   HARESU_URL=http://127.0.0.1:8000 npm run smoke
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createServer } from "node:http";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import App from "./App";

const BASE: string | undefined = (globalThis as any).process?.env?.HARESU_URL;
const errors: string[] = [];

beforeAll(() => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = ((url: any, init?: any) => realFetch(`${BASE}${url}`, init)) as typeof fetch;
  class RO { observe() {} unobserve() {} disconnect() {} }
  (globalThis as any).ResizeObserver = RO;
  (globalThis as any).DOMMatrixReadOnly = class { m22 = 1; constructor() {} };
  vi.spyOn(console, "error").mockImplementation((...args) => { errors.push(args.map(String).join(" ")); });
});
afterEach(cleanup);

const post = (path: string, body: unknown) =>
  fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());

const get = (path: string) => fetch(path).then((r) => r.json());

async function pick(id: string) {
  const select = (await screen.findByLabelText("Harness")) as HTMLSelectElement;
  await waitFor(() => expect(Array.from(select.options).some((o) => o.value === id)).toBe(true));
  fireEvent.change(select, { target: { value: id } });
}

test.skipIf(!BASE)("build a harness from scratch on the empty canvas", async () => {
  const created = await post("/api/harnesses", { name: `Cero ${Date.now()}`, example: "blank" });
  render(<App />);
  await pick(created.id);

  // An empty canvas already has the node list open.
  await screen.findByText("Empty canvas");
  fireEvent.click(await screen.findByText("Input", { selector: ".add-name" }));
  fireEvent.click(screen.getByText("Agent", { selector: ".add-name" }));
  fireEvent.click(screen.getByText("Output", { selector: ".add-name" }));
  fireEvent.click(screen.getByText("Calculator", { selector: ".add-name" }));
  await waitFor(() => expect(document.querySelectorAll(".plate").length).toBe(4));

  // Each node says what it is missing.
  await waitFor(() => expect(document.querySelectorAll(".tag.warn").length).toBe(4));

  // The connections are missing: the to-do label says so.
  const pill = await screen.findByText(/Missing \d+ things? to be able to run/);
  fireEvent.click(pill);
  await screen.findByText("Connect the Input to a Router or to an Agent.");

  // The selected node's panel appears and closes.
  await screen.findByLabelText("Node settings");
  fireEvent.click(screen.getByLabelText("Close"));
  expect(screen.queryByLabelText("Node settings")).toBeNull();

  // An incomplete draft can be saved.
  fireEvent.click(screen.getByText("Save"));
  await screen.findByText(/Saved as v001/);

  // Help lives in its own section and knows which step you are on.
  fireEvent.click(screen.getByText("Help"));
  await screen.findByText("Where you are");
  await screen.findByText("Go to Canvas");
  expect(errors).toEqual([]);
}, 30000);

test.skipIf(!BASE)("test, cycle, runs and evals with the example", async () => {
  const created = await post("/api/harnesses", { name: `Smoke ${Date.now()}`, example: "support" });
  render(<App />);
  await pick(created.id);
  await screen.findByText("Payments agent");
  await screen.findByText("Ready to run");

  // Test a task in the test panel.
  fireEvent.click(screen.getByText("Test"));
  fireEvent.change(screen.getByLabelText("Test task"), { target: { value: "My email is ana@example.com and my package arrived damaged" } });
  fireEvent.click(screen.getByText("Send"));
  await screen.findByText(/\[REDACTED\]/, { selector: ".bubble" });
  await screen.findByText(/Handled by ag_shipping/);
  // The path stays marked on the nodes.
  await screen.findByText("step 1", { selector: ".tag.run" });
  await screen.findByText("used", { selector: ".tag.run" });

  // Automatic cycle: the chart appears and v001 is accepted.
  fireEvent.click(screen.getByText("Cycle", { selector: "button.tab" }));
  fireEvent.click(await screen.findByText("Automatic cycle"));
  await screen.findByText("Score per iteration", {}, { timeout: 15000 });
  await waitFor(() => expect(screen.getAllByText("accepted").length).toBeGreaterThan(0));

  // The accepted version opens on the canvas with what changed.
  fireEvent.click(screen.getAllByText("v001", { selector: "button.link" })[0]);
  await screen.findByText("changed", { selector: ".tag.diff" });
  fireEvent.click(screen.getByText("What changed"));
  await screen.findByText("Diagnosis");

  // Results layer: each agent says how many cases pass.
  fireEvent.click(screen.getByText("Results"));
  await screen.findByText("6 of 6 cases pass");

  // Harness menu.
  fireEvent.click(screen.getByLabelText("Harness options"));
  await screen.findByText("Export to a file");

  fireEvent.click(screen.getByText("Runs", { selector: "button.tab" }));
  fireEvent.click((await screen.findAllByText("d3"))[0]);
  await screen.findByText("Step by step");
  fireEvent.click(screen.getByText("Evals", { selector: "button.tab" }));
  await screen.findByText("Rubric for the judge");
  expect(errors).toEqual([]);
}, 30000);

test.skipIf(!BASE)("the assistant builds the harness live on the empty canvas", async () => {
  const created = await post("/api/harnesses", { name: `IA ${Date.now()}`, example: "blank" });
  render(<App />);
  await pick(created.id);
  await screen.findByText("Empty canvas");

  fireEvent.click(screen.getByText("Build with AI", { selector: "button" }));
  fireEvent.change(screen.getByLabelText("Message for the assistant"), { target: { value: "support for payments, shipping and account" } });
  fireEvent.click(screen.getByText("Send", { selector: ".ai-pop button" }));

  // While it works the AI cursor is visible and the canvas fills up.
  await waitFor(() => expect(document.querySelector(".ai-cursor")).not.toBeNull(), { timeout: 5000 });
  await waitFor(() => expect(document.querySelectorAll(".plate").length).toBeGreaterThan(2), { timeout: 10000 });
  await screen.findByText("The AI is building…");

  // When done: 3 agents, router, contexts, policy and meta-agent; the cursor goes away.
  await screen.findByText(/Review the Control section/, {}, { timeout: 25000 });
  await waitFor(() => expect(document.querySelector(".ai-cursor")).toBeNull());
  expect(document.querySelectorAll(".plate").length).toBe(12);
  await screen.findByText("Payments agent");
  await screen.findByText(/Ready to run/);

  // What it built can still be managed by hand and saved.
  fireEvent.click(screen.getByText("Global governance"));
  await screen.findByLabelText("Node settings");
  fireEvent.click(screen.getByLabelText("Close"));
  fireEvent.click(screen.getByText("Save"));
  await screen.findByText(/Saved as v001/);
  expect(errors).toEqual([]);
}, 40000);

test.skipIf(!BASE)("an external agent edits and the canvas replays it live", async () => {
  const created = await post("/api/harnesses", { name: `External ${Date.now()}`, example: "blank" });
  const agent = (tool: string, args: object = {}) => post(`/api/harnesses/${created.id}/agent`, { tool, args, agent: "Claude Code" });
  render(<App />);
  await pick(created.id);
  await screen.findByText("Empty canvas");
  await new Promise((r) => setTimeout(r, 1300)); // the canvas is already listening

  await agent("add_node", { id: "input", type: "input", name: "Input" });
  await agent("add_node", { id: "ag", type: "agent", name: "Remote agent" });
  await agent("connect", { source: "input", target: "ag" });

  await screen.findByText("Claude Code is building…", {}, { timeout: 6000 });
  await waitFor(() => expect(document.querySelector(".ai-cursor")?.textContent).toMatch(/^Claude Code · /), { timeout: 6000 });
  await screen.findByText("Remote agent", {}, { timeout: 8000 });
  await waitFor(() => expect(document.querySelector(".ai-cursor")).toBeNull(), { timeout: 8000 });
  expect(document.querySelectorAll(".plate").length).toBe(2);
  expect(errors).toEqual([]);
}, 40000);

test.skipIf(!BASE)("a link with #id opens that harness", async () => {
  await post("/api/harnesses", { name: `Primero ${Date.now()}`, example: "blank" });
  const target = await post("/api/harnesses", { name: `Zeta link ${Date.now()}`, example: "support" });
  location.hash = `#${target.id}`;
  render(<App />);
  const select = (await screen.findByLabelText("Harness")) as HTMLSelectElement;
  await waitFor(() => expect(select.value).toBe(target.id));
  await screen.findByText("Payments agent");
  expect(errors).toEqual([]);
}, 20000);

test.skipIf(!BASE)("choose each provider's key from Settings", async () => {
  const created = await post("/api/harnesses", { name: `Keys ${Date.now()}`, example: "blank" });
  render(<App />);
  await pick(created.id);
  fireEvent.click(screen.getByText("Settings", { selector: "button.tab" }));
  await screen.findByText(/Each model is written as/);
  const type = (id: string, value: string) => fireEvent.change(document.getElementById(id)!, { target: { value } });
  const save = (id: string) => fireEvent.click(document.getElementById(id)!.closest("form")!.querySelector("button")!);

  // An Anthropic key becomes the one in use for its provider, and the top bar reflects it.
  type("key-name-anthropic", "Test");
  type("key-value-anthropic", "sk-ant-api03-" + "Z".repeat(40));
  save("key-value-anthropic");
  await screen.findByText("in use: “Test” ····ZZZZ");
  await screen.findByText("Keys: Anthropic", { selector: ".key-chip" });
  expect(document.body.textContent).not.toContain("Z".repeat(40));
  expect((document.getElementById("key-value-anthropic") as HTMLInputElement).value).toBe("");

  // Each key goes with its provider: an Anthropic one does not go into OpenAI.
  type("key-value-openai", "sk-ant-api03-" + "Y".repeat(40));
  save("key-value-openai");
  await screen.findByText(/That key belongs to Anthropic, not to OpenAI/);
  type("key-name-openai", "Mine");
  type("key-value-openai", "sk-proj-" + "X".repeat(40));
  save("key-value-openai");
  await screen.findByText("Keys: Anthropic, OpenAI", { selector: ".key-chip" });

  // Cleanup: remove the test keys.
  for (const id of ["anthropic", "openai"]) {
    await waitFor(() => expect(document.querySelector(`[data-provider="${id}"] .danger-text`)).not.toBeNull());
    fireEvent.click(document.querySelector(`[data-provider="${id}"] .danger-text`)!);
    fireEvent.click(await screen.findByText("Yes, remove"));
    await waitFor(() => expect(document.querySelector(`[data-provider="${id}"] .key`)).toBeNull());
  }
  await screen.findByText("No keys", { selector: ".key-chip" });
}, 30000);


test.skipIf(!BASE)("choose the model from a list made from the keys that are set", async () => {
  // A fake provider that serves its model list (the haresu server was started pointing at it).
  const fake = createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ data: [{ id: "fake-chat-2" }, { id: "text-embedding-fake" }, { id: "fake-chat-1" }] }));
  });
  await new Promise<void>((r) => fake.listen(8766, "127.0.0.1", r));
  try {
    const created = await post("/api/harnesses", { name: `Models ${Date.now()}`, example: "blank" });
    render(<App />);
    await pick(created.id);

    // Without keys there is only the test model, and the interface says so.
    fireEvent.click(screen.getByText("Settings", { selector: "button.tab" }));
    await screen.findByText(/Only the test model is available/);
    const type = (id: string, value: string) => fireEvent.change(document.getElementById(id)!, { target: { value } });
    type("key-name-openai", "Mine");
    type("key-value-openai", "sk-proj-" + "X".repeat(40));
    fireEvent.click(document.getElementById("key-value-openai")!.closest("form")!.querySelector("button")!);

    // With the key, the list comes from the provider (without the models that do not chat).
    const def = (await screen.findByLabelText("Default model")) as HTMLSelectElement;
    await within(def).findByText("fake-chat-2");
    expect(screen.queryByText("text-embedding-fake")).toBeNull();
    fireEvent.change(def, { target: { value: "openai:fake-chat-2" } });
    await waitFor(() => expect(def.value).toBe("openai:fake-chat-2")); // it shows as chosen when the app already knows it
    expect((await get("/api/preferences")).default_model).toBe("openai:fake-chat-2");

    // A new agent is born with that model, and the panel says which key it uses.
    fireEvent.click(screen.getByText("Canvas", { selector: "button.tab" }));
    await screen.findByText("Empty canvas"); // an empty canvas already has the node list open
    fireEvent.click(await screen.findByText("Agent", { selector: ".add-name" }));
    const picker = (await screen.findByLabelText("Model")) as HTMLSelectElement;
    expect(picker.value).toBe("openai:fake-chat-2");
    await screen.findByText(/OpenAI: uses the key “Mine” ····XXXX/);
    fireEvent.change(picker, { target: { value: "mock" } });
    await screen.findByText("Test model: no key needed.");
    fireEvent.change(picker, { target: { value: "openai:fake-chat-1" } });
    await screen.findByText(/OpenAI: uses the key/);
    fireEvent.click(screen.getByLabelText("Close"));

    // The assistant also chooses from the list, and remembers what was chosen.
    fireEvent.click(screen.getByText("Build with AI", { selector: "button" }));
    const ai = (await screen.findByLabelText("Assistant model")) as HTMLSelectElement;
    expect(ai.value).toBe("openai:fake-chat-2");
    fireEvent.change(ai, { target: { value: "openai:fake-chat-1" } });
    await waitFor(async () => expect((await get("/api/preferences")).assistant_model).toBe("openai:fake-chat-1"));

    // The evals judge, the same.
    fireEvent.click(screen.getByText("Evals", { selector: "button.tab" }));
    expect(((await screen.findByLabelText("Judge model")) as HTMLSelectElement).options.length).toBeGreaterThan(3);
    expect(errors).toEqual([]);
  } finally {
    await fetch("/api/preferences", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_model: "mock", assistant_model: "" }) }); // empty: the assistant goes back to following the default model
    const settings = await get("/api/settings");
    for (const p of settings.providers) for (const k of p.keys) await fetch(`/api/settings/keys/${k.id}`, { method: "DELETE" });
    await new Promise<void>((r) => fake.close(() => r()));
  }
}, 40000);

test.skipIf(!BASE)("OpenCode: one key, two plans grouped in the selector", async () => {
  const entry = (id: string, name: string) => ({ id, name, type: "chat", capabilities: { tools: { supported: true } },
    api: { "chat-completions": { base_url: "http://127.0.0.1:8767/oc/openai/v1" } } });
  const fake = createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    const data = req.url === "/oc/go/v1/models" ? [entry("fake-go-1", "Fake Go 1")]
      : req.url === "/oc/v1/models" ? [entry("fake-con-1", "Fake Console 1"), { id: "fake-emb", name: "Emb", type: "embedding" }] : [];
    res.end(JSON.stringify({ data }));
  });
  await new Promise<void>((r) => fake.listen(8767, "127.0.0.1", r));
  try {
    const created = await post("/api/harnesses", { name: `OpenCode ${Date.now()}`, example: "blank" });
    render(<App />);
    await pick(created.id);
    fireEvent.click(screen.getByText("Settings", { selector: "button.tab" }));
    await screen.findByText(/One key works for both plans/);
    const type = (id: string, value: string) => fireEvent.change(document.getElementById(id)!, { target: { value } });
    type("key-name-opencode", "Mine");
    type("key-value-opencode", "oc-" + "W".repeat(40));
    fireEvent.click(document.getElementById("key-value-opencode")!.closest("form")!.querySelector("button")!);
    await screen.findByText("Keys: OpenCode", { selector: ".key-chip" });

    // The selector groups by plan and leaves out what is not chat.
    const def = (await screen.findByLabelText("Default model")) as HTMLSelectElement;
    await screen.findByText(/Fake Go 1 \(fake-go-1\)/, { selector: "option" });
    expect(Array.from(def.querySelectorAll("optgroup")).map((g) => g.label)).toEqual(["OpenCode Console (pay as you go)", "OpenCode Go (subscription)"]);
    expect(screen.queryByText("Emb")).toBeNull();
    fireEvent.change(def, { target: { value: "opencode:go/fake-go-1" } });
    await waitFor(() => expect(def.value).toBe("opencode:go/fake-go-1"));

    // A new agent is born with that model and the panel says which key it uses.
    fireEvent.click(screen.getByText("Canvas", { selector: "button.tab" }));
    await screen.findByText("Empty canvas");
    fireEvent.click(await screen.findByText("Agent", { selector: ".add-name" }));
    expect(((await screen.findByLabelText("Model")) as HTMLSelectElement).value).toBe("opencode:go/fake-go-1");
    await screen.findByText(/OpenCode \(Go and Console\): uses the key “Mine” ····WWWW/);
    expect(errors).toEqual([]);
  } finally {
    await fetch("/api/preferences", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_model: "mock", assistant_model: "" }) });
    const settings = await get("/api/settings");
    for (const p of settings.providers) for (const k of p.keys) await fetch(`/api/settings/keys/${k.id}`, { method: "DELETE" });
    await new Promise<void>((r) => fake.close(() => r()));
  }
}, 40000);

test.skipIf(!BASE)("registering OpenRouter models shortens the agents' list", async () => {
  const ids = Array.from({ length: 40 }, (_, i) => `vendor/model-${i + 1}`);
  const fake = createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ data: req.url === "/or/v1/models" ? ids.map((id) => ({ id })) : [] }));
  });
  await new Promise<void>((r) => fake.listen(8766, "127.0.0.1", r));
  const type = (id: string, value: string) => fireEvent.change(document.getElementById(id)!, { target: { value } });
  const addAgent = async () => {
    fireEvent.click(screen.getByText("Canvas", { selector: "button.tab" }));
    await screen.findByText("Empty canvas");
    fireEvent.click(await screen.findByText("Agent", { selector: ".add-name" }));
    return (await screen.findByLabelText("Model")) as HTMLSelectElement;
  };
  try {
    const created = await post("/api/harnesses", { name: `Registry ${Date.now()}`, example: "blank" });
    render(<App />);
    await pick(created.id);
    fireEvent.click(screen.getByText("Settings", { selector: "button.tab" }));
    await screen.findByText(/Each model is written as/);
    type("key-name-openrouter", "Main");
    type("key-value-openrouter", "sk-or-" + "P".repeat(40));
    fireEvent.click(document.getElementById("key-value-openrouter")!.closest("form")!.querySelector("button")!);
    await screen.findByText("Keys: OpenRouter", { selector: ".key-chip" });

    // Without registering anything the full list appears, and the interface suggests registering the ones you use.
    let picker = await addAgent();
    await waitFor(() => expect(picker.querySelectorAll("optgroup option").length).toBe(40));
    await screen.findByText(/OpenRouter offers 40 models/);

    // Two models are registered: one with the provider's key and another with its own new key.
    fireEvent.click(screen.getByText("Settings", { selector: "button.tab" }));
    await screen.findByText("My models");
    type("reg-model", "vendor/model-7");
    type("reg-name", "My favorite");
    fireEvent.click(screen.getByText("Register model"));
    await screen.findByText(/Registered “My favorite”/);
    type("reg-model", "vendor/model-9");
    type("reg-name", "With own key");
    type("reg-key", "__new");
    type("reg-newkey-name", "Work");
    type("reg-newkey", "sk-or-" + "Y".repeat(40));
    fireEvent.click(screen.getByText("Register model"));
    await screen.findByText(/Registered “With own key”/);
    await screen.findByText(/key “Work” ····YYYY/);
    expect(document.body.textContent).not.toContain("Y".repeat(40));

    // Now the selector shows only the registered ones, with their name; a link lets you see all.
    picker = await addAgent();
    await waitFor(() => expect(Array.from(picker.querySelectorAll("optgroup option")).map((o) => o.textContent))
      .toEqual(["My favorite (vendor/model-7)", "With own key (vendor/model-9)"]));
    expect(screen.queryByText(/OpenRouter offers 40 models/)).toBeNull();
    fireEvent.click(await screen.findByText("See all models (38 more)"));
    await waitFor(() => expect(picker.querySelectorAll("optgroup option").length).toBe(40));
    fireEvent.click(screen.getByText("See only my models"));
    await waitFor(() => expect(picker.querySelectorAll("optgroup option").length).toBe(2));

    // Each registered model uses its key: the panel says which.
    fireEvent.change(picker, { target: { value: "openrouter:vendor/model-9" } });
    await screen.findByText(/OpenRouter: uses the key “Work” ····YYYY/);
    fireEvent.change(picker, { target: { value: "openrouter:vendor/model-7" } });
    await screen.findByText(/OpenRouter: uses the key “Main” ····PPPP/);
    expect(errors).toEqual([]);
  } finally {
    const reg = await get("/api/registry");
    for (const m of reg.models) await fetch(`/api/registry/${m.id}`, { method: "DELETE" });
    const settings = await get("/api/settings");
    for (const p of settings.providers) for (const k of p.keys) await fetch(`/api/settings/keys/${k.id}`, { method: "DELETE" });
    await new Promise<void>((r) => fake.close(() => r()));
  }
}, 60000);
