/**
 * A página de conversa ao vivo.
 *
 * Quem está falando não lê a tela: ela existe para dar sinal de que está
 * funcionando — que o microfone está aberto, que a EVE ouviu, que ela mexeu
 * na memória — e para deixar um rastro escrito depois.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { LiveClient, type LiveEvent } from "./live";

type Fala = { de: "voce" | "eve"; texto: string };
type Acao = { nome: string; ok: boolean | null; erro: string | null };

export function LivePage({ onVoltar }: { onVoltar: () => void }) {
  const [ligada, setLigada] = useState(false);
  const [abrindo, setAbrindo] = useState(false);
  const [falas, setFalas] = useState<Fala[]>([]);
  const [acoes, setAcoes] = useState<Acao[]>([]);
  const [erro, setErro] = useState<{ texto: string; dica?: string } | null>(null);
  const [info, setInfo] = useState<{ model: string; voice: string } | null>(null);
  const [rascunho, setRascunho] = useState("");
  const cliente = useRef<LiveClient | null>(null);
  const fim = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fim.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [falas, acoes]);

  const receber = useCallback((e: LiveEvent) => {
    switch (e.kind) {
      case "ready":
        setInfo({ model: e.model, voice: e.voice });
        setLigada(true);
        setAbrindo(false);
        break;
      // A transcrição chega em pedaços: emenda no que já está lá, em vez de
      // empilhar uma linha por sílaba.
      case "partial":
        setFalas((f) => emendar(f, "voce", e.text));
        break;
      case "reply":
        setFalas((f) => emendar(f, "eve", e.text));
        break;
      case "tool":
        setAcoes((a) => [...a, { nome: e.name, ok: null, erro: null }]);
        break;
      case "tool_result":
        setAcoes((a) =>
          a.map((x, i) => (i === a.length - 1 ? { ...x, ok: e.ok, erro: e.error } : x)),
        );
        break;
      case "turn":
        setFalas((f) => f.map((x) => ({ ...x, fechada: true }) as Fala));
        break;
      case "error":
        setErro({ texto: e.error, dica: e.hint });
        if (e.fatal) {
          setLigada(false);
          setAbrindo(false);
        }
        break;
      case "closed":
        setLigada(false);
        setAbrindo(false);
        break;
    }
  }, []);

  const alternar = async () => {
    if (cliente.current?.active) {
      await cliente.current.stop();
      cliente.current = null;
      setLigada(false);
      return;
    }
    setErro(null);
    setAbrindo(true);
    const c = new LiveClient(receber);
    cliente.current = c;
    try {
      await c.start();
    } catch (exc) {
      setErro({ texto: exc instanceof Error ? exc.message : "não consegui abrir o microfone" });
      setAbrindo(false);
      cliente.current = null;
    }
  };

  useEffect(() => () => void cliente.current?.stop(), []);

  const enviar = () => {
    const texto = rascunho.trim();
    if (!texto || !ligada) return;
    cliente.current?.enviarTexto(texto);
    setFalas((f) => [...f, { de: "voce", texto }]);
    setRascunho("");
  };

  return (
    <div className="app live">
      <header className="topbar">
        <div className="brand">
          <span className={`pulse ${ligada ? "live" : "off"}`} />
          ao vivo
        </div>
        <div className="topbar-meta">
          {info && (
            <span>
              {info.model} · voz {info.voice}
            </span>
          )}
        </div>
        <button className="ghost" onClick={onVoltar}>
          voltar
        </button>
      </header>

      <main className="stream">
        {falas.length === 0 && (
          <div className="empty">
            <h1>Conversa ao vivo</h1>
            <p>
              Um modelo só ouve e responde, sem transcrever no meio. Ele enxerga sua memória e pode
              criar, corrigir e apagar o que você pedir.
            </p>
            <p className="dim">Aperte o microfone e fale. Pode interromper a EVE no meio.</p>
          </div>
        )}
        {falas.map((f, i) => (
          <div key={i} className={`turn ${f.de === "voce" ? "user" : ""}`}>
            <div className="bubble">{f.texto}</div>
          </div>
        ))}
        {acoes.map((a, i) => (
          <div key={`a${i}`} className="toolchip">
            <span className={a.ok === false ? "erro" : ""}>
              {a.nome}
              {a.ok === null ? " …" : a.ok ? " ✓" : ` — ${a.erro ?? "falhou"}`}
            </span>
          </div>
        ))}
        <div ref={fim} />
      </main>

      <div className="composer">
        {erro && (
          <div className="voice-error">
            {erro.texto}
            {erro.dica && <div className="dim">{erro.dica}</div>}
          </div>
        )}
        <div className="field">
          <button
            className={`mic ${ligada ? "on" : ""}`}
            onClick={alternar}
            disabled={abrindo}
            title={ligada ? "Encerrar" : "Começar a falar"}
          >
            {abrindo ? "…" : ligada ? "■" : "●"}
          </button>
          <textarea
            rows={1}
            value={rascunho}
            placeholder={ligada ? "fale, ou escreva aqui" : "aperte o microfone para começar"}
            onChange={(e) => setRascunho(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                enviar();
              }
            }}
          />
          <button className="send" disabled={!ligada || !rascunho.trim()} onClick={enviar}>
            ↑
          </button>
        </div>
      </div>
    </div>
  );
}

/** Junta o pedaço novo à última fala de quem está falando. */
function emendar(falas: Fala[], de: Fala["de"], texto: string): Fala[] {
  const ultima = falas[falas.length - 1];
  if (ultima && ultima.de === de) {
    return [...falas.slice(0, -1), { de, texto: `${ultima.texto}${texto}` }];
  }
  return [...falas, { de, texto }];
}
