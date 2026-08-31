/**
 * A esfera de partículas.
 *
 * Ela não é enfeite: é o único jeito de saber, sem ler nada, se a EVE está
 * ouvindo, pensando ou falando. Por isso cada estado tem um movimento próprio,
 * e a amplitude vem do áudio de verdade — quando ela fala, a esfera pulsa com
 * a voz, não com um temporizador.
 *
 * Canvas 2D em vez de WebGL: são ~2600 pontos, cabe folgado, e não traz
 * dependência nenhuma para o build.
 */

import { useEffect, useRef } from "react";

export type Estado = "desligada" | "parada" | "ouvindo" | "pensando" | "falando";

const PONTOS = 2600;
const RAIO_BASE = 0.78;

type Ponto = { x: number; y: number; z: number };

/** Espiral de Fibonacci: distribui pontos numa esfera sem acumular nos polos. */
function semear(n: number): Ponto[] {
  const pontos: Ponto[] = [];
  const passo = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const raio = Math.sqrt(Math.max(0, 1 - y * y));
    const teta = passo * i;
    pontos.push({ x: Math.cos(teta) * raio, y, z: Math.sin(teta) * raio });
  }
  return pontos;
}

/** Ruído barato e determinístico: senos somados bastam para a superfície viver. */
function ondular(p: Ponto, t: number, forca: number): number {
  return (
    forca *
    (Math.sin(p.x * 3.1 + t * 1.7) * 0.34 +
      Math.sin(p.y * 4.3 - t * 1.1) * 0.28 +
      Math.sin(p.z * 2.7 + t * 2.3) * 0.22)
  );
}

const FEITIO: Record<Estado, { giro: number; forca: number; brilho: number; escala: number }> = {
  // Desligada quase não se mexe: parada tem de parecer parada, não travada.
  desligada: { giro: 0.05, forca: 0.014, brilho: 0.66, escala: 0.94 },
  parada: { giro: 0.11, forca: 0.03, brilho: 0.85, escala: 1 },
  // Ouvindo, ela se abre e respira devagar — é a postura de quem espera.
  ouvindo: { giro: 0.16, forca: 0.055, brilho: 1, escala: 1.05 },
  // Pensando, fecha e acelera: a energia vai para dentro.
  pensando: { giro: 0.55, forca: 0.045, brilho: 0.94, escala: 0.93 },
  // Falando, a onda sai do centro para fora, no ritmo da voz.
  falando: { giro: 0.2, forca: 0.13, brilho: 1.15, escala: 1.08 },
};

export function Esfera({ estado, nivel = 0 }: { estado: Estado; nivel?: number }) {
  const tela = useRef<HTMLCanvasElement>(null);
  const alvo = useRef({ estado, nivel });
  alvo.current = { estado, nivel };

  useEffect(() => {
    const canvas = tela.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const pontos = semear(PONTOS);
    let quadro = 0;
    let t = 0;
    let anterior = performance.now();
    // Os parâmetros são perseguidos, não trocados: mudar de estado no meio de
    // um giro daria um solavanco.
    const atual = { ...FEITIO[estado], pulso: 0 };

    const desenhar = (agora: number) => {
      const dt = Math.min(0.05, (agora - anterior) / 1000);
      anterior = agora;
      t += dt;

      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const largura = canvas.clientWidth;
      const altura = canvas.clientHeight;
      if (canvas.width !== largura * dpr || canvas.height !== altura * dpr) {
        canvas.width = largura * dpr;
        canvas.height = altura * dpr;
      }

      const meta = FEITIO[alvo.current.estado];
      const k = 1 - Math.exp(-dt * 3.2);
      atual.giro += (meta.giro - atual.giro) * k;
      atual.forca += (meta.forca - atual.forca) * k;
      atual.brilho += (meta.brilho - atual.brilho) * k;
      atual.escala += (meta.escala - atual.escala) * k;
      atual.pulso += (alvo.current.nivel - atual.pulso) * (1 - Math.exp(-dt * 12));

      const cx = (largura * dpr) / 2;
      const cy = (altura * dpr) / 2;
      const raioTela = Math.min(cx, cy) * RAIO_BASE;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      quadro += atual.giro * dt;

      const cos = Math.cos(quadro);
      const sen = Math.sin(quadro);
      const inclina = Math.sin(t * 0.23) * 0.16;
      const cosI = Math.cos(inclina);
      const senI = Math.sin(inclina);

      // A voz empurra a superfície para fora; o estado define o quanto.
      const empurrao = 1 + atual.pulso * (alvo.current.estado === "falando" ? 0.16 : 0.05);
      const escala = atual.escala * empurrao;
      const forca = atual.forca * (1 + atual.pulso * 1.6);

      for (const p of pontos) {
        // Gira em Y, depois inclina em X: dá o balanço das referências.
        const x1 = p.x * cos - p.z * sen;
        const z1 = p.x * sen + p.z * cos;
        const y1 = p.y * cosI - z1 * senI;
        const z2 = p.y * senI + z1 * cosI;

        const r = escala * (1 + ondular(p, t, forca));
        const px = cx + x1 * r * raioTela;
        const py = cy + y1 * r * raioTela;

        // Profundidade: o que está atrás fica menor e mais apagado. É o que
        // faz uma nuvem de pontos parecer uma esfera e não um disco.
        const profundidade = (z2 + 1) / 2;
        const tamanho = (0.6 + profundidade * 1.6) * dpr;
        const alfa = Math.min(1, (0.22 + profundidade * 0.78) * atual.brilho);

        ctx.globalAlpha = alfa;
        ctx.fillStyle = "#fff";
        ctx.fillRect(px, py, tamanho, tamanho);
      }
      ctx.globalAlpha = 1;
      id = requestAnimationFrame(desenhar);
    };

    let id = requestAnimationFrame(desenhar);
    return () => cancelAnimationFrame(id);
    // Sem dependências: o laço lê o estado por `alvo`, e recriá-lo a cada
    // mudança reiniciaria o giro do zero.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <canvas ref={tela} className={`esfera ${estado}`} aria-hidden="true" />;
}
