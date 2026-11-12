export type ProviderInfo = {
  id: string; label: string; kind: string; models: string[]; available: boolean; reason: string;
};
export type Status = {
  version: string;
  user_name: string;
  providers: ProviderInfo[];
  default_provider: string | null;
  sass_levels: string[];
  vault: { available: boolean; writable: boolean; folder: string; notes: number; name: string; path: string };
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

// ---- settings ------------------------------------------------------------------
export type ProviderKind = "claude_code" | "portkey" | "openai_compat";
/** Secrets come back only as `<name>_set` flags. In a draft, a secret that is `undefined` keeps
 *  the saved value, "" clears it, and any other string replaces it. */
export type ProviderSettings = {
  id: string; label: string; kind: ProviderKind; enabled: boolean; models: string[];
  oauth_token?: string; oauth_token_set?: boolean;
  base_url?: string; api_key?: string; api_key_set?: boolean; tools?: boolean; verify_tls?: boolean;
  virtual_key?: string; virtual_key_set?: boolean; config?: string; gateway_provider?: string;
  headers?: Record<string, string>;
};
export type SettingsDoc = {
  profile: { user_name: string; whisper_model: string };
  vault: { path: string; folder: string; writable: boolean };
  providers: ProviderSettings[];
  default_provider: string;
  vaults_root: string;
  vaults_root_available: boolean;
  claude_cli: boolean;
  whisper_models: string[];
};
export type VaultInfo = { path: string; name: string };
export type FolderListing = { path: string; is_vault: boolean; folders: { path: string; name: string; vault: boolean }[] };
export type CheckResult = { ok: boolean; message: string; models?: string[] };

export const settingsApi = {
  get: () => request<SettingsDoc>("/api/settings"),
  save: (doc: Partial<SettingsDoc>) =>
    request<SettingsDoc>("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(doc) }),
  test: (p: ProviderSettings) => post<CheckResult>("/api/settings/test", p),
  vaults: () => request<VaultInfo[]>("/api/settings/vaults"),
  folders: (path: string) => request<FolderListing>(`/api/settings/folders?path=${encodeURIComponent(path)}`),
  createVault: (parent: string, name: string) => post<{ path: string }>("/api/settings/vaults", { parent, name }),
};
