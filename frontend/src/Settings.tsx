import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  settingsApi, type CheckResult, type FolderListing, type ProviderKind, type ProviderSettings,
  type SettingsDoc, type Status, type VaultInfo,
} from "./api";

export type Section = "providers" | "vault" | "general";
const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: "providers", label: "AI providers", hint: "Claude, Portkey and friends" },
  { id: "vault", label: "Obsidian vault", hint: "Skippy's second brain" },
  { id: "general", label: "You & voice", hint: "Name and speech model" },
];

type Preset = { key: string; title: string; blurb: string; make: () => Omit<ProviderSettings, "id"> };
const PRESETS: Preset[] = [
  { key: "claude", title: "Claude subscription", blurb: "Your Pro/Max plan via the Claude Code CLI",
    make: () => ({ label: "Claude", kind: "claude_code", enabled: true, models: ["sonnet", "opus", "haiku"], oauth_token: "" }) },
  { key: "portkey", title: "Portkey gateway", blurb: "Your company's AI gateway",
    make: () => ({ label: "Portkey", kind: "portkey", enabled: true, models: [], base_url: "", api_key: "",
      virtual_key: "", config: "", gateway_provider: "", tools: true, verify_tls: true }) },
  { key: "openai", title: "OpenAI", blurb: "An OpenAI API key",
    make: () => ({ label: "OpenAI", kind: "openai_compat", enabled: true, models: [], base_url: "https://api.openai.com/v1",
      api_key: "", tools: true, verify_tls: true }) },
  { key: "ollama", title: "Ollama", blurb: "Models running locally on this PC",
    make: () => ({ label: "Ollama", kind: "openai_compat", enabled: true, models: [], base_url: "http://host.docker.internal:11434/v1",
      api_key: "ollama", tools: false, verify_tls: true }) },
  { key: "custom", title: "Other OpenAI-compatible", blurb: "LiteLLM, LM Studio, OpenRouter…",
    make: () => ({ label: "Custom AI", kind: "openai_compat", enabled: true, models: [], base_url: "", api_key: "",
      tools: true, verify_tls: true }) },
];
const KIND_LABEL: Record<ProviderKind, string> = { claude_code: "Claude CLI", portkey: "Portkey", openai_compat: "OpenAI-compatible" };
const SETUP_CMD = "docker exec -it skippy claude setup-token";

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "provider";
const uniqueId = (base: string, taken: string[]) => {
  let id = slug(base), n = 2;
  while (taken.includes(id)) id = `${slug(base)}-${n++}`;
  return id;
};

