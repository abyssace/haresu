import type { Node } from "@xyflow/react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon } from "./icons";
import { ModelPicker } from "./ModelPicker";

/** Says which provider a model belongs to and which key it will use. */
export function ModelHint({ model }: { model: string }) {
  const [info, setInfo] = useState<any>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      api.get(`/resolve?model=${encodeURIComponent(model || "mock")}`).then(setInfo).catch(() => setInfo(null));
    }, 250);
    return () => clearTimeout(t);
  }, [model]);
  if (!info) return null;
  if (info.provider === "mock") return <small className="muted">Test model: no key needed.</small>;
  if (info.ok) {
    return <small className="hint-ok">{info.provider_name}: uses {info.key_name ? `the key “${info.key_name}”` : "the environment key"} ····{info.last4}</small>;
  }
  return <small className="hint-bad">{info.error}</small>;
}

const csv = (list: string[] | undefined) => (list ?? []).join(", ");
const uncsv = (text: string) => text.split(",").map((s) => s.trim()).filter(Boolean);

function Field({ label, children }: { label: string; children: any }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

const MAX_FILE = 2 * 1024 * 1024;
const TEXT_FILES = ".txt,.md,.markdown,.csv,.tsv,.json,.jsonl,.yaml,.yml,.log,.html,.xml";

/** Splits a text into manageable documents, respecting paragraphs. */
export function chunk(name: string, text: string, size = 1200): { id: string; title: string; text: string }[] {
  const parts: string[] = [];
  let cur = "";
  for (const para of text.replace(/\r/g, "").split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean)) {
    for (let i = 0; i < para.length; i += size) {
      const piece = para.slice(i, i + size);
      if (cur && cur.length + piece.length + 2 > size) { parts.push(cur); cur = ""; }
      cur = cur ? `${cur}\n\n${piece}` : piece;
    }
  }
  if (cur) parts.push(cur);
  const stamp = Date.now().toString(36);
  return parts.map((t, i) => ({
    id: `${name.replace(/[^a-zA-Z0-9]+/g, "-").slice(0, 24)}-${stamp}-${i + 1}`,
    title: parts.length > 1 ? `${name} · part ${i + 1} of ${parts.length}` : name,
    text: t,
  }));
}

