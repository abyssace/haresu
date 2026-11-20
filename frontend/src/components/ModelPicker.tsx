import { createContext, useContext, useState } from "react";

export type ModelsState = { loading: boolean; providers: any[]; refresh: () => void };
export const ModelsCtx = createContext<ModelsState>({ loading: false, providers: [], refresh: () => {} });

const INFER: [string, string][] = [["claude", "anthropic"], ["gpt-", "openai"], ["gemini", "google"], ["mistral-", "mistral"], ["deepseek", "deepseek"]];
const LONG_LIST = 25; // from here on, registering only the models that are used is suggested

/** The provider:model form of a name (same as the server's). */
export function canon(model: string): string {
  const m = (model || "mock").trim();
  if (m.startsWith("mock")) return "mock";
  if (m.includes(":")) return m;
  for (const [prefix, provider] of INFER) if (m.startsWith(prefix)) return `${provider}:${m}`;
  return m;
}

const label = (m: any) => (m.name === m.id ? m.id : `${m.name} (${m.id})`);

/**
 * Choose the model from a list built from the providers that have a key set.
 * If a provider has registered models (“My models”), only those are shown.
 */
export function ModelPicker({ value, onChange, label: ariaLabel = "Model", disabled = false }: {
  value: string; onChange: (model: string) => void; label?: string; disabled?: boolean;
}) {
  const ctx = useContext(ModelsCtx);
  const current = canon(value);
  const [filter, setFilter] = useState("");
  const [all, setAll] = useState(false);
  const [other, setOther] = useState(false);
  const [typed, setTyped] = useState("");

  const q = filter.trim().toLowerCase();
  const matches = (m: any) => !q || `${m.name} ${m.id}`.toLowerCase().includes(q) || m.value === current;

  // A provider with registered models shows only those, unless viewing all is requested.
  const groups: { id: string; name: string; shown: any[] }[] = [];
  let hidden = 0;
  for (const p of ctx.providers) {
    const reg: any[] = p.registered ?? [];
    const curated = reg.length > 0 && !all;
    if (curated) {
      const shown = reg.map((r) => ({ id: r.id, name: r.name, value: r.value, group: p.name }));
      const mine = new Set(reg.map((r) => r.value));
      hidden += p.models.filter((m: any) => !mine.has(m.value)).length;
      const extra = p.models.find((m: any) => m.value === current && !mine.has(m.value)); // the one already chosen stays visible
      if (extra) shown.push({ ...extra, name: `${extra.name} · not registered` });
      if (shown.some(matches)) groups.push({ id: p.id, name: p.name, shown: shown.filter(matches) });
      continue;
    }
    const names: string[] = Array.from(new Set(p.models.map((m: any) => m.group ?? p.name)));
    for (const g of names) {
      const shown = p.models.filter((m: any) => (m.group ?? p.name) === g && matches(m));
      if (shown.length) groups.push({ id: `${p.id}:${g}`, name: g, shown });
    }
  }
  const total = groups.reduce((n, g) => n + g.shown.length, 0);
  const known = current === "mock" || ctx.providers.some((p) => p.models.some((m: any) => m.value === current)
    || (p.registered ?? []).some((r: any) => r.value === current));
  const failed = ctx.providers.filter((p) => p.error);
  const long = ctx.providers.find((p) => !(p.registered ?? []).length && p.models.length > LONG_LIST);
  const anyCurated = ctx.providers.some((p) => (p.registered ?? []).length > 0);

  function commitOther() {
    const v = typed.trim();
    if (v) onChange(v);
    setOther(false);
    setTyped("");
  }

  return (
    <div className="model-picker">
      <select aria-label={ariaLabel} disabled={disabled} value={other ? "__other" : known ? current : "__custom"}
        onChange={(e) => {
          if (e.target.value === "__other") { setOther(true); return; }
          if (e.target.value === "__custom") return;
          setOther(false);
          onChange(e.target.value);
        }}>
        <option value="mock">Test model (mock, no key)</option>
        {groups.map((g) => (
          <optgroup key={g.id} label={g.name}>
            {g.shown.map((m: any) => <option key={m.value} value={m.value}>{label(m)}</option>)}
          </optgroup>
        ))}
        {!known && !other && <option value="__custom">{value} (not in the list)</option>}
        <option value="__other">Write another model…</option>
      </select>
      {total > LONG_LIST && <input className="model-filter" aria-label={`Filter ${ariaLabel.toLowerCase()}`} placeholder={`Filter ${total} models…`}
        value={filter} onChange={(e) => setFilter(e.target.value)} />}
      {other && (
        <input autoFocus aria-label="Model typed by hand" placeholder="provider:model" value={typed} onChange={(e) => setTyped(e.target.value)}
          onBlur={commitOther} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitOther(); } if (e.key === "Escape") { setOther(false); setTyped(""); } }} />
      )}
      {ctx.loading && <small className="muted">Loading models…</small>}
      {!ctx.loading && ctx.providers.length === 0 && <small className="muted">Only the test model is available. Add a key in Settings to choose real models.</small>}
      {long && <small className="muted">{long.name} offers {long.models.length} models. In Settings, “My models”, register the ones you use and this list gets shorter.</small>}
      {failed.map((p) => <small key={p.id} className="hint-bad">{p.error} You can type the model by hand.</small>)}
      {anyCurated && (all || hidden > 0) && (
        <button type="button" className="link small" onClick={() => setAll((v) => !v)}>
          {all ? "See only my models" : `See all models (${hidden} more)`}
        </button>
      )}
      {!ctx.loading && ctx.providers.length > 0 && <button type="button" className="link small" onClick={ctx.refresh}>Refresh list</button>}
    </div>
  );
}
