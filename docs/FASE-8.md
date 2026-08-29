# Fase 8 — Voz

Concluída em 2026-08-29.

## A cadeia

```
microfone (navegador, 16 kHz PCM)
   ↓ WebSocket /ws/voice
Deepgram nova-3  →  parcial · parcial · frase pronta
   ↓
motor de conversa (router → ferramenta ou modelo)
   ↓ resposta em streaming, cortada por frase
Cartesia sonic-3  →  PCM 24 kHz
   ↓ WebSocket
alto-falante
```

As credenciais ficam no daemon. O navegador nunca vê a chave do Deepgram nem a
do Cartesia — só áudio.

## Medido ponta a ponta

"Que horas são?" falado, contra o daemon real:

| marco | tempo |
|---|---|
| primeira parcial | 1467 ms *(durante a fala)* |
| transcrição final | 1931 ms |
| ferramenta executada | 1931 ms |
| **primeiro áudio de volta** | **2259 ms** |
| fim da fala | 3139 ms |

Entre a frase terminar e a EVE começar a falar: **328 ms**.

## Duas otimizações que valeram a diferença

**Socket de TTS persistente.** Abrir a conexão com o Cartesia custa ~900 ms —
quase toda a latência da primeira frase. Mantendo-a quente: **901 ms → 256 ms**
para o primeiro áudio.

**Aquecimento durante a fala do usuário.** A conexão é aberta assim que a
sessão de voz começa, enquanto a pessoa ainda está falando. Primeiro áudio de
volta: **3928 ms → 2259 ms**.

## Decisões

**A EVE fala por frase, não por resposta.** O texto é cortado em fronteiras de
frase (mínimo de 40 caracteres, para não soar picotado) e cada trecho vai
falando enquanto o resto ainda está sendo gerado.

**Interromper é imediato.** Se o usuário volta a falar enquanto a EVE fala, a
síntese é cancelada e o navegador corta os buffers já agendados — parar de
gerar não basta, o que já foi enviado ainda tocaria.

**Keepalive para a transcrição.** A Deepgram encerra a conexão depois de ~10 s
sem dados. Microfone mudo, pausa longa ou aba em segundo plano derrubavam a
sessão; um ping periódico resolve.

**Captura já em 16 kHz.** O `AudioContext` do navegador faz a reamostragem;
não há conversão manual nem biblioteca de áudio.

## Bug que o teste ponta a ponta revelou

Meu primeiro teste mandava a fala e parava. A Deepgram nunca fechava a frase e
morria por timeout. O erro era do teste, não do código: **um microfone real
continua mandando silêncio, e é o silêncio que dispara o endpointing.** Com
1 s de silêncio no fim, a transcrição fecha corretamente.

O mesmo cenário existia em produção (mic mudo), e foi o que motivou o
keepalive.

## Verificado e não verificado

Verificado contra as APIs reais: síntese, transcrição, a cadeia inteira pelo
`/ws/voice`, e o diagnóstico `eve voice test` (a EVE fala e transcreve o
próprio áudio).

**Não verificado:** a captura de microfone no navegador. O código está escrito
e checado por tipos, mas não foi exercitado com um microfone de verdade — o
navegador automatizado não concede acesso ao microfone.

## Interfaces

```bash
eve voice say "Olá, eu sou a EVE"    # fala e toca
eve voice test                        # diagnóstico das duas pontas
```

Na interface web, o botão de microfone no campo de mensagem. A barra mostra o
que está sendo ouvido em tempo real e avisa quando a EVE está falando.

## Testes

482 no total (11 novos).
