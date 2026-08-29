# Fase 11 — Agentes

Concluída em 2026-08-29.

## Plano inspecionável, passos reais

```
$ eve chat "pesquise três modelos de Kindle, compare os preços e me recomende um"

  1. Pesquisar três modelos atuais de Kindle
  2. Levantar os preços de cada modelo
  3. Comparar as especificações técnicas
  4. Recomendar o melhor custo-benefício

→ web.search{"query": "modelos atuais de Kindle e preços Brasil"}
[comparação com preços reais]
```

O plano é para o usuário ver o que vem; os passos são o que aconteceu de
verdade. Mantê-los separados evita a tentação de fingir progresso — marcar
"passo 2 concluído" porque o modelo disse que sim, e não porque algo aconteceu.

## A tarefa sobrevive ao cliente

A tarefa roda numa corrotina própria, não no gerador da conversa. Fechar o
terminal ou a aba interrompe o acompanhamento, não o trabalho:

```
$ eve chat "pesquise três modelos..." | head -4     # fecho no meio
$ eve task list
8b02bdba  concluída  pesquise três modelos de Kindle...   1 passo   9s
```

## Cinco problemas que só apareceram rodando de verdade

**1. Ordenar por nome descartava a relevância.** A seleção rankeava as
ferramentas e depois ordenava alfabeticamente — o modelo via `browser.*` antes
de `web.search` e ia na primeira plausível. Um pedido de pesquisa virava sete
tentativas de preencher formulário no navegador. Preservar a ordem do ranking
levou a mesma tarefa de **mais de 4 minutos de falhas para 3 segundos com a
resposta certa**.

**2. Esperar 120 s por quem não está lá.** Uma ferramenta CONFIRM aguardava o
prazo cheio mesmo sem nenhuma interface aberta. Num agente rodando sozinho,
dois minutos por ferramenta arruínam a tarefa. Agora o prazo cai para 5 s
quando não há observador — "sem resposta significa não" continua valendo.

**3. O modelo ignora avisos em texto.** Ele repetiu a mesma chamada inválida
sete vezes, com o aviso "não repita" em cada resposta. A recusa virou
estrutural: a partir da terceira, a chamada nem chega à ferramenta.

**4. Falhar sem fim.** Três rodadas seguidas sem nenhum passo bem-sucedido
encerram a tarefa com "não consegui avançar" — melhor que inventar.

**5. Abrir páginas sem ler.** Numa execução o agente abriu oito páginas, não
leu nenhuma e respondeu com preços "estimados" de conhecimento próprio. Parecia
pesquisa e não era. O prompt agora exige ler o que abre e distinguir o que veio
de fonte do que é estimativa.

## Uma coisa que parecia bug e não era

`browser.fill` executou sem eu ver quem autorizou — o Tool Bus segurou a
chamada por 86 s e depois ela rodou. Era o usuário: o cartão de confirmação
apareceu na interface web e ele clicou. O sistema de permissão funcionou
inclusive atravessando superfícies — o agente pediu, a interface perguntou, a
pessoa respondeu.

O episódio rendeu uma melhoria real de observabilidade: `duration_ms` incluía
o tempo parado esperando autorização, fazendo uma ferramenta instantânea
parecer lentíssima. Agora `waited_ms` é contabilizado à parte.

## Efeito colateral na memória

Uma tarefa de pesquisa sobre fones gerou **oito memórias inventadas** sobre as
preferências do usuário — extraídas das respostas da própria EVE. Uma delas
depois contaminou a resposta de uma pergunta sem relação nenhuma, sobre
celulares.

Dois cortes estruturais:

- **Só se extrai memória de conversa.** Pedir uma ação, uma pesquisa ou uma
  tarefa não é contar algo sobre si.
- **O extrator lê só o que o usuário disse.** Incluir as respostas da EVE fazia
  as conclusões dela virarem fatos sobre o usuário. A instrução no prompt não
  bastava; a informação não pode nem chegar lá.

Mais um teto de 3 memórias por conversa: oito de uma troca não é aprendizado,
é ruído.

## Interfaces

```bash
eve task list | show | cancel
```

```
GET /api/tasks · /api/tasks/{id} · POST /api/tasks/{id}/cancel
```

Na interface web, o plano aparece como lista numerada antes dos passos.

## Testes

532 no total (21 novos).
