/**
 * A página de conversa ao vivo.
 *
 * Só a esfera e um botão. Quem está falando não lê a tela, e a esfera já diz
 * em que pé está a conversa pelo próprio movimento — pôr texto ao lado seria
 * dizer duas vezes a mesma coisa, uma delas do jeito errado.
 *
 * O que sobrou de texto é o que não dá para mostrar de outro jeito: quando
 * alguma coisa falha, e o que fazer a respeito.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Esfera, type Estado } from "./Esfera";
import { LiveClient, type LiveEvent, type Motor } from "./live";

export function LivePage({ motor = "auto" }: { motor?: Motor }) {
  const [ligada, setLigada] = useState(false);
  const [abrindo, setAbrindo] = useState(false);
  const [ouvindo, setOuvindo] = useState(false);
  const [falando, setFalando] = useState(false);
  const [pensando, setPensando] = useState(false);
  const [nivel, setNivel] = useState(0);
  const [erro, setErro] = useState<{ texto: string; dica?: string } | null>(null);
  const cliente = useRef<LiveClient | null>(null);

  const estado: Estado = !ligada
    ? "desligada"
    : falando
      ? "falando"
      : pensando
        ? "pensando"
        : ouvindo
          ? "ouvindo"
          : "parada";

  const receber = useCallback((e: LiveEvent) => {
    switch (e.kind) {
      case "ready":
        setLigada(true);
        setAbrindo(false);
        break;
      case "listening":
        setOuvindo(e.on);
        break;
      case "speaking":
        setFalando(e.on);
        break;
      // Ela parou de falar e a resposta ainda não veio: é aqui que a esfera
      // se fecha e acelera.
      case "final":
        setPensando(true);
        break;
      case "reply":
      case "turn":
        setPensando(false);
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
        setOuvindo(false);
        setFalando(false);
        break;
    }
  }, []);

  // O nível vem do áudio, não de um temporizador: quando ela fala, a esfera
  // pulsa com a voz dela; quando você fala, com a sua.
  useEffect(() => {
    if (!ligada) {
      setNivel(0);
      return;
    }
    let id = 0;
    const ler = () => {
      setNivel(cliente.current?.nivel(falando) ?? 0);
      id = requestAnimationFrame(ler);
    };
    id = requestAnimationFrame(ler);
    return () => cancelAnimationFrame(id);
  }, [ligada, falando]);

  useEffect(() => () => void cliente.current?.stop(), []);

  const alternar = async () => {
    if (cliente.current?.active) {
      await cliente.current.stop();
      cliente.current = null;
      setLigada(false);
      setOuvindo(false);
      setFalando(false);
      return;
    }
    setErro(null);
    setAbrindo(true);
    const c = new LiveClient(receber, motor);
    cliente.current = c;
    try {
      await c.start();
    } catch (exc) {
      setErro({ texto: exc instanceof Error ? exc.message : "não consegui abrir a conversa" });
      setAbrindo(false);
      cliente.current = null;
    }
  };

  return (
    <main className="palco">
      <Esfera estado={estado} nivel={nivel} />

      <button
        className={`interruptor ${ligada ? "on" : ""}`}
        onClick={alternar}
        disabled={abrindo}
        aria-label={ligada ? "Encerrar a conversa" : "Começar a falar"}
      >
        <span className="marca" />
        <span className="dizeres">
          {abrindo ? "abrindo" : ligada ? "encerrar" : "falar com a EVE"}
        </span>
      </button>

      {erro && (
        <div className="voice-error">
          {erro.texto}
          {erro.dica && <div className="dim">{erro.dica}</div>}
        </div>
      )}
    </main>
  );
}
