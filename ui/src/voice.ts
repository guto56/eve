/**
 * Voz no navegador.
 *
 * Captura o microfone já em 16 kHz (o AudioContext faz a reamostragem), manda
 * quadros PCM de 16 bits pelo WebSocket e toca de volta o áudio que a EVE
 * responde, agendando os pedaços em sequência para não haver estalo entre um
 * e outro.
 *
 * A chave do Deepgram e a do Cartesia ficam no daemon; o navegador só vê áudio.
 */

export type VoiceEvent =
  | { kind: "ready"; inputRate: number; outputRate: number }
  | { kind: "listening"; on: boolean }
  | { kind: "partial"; text: string }
  | { kind: "final"; text: string }
  | { kind: "reply"; text: string }
  | { kind: "tool"; name: string }
  | { kind: "speaking"; on: boolean }
  | { kind: "interrupted" }
  | { kind: "error"; error: string; fatal?: boolean }
  | { kind: "closed" };

// O worklet roda na thread de áudio: só converte float32 em int16 e entrega.
export const PROCESSOR = `
class Captura extends AudioWorkletProcessor {
  process(inputs) {
    const canal = inputs[0]?.[0];
    if (!canal) return true;
    const pcm = new Int16Array(canal.length);
    for (let i = 0; i < canal.length; i++) {
      const v = Math.max(-1, Math.min(1, canal[i]));
      pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}
registerProcessor('captura', Captura);
`;

export class VoiceClient {
  private socket: WebSocket | null = null;
  private capture: AudioContext | null = null;
  private playback: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private proximoInicio = 0;
  private tocando: AudioBufferSourceNode[] = [];
  private outputRate = 24000;

  private onEvent: (event: VoiceEvent) => void;

  constructor(onEvent: (event: VoiceEvent) => void) {
    this.onEvent = onEvent;
  }

  get active() {
    return this.socket !== null;
  }

  async start() {
    if (this.socket) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });

    const protocolo = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocolo}//${location.host}/ws/voice`);
    socket.binaryType = "arraybuffer";
    this.socket = socket;

    socket.onmessage = (event) => this.receber(event);
    socket.onclose = () => {
      this.onEvent({ kind: "closed" });
      this.limpar();
    };
    socket.onerror = () => this.onEvent({ kind: "error", error: "conexão de voz caiu" });

    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve();
      setTimeout(() => reject(new Error("tempo esgotado")), 8000);
    });

    // Pedir 16 kHz direto evita reamostrar na mão.
    const capture = new AudioContext({ sampleRate: 16000 });
    this.capture = capture;
    const blob = new Blob([PROCESSOR], { type: "application/javascript" });
    await capture.audioWorklet.addModule(URL.createObjectURL(blob));

    const node = new AudioWorkletNode(capture, "captura");
    node.port.onmessage = (event) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(event.data as ArrayBuffer);
    };
    capture.createMediaStreamSource(this.stream).connect(node);
    // O worklet não produz saída; ligar ao destino apenas o mantém rodando.
    node.connect(capture.destination);
    this.node = node;
  }

  private receber(event: MessageEvent) {
    if (event.data instanceof ArrayBuffer) {
      this.tocar(event.data);
      return;
    }
    const frame = JSON.parse(event.data as string);
    switch (frame.type) {
      case "ready":
        this.outputRate = frame.output_sample_rate;
        this.onEvent({
          kind: "ready",
          inputRate: frame.input_sample_rate,
          outputRate: frame.output_sample_rate,
        });
        break;
      case "listening":
        this.onEvent({ kind: "listening", on: frame.on });
        break;
      case "partial":
        this.onEvent({ kind: "partial", text: frame.text });
        break;
      case "final":
        this.onEvent({ kind: "final", text: frame.text });
        break;
      case "reply":
        this.onEvent({ kind: "reply", text: frame.text });
        break;
      case "tool":
        this.onEvent({ kind: "tool", name: frame.name });
        break;
      case "speaking":
        this.onEvent({ kind: "speaking", on: frame.on });
        break;
      case "interrupted":
        this.silenciar();
        this.onEvent({ kind: "interrupted" });
        break;
      case "error":
        this.onEvent({ kind: "error", error: frame.error, fatal: frame.fatal });
        break;
    }
  }

  private tocar(buffer: ArrayBuffer) {
    if (!this.playback) this.playback = new AudioContext({ sampleRate: this.outputRate });
    const contexto = this.playback;

    const pcm = new Int16Array(buffer);
    const audio = contexto.createBuffer(1, pcm.length, this.outputRate);
    const canal = audio.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) canal[i] = pcm[i] / 0x8000;

    const fonte = contexto.createBufferSource();
    fonte.buffer = audio;
    fonte.connect(contexto.destination);

    // Emenda no fim do pedaço anterior; se já passou, toca agora.
    const agora = contexto.currentTime;
    const inicio = Math.max(this.proximoInicio, agora);
    fonte.start(inicio);
    this.proximoInicio = inicio + audio.duration;

    this.tocando.push(fonte);
    fonte.onended = () => {
      this.tocando = this.tocando.filter((f) => f !== fonte);
    };
  }

  /** Corta a fala em andamento — é o que faz a interrupção parecer imediata. */
  private silenciar() {
    this.tocando.forEach((fonte) => {
      try {
        fonte.stop();
      } catch {
        /* já terminou */
      }
    });
    this.tocando = [];
    this.proximoInicio = 0;
  }

  interrupt() {
    this.silenciar();
    this.socket?.send(JSON.stringify({ op: "interrupt" }));
  }

  async stop() {
    this.silenciar();
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ op: "bye" }));
    }
    this.socket?.close();
    this.limpar();
  }

  private limpar() {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.capture?.close().catch(() => {});
    this.node = null;
    this.stream = null;
    this.capture = null;
    this.socket = null;
  }
}
