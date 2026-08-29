import { useEffect, useState } from "react";
import { api } from "./api";
import type { Approval, MemoryItem, Status, ToolRun, ToolSpec, Turn } from "./types";

const RISCO_LABEL: Record<string, string> = {
  safe: "segura",
  confirm: "pede confirmação",
  privileged: "privilegiada",
  blocked: "bloqueada",
};

function resumoArgs(args: Record<string, unknown>) {
  const texto = JSON.stringify(args);
  return texto === "{}" ? "" : texto;
}

export function ToolChip({ run }: { run: ToolRun }) {
  const pendente = run.ok === undefined;
  return (
    <div className="tool" title={run.error ?? ""}>
      {pendente ? (
        <span className="spin" />
      ) : (
        <span className={`mark ${run.ok ? "ok" : "fail"}`}>{run.ok ? "●" : "✕"}</span>
      )}
      <code>{run.name}</code>
      <span className="args">{resumoArgs(run.arguments)}</span>
      {run.ok === false && <span className="args">{run.errorKind}</span>}
    </div>
  );
}

export function TurnView({ turn, detalhes }: { turn: Turn; detalhes: boolean }) {
  if (turn.role === "user") {
    return (
      <div className="turn user">
        <div className="bubble">{turn.text}</div>
      </div>
    );
  }
  return (
    <div className="turn">
      {detalhes && turn.routing && (
        <div className="routing">
          <span className="badge">{turn.routing.route}</span>
          <span className="badge">{turn.routing.decided_by}</span>
          <span>{turn.routing.latency_ms.toFixed(0)} ms</span>
          {turn.routing.fast_path ? (
            <span className="badge fast">sem modelo</span>
          ) : (
            <span>{turn.routing.tools.length} ferramentas</span>
          )}
        </div>
      )}
      {turn.tools.map((run) => (
        <ToolChip key={run.id} run={run} />
      ))}
      {turn.error ? (
        <div className="said error">{turn.error}</div>
      ) : (
        <div className="said">
          {turn.text}
          {turn.streaming && <span className="caret" />}
        </div>
      )}
      {!turn.streaming && turn.sources?.length ? (
        <div className="sources">
          {turn.sources.slice(0, 6).map((url) => (
            <a key={url} href={url} target="_blank" rel="noreferrer noopener">
              {new URL(url).hostname.replace(/^www\./, "")}
            </a>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ApprovalCard({
  approval,
  onDecide,
}: {
  approval: Approval;
  onDecide: (id: string, ok: boolean) => void;
}) {
  const args = resumoArgs(approval.args);
  return (
    <div className="approval">
      <h4>
        A EVE quer executar <code>{approval.tool}</code>
      </h4>
      {args && <pre>{JSON.stringify(approval.args, null, 2)}</pre>}
      <div className="why">{approval.reason}</div>
      <div className="row">
        <button className="yes" onClick={() => onDecide(approval.id, true)}>
          Autorizar
        </button>
        <button className="no" onClick={() => onDecide(approval.id, false)}>
          Não
        </button>
      </div>
    </div>
  );
}

export function Empty({ onPick }: { onPick: (texto: string) => void }) {
  return (
    <div className="empty">
      <h1>EVE</h1>
      <p>Pergunte, ou peça uma ação no seu Mac.</p>
      <div className="hints" style={{ justifyContent: "center", marginTop: 6 }}>
        {["que horas são", "quais aplicativos estão abertos", "quanto de RAM tem esse Mac"].map(
          (exemplo) => (
            <button key={exemplo} className="hint" onClick={() => onPick(exemplo)}>
              {exemplo}
            </button>
          ),
        )}
      </div>
    </div>
  );
}

function Linha({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="row-item">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}

export function Panel({ status, onClose }: { status: Status | null; onClose: () => void }) {
  const [aba, setAba] = useState<"sistema" | "memoria" | "ferramentas">("sistema");
  const [memorias, setMemorias] = useState<MemoryItem[]>([]);
  const [ferramentas, setFerramentas] = useState<ToolSpec[]>([]);

  useEffect(() => {
    if (aba === "memoria") api.memory().then((r) => setMemorias(r.memories)).catch(() => {});
    if (aba === "ferramentas") api.tools().then((r) => setFerramentas(r.tools)).catch(() => {});
  }, [aba]);

  return (
    <aside className="panel">
      <div className="head">
        {(["sistema", "memoria", "ferramentas"] as const).map((nome) => (
          <button
            key={nome}
            className="ghost"
            style={aba === nome ? { borderColor: "var(--accent-dim)", color: "var(--text)" } : {}}
            onClick={() => setAba(nome)}
          >
            {nome}
          </button>
        ))}
        <button className="ghost close" onClick={onClose}>
          fechar
        </button>
      </div>

      {aba === "sistema" && status && (
        <div className="rows">
          <Linha k="versão" v={status.version} />
          <Linha k="no ar há" v={`${Math.floor(status.uptime_seconds / 60)} min`} />
          <Linha k="ferramentas" v={`${status.tools.count} em ${status.tools.namespaces.length} grupos`} />
          <Linha
            k="memória"
            v={`${status.memory.total} · ${status.memory.semantic_search ? "busca híbrida" : "só textual"}`}
          />
          <Linha k="conversas" v={status.chat.sessions} />
          <Linha
            k="credenciais"
            v={
              status.secrets.missing_required.length
                ? `falta ${status.secrets.missing_required.join(", ")}`
                : `${status.secrets.configured} configuradas`
            }
          />
          <h3 style={{ marginTop: 8 }}>componentes</h3>
          {Object.entries(status.components).map(([nome, estado]) => (
            <Linha key={nome} k={nome} v={estado} />
          ))}
        </div>
      )}

      {aba === "memoria" && (
        <div className="rows">
          {memorias.length === 0 && <p className="why">Nada guardado ainda.</p>}
          {memorias.map((m) => (
            <div key={m.uid} className="memory">
              <div>{m.content}</div>
              <div className="meta">
                <span className={`kind ${m.kind}`}>{m.kind}</span>
                <span>{new Date(m.updated_at * 1000).toLocaleDateString("pt-BR")}</span>
                {m.use_count > 0 && <span>usada {m.use_count}×</span>}
                <button
                  className="ghost"
                  style={{ marginLeft: "auto", padding: "1px 8px" }}
                  onClick={() => {
                    api.forgetMemory(m.uid).then(() =>
                      setMemorias((atual) => atual.filter((x) => x.uid !== m.uid)),
                    );
                  }}
                >
                  esquecer
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {aba === "ferramentas" && (
        <div className="rows">
          {ferramentas.map((t) => (
            <div key={t.name} className="memory">
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <code style={{ color: "var(--tool)", fontSize: 12.5 }}>{t.name}</code>
                <span className={`risk-${t.effective.risk}`} style={{ fontSize: 11 }}>
                  {RISCO_LABEL[t.effective.risk]}
                </span>
              </div>
              <div className="meta" style={{ color: "var(--text-dim)" }}>
                {t.description}
              </div>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
