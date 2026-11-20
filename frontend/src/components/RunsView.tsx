import { useEffect, useState } from "react";
import { api } from "../api";
import TraceView from "./TraceView";

export default function RunsView({ h, initialRun }: { h: string; initialRun: string }) {
  const [runs, setRuns] = useState<any[] | null>(null);
  const [filter, setFilter] = useState("");
  const [run, setRun] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get(`/harnesses/${h}/runs`).then(setRuns).catch((e) => setError(e.message));
  }, [h]);

  useEffect(() => {
    if (initialRun) open(initialRun);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialRun]);

  function open(id: string) {
    setError("");
    api.get(`/harnesses/${h}/runs/${id}`).then(setRun).catch((e) => setError(e.message));
  }

  if (!runs) return <p className="empty">{error || "Loading…"}</p>;
  const iterations = Array.from(new Set(runs.map((r) => r.iteration).filter(Boolean)));
  const shown = runs.filter((r) => !filter || (filter === "manual" ? r.kind === "manual" : r.iteration === filter));

  return (
    <div className="split">
      <aside className="list">
        <div className="list-head">
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter runs">
            <option value="">All runs</option>
            <option value="manual">Manual tests</option>
            {iterations.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </div>
        {shown.length === 0 && <p className="muted pad">There are no runs yet. Test a task on the canvas or measure a version.</p>}
        {shown.map((r) => (
          <button key={r.id} className={`row${run?.id === r.id ? " active" : ""}`} onClick={() => open(r.id)}>
            <span className="row-top">
              <b>{r.case_id ?? "manual"}</b>
              <span className="muted">{r.version}{r.split ? ` · ${r.split}` : ""}</span>
              {r.grade && <span className={`badge ${r.grade.passed ? "s-accepted" : "s-rejected"}`}>{r.grade.passed ? "passes" : "fails"}</span>}
              {r.metrics?.violations > 0 && <span className="badge warn">{r.metrics.violations} viol.</span>}
            </span>
            <span className="row-sub">{r.input}</span>
          </button>
        ))}
      </aside>
      <section className="detail">
        {error && <div className="note error">{error}</div>}
        {!run ? (
          <p className="muted pad">Choose a run to see its step-by-step trace.</p>
        ) : (
          <>
            <h1>{run.case_id ? `Case ${run.case_id}` : "Manual test"}</h1>
            <p className="muted">
              Version {run.version}{run.iteration ? ` · ${run.iteration}` : ""}{run.split ? ` · ${run.split}` : ""} · route: {run.metrics.route || "—"} ·{" "}
              {run.metrics.steps} steps · {run.metrics.tool_calls} tools · {run.metrics.violations} violations ·{" "}
              {run.metrics.redactions} redactions · {run.metrics.tokens_in + run.metrics.tokens_out} tokens · {run.metrics.ms} ms
            </p>
            <p><b>Tarea.</b> {run.input}</p>
            <p><b>Answer.</b> {run.output || <i>(empty)</i>}</p>
            {run.grade && (
              <>
                <h2>Grade: {run.grade.score.toFixed(2)}</h2>
                <ul className="rules">
                  {run.grade.checks.map((c: any) => (
                    <li key={c.check}>{c.ok ? "✓" : "✗"} {c.check}{c.detail && !c.ok ? ` (${c.detail})` : ""}</li>
                  ))}
                </ul>
              </>
            )}
            <h2>Step by step</h2>
            <TraceView trace={run.trace} />
          </>
        )}
      </section>
    </div>
  );
}