export default function Inspector({ node, spec, catalog, issues, onChange, onRemove, onError }: {
  node: Node; spec: any; catalog: any; issues: string[];
  onChange: (patch: { label?: string; data?: any }) => void; onRemove: () => void; onError: (text: string) => void;
}) {
  const d = node.data as any;
  const data = d.data ?? {};
  const set = (key: string, value: any) => onChange({ data: { ...data, [key]: value } });
  const num = (key: string) => (
    <input type="number" min={0} value={data[key] ?? 0} onChange={(e) => set(key, Number(e.target.value))} />
  );
  const connectedAgents: any[] = spec.edges
    .filter((e: any) => e.source === node.id)
    .map((e: any) => spec.nodes.find((n: any) => n.id === e.target))
    .filter((n: any) => n?.type === "agent");
  const modelInput = (
    <>
      <ModelPicker label="Model" value={data.model ?? "mock"} onChange={(v) => set("model", v)} />
      <ModelHint model={data.model ?? "mock"} />
    </>
  );
  const effortSelect = (
    <select value={data.effort ?? "medium"} onChange={(e) => set("effort", e.target.value)}>
      {["low", "medium", "high", "xhigh", "max"].map((x) => <option key={x}>{x}</option>)}
    </select>
  );

  async function loadFiles(files: FileList | null) {
    if (!files?.length) return;
    const docs: any[] = [];
    const skipped: string[] = [];
    for (const f of Array.from(files)) {
      if (f.size > MAX_FILE) { skipped.push(`${f.name} (over 2 MB)`); continue; }
      const text = await f.text();
      if (text.includes("\u0000")) { skipped.push(`${f.name} (not text)`); continue; }
      docs.push(...chunk(f.name, text));
    }
    if (docs.length) set("docs", [...(data.docs ?? []), ...docs]);
    if (skipped.length) onError(`Not loaded: ${skipped.join(", ")}. Only text files up to 2 MB are accepted.`);
  }

  return (
    <div>
      <div className={`insp-head t-${d.type}`}>
        <span className="card-icon"><Icon type={d.type} /></span>
        <div><b>{catalog.node_types[d.type]?.label}</b><div className="muted small">id: {node.id}</div></div>
      </div>
      <p className="muted small">{catalog.node_types[d.type]?.help}</p>
      {issues.length > 0 && <ul className="insp-issues">{issues.map((t) => <li key={t}>{t}</li>)}</ul>}
      {d.type !== "note" && <Field label="Name"><input value={d.label ?? ""} onChange={(e) => onChange({ label: e.target.value })} /></Field>}
      {d.type === "note" && (
        <Field label="Note text"><textarea rows={6} value={data.text ?? ""} onChange={(e) => set("text", e.target.value)} /></Field>
      )}

      {d.type === "agent" && (
        <>
          <Field label="Model">{modelInput}</Field>
          <Field label="Effort">{effortSelect}</Field>
          <Field label="System prompt">
            <textarea rows={10} value={data.system_prompt ?? ""} onChange={(e) => set("system_prompt", e.target.value)} />
          </Field>
        </>
      )}

      {d.type === "router" && (
        <>
          <Field label="Default destination">
            <select value={data.default ?? ""} onChange={(e) => set("default", e.target.value)}>
              <option value="">(none)</option>
              {connectedAgents.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
            </select>
          </Field>
          <label className="check" title="Useful when users ask with words the rules do not foresee">
            <input type="checkbox" checked={!!data.auto_context} onChange={(e) => set("auto_context", e.target.checked)} />
            If no rule matches, choose the agent whose context is most similar to the task
          </label>
          <h4>Rules (in order)</h4>
          {connectedAgents.length === 0 && <p className="muted small">Connect the router to its agents to be able to create rules.</p>}
          {(data.rules ?? []).map((rule: any, i: number) => {
            const update = (patch: any) => set("rules", data.rules.map((r: any, j: number) => (j === i ? { ...r, ...patch } : r)));
            return (
              <div key={rule.id ?? i} className="item">
                <Field label={`Rule ${rule.id ?? i + 1}: keywords`}>
                  <input value={csv(rule.keywords)} onChange={(e) => update({ keywords: uncsv(e.target.value) })} />
                </Field>
                <Field label="Regex (optional)"><input value={rule.regex ?? ""} onChange={(e) => update({ regex: e.target.value })} /></Field>
                <Field label="Va a">
                  <select value={rule.target ?? ""} onChange={(e) => update({ target: e.target.value })}>
                    <option value="">(choose)</option>
                    {connectedAgents.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
                  </select>
                </Field>
                <button onClick={() => set("rules", data.rules.filter((_: any, j: number) => j !== i))}>Remove rule</button>
              </div>
            );
          })}
          <button disabled={connectedAgents.length === 0} onClick={() => set("rules", [...(data.rules ?? []),
            { id: `r${Date.now().toString(36)}`, keywords: [], regex: "", target: connectedAgents[0]?.id ?? "" }])}>
            + Rule
          </button>
        </>
      )}

      {d.type === "tool" && (
        <Field label="Tool">
          <select value={data.tool} onChange={(e) => set("tool", e.target.value)}>
            {catalog.tools.map((t: any) => <option key={t.name} value={t.name}>{t.name}</option>)}
          </select>
          <small className="muted">{catalog.tools.find((t: any) => t.name === data.tool)?.description}</small>
        </Field>
      )}

      {d.type === "context" && (
        <>
          <Field label="How it reaches the agent">
            <select value={data.mode ?? "tool"} onChange={(e) => set("mode", e.target.value)}>
              <option value="tool">By search (search_context)</option>
              <option value="prompt">Pasted into the prompt</option>
            </select>
          </Field>
          <h4>Documents ({(data.docs ?? []).length})</h4>
          <label className="file-btn">
            Load text files
            <input type="file" multiple accept={TEXT_FILES} hidden onChange={(e) => { loadFiles(e.target.files); e.target.value = ""; }} />
          </label>
          <p className="muted small">Accepts .txt, .md, .csv, .json and similar. Long files are split into several documents. PDF and Word are not read yet.</p>
          {(data.docs ?? []).map((doc: any, i: number) => {
            const update = (patch: any) => set("docs", data.docs.map((x: any, j: number) => (j === i ? { ...x, ...patch } : x)));
            return (
              <div key={doc.id ?? i} className="item">
                <Field label="Title"><input value={doc.title ?? ""} onChange={(e) => update({ title: e.target.value })} /></Field>
                <Field label="Text"><textarea rows={3} value={doc.text ?? ""} onChange={(e) => update({ text: e.target.value })} /></Field>
                <button onClick={() => set("docs", data.docs.filter((_: any, j: number) => j !== i))}>Remove document</button>
              </div>
            );
          })}
          <button onClick={() => set("docs", [...(data.docs ?? []), { id: `doc-${Date.now().toString(36)}`, title: "", text: "" }])}>+ Document</button>
        </>
      )}

      {d.type === "policy" && (
        <>
          <label className="check">
            <input type="checkbox" checked={!!data.locked} onChange={(e) => set("locked", e.target.checked)} />
            Locked: the meta-agent cannot touch it
          </label>
          <Field label="Maximum steps per agent">{num("max_steps")}</Field>
          <Field label="Maximum tool calls">{num("max_tool_calls")}</Field>
          <Field label="Token limit per run (0 = no limit)">{num("max_run_tokens")}</Field>
          <Field label="Maximum characters in the answer (0 = no limit)">{num("max_output_chars")}</Field>
          <Field label="Allowed tools (empty = all)">
            <input value={csv(data.allowed_tools)} onChange={(e) => set("allowed_tools", uncsv(e.target.value))} />
          </Field>
          <Field label="Blocked tools">
            <input value={csv(data.blocked_tools)} onChange={(e) => set("blocked_tools", uncsv(e.target.value))} />
          </Field>
          <Field label="Patterns to redact in the answer (regex, one per line)">
            <textarea rows={3} value={(data.redact_patterns ?? []).join("\n")}
              onChange={(e) => set("redact_patterns", e.target.value.split("\n").filter((s) => s.trim()))} />
          </Field>
          <p className="muted small">With no connections, the policy is global. Connected to an agent, it applies only to that agent.</p>
        </>
      )}

      {d.type === "meta" && (
        <>
          <Field label="Model">{modelInput}</Field>
          <Field label="Effort">{effortSelect}</Field>
          <Field label="Team instructions">
            <textarea rows={5} value={data.instructions ?? ""} onChange={(e) => set("instructions", e.target.value)} />
          </Field>
          <Field label="Maximum operations per patch">{num("max_ops")}</Field>
          <h4>Can edit</h4>
          {["router", "agent", "context", "tool"].map((t) => (
            <label key={t} className="check">
              <input type="checkbox" checked={(data.editable_types ?? []).includes(t)}
                onChange={(e) => set("editable_types", e.target.checked
                  ? [...(data.editable_types ?? []), t] : (data.editable_types ?? []).filter((x: string) => x !== t))} />
              {catalog.node_types[t].label}
            </label>
          ))}
          <p className="muted small">It never sees held-out and cannot change the evals, the locked policies or this node.</p>
        </>
      )}

      <button className="danger" onClick={onRemove}>Delete node</button>
    </div>
  );
}
