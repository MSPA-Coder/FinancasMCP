# FinancasMCP — guia de trabalho

## Escopo e fontes de verdade

Servidor MCP de **somente leitura** para Controle Bancário e Controle de Renda
Variável. As regras pertencem às views `leitura` das fontes; não as replique.
Leia `README.md` primeiro e `docs/TESTES.md` para as diretrizes de teste.

Use Docker Compose e os comandos documentados. Não instale ferramentas no
host. Configuração e chaves vivem fora do Git em
`%USERPROFILE%\.financas-mcp\config.toml`; não as imprima, copie ou versione.

## Limites que não podem mudar por acidente

- Nenhuma ferramenta, consulta ou caminho de erro pode escrever nos bancos.
- O usuário MCP só lê as views `leitura`; tabelas, credenciais e auditoria não
  são uma API alternativa.
- Alterações nas views exigem compatibilidade com CB e CRV; a ordem de produção
  e a verificação posterior estão no README.

## Validação proporcional

| Mudança | Validação mínima antes de concluir |
| --- | --- |
| Documentação | Conferir comandos, caminhos e links alterados. |
| Código Python, consulta ou regra de leitura | `docker compose --profile quality run --build --rm --no-deps quality` e `pip-audit`, como no README. |
| Compose, Dockerfile ou dependências | `docker compose --profile quality config --quiet`, build e validação de qualidade. |
| Credenciais, túnel SSH, permissões ou views | Além da suíte, revisar o isolamento de leitura; em ambiente autorizado, rodar `tests/cliente_stdio.py` ou `python -m financas_mcp.verificar <ambiente>` sem registrar valores. |
| Mudança no CB ou CRV que afete `leitura` | Validar os três repositórios; após deploy autorizado das fontes, rodar `python -m financas_mcp.verificar producao`. |

Não faça deploy, gere credenciais nem altere produção sem instrução explícita.
Corrija testes que medem implementação em vez de mudar produção para passar.

