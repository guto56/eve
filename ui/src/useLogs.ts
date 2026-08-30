import { useCallback, useEffect, useRef, useState } from "react";
import { EveSocket } from "./api";
import type { LiveEvent } from "./types";

const LIMITE = 600;
/** Um barramento movimentado enche a tela em segundos; guardamos o recente. */

let contador = 0;

/**
 * Fluxo de tudo que acontece no Core.
 *
 * Abre um socket próprio, assinando `*` — o da conversa filtra por tópico, e
 * aqui o ponto é justamente ver o que os outros filtram. Só conecta enquanto a
 * aba está aberta.
 */
export function useLogs(ativo: boolean) {
  const [eventos, setEventos] = useState<LiveEvent[]>([]);
  const [pausado, setPausado] = useState(false);
  const [conectado, setConectado] = useState(false);
  const socketRef = useRef<EveSocket | null>(null);
  const pausadoRef = useRef(false);

  useEffect(() => {
    pausadoRef.current = pausado;
  }, [pausado]);

  useEffect(() => {
    if (!ativo) return;
    const socket = new EveSocket("*");
    socketRef.current = socket;

    const off = socket.on((frame) => {
      if (frame.type === "connection") {
        setConectado(frame.kind === "open");
        return;
      }
      if (frame.type !== "event" || pausadoRef.current) return;
      const evento = frame.event as Omit<LiveEvent, "id">;
      setEventos((atual) => {
        const proximo = [...atual, { ...evento, id: ++contador }];
        return proximo.length > LIMITE ? proximo.slice(-LIMITE) : proximo;
      });
    });

    socket.connect();
    return () => {
      off();
      socket.close();
      socketRef.current = null;
      setConectado(false);
    };
  }, [ativo]);

  const limpar = useCallback(() => setEventos([]), []);

  return { eventos, pausado, setPausado, conectado, limpar };
}

/** Agrupa por prefixo para colorir e filtrar. */
export function categoria(tipo: string): string {
  const raiz = tipo.split(".")[0];
  if (["tool", "mcp"].includes(raiz)) return "tool";
  if (["message", "router", "client"].includes(raiz)) return "chat";
  if (["memory"].includes(raiz)) return "memory";
  if (["task", "proactive"].includes(raiz)) return "task";
  if (["voice"].includes(raiz)) return "voice";
  if (["file", "git", "app", "user"].includes(raiz)) return "watch";
  return "system";
}
