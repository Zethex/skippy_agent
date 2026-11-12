export type ProviderInfo = {
  id: string; label: string; kind: string; models: string[]; available: boolean; reason: string;
};
export type Status = {
  version: string;
  user_name: string;
  providers: ProviderInfo[];
  default_provider: string | null;
  sass_levels: string[];
  vault: { available: boolean; writable: boolean; folder: string; notes: number };
  voice: { available: boolean; model: string };
};
export type Conversation = { id: string; title: string; updated_at: number };
export type StoredMessage = { role: "user" | "assistant"; content: string };
export type Todo = { line: number; done: boolean; text: string };
export type SearchHit = { path: string; title: string; snippet: string };
export type ChatEvent =
  | { type: "start"; conversation_id: string; title: string }
  | { type: "delta"; text: string }
  | { type: "tool"; name: string; detail: string }
  | { type: "error"; message: string }
  | { type: "done" };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}
const post = <T,>(url: string, body?: unknown) =>
  request<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  status: () => request<Status>("/api/status"),
  conversations: () => request<Conversation[]>("/api/conversations"),
  conversation: (id: string) => request<Conversation & { messages: StoredMessage[] }>(`/api/conversations/${id}`),
  deleteConversation: (id: string) => request(`/api/conversations/${id}`, { method: "DELETE" }),
  saveConversation: (id: string) => post<{ path: string }>(`/api/conversations/${id}/save`),
  todos: () => request<Todo[]>("/api/todos"),
  addTodo: (text: string) => post<Todo[]>("/api/todos", { text }),
  toggleTodo: (line: number, done: boolean) => post<Todo[]>("/api/todos/toggle", { line, done }),
  capture: (text: string) => post<{ path: string }>("/api/capture", { text }),
  search: (q: string) => request<SearchHit[]>(`/api/vault/search?q=${encodeURIComponent(q)}`),
  note: (path: string) => request<{ path: string; content: string }>(`/api/vault/note?path=${encodeURIComponent(path)}`),
  transcribe: (audio: Blob) => {
    const form = new FormData();
    form.append("audio", audio, "speech.webm");
    return request<{ text: string }>("/api/transcribe", { method: "POST", body: form });
  },
};

export async function* streamChat(
  body: { message: string; conversation_id: string | null; provider: string | null; model: string | null; sass: number },
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
  });
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) yield JSON.parse(line) as ChatEvent;
  }
  if (buffer.trim()) yield JSON.parse(buffer) as ChatEvent;
}
