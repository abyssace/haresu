import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addEdge, Background, BackgroundVariant, BaseEdge, Controls, EdgeLabelRenderer, getSmoothStepPath, Handle, MarkerType,
  NodeToolbar, Position, ReactFlow, ReactFlowProvider, useEdgesState, useNodesState, useReactFlow, ViewportPortal,
  type Connection, type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import { api } from "../api";
import Inspector from "./Inspector";
import TraceView from "./TraceView";
import { Icon } from "./icons";
import { ModelHint } from "./Inspector";
import { ModelPicker } from "./ModelPicker";

// What can be connected to what. The canvas does not allow connections that mean nothing.
const ALLOWED: Record<string, string[]> = {
  input: ["router", "agent"],
  router: ["agent"],
  agent: ["agent", "output"],
  tool: ["agent"],
  context: ["agent"],
  policy: ["agent"],
};
// Flow wires (where the task travels) and resource wires (what an agent has at hand).
const ATTACH = new Set(["tool", "context", "policy"]);

const TYPE_LABEL: Record<string, string> = {
  input: "Input", output: "Output", router: "Router", agent: "Agent", tool: "Tool",
  context: "Context", policy: "Policy", meta: "Meta-agent", note: "Note",
};
const TOOL_LABEL: Record<string, string> = {
  search_context: "Search", calculator: "Calculate", write_memory: "Write scratchpad", read_memory: "Read scratchpad",
};

type Item = { type: string; tool?: string; name: string; help: string };
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "Task flow", items: [
    { type: "input", name: "Input", help: "Where the task comes in" },
    { type: "router", name: "Router", help: "Rules that choose which agent handles it" },
    { type: "agent", name: "Agent", help: "A model with its prompt" },
    { type: "output", name: "Output", help: "The final answer" },
  ] },
  { title: "An agent's resources", items: [
    { type: "context", name: "Shared context", help: "Documents the connected agents see" },
    { type: "tool", tool: "search_context", name: "Search the context", help: "Searches the documents" },
    { type: "tool", tool: "calculator", name: "Calculator", help: "Does exact operations" },
    { type: "tool", tool: "write_memory", name: "Write to the scratchpad", help: "Leaves a note for other agents" },
    { type: "tool", tool: "read_memory", name: "Read the scratchpad", help: "Reads other agents' notes" },
  ] },
  { title: "Control", items: [
    { type: "policy", name: "Governance policy", help: "Limits, permissions and data to redact" },
    { type: "meta", name: "Meta-agent", help: "Proposes improvements to this harness" },
    { type: "note", name: "Note", help: "A note for people" },
  ] },
];

function summary(type: string, data: any): string {
  switch (type) {
    case "input": return "the task comes in here";
    case "output": return "the answer goes out here";
    case "router": { const n = (data.rules ?? []).length; return `${n} ${n === 1 ? "rule" : "rules"}`; }
    case "agent": return data.model || "mock";
    case "tool": return data.tool;
    case "context": { const n = (data.docs ?? []).length; return `${n} ${n === 1 ? "document" : "documents"}`; }
    case "policy": return data.locked ? "locked" : "open to proposals";
    case "meta": return data.model || "mock";
    default: return "";
  }
}

function Plate({ id, data, selected }: NodeProps) {
  const d = data as any;
  const hasTarget = ["router", "agent", "output"].includes(d.type);
  const hasSource = d.type in ALLOWED;
  const r = d.result;
  if (d.type === "note") {
    return <div className={`note-node${selected ? " selected" : ""}${d.touched ? " ai-touch" : ""}`}>{d.data.text || "Empty note. Click to write."}</div>;
  }
  return (
    <div className={`plate t-${d.type}${selected ? " selected" : ""}${d.change ? ` ${d.change}` : ""}${d.run ? " ran" : ""}${d.touched ? " ai-touch" : ""}`}>
      <NodeToolbar position={Position.Top} align="end" className="node-tools">
        <button onClick={() => d.onDuplicate(id)}>Duplicate</button>
        <button onClick={() => d.onRemove(id)}>Delete</button>
      </NodeToolbar>
      {hasTarget && <Handle type="target" position={Position.Left} />}
      <div className="plate-head">
        <Icon type={d.type} />
        <span className="plate-type">{TYPE_LABEL[d.type]}</span>
        {d.change && <span className="tag diff">{d.change === "added" ? "new" : "changed"}</span>}
        {d.run && <span className="tag run" title={d.run.title}>{d.run.label}</span>}
        {d.issues?.length > 0 && (
          <span className="tag warn" title={d.issues.join("\n")} aria-label={`${d.issues.length} avisos: ${d.issues.join(" ")}`}>
            {d.issues.length === 1 ? "1 aviso" : `${d.issues.length} avisos`}
          </span>
        )}
      </div>
      <div className="plate-body">
        <span className="plate-name">{d.label}</span>
        <span className="plate-sub">{summary(d.type, d.data)}</span>
        {d.chips?.length > 0 && (
          <span className="chips">
            {d.chips.map((c: any) => <span key={c.id} className={`chip t-${c.type}`}>{c.text}</span>)}
          </span>
        )}
        {r && (r.cases > 0 || r.calls > 0) && (
          <span className={`result${r.cases > 0 && r.passed < r.cases ? " some-fail" : ""}`}
            title={r.failed_cases?.length ? `Failed: ${r.failed_cases.join(", ")}` : ""}>
            {r.cases > 0 ? `${r.passed} of ${r.cases} cases pass` : `${r.calls} uses`}
            {r.violations > 0 ? ` · ${r.violations} violations` : ""}
          </span>
        )}
      </div>
      {hasSource && <Handle type="source" position={Position.Right} />}
    </div>
  );
}

