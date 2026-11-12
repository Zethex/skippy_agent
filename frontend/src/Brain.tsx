import { useEffect, useState, type FormEvent } from "react";
import { api, type SearchHit, type Status, type Todo } from "./api";
import { Markdown } from "./Markdown";

type Tab = "todos" | "capture" | "vault";

export function Brain({ status, refreshKey, onClose, onSetup }: { status: Status; refreshKey: number; onClose: () => void; onSetup: () => void }) {
  const [tab, setTab] = useState<Tab>("todos");
  const vault = status.vault;

  return (
    <aside className="brain" aria-label="Second brain">
      <header className="brain-head">
        <h2>Second brain</h2>
        <button className="icon-btn only-narrow" onClick={onClose} aria-label="Close second brain">✕</button>
      </header>
      {!vault.available ? (
        <div className="stack">
          <p className="hint">No Obsidian vault is connected yet. Pick one and Skippy can keep your todos, captures and notes there.</p>
          <button className="btn" onClick={onSetup}>Choose a vault</button>
        </div>
      ) : (
        <>
          <div className="tabs" role="tablist">
            {(["todos", "capture", "vault"] as Tab[]).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "tab on" : "tab"} onClick={() => setTab(t)}>
                {t === "todos" ? "Todos" : t === "capture" ? "Capture" : "Search"}
              </button>
            ))}
          </div>
          {tab === "todos" && <Todos refreshKey={refreshKey} folder={vault.folder} readOnly={!vault.writable} />}
          {tab === "capture" && <Capture folder={vault.folder} readOnly={!vault.writable} />}
          {tab === "vault" && <Search notes={vault.notes} />}
        </>
      )}
    </aside>
  );
}

function Todos({ refreshKey, folder, readOnly }: { refreshKey: number; folder: string; readOnly: boolean }) {
  const [todos, setTodos] = useState<Todo[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const run = (p: Promise<Todo[]>) => p.then((t) => { setTodos(t); setError(""); }).catch((e) => setError(e.message));

  useEffect(() => { run(api.todos()); }, [refreshKey]);

  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    run(api.addTodo(text));
    setText("");
  };
  const open = todos.filter((t) => !t.done);
  const done = todos.filter((t) => t.done);

  return (
    <div className="pane">
      {!readOnly && (
        <form onSubmit={add} className="row">
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a todo" aria-label="New todo" />
          <button className="btn" type="submit">Add</button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
      {todos.length === 0 && !error && (
        <p className="hint">Nothing on the list. Add one here, or ask Skippy to turn a plan into todos.</p>
      )}
      <ul className="todos">
        {[...open, ...done].map((t) => (
          <li key={t.line} className={t.done ? "done" : ""}>
            <label>
              <input type="checkbox" checked={t.done} disabled={readOnly} onChange={() => run(api.toggleTodo(t.line, !t.done))} />
              <span>{t.text}</span>
            </label>
          </li>
        ))}
      </ul>
      <p className="foot">Stored in {folder}/Todos.md</p>
    </div>
  );
}

function Capture({ folder, readOnly }: { folder: string; readOnly: boolean }) {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const save = (e: FormEvent) => {
    e.preventDefault();
    api.capture(text).then((r) => { setNote(`Added to ${r.path}`); setText(""); setError(""); }).catch((e) => setError(e.message));
  };
  if (readOnly) return <p className="hint">The vault is mounted read-only, so capture is off.</p>;
  return (
    <form className="pane" onSubmit={save}>
      <textarea rows={6} value={text} onChange={(e) => { setText(e.target.value); setNote(""); }}
        placeholder="A thought, a link, something to file later" aria-label="Quick capture" />
      <button className="btn" type="submit" disabled={!text.trim()}>Add to today's note</button>
      {note && <p className="ok">{note}</p>}
      {error && <p className="error">{error}</p>}
      <p className="foot">Goes to {folder}/Daily, stamped with the time</p>
    </form>
  );
}

function Search({ notes }: { notes: number }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [open, setOpen] = useState<{ path: string; content: string } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!q.trim()) { setHits(null); return; }
    const timer = setTimeout(() => api.search(q).then(setHits).catch((e) => setError(e.message)), 220);
    return () => clearTimeout(timer);
  }, [q]);

  if (open) {
    return (
      <div className="pane">
        <button className="link" onClick={() => setOpen(null)}>Back to results</button>
        <p className="note-path">{open.path}</p>
        <div className="note"><Markdown text={open.content} /></div>
      </div>
    );
  }
  return (
    <div className="pane">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${notes} notes`} aria-label="Search the vault" />
      {error && <p className="error">{error}</p>}
      {hits && hits.length === 0 && <p className="hint">No notes mention that.</p>}
      <ul className="hits">
        {hits?.map((h) => (
          <li key={h.path}>
            <button onClick={() => api.note(h.path).then(setOpen).catch((e) => setError(e.message))}>
              <strong>{h.title}</strong>
              <small>{h.path}</small>
              <span>{h.snippet}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
