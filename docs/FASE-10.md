# Fase 10 — Web e navegador

Concluída em 2026-08-29. A rota `WEB` deixou de ser um beco sem saída.

## Pesquisa com fontes

Tavily, ~2 s por consulta, com resposta sintetizada e resultados pontuados.

```
$ eve chat "quais as notícias de tecnologia de hoje?"
→ web.search{"query": "notícias de tecnologia hoje"}
[resumo do que encontrou]
  https://g1.globo.com/tecnologia/noticia/2026/08/29/...
  https://pt.euronews.com/video/2026/08/29/...
```

**As fontes vêm do resultado real da busca, não do texto do modelo.** Endereço
escrito por um modelo pode não existir; o prompt manda explicitamente não
escrever URLs, e a interface mostra as que a pesquisa devolveu. A primeira
versão duplicava — o modelo listava e a interface listava de novo.

## Navegador de verdade

Playwright com Chromium, headless, como Tool (spec §15).

| ação | tempo |
|---|---|
| `browser.open` (primeira, sobe o Chromium) | 5860 ms |
| `browser.read` | 37 ms |
| `browser.links` | 17 ms |

O navegador **fica aberto entre chamadas**. Uma tarefa de várias etapas ("abra
o site, clique aqui, leia aquilo") ficaria inviável se cada passo subisse um
processo novo. Fecha sozinho depois de 5 minutos parado.

## Riscos, na medida

| ferramenta | risco | por quê |
|---|---|---|
| `web.search`, `web.extract` | SAFE | só leem |
| `browser.open`, `read`, `links`, `screenshot`, `state`, `close` | SAFE | navegação e leitura |
| `browser.click`, `browser.fill` | **CONFIRM** | clicar numa página pode enviar formulário ou comprar algo |

`browser.screenshot` captura **a página aberta pela EVE**, não a tela do
usuário — essa continua sendo `system.screenshot`, com confirmação e permissão
de Gravação de Tela.

## Ferramentas agora: 37

`app` · `browser` · `clipboard` · `eve` · `file` · `memory` · `system` · `url` ·
`web` — mais as que vierem de MCP.

## Testes

511 no total (6 novos). O Tavily é testado com transporte simulado (o que
importa é o parsing e o mapeamento de erro); o navegador foi exercitado ao
vivo contra example.com.
