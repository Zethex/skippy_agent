import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api, streamChat, type Conversation, type Status } from "./api";
import { Brain } from "./Brain";
import { Markdown } from "./Markdown";
import { Settings, type Section } from "./Settings";
import { useRecorder } from "./useRecorder";

type Msg = { role: "user" | "assistant"; content: string; tools?: { name: string; detail: string }[]; error?: string; pending?: boolean };

const TOOL_LABEL: Record<string, string> = {
  Read: "Read", Glob: "Looked for", Grep: "Searched for", Edit: "Edited", Write: "Wrote",
  vault_search: "Searched for", vault_read: "Read", vault_write: "Wrote", vault_append: "Added to",
  todo_add: "Added todo", todo_list: "Checked the todo list",
};
const STARTERS = [
  "Plan my day from my todo list",
  "What do my notes say about ",
  "Turn this into todos: ",
  "Sketch an architecture note for ",
];
const stored = (key: string, fallback: string) => { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } };
const store = (key: string, value: string) => { try { localStorage.setItem(key, value); } catch { /* private mode */ } };

function Core({ state, size = "sm" }: { state: "idle" | "listening" | "thinking"; size?: "sm" | "lg" }) {
  return <span className={`core core-${size} is-${state}`} aria-hidden="true"><i /></span>;
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [loadError, setLoadError] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [sass, setSass] = useState(() => Number(stored("skippy.sass", "1")));
  const [notice, setNotice] = useState("");
  const [voice, setVoice] = useState<"idle" | "transcribing">("idle");
  const [brainTick, setBrainTick] = useState(0);
  const [showBrain, setShowBrain] = useState(false);
  const [showNav, setShowNav] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState<Section | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const recorder = useRecorder();

  const refreshConversations = useCallback(() => api.conversations().then(setConversations).catch(() => {}), []);

  const loadStatus = useCallback(() => api.status().then((s) => {
      setStatus(s);
      const saved = stored("skippy.provider", "");
      const pick = s.providers.find((p) => p.id === saved && p.available)?.id ?? s.default_provider ?? s.providers[0]?.id ?? null;
      setProvider(pick);
      const models = s.providers.find((p) => p.id === pick)?.models ?? [];
      const savedModel = stored("skippy.model", "");
      setModel(models.includes(savedModel) ? savedModel : models[0] ?? null);
    return s;
  }), []);

  useEffect(() => {
    loadStatus().then((s) => {
      if (!s.providers.some((p) => p.available)) setSettingsOpen("providers");
    }).catch((e) => setLoadError(e.message));
    void refreshConversations();
  }, [loadStatus, refreshConversations]);

  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }); }, [messages]);
  useEffect(() => {
    const el = input.current;
    if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 220) + "px"; }
  }, [draft]);

  const patchLast = (fn: (m: Msg) => Msg) => setMessages((all) => all.map((m, i) => (i === all.length - 1 ? fn(m) : m)));

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setDraft("");
    setNotice("");
    setMessages((all) => [...all, { role: "user", content: text.trim() }, { role: "assistant", content: "", tools: [], pending: true }]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      for await (const ev of streamChat({ message: text, conversation_id: activeId, provider, model, sass }, ctrl.signal)) {
        if (ev.type === "start") setActiveId(ev.conversation_id);
        else if (ev.type === "delta") patchLast((m) => ({ ...m, content: m.content + ev.text }));
        else if (ev.type === "tool") patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), ev] }));
        else if (ev.type === "error") patchLast((m) => ({ ...m, error: ev.message }));
      }
    } catch (e) {
      if (!ctrl.signal.aborted) patchLast((m) => ({ ...m, error: (e as Error).message }));
    } finally {
      patchLast((m) => ({ ...m, pending: false }));
      setBusy(false);
      setBrainTick((n) => n + 1);
      void refreshConversations();
    }
  };

  const openConversation = async (id: string) => {
    if (busy) return;
    const conv = await api.conversation(id);
    setActiveId(id);
    setMessages(conv.messages.map((m) => ({ role: m.role, content: m.content })));
    setShowNav(false);
    setNotice("");
  };
  const newChat = () => { if (!busy) { setActiveId(null); setMessages([]); setNotice(""); setShowNav(false); input.current?.focus(); } };
  const removeConversation = async (id: string) => {
    await api.deleteConversation(id);
    if (id === activeId) newChat();
    void refreshConversations();
  };

  const toggleMic = async () => {
    setNotice("");
    try {
      if (!recorder.recording) return await recorder.start();
      const blob = await recorder.stop();
      if (!blob) return;
      setVoice("transcribing");
      const { text } = await api.transcribe(blob);
      if (text) setDraft((d) => (d ? d.trimEnd() + " " : "") + text);
      else setNotice("I didn't catch any speech in that recording.");
      input.current?.focus();
    } catch (e) {
      const err = e as Error;
      setNotice(err.name === "NotAllowedError" ? "Microphone access was blocked. Allow it for this site and try again." : err.message);
    } finally {
      setVoice("idle");
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(draft); }
  };
  const chooseProvider = (id: string) => {
    const models = status?.providers.find((p) => p.id === id)?.models ?? [];
    setProvider(id); setModel(models[0] ?? null);
    store("skippy.provider", id);
  };

  if (loadError) return <div className="fatal"><h1>Skippy can't reach its backend</h1><p>{loadError}</p></div>;
  if (!status) return <div className="fatal"><Core state="thinking" size="lg" /></div>;

  const current = status.providers.find((p) => p.id === provider);
  const coreState = recorder.recording ? "listening" : busy || voice === "transcribing" ? "thinking" : "idle";
  const name = status.user_name;

  return (
    <div className={`shell${showBrain ? " brain-open" : ""}${showNav ? " nav-open" : ""}`}>
      <nav className="rail" aria-label="Conversations">
        <div className="brand">
          <Core state={coreState} />
          <div><h1>Skippy</h1><p>the Magnificent</p></div>
        </div>
        <button className="btn wide" onClick={newChat}>New conversation</button>
        <ul className="convs">
          {conversations.map((c) => (
            <li key={c.id} className={c.id === activeId ? "on" : ""}>
              <button className="conv" onClick={() => void openConversation(c.id)}>{c.title}</button>
              <button className="icon-btn" aria-label={`Delete ${c.title}`} onClick={() => void removeConversation(c.id)}>✕</button>
            </li>
          ))}
        </ul>
        <div className="systems">
          <button onClick={() => setSettingsOpen("providers")}><span className="k">AI</span><span className={current?.available ? "v up" : "v down"}>{current?.available ? current.label : "Set up an AI"}</span></button>
          <button onClick={() => setSettingsOpen("vault")}><span className="k">Vault</span><span className={status.vault.available ? "v up" : "v down"}>{status.vault.available ? `${status.vault.name} · ${status.vault.notes} note${status.vault.notes === 1 ? "" : "s"}${status.vault.writable ? "" : ", read-only"}` : "Choose a vault"}</span></button>
          <button onClick={() => setSettingsOpen("general")}><span className="k">Voice</span><span className={status.voice.available ? "v up" : "v down"}>{status.voice.available ? `Whisper ${status.voice.model}` : "Not installed"}</span></button>
        </div>
        <button className="btn ghost wide gear" onClick={() => setSettingsOpen("providers")}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" strokeLinecap="round" /></svg>
          Settings
        </button>
      </nav>

      <main className="stage">
        <header className="bar">
          <button className="icon-btn only-narrow" onClick={() => setShowNav(!showNav)} aria-label="Conversations">☰</button>
          <div className="dial" role="radiogroup" aria-label="How much attitude Skippy has">
            {status.sass_levels.map((label, i) => (
              <button key={label} role="radio" aria-checked={sass === i} className={sass === i ? "on" : ""}
                onClick={() => { setSass(i); store("skippy.sass", String(i)); }}>{label}</button>
            ))}
          </div>
          <span className="grow" />
          {activeId && status.vault.writable && (
            <button className="btn ghost" onClick={() => api.saveConversation(activeId).then((r) => setNotice(`Saved to ${r.path}`)).catch((e) => setNotice(e.message))}>
              Save to vault
            </button>
          )}
          <button className="btn ghost only-mid" onClick={() => setShowBrain(!showBrain)}>Second brain</button>
        </header>

        <div className="scroll" ref={scroller}>
          {messages.length === 0 ? (
            <section className="hero">
              <Core state={coreState} size="lg" />
              <h2>{name ? `What are we fixing today, ${name}?` : "What are we fixing today?"}</h2>
              <p>Type or talk. I can read and write your Obsidian vault, keep your todo list, and remember what we decide.</p>
              {(!status.providers.some((p) => p.available) || !status.vault.available) && (
                <div className="setup">
                  {!status.providers.some((p) => p.available) && <button className="btn" onClick={() => setSettingsOpen("providers")}>Connect an AI</button>}
                  {!status.vault.available && <button className="btn ghost" onClick={() => setSettingsOpen("vault")}>Choose your Obsidian vault</button>}
                </div>
              )}
              <div className="starters">
                {STARTERS.map((s) => (
                  <button key={s} onClick={() => { setDraft(s); input.current?.focus(); }}>{s.trim()}{s.endsWith(" ") ? "…" : ""}</button>
                ))}
              </div>
            </section>
          ) : (
            <ol className="thread">
              {messages.map((m, i) => (
                <li key={i} className={`msg ${m.role}`}>
                  {m.role === "assistant" && <Core state={m.pending ? "thinking" : "idle"} />}
                  <div className="body">
                    {m.tools && m.tools.length > 0 && (
                      <ul className="tools">
                        {m.tools.map((t, j) => <li key={j}>{TOOL_LABEL[t.name] ?? t.name} <span>{t.detail}</span></li>)}
                      </ul>
                    )}
                    {m.role === "user" ? <p className="plain">{m.content}</p> : <Markdown text={m.content} />}
                    {m.pending && !m.content && <p className="thinking">Thinking</p>}
                    {m.error && <p className="error" role="alert">{m.error}</p>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>

        <footer className="composer">
          {notice && <p className="notice" role="status">{notice}</p>}
          {!current?.available && (
            <p className="notice warn">
              {current ? `${current.label} is not ready: ${current.reason}.` : "No AI provider is set up yet."}{" "}
              <button className="link" onClick={() => setSettingsOpen("providers")}>Open settings</button>
            </p>
          )}
          <div className={`box${recorder.recording ? " live" : ""}`}>
            <textarea ref={input} rows={1} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey}
              placeholder={recorder.recording ? "Listening. Press the mic again when you're done." : voice === "transcribing" ? "Transcribing" : "Message Skippy"}
              aria-label="Message Skippy" />
            {status.voice.available && (
              <button className={`mic${recorder.recording ? " on" : ""}`} onClick={() => void toggleMic()} disabled={voice === "transcribing"}
                aria-pressed={recorder.recording} aria-label={recorder.recording ? "Stop recording" : "Speak"} title={recorder.recording ? "Stop recording" : "Speak"}>
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                  <rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                </svg>
              </button>
            )}
            {busy
              ? <button className="btn send" onClick={() => abort.current?.abort()}>Stop</button>
              : <button className="btn send" onClick={() => void send(draft)} disabled={!draft.trim() || !current?.available}>Send</button>}
          </div>
          <div className="pickers">
            <label>AI
              <select value={provider ?? ""} onChange={(e) => chooseProvider(e.target.value)}>
                {status.providers.map((p) => <option key={p.id} value={p.id} disabled={!p.available}>{p.label}{p.available ? "" : " (not set up)"}</option>)}
              </select>
            </label>
            {current && current.models.length > 0 && (
              <label>Model
                <select value={model ?? ""} onChange={(e) => { setModel(e.target.value); store("skippy.model", e.target.value); }}>
                  {current.models.map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
            )}
            <span className="grow" />
            <span className="keys">Enter to send, Shift+Enter for a new line</span>
          </div>
        </footer>
      </main>

      <Brain status={status} refreshKey={brainTick} onClose={() => setShowBrain(false)} onSetup={() => setSettingsOpen("vault")} />
      {settingsOpen && (
        <Settings status={status} initial={settingsOpen} onClose={() => setSettingsOpen(null)}
          onSaved={() => { void loadStatus(); setBrainTick((n) => n + 1); }} />
      )}
    </div>
  );
}
