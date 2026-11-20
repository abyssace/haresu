import { useCallback, useEffect, useState } from "react";
import { api, fmt, waitJob } from "../api";
import TrendChart from "./TrendChart";

const STATUS: Record<string, string> = {
  accepted: "accepted", candidate: "candidate", rejected: "rejected", pending_human: "awaiting approval",
};

export default function LoopView({ h, onChanged, onOpenVersion, onGo }: {
  h: string; onChanged: () => void; onOpenVersion: (v: string) => void; onGo: (tab: string) => void;
}) {
  const [data, setData] = useState<any>(null);
  const [guide, setGuide] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [rounds, setRounds] = useState(2);
  const [approver, setApprover] = useState("");

  const load = useCallback(async () => {
    const [t, g] = await Promise.all([api.get(`/harnesses/${h}/trend`), api.get(`/harnesses/${h}/guide`)]);
    setData(t);
    setGuide(g);
  }, [h]);
  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  async function run(label: string, start: () => Promise<any>, isJob = true) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const first = await start();
      const result = isJob ? await waitJob(first, setBusy) : first;
      if (result?.problems?.length) setNotice(`No proposal. ${result.problems.join(" ")} ${result.patch?.diagnosis ?? ""}`);
      else if (Array.isArray(result)) setNotice(result.map((s: any) => s.step).join(" · "));
      else if (result?.version && result.patch) setNotice(`The meta-agent proposed ${result.version}. Now measure it.`);
      else if (result?.rules) setNotice(result.accepted ? `Accepted: ${result.iteration.replace("iter-", "iteration ")} passed the gate and is now the current version.`
        : result.needs_human ? "This candidate changes governance: type your name to approve it and decide again."
        : "Rejected: it did not pass the gate. In the table you can see which rule failed.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
      await load().catch(() => {});
      onChanged();
    }
  }

  if (!data || !guide) return <p className="empty">{error || "Loading…"}</p>;

  const blocked = guide.next === "graph" || guide.next === "evals" ? guide.steps.find((s: any) => s.id === guide.next) : null;
  const measured = new Set(data.rows.map((r: any) => r.version));
  const pending = data.versions.filter((v: any) => v.version !== data.current && ["candidate", "pending_human"].includes(v.status));
  const cand = pending[pending.length - 1];
  const next = !measured.has(data.current) ? "measure" : !cand ? "propose" : !measured.has(cand.version) ? "measure-cand" : "decide";
  const pm = (v: number | null, se: number, seeds: number) => (seeds > 1 && v != null ? `${fmt(v)} ± ${fmt(se)}` : fmt(v));
  const measure = (version?: string) => run("Measuring", () => api.post(`/harnesses/${h}/loop/measure`, version ? { version } : {}));

  return (
    <div className="page">
      <h1>Improvement cycle</h1>
      <p className="lead">The current version is <b>{data.current}</b>. The blue button is what comes next.</p>

      {blocked ? (
        <div className="callout">
          <div><b>Before measuring: {blocked.title.toLowerCase()}.</b><br /><span className="muted">{blocked.text}</span></div>
          <button className="primary" onClick={() => onGo(blocked.tab)}>{blocked.action}</button>
        </div>
      ) : (
        <div className="actions">
          <button className={next === "measure" ? "primary" : ""} disabled={!!busy} onClick={() => measure()}>Measure {data.current}</button>
          <button className={next === "propose" ? "primary" : ""} disabled={!!busy || !measured.has(data.current)}
            title={measured.has(data.current) ? "" : "Measure the current version first"}
            onClick={() => run("Proposing", () => api.post(`/harnesses/${h}/loop/propose`))}>
            Ask the meta-agent for a proposal
          </button>
          <span className="spacer" />
          <label className="inline">Rounds
            <input type="number" min={1} max={10} value={rounds} onChange={(e) => setRounds(Number(e.target.value))} />
          </label>
          <button disabled={!!busy} title="Measures, proposes, measures the candidate and decides, for the rounds you indicate"
            onClick={() => run("Cycle", () => api.post(`/harnesses/${h}/loop/cycle`, { rounds }))}>
            Automatic cycle
          </button>
        </div>
      )}
      {busy && <div className="note" role="status">{busy}…</div>}
      {error && <div className="note error" role="alert">{error}</div>}
      {notice && <div className="note ok" role="status">{notice}</div>}

      {pending.length > 0 && (
        <section>
          <h2>Candidates to decide</h2>
          {pending.map((v: any) => (
            <div key={v.version} className="cand">
              <div className="cand-info">
                <b>{v.version}</b> <span className="muted">by {v.author}</span>
                {v.needs_human && <span className="badge warn">changes governance</span>}
                <div className="muted">{v.note}</div>
              </div>
              <button onClick={() => onOpenVersion(v.version)}>View on the canvas</button>
              <button className={next === "measure-cand" && v === cand ? "primary" : ""} disabled={!!busy} onClick={() => measure(v.version)}>
                {measured.has(v.version) ? "Measure again" : "Measure"}
              </button>
              {v.needs_human && (
                <input placeholder="Your name, to approve" aria-label="Approved by" value={approver} onChange={(e) => setApprover(e.target.value)} />
              )}
              <button className={next === "decide" && v === cand ? "primary" : ""}
                title={measured.has(v.version) ? "" : "Measure this candidate first"}
                disabled={!!busy || !measured.has(v.version) || !measured.has(data.current)}
                onClick={() => run("Deciding", () => api.post(`/harnesses/${h}/loop/decide`, { candidate: v.version, approved_by: approver }), false)}>
                Decide
              </button>
            </div>
          ))}
        </section>
      )}

      <section>
        <h2>Trend</h2>
        {data.rows.length === 0 ? (
          <p className="muted">There are no measurements yet.</p>
        ) : (
          <>
            <TrendChart rows={data.rows} />
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Version</th><th className="r">Dev</th><th className="r">Held-out</th><th className="r">Gain</th>
                    <th className="r">Violations</th><th>Estado</th><th>Why</th></tr>
                </thead>
                <tbody>
                  {data.rows.map((r: any) => (
                    <tr key={r.iteration}>
                      <td><button className="link" onClick={() => onOpenVersion(r.version)}>{r.version}</button></td>
                      <td className="r">{pm(r.dev, r.dev_se, r.seeds)}</td>
                      <td className="r">{pm(r.heldout, r.heldout_se, r.seeds)}</td>
                      <td className="r">{r.gain == null ? "—" : `${r.gain >= 0 ? "+" : ""}${r.gain.toFixed(3)}`}</td>
                      <td className="r">{r.violations}</td>
                      <td><span className={`badge s-${r.status.replace(/\s+/g, "-")}`}>{r.status}</span></td>
                      <td>
                        {r.decision ? (
                          <details>
                            <summary>{r.decision.rules.filter((x: any) => x.ok).length} de {r.decision.rules.length} gate rules</summary>
                            <ul className="rules">
                              {r.decision.rules.map((x: any) => (
                                <li key={x.rule} className={x.ok ? "" : "bad"}>{x.ok ? "✓" : "✗"} {x.rule}: {x.detail}</li>
                              ))}
                            </ul>
                          </details>
                        ) : <span className="muted">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section>
        <h2>Versiones</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Version</th><th>Comes from</th><th>Author</th><th>Estado</th><th>Note</th></tr></thead>
            <tbody>
              {data.versions.map((v: any) => (
                <tr key={v.version}>
                  <td><button className="link" onClick={() => onOpenVersion(v.version)}>{v.version}</button>{v.version === data.current ? " · current" : ""}</td>
                  <td>{v.parent ?? "—"}</td>
                  <td>{v.author}</td>
                  <td>{STATUS[v.status] ?? v.status}</td>
                  <td>{v.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
