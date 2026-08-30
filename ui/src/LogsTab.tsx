import { useEffect, useMemo, useRef, useState } from "react";
import { api, LOG_SOURCES, type LogSource } from "./api";
import type { LogEntry } from "./types";
import { categoria, useLogs } from "./useLogs";

const CATEGORIAS = ["tool", "chat", "memory", "task", "voice", "watch", "system"] as const;

function hora(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.toLocaleTimeString("pt-BR", { hour12: false })}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

function resumo(payload: Record<string, unknown>): string {
  const partes: string[] = [];
  for (const [chave, valor] of Object.entries(payload)) {
    if (valor === null || valor === undefined || valor === "") continue;
    const texto =
      typeof valor === "object" ? JSON.stringify(valor) : String(valor);
    partes.push(`${chave}=${texto.length > 90 ? `${texto.slice(0, 89)}…` : texto}`);
    if (partes.length >= 6) break;
  }
  return partes.join("  ");
}

/** Fluxo ao vivo de tudo que o Core faz, mais os arquivos de log. */
export function LogsTab({ ativo }: { ativo: boolean }) {
  const { eventos, pausado, setPausado, conectado, limpar } = useLogs(ativo);
  const [modo, setModo] = useState<"eventos" | "arquivo">("eventos");
  const [fonte, setFonte] = useState<LogSource>("eve");
  const [busca, setBusca] = useState("");
  const [ocultas, setOcultas] = useState<Set<string>>(new Set());
  const [arquivo, setArquivo] = useState<LogEntry[]>([]);
  const [aberto, setAberto] = useState<number | null>(null);
  const fim = useRef<HTMLDivElement>(null);
  const seguir = useRef(true);

  const visiveis = useMemo(() => {
    const alvo = busca.toLowerCase();
    return eventos.filter((e) => {
      if (ocultas.has(categoria(e.type))) return false;
      if (!alvo) return true;
      return (
        e.type.toLowerCase().includes(alvo) ||
        e.source.toLowerCase().includes(alvo) ||
        JSON.stringify(e.payload).toLowerCase().includes(alvo)
      );
    });
  }, [eventos, busca, ocultas]);

  useEffect(() => {
    if (seguir.current && !pausado) fim.current?.scrollIntoView({ block: "end" });
  }, [visiveis, arquivo, pausado]);

  useEffect(() => {
    if (modo !== "arquivo" || !ativo) return;
    let vivo = true;
    const carregar = () =>
      api
        .logs(fonte, 400, busca)
        .then((r) => vivo && setArquivo(r.entries))
        .catch(() => {});
    carregar();
    const timer = setInterval(carregar, pausado ? 1e9 : 3000);
    return () => {
      vivo = false;
      clearInterval(timer);
    };
  }, [modo, fonte, busca, pausado, ativo]);

  const alternar = (cat: string) =>
    setOcultas((atual) => {
      const proximo = new Set(atual);
      if (proximo.has(cat)) proximo.delete(cat);
      else proximo.add(cat);
      return proximo;
    });

  return (
    <div className="logs">
      <div className="logs-barra">
        <button
          className={`ghost ${modo === "eventos" ? "on" : ""}`}
          onClick={() => setModo("eventos")}
        >
          eventos
        </button>
        <button
          className={`ghost ${modo === "arquivo" ? "on" : ""}`}
          onClick={() => setModo("arquivo")}
        >
          arquivo
        </button>
        {modo === "arquivo" && (
          <select
            className="logs-fonte"
            value={fonte}
            onChange={(e) => setFonte(e.target.value as LogSource)}
          >
            {LOG_SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        )}
        <input
          className="logs-busca"
          placeholder="filtrar…"
          value={busca}
          onChange={(e) => setBusca(e.target.value)}
        />
        <button className={`ghost ${pausado ? "on" : ""}`} onClick={() => setPausado(!pausado)}>
          {pausado ? "seguir" : "pausar"}
        </button>
        <button className="ghost" onClick={modo === "eventos" ? limpar : () => setArquivo([])}>
          limpar
        </button>
      </div>

      {modo === "eventos" && (
        <div className="logs-filtros">
          {CATEGORIAS.map((cat) => (
            <button
              key={cat}
              className={`chip cat-${cat} ${ocultas.has(cat) ? "off" : ""}`}
              onClick={() => alternar(cat)}
            >
              {cat}
            </button>
          ))}
          <span className="logs-conta">
            {visiveis.length}/{eventos.length}
            {conectado ? "" : " · sem conexão"}
          </span>
        </div>
      )}

      <div className="logs-fluxo" onScroll={(e) => {
        const el = e.currentTarget;
        seguir.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}>
        {modo === "eventos"
          ? visiveis.map((e) => (
              <div
                key={e.id}
                className={`log-linha cat-${categoria(e.type)}`}
                onClick={() => setAberto(aberto === e.id ? null : e.id)}
              >
                <span className="log-hora">{hora(e.ts)}</span>
                <span className="log-tipo">{e.type}</span>
                <span className="log-origem">{e.source}</span>
                <span className="log-detalhe">{resumo(e.payload)}</span>
                {aberto === e.id && (
                  <pre className="log-bruto">{JSON.stringify(e.payload, null, 2)}</pre>
                )}
              </div>
            ))
          : arquivo.map((linha, i) => (
              <div key={i} className={`log-linha nivel-${linha.level}`}>
                <span className="log-hora">{linha.ts.slice(11, 23)}</span>
                <span className="log-tipo">{linha.event || linha.level}</span>
                <span className="log-detalhe">{linha.detail}</span>
              </div>
            ))}
        {(modo === "eventos" ? visiveis : arquivo).length === 0 && (
          <p className="logs-vazio">
            {modo === "eventos"
              ? "Nada ainda. Fale com a EVE e os eventos aparecem aqui."
              : "Arquivo vazio ou inexistente."}
          </p>
        )}
        <div ref={fim} />
      </div>
    </div>
  );
}
