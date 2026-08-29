import { useCallback, useEffect, useRef, useState } from "react";
import { api, EveSocket, type ChatFrame } from "./api";
import type { Approval, Routing, Status, Turn } from "./types";
import { VoiceClient, type VoiceEvent } from "./voice";

let seq = 0;
const nextId = () => `t${++seq}`;

export function useEve() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [voice, setVoice] = useState({
    active: false,
    listening: false,
    speaking: false,
    partial: "",
    error: "",
  });
  const socketRef = useRef<EveSocket | null>(null);
  const voiceRef = useRef<VoiceClient | null>(null);

  const patchLast = useCallback((patch: (turn: Turn) => Turn) => {
    setTurns((current) => {
      const last = current[current.length - 1];
      if (!last || last.role !== "assistant") return current;
      return [...current.slice(0, -1), patch(last)];
    });
  }, []);

  useEffect(() => {
    const socket = new EveSocket();
    socketRef.current = socket;

    const off = socket.on((frame: ChatFrame) => {
      if (frame.type === "connection") {
        setConnected(frame.kind === "open");
        return;
      }
      if (frame.type !== "chat") return;

      switch (frame.kind) {
        case "session":
          setSession(frame.session as string);
          break;
        case "task":
          patchLast((turn) => ({ ...turn, taskId: frame.id as string }));
          break;
        case "task_plan":
          patchLast((turn) => ({
            ...turn,
            plan: ((frame.task as { plan?: string[] })?.plan ?? []) as string[],
          }));
          break;
        case "routed":
          patchLast((turn) => ({ ...turn, routing: frame as unknown as Routing }));
          break;
        case "tool":
          patchLast((turn) => ({
            ...turn,
            tools: [
              ...turn.tools,
              {
                id: `${turn.id}-${turn.tools.length}`,
                name: frame.name as string,
                arguments: (frame.arguments ?? {}) as Record<string, unknown>,
              },
            ],
          }));
          break;
        case "tool_result":
          patchLast((turn) => {
            // As fontes vêm do resultado real da busca, nunca do texto do
            // modelo — endereço escrito por modelo pode não existir.
            const encontradas =
              frame.name === "web.search"
                ? ((frame.value as { sources?: string[] } | null)?.sources ?? [])
                : [];
            const sources = encontradas.length
              ? [...new Set([...(turn.sources ?? []), ...encontradas])]
              : turn.sources;
            const tools = [...turn.tools];
            for (let i = tools.length - 1; i >= 0; i--) {
              if (tools[i].name === frame.name && tools[i].ok === undefined) {
                tools[i] = {
                  ...tools[i],
                  ok: frame.ok as boolean,
                  error: frame.error as string | undefined,
                  errorKind: frame.error_kind as string | undefined,
                  durationMs: frame.duration_ms as number,
                };
                break;
              }
            }
            return { ...turn, tools, sources };
          });
          break;
        case "delta":
          patchLast((turn) => ({ ...turn, text: turn.text + (frame.text as string) }));
          break;
        case "error":
          patchLast((turn) => ({
            ...turn,
            error: (frame.error as string) ?? "algo deu errado",
            streaming: false,
          }));
          setBusy(false);
          break;
        case "done":
          patchLast((turn) => ({ ...turn, streaming: false }));
          setBusy(false);
          break;
      }
    });

    socket.connect();
    return () => {
      off();
      socket.close();
    };
  }, [patchLast]);

  // Confirmações e estado do sistema: consulta curta, só enquanto há algo
  // pendente ou a cada poucos segundos.
  useEffect(() => {
    let vivo = true;
    const tick = async () => {
      try {
        const [pendentes, estado] = await Promise.all([api.approvals(), api.status()]);
        if (!vivo) return;
        setApprovals(pendentes.pending);
        setStatus(estado);
      } catch {
        /* Core reiniciando; a próxima tentativa resolve */
      }
    };
    tick();
    const timer = setInterval(tick, busy ? 400 : 4000);
    return () => {
      vivo = false;
      clearInterval(timer);
    };
  }, [busy]);

  const send = useCallback(
    (text: string) => {
      const limpo = text.trim();
      if (!limpo || busy) return;
      setTurns((current) => [
        ...current,
        { id: nextId(), role: "user", text: limpo, tools: [] },
        { id: nextId(), role: "assistant", text: "", tools: [], streaming: true },
      ]);
      setBusy(true);
      if (!socketRef.current?.send(limpo, session)) {
        patchLast((turn) => ({ ...turn, error: "sem conexão com o Core", streaming: false }));
        setBusy(false);
      }
    },
    [busy, session, patchLast],
  );

  const decide = useCallback(async (id: string, approved: boolean) => {
    setApprovals((current) => current.filter((a) => a.id !== id));
    await api.decide(id, approved);
  }, []);

  const reset = useCallback(() => {
    setTurns([]);
    setSession(null);
  }, []);

  // ------------------------------------------------------------------ voz

  const onVoiceEvent = useCallback(
    (event: VoiceEvent) => {
      switch (event.kind) {
        case "partial":
          setVoice((v) => ({ ...v, partial: event.text }));
          break;
        case "final":
          // O que a EVE ouviu vira um turno igual ao que foi digitado.
          setVoice((v) => ({ ...v, partial: "" }));
          setTurns((current) => [
            ...current,
            { id: nextId(), role: "user", text: event.text, tools: [] },
            { id: nextId(), role: "assistant", text: "", tools: [], streaming: true },
          ]);
          break;
        case "tool":
          patchLast((turn) => ({
            ...turn,
            tools: [
              ...turn.tools,
              { id: `${turn.id}-${turn.tools.length}`, name: event.name, arguments: {}, ok: true },
            ],
          }));
          break;
        case "reply":
          patchLast((turn) => ({ ...turn, text: turn.text + event.text }));
          break;
        case "speaking":
          setVoice((v) => ({ ...v, speaking: event.on }));
          if (!event.on) patchLast((turn) => ({ ...turn, streaming: false }));
          break;
        case "listening":
          setVoice((v) => ({ ...v, listening: event.on }));
          break;
        case "interrupted":
          patchLast((turn) => ({ ...turn, streaming: false }));
          break;
        case "error":
          setVoice((v) => ({ ...v, error: event.error }));
          if (event.fatal) {
            voiceRef.current?.stop();
            setVoice((v) => ({ ...v, active: false }));
          }
          break;
        case "closed":
          setVoice({ active: false, listening: false, speaking: false, partial: "", error: "" });
          break;
      }
    },
    [patchLast],
  );

  const toggleVoice = useCallback(async () => {
    if (voiceRef.current?.active) {
      await voiceRef.current.stop();
      voiceRef.current = null;
      setVoice({ active: false, listening: false, speaking: false, partial: "", error: "" });
      return;
    }
    const client = new VoiceClient(onVoiceEvent);
    voiceRef.current = client;
    try {
      await client.start();
      setVoice((v) => ({ ...v, active: true, error: "" }));
    } catch (erro) {
      voiceRef.current = null;
      setVoice((v) => ({
        ...v,
        active: false,
        error: erro instanceof Error ? erro.message : "não consegui abrir o microfone",
      }));
    }
  }, [onVoiceEvent]);

  useEffect(() => () => void voiceRef.current?.stop(), []);

  return {
    turns,
    connected,
    busy,
    approvals,
    status,
    send,
    decide,
    reset,
    session,
    voice,
    toggleVoice,
  };
}
