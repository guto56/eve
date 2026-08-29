# Fase 4 — Credenciais e provedores de IA

Concluída em 2026-08-29.

## Benchmark que decidiu o modelo local

13 frases rotuladas à mão, prompt few-shot, temperatura 0, nesta máquina:

| modelo | acurácia | mediana | TTFT | tool calling | RAM |
|---|---|---|---|---|---|
| qwen3.5:0.8b | 11/13 | 247 ms | 34 ms | ✓ | 1,0 GB |
| **qwen3.5:2b** | **13/13** | **510 ms** | 58 ms | ✓ | 2,7 GB |
| qwen3.5:4b | 13/13 | 1372 ms | 151 ms | ✓ | 3,4 GB |

O 0.8b errou "o que eu te disse ontem sobre o projeto" (respondeu `LEMBRE`, um
rótulo que nem existe) e "analise meu projeto e descubra por que o build falha"
(`COMMAND` em vez de `TASK`). O 4b acerta tudo mas custa 2,7× o tempo do 2b sem
ganhar nada. **qwen3.5:2b é o padrão**, com o 0.8b disponível no papel `fast`.

Com prompt ingênuo (sem exemplos), 0.8b e 2b classificavam "abra o Safari" como
`CHAT`. O prompt vale mais que o tamanho do modelo nessa tarefa.

## Papéis, não implementações

| papel | modelo | quando |
|---|---|---|
| `fast` | qwen3.5:0.8b | resposta imediata, classificação trivial |
| `local` | qwen3.5:2b | conversa, intenção, roteamento |
| `external` | google/gemini-3.1-flash-lite | pesquisa, tarefas do dia a dia |
| `heavy` | anthropic/claude-sonnet-5 | raciocínio longo, análise de código |

O resto da EVE pede um papel; trocar Ollama por MLX ou OpenRouter por outro
provedor não toca em nada acima da camada `eve.ai`.

## Decisões

**Segredos só no Keychain.** O que fica em disco é a *lista de nomes* — o
Keychain não permite enumerar, e guardar só os nomes deixa a interface mostrar
o que falta sem tocar em valor nenhum. `describe()` devolve `sk-o…af59`, nunca
a chave.

**`__` no fio.** Nomes de ferramenta da EVE usam ponto (`app.open`), que a
especificação de function calling da OpenAI não aceita. Na ida viram
`app__open`; na volta, ponto de novo. `ToolRegistry.register` recusa nome que
contenha `__`, para a conversão ser sempre reversível.

**A EVE funciona sem credencial externa.** Sem `OPENROUTER_API_KEY` o papel
`external` responde 503 com a razão, e local segue funcionando.

**Testes nunca tocam o Keychain real.** `EVE_SECRETS_BACKEND=memory` troca o
backend e desliga o fallback de ambiente. O backend em memória é indexado por
`EVE_HOME`, então persiste dentro de um teste e é isolado entre testes.

## O que o teste revelou

Duas coisas que só apareceram rodando de verdade:

1. **Custo das ferramentas no prompt.** Oferecer as 23 ferramentas leva o
   prompt de ~30 para 1.943 tokens e a resposta de 510 ms para 5,2 s. A Fase 5
   precisa filtrar ferramentas por intenção antes de chamar o modelo — é
   exatamente o que a spec §35 pede.
2. **Argumentos de tool call chegam fatiados no streaming.** `{"pa` num chunk,
   `th": "/tmp/a.txt"}` no seguinte. Há acumulador por índice e teste
   específico para isso.

## Verificado ao vivo

```
✓ ollama (6 ms) — v0.33.2, 3 modelo(s)
✓ openrouter (368 ms) — credencial válida; modelo padrão google/gemini-3.1-flash-lite
```

Os quatro papéis responderam, com streaming, e tanto o modelo local quanto o
externo emitiram tool calls corretas (`app.activate{"name":"Safari"}` e
`clipboard.write{"text":"eve funcionando"}`).

## Testes

310 no total (97 novos), 50 s. Inclui provedor local contra o Ollama real,
provedor externo com transporte simulado para o parsing de SSE, e uma chamada
ao vivo ao OpenRouter que é pulada quando não há credencial.
