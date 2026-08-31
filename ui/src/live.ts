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

export type Motor = "auto" | "nativo" | "openrouter" | "gemini";

export class LiveClient {
  private socket: WebSocket | null = null;
  private capture: AudioContext | null = null;
  private playback: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private proximoInicio = 0;
  private tocando: AudioBufferSourceNode[] = [];
  private outputRate = 24000;
  private nativo = false;
  private falandoAgora = false;
  private reconhecimento: any = null;
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
    // No motor nativo quem ouve e fala é o navegador: nada de microfone bruto
    // subindo pelo socket.
    if (this.nativo) {
      this.ouvirNativo();
      return;
    }

    // Sem microfone não há conversa ao vivo: a página é só voz. Encerra a
    // sessão e diz o que fazer, em vez de deixar a esfera acesa esperando uma
    // fala que nunca vai chegar.
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    } catch {
      this.onEvent({
        kind: "error",
        fatal: true,
        error: "sem acesso ao microfone",
        hint: "autorize o microfone para este site e tente de novo",
      });
      await this.stop();
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
    if (frame.kind === "speaking") this.falandoAgora = frame.on;
    if (frame.kind === "ready") {
      this.outputRate = frame.outputRate || 24000;
      this.nativo = frame.engine === "nativo";
    }
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

  // ------------------------------------------------------------- nativo

  /** Ouve pelo próprio navegador: sem chave, sem áudio subindo. */
  private ouvirNativo() {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      this.onEvent({
        kind: "error",
        fatal: true,
        error: "este navegador não sabe ouvir",
        hint: "use o Chrome ou o Safari, ou troque o motor para openrouter",
      });
      return;
    }
    const r = new SR();
    r.lang = "pt-BR";
    r.continuous = true;
    r.interimResults = true;
    // Pede o reconhecimento no próprio aparelho quando houver; sem ele, o
    // navegador usa o serviço dele.
    try {
      r.processLocally = true;
    } catch {
      /* navegador antigo */
    }

    r.onresult = (e: any) => {
      let parcial = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) {
          this.onEvent({ kind: "final", text: t.trim() });
          this.socket?.send(JSON.stringify({ op: "texto", text: t.trim() }));
        } else {
          parcial += t;
        }
      }
      if (parcial) {
        // Quem percebe que o usuário voltou a falar é o navegador, não o
        // servidor: sem este aviso a EVE seguiria falando por cima.
        if (this.falandoAgora) this.pedirSilencio();
        this.onEvent({ kind: "partial", text: parcial });
      }
    };
    // O reconhecimento para sozinho depois de um tempo calado; religar mantém
    // a conversa aberta enquanto o usuário não encerrar.
    r.onend = () => {
      if (this.reconhecimento === r && this.socket) {
        try {
          r.start();
        } catch {
          /* já estava rodando */
        }
      }
    };
    r.onerror = (e: any) => {
      if (e.error === "not-allowed") {
        this.onEvent({
          kind: "error",
          fatal: true,
          error: "sem acesso ao microfone",
          hint: "autorize o microfone para este site e tente de novo",
        });
        void this.stop();
      }
    };

    this.reconhecimento = r;
    r.start();
    this.onEvent({ kind: "listening", on: true });
  }

  private pedirSilencio() {
    this.falandoAgora = false;
    this.silenciar();
    this.socket?.send(JSON.stringify({ op: "calar" }));
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
    if (this.reconhecimento) {
      const r = this.reconhecimento;
      this.reconhecimento = null;
      try {
        r.stop();
      } catch {
        /* já parado */
      }
    }
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
