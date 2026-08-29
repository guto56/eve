# Fase 2 — Tool Bus e Permission Engine

Concluída em 2026-08-29.

## O caminho de toda ação

```
modelo / CLI / interface
    ↓ call(nome, args)
validação do esquema (pydantic, extra="forbid")
    ↓
Permission Engine  →  negada  →  tool.denied + auditoria
    ↓ precisa confirmar?
ApprovalBroker  →  sem resposta no prazo  →  negada
    ↓ aprovada
execução com prazo
    ↓
tool.completed | tool.failed  +  auditoria
```

O handler de uma ferramenta é inalcançável sem passar por validação e decisão de
permissão. Não existe atalho: a CLI usa a mesma API HTTP que a interface web.

## Módulos

| Módulo | Responsabilidade |
|---|---|
| `eve.tools.spec` | `ToolSpec`, `RiskLevel`, `ToolResult`, redação de segredos |
| `eve.tools.registry` | registro e decorador `@tool` |
| `eve.permissions` | risco efetivo e decisão (spec §11) |
| `eve.tools.approvals` | confirmações pendentes, com prazo |
| `eve.tools.audit` | JSONL com rotação, uma linha por chamada |
| `eve.tools.bus` | orquestra tudo e emite os eventos |
| `eve.tools.builtin` | `system.info`, `system.time`, `eve.echo` |

## Decisões

**Sem resposta significa não.** Uma confirmação que expira é negada, nunca
executada. O prazo é configurável (`permissions.confirm_timeout`).

**Concessão não é permissão de uso.** Uma ferramenta PRIVILEGED com concessão
ainda pede confirmação a cada chamada. A concessão só a tira do estado "nem
pergunte".

**Bloqueado é bloqueado.** Nenhuma concessão contorna `BLOCKED` — há teste
específico para isso.

**Regra mais específica vence.** `file.read` bate `file.*`, que bate `*`.

**Segredos nunca saem.** Campos marcados como secretos chegam ao handler com o
valor real, mas aparecem como `***` em eventos e auditoria — inclusive quando a
chamada falha na validação.

**A CLI não tem porta dos fundos.** `eve tool call --yes` não envia flag de
bypass para a API: ele responde à confirmação real pelo mesmo endpoint que a
interface usaria.

## Bug encontrado pelos testes

`ToolRegistry` define `__len__`, então um registro **vazio é falsy**. O
decorador fazia `registry or default_registry()` e silenciosamente registrava
tudo no registro global — duas instâncias do Core na mesma sessão colidiam.
Agora é `registry if registry is not None else ...`.

## CLI adicionada

```
eve tool list | show | call | approvals | approve | deny | audit
eve permission list | set | unset | grant | revoke
```

`eve permission set` grava no `config.toml` e avisa o daemon para recarregar —
a política muda sem reiniciar o Core.

## Testes

151 no total (79 novos), 13 s. Inclui o fluxo completo de confirmação, concessão
e revogação contra um daemon real.