export function Settings({ status, initial, onClose, onSaved }: {
  status: Status; initial: Section; onClose: () => void; onSaved: () => void;
}) {
  const [section, setSection] = useState<Section>(initial);
  const [saved, setSaved] = useState<SettingsDoc | null>(null);
  const [draft, setDraft] = useState<SettingsDoc | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    settingsApi.get().then((s) => { setSaved(s); setDraft(s); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const dirty = useMemo(() => JSON.stringify(saved) !== JSON.stringify(draft), [saved, draft]);
  const patch = (fn: (d: SettingsDoc) => SettingsDoc) => { setDraft((d) => (d ? fn(d) : d)); setFlash(""); };

  const save = async () => {
    if (!draft) return;
    setSaving(true); setError("");
    try {
      const next = await settingsApi.save(draft);
      setSaved(next); setDraft(next); setFlash("Saved. Skippy is using the new settings.");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="settings" role="dialog" aria-modal="true" aria-label="Settings">
        <nav className="settings-nav">
          <h2>Settings</h2>
          {SECTIONS.map((s) => (
            <button key={s.id} className={section === s.id ? "on" : ""} onClick={() => setSection(s.id)}>
              <strong>{s.label}</strong><small>{s.hint}</small>
            </button>
          ))}
          <p className="foot">Skippy {status.version}. Settings are stored in the container's data volume, not in .env.</p>
        </nav>

        <div className="settings-main">
          <header className="settings-head">
            <h3>{SECTIONS.find((s) => s.id === section)?.label}</h3>
            <button className="icon-btn" onClick={onClose} aria-label="Close settings">✕</button>
          </header>
          <div className="settings-body">
            {!draft ? (
              error ? <p className="error">{error}</p> : <p className="hint">Loading…</p>
            ) : section === "providers" ? (
              <Providers doc={draft} status={status} patch={patch} open={open} setOpen={setOpen} />
            ) : section === "vault" ? (
              <VaultSection doc={draft} patch={patch} />
            ) : (
              <General doc={draft} patch={patch} />
            )}
          </div>
          <footer className="settings-foot">
            {error && draft && <p className="error grow" role="alert">{error}</p>}
            {!error && <p className={`grow ${flash ? "ok" : "hint"}`} role="status">{flash || (dirty ? "You have unsaved changes." : "")}</p>}
            <button className="btn ghost" disabled={!dirty || saving} onClick={() => { setDraft(saved); setError(""); }}>Discard</button>
            <button className="btn" disabled={!dirty || saving} onClick={() => void save()}>{saving ? "Saving…" : "Save changes"}</button>
          </footer>
        </div>
      </section>
    </div>
  );
}

// ---- providers -----------------------------------------------------------------------

function Providers({ doc, status, patch, open, setOpen }: {
  doc: SettingsDoc; status: Status; patch: (fn: (d: SettingsDoc) => SettingsDoc) => void;
  open: string | null; setOpen: (id: string | null) => void;
}) {
  const [adding, setAdding] = useState(doc.providers.length === 0);
  const live = Object.fromEntries(status.providers.map((p) => [p.id, p]));

  const update = (id: string, fn: (p: ProviderSettings) => ProviderSettings) =>
    patch((d) => ({ ...d, providers: d.providers.map((p) => (p.id === id ? fn(p) : p)) }));
  const add = (preset: Preset) => {
    const base = preset.make();
    const id = uniqueId(base.label, doc.providers.map((p) => p.id));
    patch((d) => ({ ...d, providers: [...d.providers, { ...base, id }], default_provider: d.default_provider || id }));
    setOpen(id); setAdding(false);
  };
  const remove = (id: string) => patch((d) => ({
    ...d, providers: d.providers.filter((p) => p.id !== id),
    default_provider: d.default_provider === id ? "" : d.default_provider,
  }));

  return (
    <div className="stack">
      <p className="hint">Skippy can talk to several AIs and you pick one per message. The default is used for new chats.</p>
      {doc.providers.length === 0 && <div className="empty">No AI providers yet. Add one to start chatting.</div>}
      <ul className="cards">
        {doc.providers.map((p) => {
          const known = live[p.id];
          const state = !p.enabled ? "off" : known?.available ? "up" : "down";
          const expanded = open === p.id;
          return (
            <li key={p.id} className={`card${expanded ? " open" : ""}`}>
              <div className="card-head">
                <button className="card-title" onClick={() => setOpen(expanded ? null : p.id)} aria-expanded={expanded}>
                  <span className={`dot ${state}`} aria-hidden="true" />
                  <span><strong>{p.label || "Untitled"}</strong>
                    <small>{KIND_LABEL[p.kind]}{p.models.length ? ` · ${p.models.length} model${p.models.length > 1 ? "s" : ""}` : ""}
                      {state === "down" && known ? ` · ${known.reason}` : ""}{p.enabled && !known ? " · save to apply" : ""}</small></span>
                </button>
                {doc.default_provider === p.id
                  ? <span className="badge">Default</span>
                  : <button className="link small" onClick={() => patch((d) => ({ ...d, default_provider: p.id }))}>Make default</button>}
                <Toggle checked={p.enabled} onChange={(v) => update(p.id, (q) => ({ ...q, enabled: v }))} label={`Enable ${p.label}`} />
                <button className="icon-btn chev" onClick={() => setOpen(expanded ? null : p.id)} aria-label={expanded ? "Collapse" : "Edit"}>{expanded ? "▴" : "▾"}</button>
              </div>
              {expanded && <ProviderForm p={p} claudeCli={doc.claude_cli} onChange={(fn) => update(p.id, fn)} onRemove={() => remove(p.id)} />}
            </li>
          );
        })}
      </ul>
      {adding ? (
        <div className="presets">
          <div className="presets-head"><strong>Add an AI provider</strong>
            {doc.providers.length > 0 && <button className="link small" onClick={() => setAdding(false)}>Cancel</button>}</div>
          <div className="preset-grid">
            {PRESETS.map((pr) => (
              <button key={pr.key} className="preset" onClick={() => add(pr)}><strong>{pr.title}</strong><small>{pr.blurb}</small></button>
            ))}
          </div>
        </div>
      ) : (
        <button className="btn ghost" onClick={() => setAdding(true)}>+ Add provider</button>
      )}
    </div>
  );
}

function ProviderForm({ p, claudeCli, onChange, onRemove }: {
  p: ProviderSettings; claudeCli: boolean; onChange: (fn: (p: ProviderSettings) => ProviderSettings) => void; onRemove: () => void;
}) {
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [testing, setTesting] = useState(false);
  const set = <K extends keyof ProviderSettings>(key: K, value: ProviderSettings[K]) => { onChange((q) => ({ ...q, [key]: value })); setCheck(null); };

  const test = async () => {
    setTesting(true); setCheck(null);
    try { setCheck(await settingsApi.test(p)); } catch (e) { setCheck({ ok: false, message: (e as Error).message }); }
    finally { setTesting(false); }
  };
  const offered = (check?.models ?? []).filter((m) => !p.models.includes(m));

  return (
    <div className="card-body">
      <Field label="Name"><input value={p.label} onChange={(e) => set("label", e.target.value)} /></Field>

      {p.kind === "claude_code" ? (
        <>
          {!claudeCli && <p className="notice warn">The Claude CLI isn't installed in this image. Rebuild with INSTALL_CLAUDE_CODE=true to use this.</p>}
          <Secret label="Subscription token" value={p.oauth_token} isSet={!!p.oauth_token_set} onChange={(v) => set("oauth_token", v)}
            placeholder="sk-ant-oat01-…" />
          <div className="howto">
            <p>To get a token, run this once in a terminal on this PC, sign in when the browser opens, then paste the token above.</p>
            <CopyLine text={SETUP_CMD} />
          </div>
        </>
      ) : (
        <>
          <Field label={p.kind === "portkey" ? "Gateway URL" : "Base URL"} hint={p.kind === "portkey" ? "Usually ends in /v1" : undefined}>
            <input value={p.base_url ?? ""} onChange={(e) => set("base_url", e.target.value)}
              placeholder={p.kind === "portkey" ? "https://portkey.yourcompany.com/v1" : "https://…/v1"} />
          </Field>
          <Secret label={p.kind === "portkey" ? "Portkey API key" : "API key"} value={p.api_key} isSet={!!p.api_key_set} onChange={(v) => set("api_key", v)} />
          {p.kind === "portkey" && (
            <details className="more">
              <summary>Routing options (virtual key, config, provider)</summary>
              <p className="hint">Fill in whichever your Portkey setup uses to choose the upstream model provider.</p>
              <Secret label="Virtual key" value={p.virtual_key} isSet={!!p.virtual_key_set} onChange={(v) => set("virtual_key", v)} optional />
              <Field label="Config ID" optional><input value={p.config ?? ""} onChange={(e) => set("config", e.target.value)} placeholder="pc-…" /></Field>
              <Field label="Provider" optional><input value={p.gateway_provider ?? ""} onChange={(e) => set("gateway_provider", e.target.value)} placeholder="e.g. @anthropic or openai" /></Field>
            </details>
          )}
        </>
      )}

      <Field label="Models" hint={p.kind === "claude_code" ? "sonnet, opus and haiku always point at the latest versions" : "The first one is the default. Test the connection to see what's available."}>
        <ModelList models={p.models} onChange={(m) => set("models", m)} />
      </Field>

      {p.kind !== "claude_code" && (
        <div className="toggles">
          <Toggle checked={p.tools ?? true} onChange={(v) => set("tools", v)} label="Let this AI use vault tools"
            hint="Turn off if a model rejects function calling" />
          <Toggle checked={p.verify_tls ?? true} onChange={(v) => set("verify_tls", v)} label="Verify TLS certificates"
            hint="Only turn off for a gateway with a self-signed certificate" />
        </div>
      )}

      <div className="card-actions">
        <button className="btn ghost" onClick={() => void test()} disabled={testing}>{testing ? "Testing…" : "Test connection"}</button>
        <span className="grow" />
        <button className="link danger" onClick={onRemove}>Remove provider</button>
      </div>
      {check && (
        <div className={`check ${check.ok ? "good" : "bad"}`} role="status">
          <p>{check.message}</p>
          {offered.length > 0 && (
            <div className="offer">
              <div className="offer-head"><span>Add models:</span>
                <button className="link small" onClick={() => set("models", [...p.models, ...offered])}>Add all {offered.length}</button></div>
              <div className="chips">
                {offered.slice(0, 60).map((m) => <button key={m} className="chip add" onClick={() => onChange((q) => ({ ...q, models: [...q.models, m] }))}>+ {m}</button>)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ModelList({ models, onChange }: { models: string[]; onChange: (m: string[]) => void }) {
  const [text, setText] = useState("");
  const commit = () => {
    const extra = text.split(",").map((s) => s.trim()).filter((s) => s && !models.includes(s));
    if (extra.length) onChange([...models, ...extra]);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); }
    else if (e.key === "Backspace" && !text && models.length) onChange(models.slice(0, -1));
  };
  return (
    <div className="chips input-like">
      {models.map((m, i) => (
        <span key={m} className={`chip${i === 0 ? " first" : ""}`}>
          {m}
          {i > 0 && <button aria-label={`Make ${m} the default model`} title="Make default" onClick={() => onChange([m, ...models.filter((x) => x !== m)])}>★</button>}
          <button aria-label={`Remove ${m}`} onClick={() => onChange(models.filter((x) => x !== m))}>✕</button>
        </span>
      ))}
      <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onBlur={commit}
        placeholder={models.length ? "Add another…" : "Type a model name and press Enter"} aria-label="Add a model" />
    </div>
  );
}

// ---- vault ---------------------------------------------------------------------------

function VaultSection({ doc, patch }: { doc: SettingsDoc; patch: (fn: (d: SettingsDoc) => SettingsDoc) => void }) {
  const [vaults, setVaults] = useState<VaultInfo[] | null>(null);
  const [scanError, setScanError] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const setVault = (v: Partial<SettingsDoc["vault"]>) => patch((d) => ({ ...d, vault: { ...d.vault, ...v } }));

  const scan = () => { setVaults(null); setScanError(""); settingsApi.vaults().then(setVaults).catch((e) => setScanError(e.message)); };
  useEffect(scan, []);

  const current = doc.vault.path;
  const root = doc.vaults_root.replace(/[\\/]+$/, "");
  const display = (rel: string) => (rel === "." ? root : `${root}/${rel}`);

  return (
    <div className="stack">
      <div className={`current-vault${current ? "" : " none"}`}>
        <span className="vault-glyph" aria-hidden="true">◆</span>
        <div className="grow">
          <small>Current vault</small>
          <strong>{current ? (current === "." ? root.split(/[\\/]/).pop() : current.split("/").pop()) : "None selected"}</strong>
          {current && <code>{display(current)}</code>}
        </div>
        {current && <button className="link small" onClick={() => setVault({ path: "" })}>Disconnect</button>}
      </div>

      {!doc.vaults_root_available ? (
        <p className="notice warn">The vaults folder isn't mounted. Set <code>SKIPPY_VAULTS_DIR</code> in <code>.env</code> and restart the container.</p>
      ) : (
        <>
          <div className="row-head">
            <strong>Vaults found in <code>{root}</code></strong>
            <button className="link small" onClick={scan}>Rescan</button>
          </div>
          {scanError ? <p className="error">{scanError}</p> : vaults === null ? <p className="hint">Looking for Obsidian vaults…</p> : vaults.length === 0 ? (
            <p className="hint">No Obsidian vaults here yet. Browse to a folder or create a new vault below.</p>
          ) : (
            <div className="vault-list" role="radiogroup" aria-label="Vault">
              {vaults.map((v) => (
                <button key={v.path} role="radio" aria-checked={current === v.path} className={current === v.path ? "on" : ""} onClick={() => setVault({ path: v.path })}>
                  <span className="radio" aria-hidden="true" />
                  <span><strong>{v.name}</strong><small>{display(v.path)}</small></span>
                </button>
              ))}
            </div>
          )}
          {browsing ? (
            <FolderBrowser root={root} onPick={(path) => { setVault({ path }); setBrowsing(false); scan(); }} onCancel={() => setBrowsing(false)} />
          ) : (
            <button className="btn ghost" onClick={() => setBrowsing(true)}>Browse folders or create a vault…</button>
          )}
        </>
      )}

      <hr />
      <Toggle checked={doc.vault.writable} onChange={(v) => setVault({ writable: v })} label="Let Skippy edit notes"
        hint="Needed for todos, quick capture and saving chats. Skippy never deletes notes, but keep the vault backed up or in git." />
      <Field label="Skippy's folder" hint="Where Skippy keeps its own notes: Todos.md, Daily/ and Chats/.">
        <input value={doc.vault.folder} onChange={(e) => setVault({ folder: e.target.value })} />
      </Field>
      <p className="hint small">Skippy can only see inside <code>{root}</code>. To use a different parent folder, change <code>SKIPPY_VAULTS_DIR</code> in <code>.env</code> and restart; Docker needs it before Skippy starts.</p>
    </div>
  );
}

function FolderBrowser({ root, onPick, onCancel }: { root: string; onPick: (path: string) => void; onCancel: () => void }) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const go = (path: string) => { setError(""); settingsApi.folders(path).then(setListing).catch((e) => setError(e.message)); };
  useEffect(() => go("."), []);

  const crumbs = listing && listing.path !== "." ? listing.path.split("/") : [];
  const create = async () => {
    if (!listing || !name.trim()) return;
    try { onPick((await settingsApi.createVault(listing.path, name.trim())).path); }
    catch (e) { setError((e as Error).message); }
  };

  return (
    <div className="browser">
      <div className="crumbs">
        <button onClick={() => go(".")}>{root.split(/[\\/]/).pop() || root}</button>
        {crumbs.map((c, i) => (<span key={i}> / <button onClick={() => go(crumbs.slice(0, i + 1).join("/"))}>{c}</button></span>))}
      </div>
      {error && <p className="error">{error}</p>}
      {listing && (
        <>
          <ul className="folders">
            {listing.folders.length === 0 && <li className="hint">No subfolders</li>}
            {listing.folders.map((f) => (
              <li key={f.path}>
                <button onClick={() => go(f.path)}><span aria-hidden="true">{f.vault ? "◆" : "▸"}</span> {f.name}{f.vault && <small> vault</small>}</button>
              </li>
            ))}
          </ul>
          <div className="browser-actions">
            <button className="btn" onClick={() => onPick(listing.path)}>Use this folder</button>
            {!listing.is_vault && <span className="hint small">Not an Obsidian vault yet; that's fine, Skippy just needs Markdown files.</span>}
          </div>
          <div className="row">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New vault name" aria-label="New vault name"
              onKeyDown={(e) => { if (e.key === "Enter") void create(); }} />
            <button className="btn ghost" disabled={!name.trim()} onClick={() => void create()}>Create vault here</button>
          </div>
        </>
      )}
      <button className="link small" onClick={onCancel}>Close browser</button>
    </div>
  );
}

// ---- general -------------------------------------------------------------------------

const WHISPER_HINT: Record<string, string> = {
  "tiny.en": "fastest, least accurate", "base.en": "fast, good for clear speech",
  "small.en": "slower, noticeably more accurate", "medium.en": "slowest, most accurate; needs a fast CPU",
};

function General({ doc, patch }: { doc: SettingsDoc; patch: (fn: (d: SettingsDoc) => SettingsDoc) => void }) {
  const setProfile = (v: Partial<SettingsDoc["profile"]>) => patch((d) => ({ ...d, profile: { ...d.profile, ...v } }));
  return (
    <div className="stack">
      <Field label="Your name" hint="Skippy uses it in greetings and its notes.">
        <input value={doc.profile.user_name} onChange={(e) => setProfile({ user_name: e.target.value })} placeholder="What should Skippy call you?" />
      </Field>
      <Field label="Speech recognition model" hint="Whisper runs inside the container; nothing you say leaves this PC. A new model downloads on first use.">
        <select value={doc.profile.whisper_model} onChange={(e) => setProfile({ whisper_model: e.target.value })}>
          {doc.whisper_models.map((m) => <option key={m} value={m}>{m}: {WHISPER_HINT[m] ?? ""}</option>)}
        </select>
      </Field>
    </div>
  );
}

// ---- small pieces --------------------------------------------------------------------

function Field({ label, hint, optional, children }: { label: string; hint?: string; optional?: boolean; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}{optional && <em> optional</em>}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

function Secret({ label, value, isSet, onChange, placeholder, optional }: {
  label: string; value: string | undefined; isSet: boolean; onChange: (v: string | undefined) => void; placeholder?: string; optional?: boolean;
}) {
  const kept = value === undefined && isSet;
  const cleared = value === "" && isSet;
  return (
    <Field label={label} optional={optional}>
      <div className="secret">
        <input type="password" autoComplete="off" value={value ?? ""} onChange={(e) => onChange(e.target.value)}
          placeholder={kept ? "Saved. Type to replace it." : cleared ? "Will be removed when you save" : placeholder ?? ""} />
        {kept && <button className="link small" onClick={() => onChange("")}>Remove</button>}
        {(cleared || (value && isSet)) && <button className="link small" onClick={() => onChange(undefined)}>Keep saved</button>}
      </div>
    </Field>
  );
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className={`toggle${hint ? " with-text" : ""}`}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} aria-label={hint ? undefined : label} />
      <span className="track" aria-hidden="true"><span /></span>
      {hint && <span className="toggle-text"><strong>{label}</strong><small>{hint}</small></span>}
    </label>
  );
}

function CopyLine({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copyline">
      <code>{text}</code>
      <button className="link small" onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
      }}>{copied ? "Copied" : "Copy"}</button>
    </div>
  );
}
