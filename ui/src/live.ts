/**
 * Conversa ao vivo no navegador.
 *
 * Mesmo áudio da página de voz — 16 kHz PCM16 sobe, 24 kHz desce — mas do
 * outro lado há um modelo só, que ouve e fala. O que muda aqui é o
 * vocabulário do socket e o fato de a fala poder ser cortada no meio quando
 * o usuário volta a falar.
 */

import { PROCESSOR } from "./voice";

export type LiveEvent =
  | {
      kind: "ready";
      engine: string;
      model: string;
      voice: string;
      tools: string[];
      incremental: boolean;
      outputRate: number;
    }
  | { kind: "partial"; text: string }
  | { kind: "final"; text: string }
  | { kind: "listening"; on: boolean }
  | { kind: "speaking"; on: boolean }
  | { kind: "reply"; text: string }
  | { kind: "tool"; name: string; arguments: Record<string, unknown> }
  | { kind: "tool_result"; name: string; ok: boolean; error: string | null }
  | { kind: "approval"; event: Record<string, unknown> }
  | { kind: "interrupted" }
  | { kind: "turn" }
  | { kind: "error"; error: string; fatal?: boolean; hint?: string }
  | { kind: "closed" };

export type Motor = "auto" | "openrouter" | "gemini";

export class LiveClient {
  private socket: WebSocket | null = null;
  private capture: AudioContext | null = null;
  private playback: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private proximoInicio = 0;
  private tocando: AudioBufferSourceNode[] = [];
  private outputRate = 24000;
  private analiseSaida: AnalyserNode | null = null;
  private analiseEntrada: AnalyserNode | null = null;
  private amostra = new Uint8Array(64);

  private onEvent: (e: LiveEvent) => void;
  private motor: Motor;

  constructor(onEvent: (e: LiveEvent) => void, motor: Motor = "auto") {
    this.onEvent = onEvent;
    this.motor = motor;
  }

  get active() {
    return this.socket !== null;
  }

  async start() {
    if (this.socket) return;

    const protocolo = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocolo}//${location.host}/ws/live?motor=${this.motor}`);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onmessage = (e) => this.receber(e);
    socket.onclose = () => {
      this.onEvent({ kind: "closed" });
      this.limpar();
    };
    socket.onerror = () => this.onEvent({ kind: "error", error: "a conexão caiu" });

    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve();
      setTimeout(() => reject(new Error("tempo esgotado")), 8000);
    });

    // O microfone só é pedido depois que o servidor aceitou: sem chave, a
    // página não tem por que acender a luz do microfone do usuário.
    //
    // E negá-lo não encerra a sessão: dá para conversar escrevendo, e derrubar
    // tudo porque o microfone falhou seria tirar o que ainda funcionava.
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    } catch {
      this.onEvent({
        kind: "error",
        error: "sem acesso ao microfone — dá para conversar escrevendo",
      });
      return;
    }

    const capture = new AudioContext({ sampleRate: 16000 });
    this.capture = capture;
    const blob = new Blob([PROCESSOR], { type: "application/javascript" });
    await capture.audioWorklet.addModule(URL.createObjectURL(blob));

    const node = new AudioWorkletNode(capture, "captura");
    node.port.onmessage = (e) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(e.data as ArrayBuffer);
    };
    const fonte = capture.createMediaStreamSource(this.stream);
    fonte.connect(node);
    // Uma derivação só para medir: é o que deixa a esfera reagir à sua voz.
    const analise = capture.createAnalyser();
    analise.fftSize = 128;
    fonte.connect(analise);
    this.analiseEntrada = analise;
    node.connect(capture.destination);
    this.node = node;
  }

  enviarTexto(texto: string) {
    this.socket?.send(JSON.stringify({ op: "texto", text: texto }));
  }

  private receber(evento: MessageEvent) {
    if (evento.data instanceof ArrayBuffer) {
      this.tocar(evento.data);
      return;
    }
    const frame = JSON.parse(evento.data as string) as LiveEvent;
    if (frame.kind === "ready") this.outputRate = frame.outputRate || 24000;
    if (frame.kind === "interrupted") this.silenciar();
    this.onEvent(frame);
  }

  private tocar(buffer: ArrayBuffer) {
    if (!this.playback) this.playback = new AudioContext({ sampleRate: this.outputRate });
    const contexto = this.playback;

    const pcm = new Int16Array(buffer);
    if (!pcm.length) return;
    const audio = contexto.createBuffer(1, pcm.length, this.outputRate);
    const canal = audio.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) canal[i] = pcm[i] / 0x8000;

    if (!this.analiseSaida) {
      const analise = contexto.createAnalyser();
      analise.fftSize = 128;
      analise.connect(contexto.destination);
      this.analiseSaida = analise;
    }
    const fonte = contexto.createBufferSource();
    fonte.buffer = audio;
    fonte.connect(this.analiseSaida);

    // Emenda no fim do pedaço anterior; se já passou, toca agora.
    const inicio = Math.max(this.proximoInicio, contexto.currentTime);
    fonte.start(inicio);
    this.proximoInicio = inicio + audio.duration;

    this.tocando.push(fonte);
    fonte.onended = () => {
      this.tocando = this.tocando.filter((f) => f !== fonte);
    };
  }

  /** Corta a fala em andamento — é o que faz a interrupção parecer imediata. */
  private silenciar() {
    this.tocando.forEach((f) => {
      try {
        f.stop();
      } catch {
        /* já terminou */
      }
    });
    this.tocando = [];
    this.proximoInicio = 0;
  }

  /** Quanto som está passando agora, de 0 a 1. */
  nivel(saida: boolean): number {
    const analise = saida ? this.analiseSaida : this.analiseEntrada;
    if (!analise) return 0;
    analise.getByteTimeDomainData(this.amostra);
    let soma = 0;
    for (let i = 0; i < this.amostra.length; i++) {
      const v = (this.amostra[i] - 128) / 128;
      soma += v * v;
    }
    // Raiz quadrática média, esticada: a fala normal fica longe do topo da
    // escala, e sem esticar a esfera mal se mexeria.
    return Math.min(1, Math.sqrt(soma / this.amostra.length) * 3.2);
  }

  async stop() {
    this.silenciar();
    this.socket?.close();
    this.limpar();
  }

  private limpar() {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.capture?.close().catch(() => {});
    this.playback?.close().catch(() => {});
    this.node = null;
    this.stream = null;
    this.capture = null;
    this.playback = null;
    this.socket = null;
    this.analiseSaida = null;
    this.analiseEntrada = null;
    this.proximoInicio = 0;
  }
}
