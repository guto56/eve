export type Role = "user" | "assistant";

export interface ToolRun {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  ok?: boolean;
  error?: string;
  errorKind?: string;
  durationMs?: number;
}

export interface Turn {
  sources?: string[];
  plan?: string[];
  taskId?: string;
  id: string;
  role: Role;
  text: string;
  tools: ToolRun[];
  routing?: Routing;
  streaming?: boolean;
  error?: string;
}

export interface Routing {
  route: string;
  decided_by: string;
  latency_ms: number;
  tools: string[];
  fast_path: boolean;
  reason: string;
}

export interface Approval {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  risk: string;
  reason: string;
  waiting_seconds: number;
}

export interface Status {
  version: string;
  uptime_seconds: number;
  server: { host: string; port: number };
  components: Record<string, string>;
  tools: { count: number; namespaces: string[]; pending_approvals: number };
  memory: { total: number; semantic_search: boolean };
  chat: { sessions: number };
  secrets: { configured: number; missing_required: string[] };
}

export interface MemoryItem {
  uid: string;
  content: string;
  kind: string;
  importance: number;
  updated_at: number;
  use_count: number;
}

export interface ToolSpec {
  name: string;
  description: string;
  risk: string;
  effective: { risk: string; allowed: boolean; needs_confirmation: boolean };
}
