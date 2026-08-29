# Fase 6 — Memória

Concluída em 2026-08-29.

## Como funciona

SQLite num arquivo só (`~/.eve/data/eve.db`), com FTS5 para busca textual e
sqlite-vec para semântica. Embeddings gerados localmente pelo `embeddinggemma`
via Ollama — nenhum texto de memória sai da máquina para virar vetor.

As quatro camadas da spec §17: `working` (expira em 6h), `episodic`,
`semantic`, `procedural`.

## Busca híbrida

FTS5 acha o que o usuário escreveu com as mesmas palavras; a busca vetorial
acha o que ele escreveu com outras. As duas listas são fundidas por RRF
(Reciprocal Rank Fusion), que combina ordenações sem exigir que os escores
sejam comparáveis entre si.

```
"do que você lembra sobre a stack do projeto"
  → o projeto EVE usa Python 3.13 com uv e FastAPI
```

Nenhuma palavra em comum além de "projeto".

## Três coisas que só a medição resolveu

**O EmbeddingGemma precisa de prefixo de tarefa.** Sem `task: search result |
query:` na consulta e `title: none | text:` no documento, a qualidade cai. A
margem entre o acerto e o segundo colocado passou de **+0,040 para +0,106**, e
a similaridade de uma consulta sem relação caiu de 0,345 para 0,249.

**Busca vetorial sem piso sempre devolve alguma coisa.** O vizinho mais próximo
de "receita de bolo de cenoura" no meio das suas memórias ainda é uma memória
sua — e entrar no contexto do modelo como se fosse relevante é pior do que não
achar nada. Com distância de cosseno e piso em 0,72, essa consulta passa a
devolver vazio.

**O limiar de deduplicação teve de ser conservador.** Medido nesta máquina:

| relação | distância |
|---|---|
| idêntica | 0,0000 |
| paráfrase | 0,0844 |
| mesma ideia, outras palavras | 0,2264 |
| relacionada mas diferente | 0,2362 |
| sem relação | 0,5443 |

As duas do meio são indistinguíveis. O limiar ficou em **0,15**: funde só o que
é quase certamente repetido. Fundir "marque meus encontros cedo" com "prefiro
almoçar às 13h" perderia informação de verdade.

## Caminho rápido também na memória

"Lembre que eu prefiro reuniões de manhã" levava **11,4 s** passando pelo
modelo. Com regra determinística e frase de confirmação pronta: **89 ms**.
Consultar leva 47 ms.

## Bugs encontrados rodando

1. **Fato guardado duas vezes.** O caminho rápido gravava o texto cru e a
   extração automática gravava a mesma coisa reescrita em terceira pessoa —
   perto demais para ser útil, longe demais para o deduplicador reconhecer.
   Agora, quando o usuário foi explícito, não há extração.
2. **Acentos sumiam.** As regras casam contra texto normalizado; o argumento
   voltava normalizado junto. Guardava "reunioes de manha".
3. **Colisão de nomes no doctor.** Já existia um `check_memory` (a RAM da
   máquina). Viraram `check_ram` e `check_memory_store`.
4. **Trocar o modelo de embedding quebrava tudo.** Dimensão diferente da do
   banco derrubava toda busca com erro cru de SQLite. Agora desliga a busca
   semântica, registra o motivo e mantém a textual.
5. **O modelo adotava as preferências do usuário.** Memória guardada em
   primeira pessoa voltava como "prefiro reuniões de manhã" dito pela EVE.
   Apresentar como fala relatada entre aspas resolveu sem depender de o modelo
   obedecer a uma instrução.

E um teste instável por construção: o embedder falso usava `hash()`, que é
aleatorizado por processo — a colisão entre duas frases sem relação mudava a
cada execução.

## Interfaces

```bash
eve memory list | search | add | forget | stats
```

```
GET/POST /api/memory · /api/memory/search · /api/memory/recent
```

Ferramentas: `memory.remember`, `memory.recall`, `memory.forget`,
`memory.list`. A rota MEMORY do router deixou de ser um beco sem saída.

## Testes

467 no total (35 novos).