function Wire({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd }: EdgeProps) {
  const flow = useReactFlow();
  const [hover, setHover] = useState(false);
  const d = (data ?? {}) as any;
  const [path, cx, cy] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 12 });
  return (
    <g className={`wire ${d.kind}${d.active ? " active" : ""}${selected ? " selected" : ""} t-${d.sourceType}`}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <BaseEdge id={id} path={path} markerEnd={d.kind === "flow" ? markerEnd : undefined} interactionWidth={22} />
      {(hover || selected) && (
        <EdgeLabelRenderer>
          <button className="wire-del nodrag nopan" style={{ transform: `translate(-50%, -50%) translate(${cx}px, ${cy}px)` }}
            aria-label="Remove connection" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
            onClick={() => flow.deleteElements({ edges: [{ id }] })}>×</button>
        </EdgeLabelRenderer>
      )}
    </g>
  );
}

const nodeTypes = { card: Plate };
const edgeTypes = { wire: Wire };

type Props = {
  h: string; name: string; catalog: any; initialVersion: string;
  onChanged: () => void; onOpenRun: (id: string) => void; onOpenHarness: (id: string) => void;
};

export default function Canvas(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function CanvasInner({ h, name, catalog, initialVersion, onChanged, onOpenRun, onOpenHarness }: Props) {
  const flow = useReactFlow();
  const flowRef = useRef(flow); // the identity of `flow` changes when the view starts; loading the version must not depend on it
  flowRef.current = flow;
  const wrap = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [current, setCurrent] = useState("");
  const [version, setVersion] = useState(initialVersion);
  const [meta, setMeta] = useState<any>(null);
  const [base, setBase] = useState<any>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selected, setSelected] = useState("");
  const [dirty, setDirty] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [panel, setPanel] = useState<"" | "add" | "save" | "test" | "todo" | "patch" | "menu" | "ai">("");
  const [aiChat, setAiChat] = useState<{ role: string; text: string; kind?: string }[]>([]);
  const [aiInput, setAiInput] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [worker, setWorker] = useState("IA");
  const [aiModel, setAiModel] = useState<string>(catalog.preferences?.assistant_model ?? "mock");
  const [cursor, setCursor] = useState<{ x: number; y: number; label: string } | null>(null);
  const [touched, setTouched] = useState("");
  const aiJob = useRef("");
  const nodesRef = useRef<Node[]>([]);
  const chatEnd = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [note, setNote] = useState("");
  const [toast, setToast] = useState<{ kind: string; text: string } | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [byNode, setByNode] = useState<Record<string, string[]>>({});
  const [overlay, setOverlay] = useState<any>(null);
  const [showResults, setShowResults] = useState(false);
  const [lastRun, setLastRun] = useState<any[] | null>(null);
  const [task, setTask] = useState("");
  const [chat, setChat] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState(name);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const toNode = useCallback((n: any, diff: any): Node => ({
    id: n.id, type: "card", position: n.position ?? { x: 0, y: 0 },
    data: {
      type: n.type, label: n.label, data: n.data,
      change: diff?.added_nodes?.includes(n.id) ? "added" : diff?.changed_nodes?.includes(n.id) ? "changed" : "",
    },
  }), []);

  const loadHarness = useCallback(async () => {
    const info = await api.get(`/harnesses/${h}`);
    setVersions(info.versions);
    setCurrent(info.current);
    return info;
  }, [h]);

  const loadVersion = useCallback(async (v: string) => {
    const [data, ov] = await Promise.all([
      api.get(`/harnesses/${h}/versions/${v}`),
      api.get(`/harnesses/${h}/overlay?version=${v}`).catch(() => null),
    ]);
    setMeta({ ...data.meta, diff: data.diff });
    setBase(data.spec);
    setNodes(data.spec.nodes.map((n: any) => toNode(n, data.diff)));
    setEdges(data.spec.edges.map((e: any) => ({ id: e.id, source: e.source, target: e.target })));
    setOverlay(ov?.iteration ? ov : null);
    setSelected("");
    setDirty(false);
    setChat([]);
    setLastRun(null);
    setPanel((cur) => (cur === "ai" ? cur : data.spec.nodes.length === 0 ? "add" : ""));
    setLoaded(true);
    setTimeout(() => flowRef.current.fitView({ padding: 0.2, maxZoom: 1 }), 50);
  }, [h, setNodes, setEdges, toNode]);

  useEffect(() => {
    loadHarness().then((info) => {
      const v = initialVersion || info.current;
      setVersion(v);
      return loadVersion(v);
    }).catch((e) => setToast({ kind: "error", text: e.message }));
  }, [loadHarness, loadVersion, initialVersion]);

  nodesRef.current = nodes;
  useEffect(() => { chatEnd.current?.scrollIntoView?.({ block: "end" }); }, [aiChat]);

  const spec = useMemo(() => ({
    name: base?.name ?? "", description: base?.description ?? "",
    nodes: nodes.map((n) => {
      const d = n.data as any;
      return { id: n.id, type: d.type, label: d.label, position: { x: Math.round(n.position.x), y: Math.round(n.position.y) }, data: d.data };
    }),
    edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
  }), [nodes, edges, base]);

  // Live to-do: what the graph is missing, overall and node by node.
  const structure = useMemo(() => JSON.stringify({ n: spec.nodes.map((n) => [n.id, n.type, n.data]), e: spec.edges }), [spec]);
  useEffect(() => {
    const t = setTimeout(() => {
      api.post("/validate", { spec }).then((r) => { setProblems(r.problems); setByNode(r.by_node ?? {}); }).catch(() => {});
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure]);

  const typeOf = useCallback((id: string | null) => (nodes.find((n) => n.id === id)?.data as any)?.type as string | undefined, [nodes]);

  const isValidConnection = useCallback((c: Connection | Edge) => {
    const s = typeOf(c.source), t = typeOf(c.target);
    return !!s && !!t && c.source !== c.target && (ALLOWED[s] ?? []).includes(t);
  }, [typeOf]);

  const onConnect = useCallback((c: Connection) => {
    setEdges((es) => addEdge({ ...c, id: `e-${c.source}-${c.target}` }, es));
    setDirty(true);
  }, [setEdges]);

  const freshId = (stem: string) => {
    let k = 1;
    while (nodes.some((n) => n.id === `${stem}_${k}`)) k++;
    return `${stem}_${k}`;
  };

  function addNode(item: Item, at?: { x: number; y: number }) {
    if (item.type === "input" && nodes.some((n) => (n.data as any).type === "input")) {
      setToast({ kind: "error", text: "There is already an Input node; there can be only one." });
      return;
    }
    const rect = wrap.current?.getBoundingClientRect();
    const position = at ?? flow.screenToFlowPosition({
      x: (rect?.left ?? 0) + (rect?.width ?? 800) / 2 - 100 + (nodes.length % 5) * 28,
      y: (rect?.top ?? 0) + (rect?.height ?? 600) / 2 - 40 + (nodes.length % 5) * 28,
    });
    const data = structuredClone(catalog.node_types[item.type].defaults);
    if (item.tool) data.tool = item.tool;
    if (item.type === "agent" || item.type === "meta") data.model = catalog.preferences?.default_model ?? "mock"; // the default model
    const id = freshId(item.tool ?? item.type);
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), { ...toNode({ id, type: item.type, label: item.name, position, data }, null), selected: true }]);
    setSelected(id);
    setDirty(true);
  }

  function onDrop(e: React.DragEvent) {
    const raw = e.dataTransfer.getData("application/haresu");
    if (!raw) return;
    e.preventDefault();
    const pos = flow.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    addNode(JSON.parse(raw), { x: pos.x - 100, y: pos.y - 30 });
  }

  function updateNode(id: string, patch: { label?: string; data?: any }) {
    setNodes((ns) => ns.map((n) => n.id !== id ? n : {
      ...n, data: { ...n.data, ...(patch.label !== undefined ? { label: patch.label } : {}), ...(patch.data !== undefined ? { data: patch.data } : {}) },
    }));
    setDirty(true);
  }

  const removeNode = useCallback((id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
    setSelected((s) => (s === id ? "" : s));
    setDirty(true);
  }, [setNodes, setEdges]);

  const duplicateNode = useCallback((id: string) => {
    setNodes((ns) => {
      const src = ns.find((n) => n.id === id);
      if (!src) return ns;
      const d = src.data as any;
      if (d.type === "input") return ns;
      const stem = d.type === "tool" ? d.data.tool : d.type;
      let k = 1;
      while (ns.some((n) => n.id === `${stem}_${k}`)) k++;
      const copy: Node = {
        id: `${stem}_${k}`, type: "card", position: { x: src.position.x + 40, y: src.position.y + 40 }, selected: true,
        data: { type: d.type, label: `${d.label} (copy)`, data: structuredClone(d.data), change: "" },
      };
      setSelected(copy.id);
      return [...ns.map((n) => ({ ...n, selected: false })), copy];
    });
    setDirty(true);
  }, [setNodes]);

  async function save(makeCurrent: boolean) {
    try {
      const r = await api.post(`/harnesses/${h}/versions`, { spec, parent: version, note: note || "Manual edit", make_current: makeCurrent });
      await loadHarness();
      setVersion(r.version);
      await loadVersion(r.version);
      setNote("");
      onChanged();
      setToast({ kind: "ok", text: makeCurrent ? `Saved as ${r.version}. It is now the current version.`
        : `Saved as ${r.version}, as a candidate. In Cycle you can measure it against the current version.` });
    } catch (e: any) {
      setToast({ kind: "error", text: e.message });
    }
  }

  async function runTask() {
    const text = task.trim();
    if (!text || busy) return;
    setBusy(true);
    setTask("");
    try {
      const r = await api.post(`/harnesses/${h}/run`, { spec, version: dirty ? "" : version, input: text });
      setChat((c) => [...c, { task: text, ...r }]);
      setLastRun(r.trace);
      setShowResults(false);
    } catch (e: any) {
      setChat((c) => [...c, { task: text, failed: e.message }]);
    } finally {
      setBusy(false);
    }
  }

  // --- live replay: every event (from the assistant or from an external agent over MCP) is shown on the canvas,
  //     with a cursor that goes to where it happens
  async function playEvents(events: any[], external: boolean) {
    const calm = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    let pace = 1; // with many events queued it replays faster, so it does not fall behind
    const wait = (ms: number) => new Promise((r) => setTimeout(r, calm ? Math.min(ms, 60) : ms * pace));
    const pos = new Map(nodesRef.current.map((n) => [n.id, { ...n.position }]));
    const say = (t: string, kind?: string) => setAiChat((c) => [...c, { role: "assistant", text: t, kind }]);

    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      pace = events.length - i > 6 ? 0.4 : 1;
      const who = e.agent ?? "IA";
      const point = async (x: number, y: number, label: string, ms = 420) => { setCursor({ x, y, label: `${who} · ${label}` }); await wait(ms); };
      if (e.type === "say") say(e.text);
      else if (e.type === "evals") { say(`${external ? `${who} added` : "I added"} ${e.added} test cases. There are now ${e.dev} dev and ${e.heldout} held-out.`, "event"); onChanged(); }
      else if (e.type === "test") { setLastRun(e.trace); say(`${external ? `${who} tried` : "I tried"} “${e.task}” → ${e.metrics.error || e.output || "(no answer)"}`, "event"); await wait(500); }
      else if (e.type === "measured") { say(`${external ? `${who} measured` : "I measured"} ${e.version}: dev ${e.dev?.toFixed(3) ?? "—"}, held-out ${e.heldout?.toFixed(3) ?? "—"}.`, "event"); onChanged(); }
      else if (e.type === "changed") onChanged();
      else if (e.type === "reload") {
        // The external agent started (or restarted) from the current version: the canvas goes to that point.
        const info = await loadHarness();
        setVersion(info.current);
        await loadVersion(info.current);
        pos.clear();
        nodesRef.current.forEach((n) => pos.set(n.id, { ...n.position }));
        const fresh = await api.get(`/harnesses/${h}/versions/${info.current}`);
        fresh.spec.nodes.forEach((n: any) => pos.set(n.id, n.position));
      } else if (e.type === "saved") {
        await loadHarness();
        setVersion(e.version);
        setDirty(false);
        onChanged();
        say(e.current ? `${external ? `${who} saved` : "I saved"} ${e.version}. It is now the current version.` : `${external ? `${who} saved` : "I saved"} ${e.version} as a candidate.`, "event");
      } else if (e.type === "op") {
        if (e.op === "add_node") {
          const n = e.node;
          await point(n.position.x + 110, n.position.y + 36, `adds ${n.label}`);
          pos.set(n.id, n.position);
          setNodes((ns) => [...ns.filter((x) => x.id !== n.id), toNode(n, null)]);
          setTouched(n.id);
          await wait(120);
          flow.fitView({ padding: 0.25, maxZoom: 1, duration: calm ? 0 : 350 });
          await wait(260);
        } else if (e.op === "add_edge") {
          const s = pos.get(e.edge.source), t = pos.get(e.edge.target);
          if (s) await point(s.x + 220, s.y + 40, "connects", 320);
          if (t) await point(t.x, t.y + 40, "connects", 380);
          setEdges((es) => [...es.filter((x) => x.id !== e.edge.id), e.edge]);
          await wait(140);
        } else if (e.op === "set_node") {
          const p = pos.get(e.id);
          if (p) await point(p.x + 110, p.y + 36, `edits ${e.label}`);
          setNodes((ns) => ns.map((n) => (n.id !== e.id ? n : { ...n, data: { ...n.data, label: e.label, data: e.data } })));
          setTouched(e.id);
          await wait(260);
        } else if (e.op === "remove_node") {
          const p = pos.get(e.id);
          if (p) await point(p.x + 110, p.y + 36, "deletes");
          setNodes((ns) => ns.filter((n) => n.id !== e.id));
          setEdges((es) => es.filter((x) => x.source !== e.id && x.target !== e.id));
          pos.delete(e.id);
          await wait(160);
        } else if (e.op === "remove_edge") {
          const s = pos.get(e.source);
          if (s) await point(s.x + 220, s.y + 40, "disconnects", 320);
          setEdges((es) => es.filter((x) => !(x.source === e.source && x.target === e.target)));
          await wait(140);
        }
        setDirty(true);
      }
    }
  }
  const playRef = useRef(playEvents);
  playRef.current = playEvents;
  const busyRef = useRef(false);
  busyRef.current = aiBusy;

  async function askAssistant() {
    const text = aiInput.trim();
    if (!text || aiBusy) return;
    const history = aiChat.filter((m) => !m.kind).map((m) => ({ role: m.role, text: m.text }));
    setAiInput("");
    setAiBusy(true);
    setWorker("IA");
    setLastRun(null);
    setShowResults(false);
    setSelected("");
    setAiChat((c) => [...c, { role: "user", text }]);
    try {
      let job = await api.post(`/harnesses/${h}/assistant`, { message: text, spec, model: aiModel, history });
      aiJob.current = job.id;
      let seen = 0;
      for (;;) {
        job = await api.get(`/jobs/${job.id}?after=${seen}`);
        seen = job.next;
        await playEvents(job.events, false);
        if (job.status !== "running" && job.events.length === 0) break;
        if (job.status === "running") await new Promise((r) => setTimeout(r, 250));
      }
      if (job.status === "error") setAiChat((c) => [...c, { role: "assistant", text: job.error, kind: "error" }]);
    } catch (e: any) {
      setAiChat((c) => [...c, { role: "assistant", text: e.message, kind: "error" }]);
    } finally {
      aiJob.current = "";
      setCursor(null);
      setTouched("");
      setAiBusy(false);
    }
  }
  const stopAssistant = () => { if (aiJob.current) api.post(`/jobs/${aiJob.current}/cancel`).catch(() => {}); };

  // External agents (MCP): the canvas listens to what they do and replays it just like the assistant.
  useEffect(() => {
    let stop = false;
    let seen = -1;
    (async () => {
      while (!stop) {
        try {
          const r = await api.get(`/harnesses/${h}/live?after=${seen}`);
          if (stop) return;
          if (seen >= 0 && r.events.length > 0) {
            if (busyRef.current) { await new Promise((res) => setTimeout(res, 500)); continue; } // wait for the assistant to finish
            setAiBusy(true);
            setWorker(r.events[0].agent ?? "An agent");
            setSelected("");
            setShowResults(false);
            try { await playRef.current(r.events, true); } finally { setCursor(null); setTouched(""); setAiBusy(false); }
          }
          seen = r.next;
        } catch { /* the server did not answer; retry */ }
        await new Promise((res) => setTimeout(res, 1000));
      }
    })();
    return () => { stop = true; };
  }, [h]);

  // --- harness menu
  async function menuAction(action: () => Promise<void>) {
    try { await action(); setPanel(""); } catch (e: any) { setToast({ kind: "error", text: e.message }); }
  }
  const rename = () => menuAction(async () => { await api.request("PATCH", `/harnesses/${h}`, { name: newName.trim() }); onChanged(); });
  const duplicate = () => menuAction(async () => { const info = await api.post(`/harnesses/${h}/duplicate`, { name: `${name} (copy)` }); onOpenHarness(info.id); });
  const remove = () => menuAction(async () => { await api.request("DELETE", `/harnesses/${h}`); onOpenHarness(""); });
  const exportJson = () => menuAction(async () => {
    const data = await api.get(`/harnesses/${h}/export`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `${h}.haresu.json` });
    a.click();
    URL.revokeObjectURL(url);
  });
  async function importJson(file: File | undefined) {
    if (!file) return;
    await menuAction(async () => {
      let data: any;
      try { data = JSON.parse(await file.text()); } catch { throw new Error("That file is not valid JSON."); }
      const info = await api.post("/harnesses", { name: `${data.name ?? "Imported"} (imported ${new Date().toLocaleTimeString()})`, import: data });
      onOpenHarness(info.id);
    });
  }

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.kind === "error" ? 8000 : 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // --- what is painted over the graph: the last test or the results of the last measurement
  const runView = useMemo(() => {
    const marks: Record<string, { label: string; title: string }> = {};
    const active = new Set<string>();
    if (!lastRun) return { marks, active };
    const input = spec.nodes.find((n) => n.type === "input");
    let prev = input?.id ?? "";
    let step = 0;
    if (input) marks[input.id] = { label: "start", title: "The task came in here" };
    for (const e of lastRun) {
      if (e.type === "routing" && e.router) {
        marks[e.router] = { label: "chose", title: `Chose ${e.target || "nobody"}: ${e.reason}` };
        active.add(`${prev}>${e.router}`);
        prev = e.router;
      } else if (e.type === "agent_start") {
        step += 1;
        marks[e.agent] = { label: `step ${step}`, title: "This agent handled the task" };
        active.add(`${prev}>${e.agent}`);
        prev = e.agent;
      } else if (e.type === "tool" && e.allowed) {
        for (const ed of spec.edges) {
          const src = spec.nodes.find((n) => n.id === ed.source);
          if (ed.target !== e.agent || !src) continue;
          if (src.type === "tool" && src.data.tool === e.tool) { marks[src.id] = { label: "used", title: `${e.agent} used it` }; active.add(`${src.id}>${e.agent}`); }
          if (src.type === "context" && e.tool === "search_context") { marks[src.id] = { label: "consulted", title: `${e.agent} consulted it` }; active.add(`${src.id}>${e.agent}`); }
        }
      } else if (e.type === "violation" && e.agent && marks[e.agent]) {
        marks[e.agent] = { label: "violation", title: e.detail };
      }
    }
    const out = spec.edges.find((ed) => ed.source === prev && spec.nodes.find((n) => n.id === ed.target)?.type === "output");
    if (out && step > 0) { active.add(`${prev}>${out.target}`); marks[out.target] = { label: "answer", title: "The answer came out here" }; }
    return { marks, active };
  }, [lastRun, spec]);

  const viewNodes = useMemo(() => nodes.map((n) => {
    const d = n.data as any;
    const chips = d.type !== "agent" ? [] : spec.edges.filter((e) => e.target === n.id).map((e) => spec.nodes.find((x) => x.id === e.source))
      .filter((s): s is any => !!s && ATTACH.has(s.type))
      .map((s) => ({ id: s.id, type: s.type, text: s.type === "tool" ? (TOOL_LABEL[s.data.tool] ?? s.data.tool) : s.label }));
    return { ...n, data: { ...d, chips, issues: aiBusy ? undefined : byNode[n.id], run: runView.marks[n.id], touched: touched === n.id,
      result: showResults && !dirty ? overlay?.nodes?.[n.id] : undefined, onDuplicate: duplicateNode, onRemove: removeNode } };
  }), [nodes, spec, byNode, runView, showResults, dirty, overlay, duplicateNode, removeNode, aiBusy, touched]);

  const viewEdges = useMemo(() => edges.map((e) => {
    const st = typeOf(e.source) ?? "";
    return { ...e, type: "wire", markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      data: { kind: ATTACH.has(st) ? "attach" : "flow", sourceType: st, active: runView.active.has(`${e.source}>${e.target}`) } };
  }), [edges, typeOf, runView]);

  const toggle = (p: typeof panel) => { setConfirmDelete(false); setNewName(name); setPanel((cur) => (cur === p ? "" : p)); };
  const selectedNode = nodes.find((n) => n.id === selected);
  const patch = meta?.patch;
  const q = query.trim().toLowerCase();
  const firstTime = versions.length <= 1 && current === version;
  const warnCount = Object.values(byNode).reduce((a, b) => a + b.length, 0);

  return (
    <div className="canvas" ref={wrap} onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <ReactFlow
        nodes={viewNodes} edges={viewEdges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        isValidConnection={isValidConnection} minZoom={0.3} maxZoom={1.5} snapToGrid snapGrid={[20, 20]}
        deleteKeyCode={aiBusy ? null : ["Backspace", "Delete"]} connectionLineStyle={{ strokeWidth: 2 }}
        nodesDraggable={!aiBusy} nodesConnectable={!aiBusy}
        onNodesChange={(c) => { onNodesChange(c); if (c.some((x) => x.type === "remove" || (x.type === "position" && x.dragging))) setDirty(true); if (c.some((x) => x.type === "remove" && x.id === selected)) setSelected(""); }}
        onEdgesChange={(c) => { onEdgesChange(c); if (c.some((x) => x.type === "remove")) setDirty(true); }}
        onConnect={onConnect}
        onNodeClick={(_, n) => { if (aiBusy) return; setSelected(n.id); if (panel !== "test" && panel !== "ai") setPanel(""); }}
        onPaneClick={() => { setSelected(""); if (panel !== "test" && panel !== "ai") setPanel(""); }}
      >
        <Background variant={BackgroundVariant.Cross} gap={40} size={5} lineWidth={1} />
        <Controls showInteractive={false} position="bottom-left" />
        {cursor && (
          <ViewportPortal>
            <div className="ai-cursor" style={{ transform: `translate(${cursor.x}px, ${cursor.y}px)` }} aria-hidden="true">
              <svg width="20" height="22" viewBox="0 0 20 22"><path d="M2 1.5v17l4.6-4.3 3 6.6 2.9-1.3-3-6.5 6.3-.4z" /></svg>
              <span>{cursor.label}</span>
            </div>
          </ViewportPortal>
        )}
      </ReactFlow>

      {loaded && nodes.length === 0 && !aiBusy && (
        <div className="blank">
          <h2>Empty canvas</h2>
          <p>Add your first node with the button <b>+</b>, or press <b>Build with AI</b> and describe what you want: you will watch it being built here.</p>
        </div>
      )}

      {/* Top left: add nodes */}
      <div className="dock top-left">
        <div className="dock-row">
          <button className="fab" aria-label="Add node" aria-expanded={panel === "add"} disabled={aiBusy || !loaded} onClick={() => toggle("add")}>
            {panel === "add" ? "×" : "+"}
          </button>
          <button className={aiBusy ? "ai-btn working" : "ai-btn"} aria-expanded={panel === "ai"} onClick={() => toggle("ai")}>
            {aiBusy ? (worker === "IA" ? "The AI is building…" : `${worker} is building…`) : "Build with AI"}
          </button>
        </div>
        {panel === "ai" && (
          <div className="pop ai-pop">
            <div className="ai-head">
              <b>Assistant</b>
              <ModelPicker label="Assistant model" value={aiModel} disabled={aiBusy}
                onChange={(v) => { setAiModel(v); api.put("/preferences", { assistant_model: v }).catch(() => {}); }} />
            </div>
            <p className="hint">
              {aiModel === "mock"
                ? "The template builds a standard structure with the topics you mention. It does not understand free-form instructions."
                : "A real model builds the graph, writes prompts and test cases, and tests them. Write provider:model."}
            </p>
            {aiModel !== "mock" && <ModelHint model={aiModel} />}
            <div className="chat">
              {aiChat.length === 0 && (
                <p className="hint">Describe what you want, for example: “a support harness for payments, shipping and account”. You will see every node and every connection appear on the canvas, and afterwards you can move everything by hand.</p>
              )}
              {aiChat.map((m, i) => (
                <div key={i} className={m.role === "user" ? "bubble me" : m.kind === "error" ? "bubble err" : m.kind === "event" ? "bubble event" : "bubble"}>{m.text}</div>
              ))}
              <div ref={chatEnd} />
            </div>
            <form className="test-form" onSubmit={(e) => { e.preventDefault(); askAssistant(); }}>
              <input autoFocus placeholder="What do you want to build?" aria-label="Message for the assistant" value={aiInput} disabled={aiBusy} onChange={(e) => setAiInput(e.target.value)} />
              {aiBusy ? (worker === "IA" ? <button type="button" onClick={stopAssistant}>Stop</button> : <button type="button" disabled>Paused</button>)
                : <button className="primary" disabled={!aiInput.trim()}>Send</button>}
            </form>
          </div>
        )}
        {panel === "add" && (
          <div className="pop add-pop">
            <input autoFocus placeholder="Search node…" aria-label="Search node" value={query} onChange={(e) => setQuery(e.target.value)} />
            <div className="add-list">
              {GROUPS.map((g) => {
                const items = g.items.filter((i) => !q || `${i.name} ${i.help} ${i.tool ?? ""}`.toLowerCase().includes(q));
                return items.length === 0 ? null : (
                  <div key={g.title}>
                    <div className="group-title">{g.title}</div>
                    {items.map((item) => (
                      <button key={item.name} className={`add-item t-${item.type}`} draggable
                        onDragStart={(e) => { e.dataTransfer.setData("application/haresu", JSON.stringify(item)); e.dataTransfer.effectAllowed = "move"; }}
                        onClick={() => addNode(item)}>
                        <span className="add-icon"><Icon type={item.type} /></span>
                        <span><span className="add-name">{item.name}</span><span className="add-help">{item.help}</span></span>
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
            <p className="hint">Click to add it or drag it onto the canvas.</p>
          </div>
        )}
      </div>

      {/* Top right: layers, version, save and menu */}
      <div className="dock top-right">
        {lastRun && <button className="layer on" onClick={() => setLastRun(null)} title="Removes the marks of the last test from the canvas">Hide path</button>}
        {overlay && !dirty && (
          <button className={showResults ? "layer on" : "layer"} aria-pressed={showResults}
            title={`Results of ${overlay.iteration.replace("iter-", "measurement ")} on each node`}
            onClick={() => { setShowResults((v) => !v); setLastRun(null); }}>
            Results
          </button>
        )}
        {patch && !dirty && <button className="layer" onClick={() => toggle("patch")}>What changed</button>}
        <select aria-label="Version" value={version} disabled={aiBusy} onChange={(e) => { setVersion(e.target.value); loadVersion(e.target.value); }}>
          {versions.map((v) => (
            <option key={v.version} value={v.version}>{v.version}{v.version === current ? " · current" : ""}</option>
          ))}
        </select>
        <button className="primary" disabled={!dirty || aiBusy} onClick={() => (firstTime ? save(true) : toggle("save"))}>
          {dirty ? "Save" : "Saved"}
        </button>
        <button className="icon-btn" aria-label="Harness options" aria-expanded={panel === "menu"} onClick={() => toggle("menu")}>⋯</button>
        {panel === "save" && (
          <div className="pop side-pop">
            <label className="field"><span>What did you change? (optional)</span>
              <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
            <button className="primary" onClick={() => save(false)}>Save as candidate</button>
            <p className="hint">It is kept apart. In Cycle you measure it against the current version and the gate decides.</p>
            <button onClick={() => save(true)}>Save and use now</button>
            <p className="hint">Replaces the current version without measuring it.</p>
          </div>
        )}
        {panel === "patch" && patch && (
          <div className="pop side-pop wide">
            <b>{meta.author} changed {meta.parent}</b>
            <p><span className="k">Diagnosis</span>{patch.diagnosis}</p>
            <p><span className="k">What it expects</span>{patch.prediction}</p>
            <p><span className="k">Risk</span>{patch.risk}</p>
            <p className="hint">The nodes marked on the canvas are the ones that changed.</p>
          </div>
        )}
        {panel === "menu" && (
          <div className="pop side-pop">
            <form className="rename" onSubmit={(e) => { e.preventDefault(); if (newName.trim()) rename(); }}>
              <input aria-label="Harness name" value={newName} onChange={(e) => setNewName(e.target.value)} />
              <button disabled={!newName.trim() || newName.trim() === name}>Rename</button>
            </form>
            <button className="menu-item" onClick={duplicate}>Duplicate harness</button>
            <button className="menu-item" onClick={exportJson}>Export to a file</button>
            <button className="menu-item" onClick={() => fileInput.current?.click()}>Import from a file</button>
            <input ref={fileInput} type="file" accept=".json,application/json" hidden
              onChange={(e) => { importJson(e.target.files?.[0]); e.target.value = ""; }} />
            {confirmDelete
              ? <button className="menu-item danger-fill" onClick={remove}>Yes, delete “{name}” and all its history</button>
              : <button className="menu-item danger-text" onClick={() => setConfirmDelete(true)}>Delete harness</button>}
          </div>
        )}
      </div>

      {/* Bottom center: to-do */}
      {nodes.length > 0 && (
        <div className="dock bottom-center">
          <button className={problems.length ? "pill warn" : warnCount ? "pill soft" : "pill ok"} onClick={() => toggle("todo")} aria-expanded={panel === "todo"}>
            {problems.length ? `Missing ${problems.length} ${problems.length === 1 ? "thing" : "things"} to be able to run`
              : warnCount ? `Ready to run · ${warnCount} ${warnCount === 1 ? "warning" : "warnings"}` : "Ready to run"}
          </button>
          {panel === "todo" && (problems.length > 0 || warnCount > 0) && (
            <div className="pop todo-pop">
              {problems.length > 0 && <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
              {warnCount > 0 && (
                <>
                  <div className="k">Warnings per node</div>
                  <ul>
                    {Object.entries(byNode).map(([id, list]) => {
                      const label = (nodes.find((n) => n.id === id)?.data as any)?.label ?? id;
                      return list.map((t) => (
                        <li key={id + t}><button className="link" onClick={() => { setSelected(id); setPanel(""); }}>{label}</button>: {t}</li>
                      ));
                    })}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* Bottom right: test */}
      <div className="dock bottom-right">
        {panel === "test" && (
          <div className="pop test-pop">
            <div className="test-head"><b>Test</b><span className="hint">Runs the canvas as it is. The path stays marked on the nodes.</span></div>
            <div className="chat">
              {chat.length === 0 && <p className="hint">Write a task the way a user would write it.</p>}
              {chat.map((c, i) => (
                <div key={i} className="turn">
                  <div className="bubble me">{c.task}</div>
                  {c.failed ? <div className="bubble err">{c.failed}</div> : (
                    <>
                      <div className={`bubble${c.metrics.error ? " err" : ""}`}>{c.metrics.error || c.output || "(no answer)"}</div>
                      <details className="steps-detail">
                        <summary>
                          {c.metrics.route ? `Handled by ${c.metrics.agents.join(" → ")}` : "It did not reach any agent"} · {c.metrics.tool_calls} tools
                          {c.metrics.violations > 0 ? ` · ${c.metrics.violations} violations` : ""}
                        </summary>
                        <TraceView trace={c.trace} compact />
                        <button className="link" onClick={() => onOpenRun(c.run_id)}>Open in Runs</button>
                      </details>
                    </>
                  )}
                </div>
              ))}
              {busy && <div className="bubble">Running…</div>}
            </div>
            <form className="test-form" onSubmit={(e) => { e.preventDefault(); runTask(); }}>
              <input autoFocus placeholder="Type a task…" aria-label="Test task" value={task} onChange={(e) => setTask(e.target.value)} />
              <button className="primary" disabled={busy || !task.trim()}>Send</button>
            </form>
          </div>
        )}
        <button className="fab wide" aria-expanded={panel === "test"} onClick={() => toggle("test")}>
          {panel === "test" ? "Close" : "Test"}
        </button>
      </div>

      {/* Selected node panel */}
      {selectedNode && (
        <aside className="drawer" aria-label="Node settings">
          <button className="close" aria-label="Close" onClick={() => setSelected("")}>×</button>
          <Inspector node={selectedNode} spec={spec} catalog={catalog} issues={byNode[selectedNode.id] ?? []}
            onChange={(p) => updateNode(selectedNode.id, p)} onRemove={() => removeNode(selectedNode.id)}
            onError={(text) => setToast({ kind: "error", text })} />
        </aside>
      )}

      {toast && <div className={`toast ${toast.kind}`} role="status">{toast.text}</div>}
    </div>
  );
}
