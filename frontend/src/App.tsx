import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import Canvas from "./components/Canvas";
import LoopView from "./components/LoopView";
import RunsView from "./components/RunsView";
import EvalsView from "./components/EvalsView";
import HelpView from "./components/HelpView";
import SettingsView from "./components/SettingsView";
import { ModelsCtx } from "./components/ModelPicker";

const TABS = [
  ["canvas", "Canvas"],
  ["evals", "Evals"],
  ["loop", "Cycle"],
  ["runs", "Runs"],
] as const;

export default function App() {
  const [catalog, setCatalog] = useState<any>(null);
  const [harnesses, setHarnesses] = useState<any[]>([]);
  const [h, setH] = useState("");
  const [tab, setTab] = useState<string>("canvas");
  const [openRun, setOpenRun] = useState("");
  const [openVersion, setOpenVersion] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [tick, setTick] = useState(0);
  const [keyInfo, setKeyInfo] = useState<any>(null);
  const wantedRef = useRef("");
  const harnessIds = useRef<string[]>([]);

  const reload = useCallback(async () => {
    const list = await api.get("/harnesses");
    setHarnesses(list);
    setTick((t) => t + 1);
    return list;
  }, []);

  const [models, setModels] = useState<{ loading: boolean; providers: any[] }>({ loading: true, providers: [] });
  const loadModels = useCallback(async (refresh = false) => {
    setModels((m) => ({ ...m, loading: true }));
    try {
      const r = await api.get(`/models${refresh ? "?refresh=true" : ""}`);
      setModels({ loading: false, providers: r.providers });
    } catch {
      setModels({ loading: false, providers: [] });
    }
  }, []);

  const refreshKeys = useCallback(async () => {
    const [k, c] = await Promise.all([api.get("/settings"), api.get("/catalog")]);
    setKeyInfo(k);
    setCatalog(c);
    loadModels();
  }, [loadModels]);

  useEffect(() => {
    api.get("/settings").then(setKeyInfo).catch(() => {});
    loadModels();
    Promise.all([api.get("/catalog"), reload()])
      .then(([c, list]) => {
        setCatalog(c);
        const wanted = decodeURIComponent(location.hash.slice(1));
        if (list.length) setH(list.some((x: any) => x.id === wanted) ? wanted : list[0].id);
      })
      .catch((e) => setError(`I could not reach the server: ${e.message}`));
  }, [reload, loadModels]);

  // An external agent can create harnesses: the list refreshes by itself.
  useEffect(() => {
    const t = setInterval(() => {
      api.get("/harnesses").then((list) => {
        setHarnesses((cur) => (JSON.stringify(cur) === JSON.stringify(list) ? cur : list));
        // A link with #id opens that harness as soon as it exists (an agent may have just created it).
        const wanted = decodeURIComponent(location.hash.slice(1));
        setH((cur) => (wanted && wanted !== cur && wantedRef.current !== wanted && list.some((x: any) => x.id === wanted)
          ? (wantedRef.current = wanted) : cur || list[0]?.id || ""));
      }).catch(() => {});
    }, 2000);
    const onHash = () => { wantedRef.current = ""; const w = decodeURIComponent(location.hash.slice(1)); if (w) setH((cur) => (harnessIds.current.includes(w) ? w : cur)); };
    window.addEventListener("hashchange", onHash);
    return () => { clearInterval(t); window.removeEventListener("hashchange", onHash); };
  }, []);

  // The address follows the open harness, so you can share it or come back to it.
  useEffect(() => {
    harnessIds.current = harnesses.map((x) => x.id);
    if (h && decodeURIComponent(location.hash.slice(1)) !== h) history.replaceState(null, "", `#${h}`);
  }, [h, harnesses]);

  async function create(newName: string, example: string) {
    setError("");
    try {
      const info = await api.post("/harnesses", { name: newName, example });
      await reload();
      setH(info.id);
      setOpenVersion("");
      setOpenRun("");
      setCreating(false);
      setName("");
      setTab("canvas");
    } catch (e: any) {
      setError(e.message);
    }
  }

  function createExample() {
    let n = "Support example";
    for (let k = 2; harnesses.some((x) => x.name === n); k++) n = `Support example ${k}`;
    create(n, "support");
  }

  const empty = catalog && harnesses.length === 0;
  const ready = (keyInfo?.providers ?? []).filter((p: any) => p.source !== "none");
  const form = (
    <form className="new-form" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create(name.trim(), "blank"); }}>
      <input autoFocus placeholder="Harness name" aria-label="Harness name" value={name} onChange={(e) => setName(e.target.value)} />
      <button className="primary" disabled={!name.trim()}>Create</button>
      {!empty && <button type="button" onClick={() => setCreating(false)}>Cancel</button>}
    </form>
  );

  return (
    <ModelsCtx.Provider value={{ ...models, refresh: () => loadModels(true) }}>
    <div className="app">
      <header className="topbar">
        <strong className="brand">haresu</strong>
        {!empty && (
          <>
            <select value={h} aria-label="Harness"
              onChange={(e) => { setH(e.target.value); setOpenVersion(""); setOpenRun(""); }}>
              {harnesses.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
            <button onClick={() => setCreating((v) => !v)} aria-expanded={creating}>New</button>
            <nav className="tabs" aria-label="Sections">
              {TABS.map(([id, label]) => (
                <button key={id} className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>{label}</button>
              ))}
            </nav>
          </>
        )}
        <button className={`key-chip${keyInfo && ready.length === 0 ? " none" : ""}`} onClick={() => setTab("settings")}
          title="Choose the keys of the model providers">
          {keyInfo ? (ready.length === 0 ? "No keys" : `Keys: ${ready.map((p: any) => p.name.split(" (")[0]).join(", ")}`) : "Keys"}
        </button>
        <button className={tab === "settings" ? "tab active" : "tab"} onClick={() => setTab("settings")}>Settings</button>
        <button className={tab === "help" ? "tab active" : "tab"} onClick={() => setTab("help")}>Help</button>
      </header>

      {creating && !empty && <div className="strip">{form}</div>}
      {error && <div className="strip error" role="alert">{error}</div>}

      <main className="main">
        {tab === "settings" ? (
          <SettingsView onChanged={refreshKeys} />
        ) : tab === "help" ? (
          <HelpView h={h} tick={tick} onGo={setTab} onExample={createExample} />
        ) : empty ? (
          <div className="welcome">
            <h1>Create your first harness</h1>
            <p>A harness is what surrounds your agents: which agent each task goes to, what context it sees, which tools it uses and which limits it respects. You start with an empty canvas and build it your way.</p>
            {form}
            <p className="muted">Prefer to see one already built? In <button className="link" onClick={() => setTab("help")}>Help</button> you can create an example.</p>
          </div>
        ) : catalog && h ? (
          <>
            {tab === "canvas" && (
              <Canvas key={h + openVersion} h={h} name={harnesses.find((x) => x.id === h)?.name ?? h} catalog={catalog}
                initialVersion={openVersion} onChanged={reload}
                onOpenHarness={async (id) => { const list = await reload(); setOpenVersion(""); setOpenRun(""); setH(id || list[0]?.id || ""); }}
                onOpenRun={(id) => { setOpenRun(id); setTab("runs"); }} />
            )}
            {tab === "loop" && (
              <LoopView key={h} h={h} onChanged={reload} onGo={setTab}
                onOpenVersion={(v) => { setOpenVersion(v); setTab("canvas"); }} />
            )}
            {tab === "runs" && <RunsView key={h} h={h} initialRun={openRun} />}
            {tab === "evals" && <EvalsView key={h} h={h} catalog={catalog} onChanged={reload} />}
          </>
        ) : null}
      </main>
    </div>
    </ModelsCtx.Provider>
  );
}
