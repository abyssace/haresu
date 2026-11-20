import { useEffect, useState } from "react";
import { ModelPicker } from "./ModelPicker";
import { api } from "../api";

const join = (list: string[] | undefined) => (list ?? []).join(" | ");
const split = (text: string) => text.split("|").map((s) => s.trim()).filter(Boolean);

export default function EvalsView({ h, catalog, onChanged }: { h: string; catalog: any; onChanged: () => void }) {
  const [evals, setEvals] = useState<any>(null);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    api.get(`/harnesses/${h}/evals`).then(setEvals).catch((e) => setMsg({ kind: "error", text: e.message }));
  }, [h]);

  if (!evals) return <p className="empty">{msg?.text || "Loading…"}</p>;

  const change = (next: any) => { setEvals(next); setDirty(true); };
  const setCase = (i: number, patch: any) =>
    change({ ...evals, cases: evals.cases.map((c: any, j: number) => (j === i ? { ...c, ...patch } : c)) });
  const setExpect = (i: number, patch: any) => setCase(i, { expect: { ...(evals.cases[i].expect ?? {}), ...patch } });
  const setGate = (key: string, value: any) => change({ ...evals, gate: { ...evals.gate, [key]: value } });

  async function save() {
    try {
      await api.put(`/harnesses/${h}/evals`, evals);
      setDirty(false);
      onChanged();
      setMsg({ kind: "ok", text: "Evals saved. Measure the current version again so the comparison is fair." });
    } catch (e: any) {
      setMsg({ kind: "error", text: e.message });
    }
  }

  const counts = { dev: 0, heldout: 0 } as Record<string, number>;
  evals.cases.forEach((c: any) => { counts[c.split ?? "dev"]++; });

  return (
    <div className="page">
      <h1>Evals</h1>
      <p className="lead">
        The <b>dev</b> cases are the ones the meta-agent can see in order to learn. The <b>held-out</b> ones are hidden from it: the gate uses them to check that an improvement is real and not just memorization of the dev cases. There are {counts.dev} dev cases and {counts.heldout} held-out.
      </p>

      <h2>Gate</h2>
      <div className="actions">
        <label>Minimum improvement in held-out{" "}
          <input type="number" step="0.01" value={evals.gate?.min_heldout_gain ?? 0.05} onChange={(e) => setGate("min_heldout_gain", Number(e.target.value))} />
        </label>
        <label>Maximum drop in dev{" "}
          <input type="number" step="0.01" value={evals.gate?.max_dev_drop ?? 0.02} onChange={(e) => setGate("max_dev_drop", Number(e.target.value))} />
        </label>
        <label>New violations allowed{" "}
          <input type="number" min={0} value={evals.gate?.max_new_violations ?? 0} onChange={(e) => setGate("max_new_violations", Number(e.target.value))} />
        </label>
        <label title="With real models the answers vary: repeat so the gate can tell an improvement from noise">Repetitions per measurement{" "}
          <input type="number" min={1} max={10} value={evals.gate?.seeds ?? 1} onChange={(e) => setGate("seeds", Number(e.target.value))} />
        </label>
        <label className="check">
          <input type="checkbox" checked={evals.gate?.policy_changes_need_human ?? true} onChange={(e) => setGate("policy_changes_need_human", e.target.checked)} />
          Governance changes need a person
        </label>
      </div>

      <h2>Judge</h2>
      <div className="actions">
        <div className="field"><span>Judge model</span>
          <ModelPicker label="Judge model" value={evals.judge?.model ?? "mock"} onChange={(v) => change({ ...evals, judge: { model: v } })} />
        </div>
        <span className="muted small grow">
          The judge grades the “Rubric” column: it reads the answer and decides whether it meets what you wrote. “mock” only compares words; with a real model it understands the meaning.
        </span>
      </div>

      <h2>Cases</h2>
      <p className="muted small">
        Each case is a task and how you know the answer is good. You can combine checks: text that must appear, text that must not appear, which agent it must reach and a rubric for the judge. Leave empty whatever you do not use.
      </p>
      <div className="table-wrap"><table className="edit">
        <thead>
          <tr><th>Id</th><th>Split</th><th>Tarea</th><th>Must contain (separate with |)</th><th>Must not contain</th><th>Expected route</th><th>Rubric for the judge</th><th /></tr>
        </thead>
        <tbody>
          {evals.cases.map((c: any, i: number) => (
            <tr key={i}>
              <td><input value={c.id ?? ""} onChange={(e) => setCase(i, { id: e.target.value })} style={{ width: 64 }} /></td>
              <td>
                <select value={c.split ?? "dev"} onChange={(e) => setCase(i, { split: e.target.value })}>
                  <option value="dev">dev</option>
                  <option value="heldout">held-out</option>
                </select>
              </td>
              <td><textarea rows={2} value={c.input ?? ""} onChange={(e) => setCase(i, { input: e.target.value })} /></td>
              <td><input value={join(c.expect?.contains)} onChange={(e) => setExpect(i, { contains: split(e.target.value) })} /></td>
              <td><input value={join(c.expect?.not_contains)} onChange={(e) => setExpect(i, { not_contains: split(e.target.value) })} /></td>
              <td><input value={c.expect?.route ?? ""} onChange={(e) => setExpect(i, { route: e.target.value })} style={{ width: 110 }} /></td>
              <td><textarea rows={2} value={c.expect?.rubric ?? ""} onChange={(e) => setExpect(i, { rubric: e.target.value })} /></td>
              <td><button onClick={() => change({ ...evals, cases: evals.cases.filter((_: any, j: number) => j !== i) })}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <div className="actions">
        <button onClick={() => change({ ...evals, cases: [...evals.cases, { id: `c${evals.cases.length + 1}`, split: "dev", input: "", expect: { contains: [] } }] })}>
          + Case
        </button>
        <button className="primary" disabled={!dirty} onClick={save}>Save evals</button>
        {msg && <span className={msg.kind}>{msg.text}</span>}
      </div>
    </div>
  );
}
