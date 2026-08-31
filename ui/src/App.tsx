import { useEffect, useRef, useState } from "react";
import { ApprovalCard, Empty, Panel, TurnView } from "./components";
import { LivePage } from "./LivePage";
import { useEve } from "./useEve";

export default function App() {
  const { turns, connected, busy, approvals, status, send, decide, reset, voice, toggleVoice } =
    useEve();
  const [rascunho, setRascunho] = useState("");
  const [detalhes, setDetalhes] = useState(false);
  const [painel, setPainel] = useState(false);
  const [pagina, setPagina] = useState<Pagina>("conversa");
  const fim = useRef<HTMLDivElement>(null);
  const campo = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fim.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, approvals]);

  useEffect(() => {
    const area = campo.current;
    if (!area) return;
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, 190)}px`;
  }, [rascunho]);

  const enviar = (texto?: string) => {
    const conteudo = texto ?? rascunho;
    if (!conteudo.trim()) return;
    send(conteudo);
    setRascunho("");
  };

  return (
    <div className="app">
      <Menu atual={pagina} onIr={setPagina} onPainel={() => setPainel(true)} />
      {pagina === "aovivo" ? (
        <LivePage />
      ) : (
        <Conversa />
      )}
      {painel && <Panel status={status} onClose={() => setPainel(false)} />}
    </div>
  );

  function Conversa() {
    return (
      <>
      <header className="topbar">
        <div className="brand">
          <span className={`pulse ${connected ? "live" : "off"}`} />
          EVE
        </div>
        <div className="topbar-meta">
          {status && (
            <>
              <span>
                <b>{status.tools.count}</b> ferramentas
              </span>
              <span>
                <b>{status.memory.total}</b> memórias
              </span>
            </>
          )}
          {!connected && <span style={{ color: "var(--warn)" }}>reconectando…</span>}
        </div>
        <button className="ghost" onClick={() => setDetalhes((v) => !v)}>
          {detalhes ? "menos" : "detalhes"}
        </button>
        {turns.length > 0 && (
          <button className="ghost" onClick={reset}>
            nova
          </button>
        )}
      </header>

      <main className="stream">
        {turns.length === 0 && approvals.length === 0 ? (
          <Empty onPick={enviar} />
        ) : (
          turns.map((turn) => <TurnView key={turn.id} turn={turn} detalhes={detalhes} />)
        )}
        {approvals.map((a) => (
          <ApprovalCard key={a.id} approval={a} onDecide={decide} />
        ))}
        <div ref={fim} />
      </main>

      <div className="composer">
        {voice.active && (
          <div className={`voicebar ${voice.speaking ? "falando" : ""}`}>
            <span className={`wave ${voice.listening ? "on" : ""}`}>
              <i /><i /><i />
            </span>
            <span className="heard">
              {voice.speaking
                ? "a EVE está falando — fale para interromper"
                : voice.partial || "ouvindo…"}
            </span>
          </div>
        )}
        {voice.error && <div className="voice-error">{voice.error}</div>}
        <div className="field">
          <button
            className={`mic ${voice.active ? "on" : ""}`}
            onClick={toggleVoice}
            title={voice.active ? "Encerrar a voz" : "Falar com a EVE"}
          >
            {voice.active ? "■" : "●"}
          </button>
          <textarea
            ref={campo}
            rows={1}
            value={rascunho}
            placeholder={
              voice.active
                ? "microfone aberto — pode falar"
                : busy
                  ? "a EVE está trabalhando…"
                  : "Fale com a EVE"
            }
            onChange={(e) => setRascunho(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                enviar();
              }
            }}
          />
          <button className="send" disabled={busy || !rascunho.trim()} onClick={() => enviar()}>
            ↑
          </button>
        </div>
      </div>
      </>
    );
  }
}

type Pagina = "conversa" | "aovivo";

/** O menu fica à direita: a leitura é à esquerda, e navegar não disputa com ela. */
function Menu({
  atual,
  onIr,
  onPainel,
}: {
  atual: Pagina;
  onIr: (p: Pagina) => void;
  onPainel: () => void;
}) {
  const paginas: { id: Pagina; rotulo: string; icone: string }[] = [
    { id: "conversa", rotulo: "conversa", icone: "✱" },
    { id: "aovivo", rotulo: "ao vivo", icone: "◉" },
  ];
  return (
    <nav className="menu">
      {paginas.map((p) => (
        <button
          key={p.id}
          className={`item ${atual === p.id ? "ativo" : ""}`}
          onClick={() => onIr(p.id)}
          title={p.rotulo}
        >
          <span className="icone">{p.icone}</span>
          <span className="rotulo">{p.rotulo}</span>
        </button>
      ))}
      <div className="separa" />
      <button className="item" onClick={onPainel} title="painel">
        <span className="icone">▤</span>
        <span className="rotulo">painel</span>
      </button>
    </nav>
  );
}
