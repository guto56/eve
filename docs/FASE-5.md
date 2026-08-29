# Fase 5 — Router e conversa

Concluída em 2026-08-29.

## Três camadas, da mais barata para a mais cara

```
mensagem
  ↓  regra determinística      < 1 ms
caminho rápido? → executa a ferramenta e responde, sem modelo nenhum
  ↓  não
classificador local (qwen3.5:2b)   ~500 ms
  ↓
ferramentas filtradas pela rota
  ↓
modelo com streaming → Tool Bus → modelo → resposta
```

## Resultado medido

29 frases rotuladas à mão, cobrindo as cinco rotas:

| métrica | valor |
|---|---|
| acurácia | **29/29** |
| latência mediana | **0,1 ms** |
| p90 | 594 ms |
| decididas por regra | 19/29 |
| **caminho rápido (zero LLM)** | **10/29** |

"Que horas são" responde em **1 ms**, ponta a ponta, sem tocar em modelo nenhum.

## Filtragem de ferramentas

O problema herdado da Fase 4: oferecer as 23 ferramentas custava ~2.000 tokens
e segundos de latência. A rota define os namespaces plausíveis e um escore
lexical ordena o resto.

| | ferramentas | tokens | latência | escolha |
|---|---|---|---|---|
| todas | 23 | 2061 | 5588 ms | `file.mkdir` |
| filtradas | 8 | **1056** | **2352 ms** | `file.mkdir` |

−49% de tokens, −58% de latência, mesma decisão correta.

## Decisões

**Regras são conservadoras.** Na dúvida elas não disparam. Um falso positivo
aqui não é uma resposta ruim: é uma ação errada no computador do usuário.
"Abra a pasta Documentos" e "abra o arquivo relatório.pdf" caem no modelo de
propósito, porque `app.open` seria a ferramenta errada.

**Veto de várias etapas.** "Pesquise três fones, compare preços e me recomende
um" começa com "pesquise", mas não é uma busca. Um padrão de veto faz a regra
desistir quando há marcas de tarefa composta.

**Resposta do caminho rápido é determinística.** Chamar um modelo só para dizer
"abri o Safari" jogaria fora o ganho de latência. Cada ferramenta comum tem um
formatador; o modelo `fast` só entra quando a forma do resultado é desconhecida.

**O padrão seguro é conversar.** Rótulo irreconhecível, modelo fora do ar,
qualquer dúvida — a entrada vira CHAT. Nunca uma ação.

**Ferramenta com confirmação continua pedindo confirmação.** O caminho rápido
não é atalho de permissão: "feche o Finder" passa pelo mesmo fluxo de aprovação
da Fase 2 e, sem resposta, é negado.

## Quatro bugs encontrados rodando de verdade

1. **`decision.as_dict()` tinha uma chave `source`** que colidia com o `source`
   do evento no barramento — `TypeError` a cada mensagem. O campo virou
   `decided_by`, que é o que ele realmente significa.
2. **Exceção dentro do gerador SSE truncava o stream em silêncio.** O cliente
   ficava esperando para sempre. Agora todo fim é anunciado, inclusive o
   inesperado.
3. **Ollama e OpenAI discordam sobre `arguments`.** A OpenAI quer string JSON;
   o Ollama quer objeto. Só falha na *segunda* rodada de uma conversa com
   ferramenta, quando a chamada anterior volta no histórico.
4. **"Quanto de RAM tem esse Mac" era classificado como MEMORY.** O modelo
   confundia memória RAM com a memória da EVE. Regra nova e desambiguação
   explícita no prompt do classificador.

E um detalhe de concorrência: no WebSocket, o "done" da conversa podia chegar
antes dos eventos de ferramenta, porque são tarefas diferentes escrevendo no
mesmo socket. A conversa agora emite a sequência inteira, em ordem, no próprio
socket.

## Interfaces

```bash
eve chat                      # conversa contínua
eve chat "que horas são"      # uma mensagem
eve chat -v "..."             # mostra rota, latência e ferramentas
```

```
POST /api/chat      SSE com a sequência da conversa
POST /api/route     só a decisão de roteamento, sem executar nada
GET  /api/sessions  sessões abertas
WS   /ws            {"op": "chat", "message": "..."}
```

## Testes

432 no total (122 novos), 54 s.
