import { useContext, useEffect, useState } from "react";
import { api } from "../api";
import { ModelPicker, ModelsCtx } from "./ModelPicker";

type Msg = { kind: string; text: string };

function ProviderCard({ p, onChanged, reload }: { p: any; onChanged: () => void; reload: () => Promise<void> }) {
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<Msg | null>(null);
  const [checking, setChecking] = useState("");
  const [confirm, setConfirm] = useState("");

  const using = p.keys.find((k: any) => k.id === p.active);
  const status = using ? `in use: “${using.name}” ····${using.last4}`
    : p.env.present ? `in use: the environment one ····${p.env.last4}` : "no key";

  async function run(action: () => Promise<any>, ok?: string) {
    setMsg(null);
    try {
      await action();
      await reload();
      onChanged();
      if (ok) setMsg({ kind: "ok", text: ok });
      return true;
    } catch (e: any) {
      setMsg({ kind: "error", text: e.message });
      return false;
    }
  }

  async function test(id?: string) {
    setChecking(id ?? "active");
    setMsg(null);
    try {
      const r = await api.post("/settings/check", { provider: p.id, ...(id ? { id } : {}) });
      setMsg({ kind: r.message.startsWith("It works") ? "ok" : "error", text: r.message });
    } catch (e: any) {
      setMsg({ kind: "error", text: e.message });
    } finally {
      setChecking("");
    }
  }

  return (
    <details className="prov" open={p.keys.length > 0 || p.id === "anthropic"} data-provider={p.id}>
      <summary>
        <span className="prov-name">{p.name}</span>
        <span className={p.source === "none" ? "prov-status none" : "prov-status"}>{status}</span>
      </summary>
      <div className="prov-body">
        {p.help && <p className="muted small">{p.help}</p>}
        <p className="muted small">Models are chosen from a list on each node. To type one by hand: <code>{p.example}</code>.</p>
        {p.keys.length > 0 && (
          <ul className="keys">
            {p.keys.map((k: any) => (
              <li key={k.id} className={k.id === p.active ? "key active" : "key"}>
                <span className="key-name">{k.name}</span>
                <code>····{k.last4}</code>
                {k.id === p.active ? <span className="badge s-accepted">in use</span> : (
                  <button onClick={() => run(() => api.post("/settings/active", { provider: p.id, id: k.id }), `Now “${k.name}” is used.`)}>Use this one</button>
                )}
                <button disabled={checking !== ""} onClick={() => test(k.id)}>{checking === k.id ? "Testing…" : "Test"}</button>
                {confirm === k.id
                  ? <button className="danger-fill" onClick={() => { setConfirm(""); run(() => api.request("DELETE", `/settings/keys/${k.id}`), `Removed “${k.name}”.`); }}>Yes, remove</button>
                  : <button className="danger-text" onClick={() => setConfirm(k.id)}>Remove</button>}
              </li>
            ))}
          </ul>
        )}
        {using && p.env.present && (
          <button onClick={() => run(() => api.post("/settings/active", { provider: p.id, id: null }), "Now the environment key is used.")}>
            Use the environment one (····{p.env.last4})
          </button>
        )}
        {p.source !== "none" && p.keys.length === 0 && (
          <button disabled={checking !== ""} onClick={() => test()}>{checking === "active" ? "Testing…" : "Test the environment key"}</button>
        )}
        <form className="key-form" onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api.post("/settings/keys", { provider: p.id, name, key }), "Key saved and in use. Press “Test” to check that the provider accepts it.")) { setKey(""); setName(""); }
        }}>
          <label className="field"><span>Name (to recognize it)</span>
            <input id={`key-name-${p.id}`} value={name} placeholder="Personal, Work…" onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field"><span>Key for {p.name}</span>
            <input id={`key-value-${p.id}`} type="password" autoComplete="off" spellCheck={false} value={key}
              placeholder={p.prefix ? `${p.prefix}…` : "paste your key"} onChange={(e) => setKey(e.target.value)} />
          </label>
          <button className="primary" disabled={!key.trim()}>Save and use</button>
        </form>
        {msg && <div className={`note ${msg.kind}`} role="status">{msg.text}</div>}
      </div>
    </details>
  );
}

