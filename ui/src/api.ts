import type { Approval, LogEntry, MemoryItem, Status, ToolSpec } from "./types";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} em ${path}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  status: () => json<Status>("/api/status"),
  tools: () => json<{ tools: ToolSpec[]; count: number }>("/api/tools"),
  approvals: () => json<{ pending: Approval[] }>("/api/approvals"),
  decide: (id: string, approved: boolean) =>
    json(`/api/approvals/${id}`, {
      method: "POST",
      body: JSON.stringify({ approved, by: "interface" }),
    }),
  memory: (limit = 50) =>
    json<{ memories: MemoryItem[] }>(`/api/memory/recent?limit=${limit}`),
  searchMemory: (q: string) =>
    json<{ memories: MemoryItem[] }>(`/api/memory/search?q=${encodeURIComponent(q)}`),
  forgetMemory: (uid: string) => json(`/api/memory/${uid}`, { method: "DELETE" }),
  logs: (source: LogSource, lines = 300, q = "") =>
    json<{ entries: LogEntry[]; path: string; count: number }>(
      `/api/logs?source=${source}&lines=${lines}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
    ),
};

export const LOG_SOURCES = ["eve", "daemon", "service", "mcp"] as const;
export type LogSource = (typeof LOG_SOURCES)[number];

export interface ChatFrame {
  type: string;
  kind?: string;
  [key: string]: unknown;
}

type Listener = (frame: ChatFrame) => void;

/**
 * Conexão com o Core.
 *
 * Um socket só carrega a conversa e os eventos. Reconecta sozinho com recuo
 * progressivo: o daemon pode reiniciar sem que a interface precise ser
 * recarregada à mão.
 */
export class EveSocket {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private attempt = 0;
  private closedByUs = false;

  private topics: string;

  constructor(topics = "message.*,tool.*,router.*,memory.*,system.*") {
    this.topics = topics;
  }

  connect() {
    this.closedByUs = false;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${location.host}/ws?topics=${encodeURIComponent(this.topics)}&history=0`;
    const socket = new WebSocket(url);
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.emit({ type: "connection", kind: "open" });
    };
    socket.onmessage = (event) => {
      try {
        this.emit(JSON.parse(event.data) as ChatFrame);
      } catch {
        /* quadro inválido é ignorado, não derruba a conexão */
      }
    };
    socket.onclose = () => {
      this.emit({ type: "connection", kind: "closed" });
      if (this.closedByUs) return;
      const delay = Math.min(500 * 2 ** this.attempt++, 8000);
      setTimeout(() => this.connect(), delay);
    };
  }

  send(message: string, session: string | null) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify({ op: "chat", message, session }));
    return true;
  }

  on(listener: Listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(frame: ChatFrame) {
    this.listeners.forEach((listener) => listener(frame));
  }

  close() {
    this.closedByUs = true;
    this.socket?.close();
  }
}
