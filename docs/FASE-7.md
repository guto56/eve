# Fase 7 — Interface web

Concluída em 2026-08-29.

## O que é

React 19 + Vite 8 + TypeScript, servida pelo próprio daemon. O build vai para
`src/eve/web/static/` e é versionado de propósito: quem instala a EVE não
deveria precisar de Node.

Uma conexão WebSocket carrega tudo — mensagem, tokens em streaming, chamadas de
ferramenta, resultado, confirmações. Reconecta sozinha com recuo progressivo:
o Core pode reiniciar sem que a página precise ser recarregada.

## Decisões

**Não parece um painel de administração.** A tela é a conversa. Estado do
sistema, memória e ferramentas ficam num painel lateral que só aparece quando
pedido.

**A confirmação acontece onde a ação foi pedida.** Quando o Tool Bus segura uma
ferramenta CONFIRM, o cartão de autorização aparece no fluxo da conversa, com
os argumentos exatos — não num diálogo separado, descolado do contexto.

**A sequência da conversa chega em ordem pelo próprio socket.** Os mesmos
passos também vão ao barramento para quem estiver observando, mas o cliente
que pediu recebe uma ordem garantida (decidido na Fase 5).

**Detalhes ficam escondidos por padrão.** O botão "detalhes" revela rota,
tempo de decisão e quantas ferramentas foram oferecidas ao modelo. Útil para
entender a EVE, ruído para conversar com ela.

**Tema segue o sistema.** Claro e escuro, sem botão.

## Verificado no navegador

O ciclo completo, ponta a ponta:

1. "escreva o texto oi no arquivo ~/Documents/eve-ui-teste.txt"
2. router → modelo → `file.write` (CONFIRM) → cartão de autorização na tela
3. Autorizar → ferramenta executa → "O texto foi salvo."
4. Arquivo conferido no disco.

## Bug que a interface revelou

A extração automática estava guardando lixo: *"O usuário solicitou que um texto
específico fosse salvo em um arquivo de sistema"*. Pedir uma ação não é contar
algo sobre si. Agora não há extração em turnos de rota COMMAND nem MEMORY, e o
que a EVE decide guardar sozinha exige importância ≥ 0,5 — quando o usuário
manda guardar, a intenção basta; quando a EVE decide, o custo do erro é dela.

## Estrutura

```
ui/src/
├── api.ts          cliente REST + WebSocket com reconexão
├── useEve.ts       estado da conversa, confirmações e status
├── components.tsx  turno, ferramenta, confirmação, painel
├── App.tsx         casca
└── index.css       sistema de design
```

## Testes

471 no total (4 novos). A interface foi verificada no navegador de verdade,
não por teste de componente — o que importava era o fluxo inteiro.