/** My models: the ones you register with their name and, if you want, their own key. The selectors show only these. */
function MyModels({ info, onChanged }: { info: any; onChanged: () => void | Promise<void> }) {
  const ctx = useContext(ModelsCtx);
  const [models, setModels] = useState<any[]>([]);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [name, setName] = useState("");
  const [keyId, setKeyId] = useState("");
  const [newKeyName, setNewKeyName] = useState("");
  const [newKey, setNewKey] = useState("");
  const [editing, setEditing] = useState("");
  const [edit, setEdit] = useState({ name: "", key_id: "" });
  const [confirm, setConfirm] = useState("");

  const usable = info.providers.filter((p: any) => p.source !== "none");
  const chosen = usable.find((p: any) => p.id === provider) ?? usable[0];
  const suggestions: any[] = ctx.providers.find((p) => p.id === chosen?.id)?.models ?? [];
  const load = () => api.get("/registry").then((r) => setModels(r.models)).catch(() => {});
  useEffect(() => { load(); }, []);

  async function run(action: () => Promise<any>, ok: string) {
    setMsg(null);
    try {
      const r = await action();
      setModels(r.models);
      await onChanged();
      setMsg({ kind: "ok", text: ok });
      return true;
    } catch (e: any) {
      setMsg({ kind: "error", text: e.message });
      return false;
    }
  }

  async function register(e: React.FormEvent) {
    e.preventDefault();
    const adding = keyId === "__new";
    const done = await run(() => api.post("/registry", {
      provider: chosen.id, model, name,
      ...(adding ? { new_key: { name: newKeyName, key: newKey } } : { key_id: keyId || null }),
    }), `Registered “${name.trim() || model.trim()}”. It now appears in the model selectors.`);
    if (done) { setModel(""); setName(""); setKeyId(""); setNewKey(""); setNewKeyName(""); }
  }

  const keyLabel = (m: any) => m.key_name ? `key “${m.key_name}” ····${m.key_last4}`
    : m.key_missing ? "its key was removed: it uses the provider's" : "the provider's key in use";

  return (
    <section>
      <h2>My models</h2>
      <p className="muted small">
        Register the models you really use, with the name you want and, if you want, their own key. When a provider has registered models, the selectors show only those (with a link to see all). It is mostly useful for providers with hundreds of models, such as OpenRouter or OpenCode.
      </p>
      {models.length > 0 && (
        <ul className="keys">
          {models.map((m) => (
            <li key={m.id} className="key">
              {editing === m.id ? (
                <>
                  <input aria-label="Model name" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
                  <select aria-label="Model key" value={edit.key_id} onChange={(e) => setEdit({ ...edit, key_id: e.target.value })}>
                    <option value="">The provider's key in use</option>
                    {(info.providers.find((p: any) => p.id === m.provider)?.keys ?? []).map((k: any) => <option key={k.id} value={k.id}>{k.name} ····{k.last4}</option>)}
                  </select>
                  <button className="primary" onClick={async () => { if (await run(() => api.put(`/registry/${m.id}`, { name: edit.name, key_id: edit.key_id || null }), "Saved.")) setEditing(""); }}>Save</button>
                  <button onClick={() => setEditing("")}>Cancel</button>
                </>
              ) : (
                <>
                  <span className="key-name">{m.name}<span className="muted small"> · {info.providers.find((p: any) => p.id === m.provider)?.name} · <code>{m.model_id}</code></span></span>
                  <span className="muted small">{keyLabel(m)}</span>
                  <button onClick={() => { setEditing(m.id); setEdit({ name: m.name, key_id: m.key_id ?? "" }); }}>Edit</button>
                  {confirm === m.id
                    ? <button className="danger-fill" onClick={() => { setConfirm(""); run(() => api.request("DELETE", `/registry/${m.id}`), `Removed “${m.name}”.`); }}>Yes, remove</button>
                    : <button className="danger-text" onClick={() => setConfirm(m.id)}>Remove</button>}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {usable.length === 0 ? (
        <p className="muted">Add a provider's key first (below) to be able to register its models.</p>
      ) : (
        <form className="key-form" onSubmit={register}>
          <label className="field"><span>Provider</span>
            <select id="reg-provider" value={chosen?.id ?? ""} onChange={(e) => { setProvider(e.target.value); setKeyId(""); setModel(""); }}>
              {usable.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Model (the provider's id; type to search among {suggestions.length || "its"} models)</span>
            <input id="reg-model" list="reg-suggest" autoComplete="off" spellCheck={false} value={model} placeholder="for example: vendor/model-name"
              onChange={(e) => setModel(e.target.value)} />
            <datalist id="reg-suggest">
              {suggestions.map((m: any) => {
                const id = m.value.slice(`${chosen.id}:`.length);
                return <option key={m.value} value={id}>{m.name}</option>;
              })}
            </datalist>
          </label>
          <label className="field"><span>Name (to recognize it in the selectors)</span>
            <input id="reg-name" value={name} placeholder={model || "the same id"} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field"><span>Key it will use</span>
            <select id="reg-key" value={keyId} onChange={(e) => setKeyId(e.target.value)}>
              <option value="">The key in use for {chosen?.name}</option>
              {chosen?.keys.map((k: any) => <option key={k.id} value={k.id}>{k.name} ····{k.last4}</option>)}
              <option value="__new">Add a new key…</option>
            </select>
          </label>
          {keyId === "__new" && (
            <>
              <label className="field"><span>Name of the new key</span>
                <input id="reg-newkey-name" value={newKeyName} placeholder="Personal, Work…" onChange={(e) => setNewKeyName(e.target.value)} />
              </label>
              <label className="field"><span>New key</span>
                <input id="reg-newkey" type="password" autoComplete="off" spellCheck={false} value={newKey} onChange={(e) => setNewKey(e.target.value)} />
              </label>
            </>
          )}
          <button className="primary" disabled={!model.trim() || (keyId === "__new" && !newKey.trim())}>Register model</button>
        </form>
      )}
      {msg && <div className={`note ${msg.kind}`} role="status">{msg.text}</div>}
    </section>
  );
}

export default function SettingsView({ onChanged }: { onChanged: () => void | Promise<void> }) {
  const [info, setInfo] = useState<any>(null);
  const [prefs, setPrefs] = useState<any>(null);
  const [error, setError] = useState("");
  const load = async () => setInfo(await api.get("/settings"));
  useEffect(() => {
    load().catch((e) => setError(e.message));
    api.get("/preferences").then(setPrefs).catch(() => {});
  }, []);
  async function pickDefault(model: string) {
    try {
      const saved = await api.put("/preferences", { default_model: model });
      await onChanged(); // the rest of the app already knows the new model when it is shown as chosen
      setPrefs(saved);
    } catch (e: any) {
      setError(e.message);
    }
  }
  if (!info) return <p className="empty">{error || "Loading…"}</p>;

  return (
    <div className="page settings">
      <h1>Settings</h1>
      <p className="lead">
        Here you set the keys of the model providers. Each model is written as <code>provider:model</code> and haresu automatically uses that provider's key, in the agents, the canvas assistant, the meta-agent and the evals judge. A model without a provider that starts with <code>claude</code>, <code>gpt-</code>, <code>gemini</code>, <code>mistral-</code> or <code>deepseek</code> is recognized on its own.
      </p>
      <section>
        <h2>Default model</h2>
        <p className="muted small">New agents and the new meta-agent you create receive it: by hand, with the assistant or from Claude Code. It does not change the existing ones; each node can use another. The list comes from the providers that have a key.</p>
        {prefs && <ModelPicker label="Default model" value={prefs.default_model} onChange={pickDefault} />}
        {error && <div className="note error" role="alert">{error}</div>}
      </section>
      <MyModels info={info} onChanged={onChanged} />
      <h2>Keys per provider</h2>
      {info.providers.map((p: any) => <ProviderCard key={p.id} p={p} onChanged={onChanged} reload={load} />)}
      <p className="muted small">
        Keys are saved on this computer, in the file <code>secrets.json</code> of the workspace, with permissions only for your user. The screen never shows them in full and each key is only sent to its own provider. If you prefer not to save them here, define the provider's environment variable (for example <code>OPENAI_API_KEY</code>) al levantar el servidor.
      </p>
    </div>
  );
}
