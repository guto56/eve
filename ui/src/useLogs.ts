import { useCallback, useEffect, useRef, useState } from "react";
import { EveSocket } from "./api";
import type { LiveEvent, LogEntry } from "./types";

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
    // Abrir a aba e ver tela vazia não informa nada. O barramento guarda um
    // histórico; pedimos o recente para a aba já nascer com contexto.
    const socket = new EveSocket("*", 200);
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

  // O barramento tem o que a EVE faz; o arquivo tem o resto — uvicorn, avisos
  // de biblioteca, tudo que nunca vira evento. Para a tela mostrar de fato
  // tudo, as duas fontes chegam juntas.
  useEffect(() => {
    if (!ativo) return;
    const fonte = new EventSource("/api/logs/stream?source=eve");
    fonte.onmessage = (e) => {
      if (pausadoRef.current) return;
      const linha = JSON.parse(e.data) as LogEntry;
      if (!linha.raw?.trim()) return;
      setEventos((atual) => {
        const proximo = [
          ...atual,
          {
            id: ++contador,
            ts: Date.now() / 1000,
            type: linha.event || `log.${linha.level}`,
            source: "daemon",
            payload: linha.event
              ? { nivel: linha.level, detalhe: linha.detail }
              : { linha: linha.detail || linha.raw },
          } satisfies LiveEvent,
        ];
        return proximo.length > LIMITE ? proximo.slice(-LIMITE) : proximo;
      });
    };
    return () => fonte.close();
  }, [ativo]);

  const limpar = useCallback(() => setEventos([]), []);

  return { eventos, pausado, setPausado, conectado, limpar };
}

/** Agrupa por prefixo para colorir e filtrar. */
export function categoria(tipo: string): string {
  // Um token por evento afoga o resto; merece filtro próprio.
  if (tipo === "message.delta") return "delta";
  const raiz = tipo.split(".")[0];
  if (["tool", "mcp"].includes(raiz)) return "tool";
  if (["message", "router", "client"].includes(raiz)) return "chat";
  if (["memory"].includes(raiz)) return "memory";
  if (["task", "proactive"].includes(raiz)) return "task";
  if (["voice"].includes(raiz)) return "voice";
  if (["file", "git", "app", "user"].includes(raiz)) return "watch";
  if (raiz === "log") return "daemon";
  return "system";
}
