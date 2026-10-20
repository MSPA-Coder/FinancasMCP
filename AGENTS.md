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
| Código Python, consulta ou regra de leitura | Testes focados no que mudou; o `quality` completo e o `pip-audit` rodam na CI do PR, e localmente (como no README) só para reproduzir uma falha dela. |
| Compose, Dockerfile ou dependências | `docker compose --profile quality config --quiet` e build; a validação de qualidade completa roda na CI do PR. |
| Credenciais, túnel SSH, permissões ou views | Além da suíte, revisar o isolamento de leitura; em ambiente autorizado, rodar `tests/cliente_stdio.py` ou `python -m financas_mcp.verificar <ambiente>` sem registrar valores. |
| Mudança no CB ou CRV que afete `leitura` | Validar os três repositórios; após deploy autorizado das fontes, rodar `python -m financas_mcp.verificar producao`. |

Não faça deploy, gere credenciais nem altere produção sem instrução explícita.

O `main` é protegido: toda mudança entra por PR com squash merge, histórico
linear e os checks obrigatórios verdes. A branch não precisa estar atualizada
em relação ao `main`; não faça merge do `main` nela só para o PR entrar. O
auto-merge está ligado: depois de `gh pr create`, rode
`gh pr merge <n> --auto --squash`, e o PR entra sozinho quando os checks
passarem. Nunca `--admin`, nunca push forçado, nunca rebase em branch
publicada.
Corrija testes que medem implementação em vez de mudar produção para passar.

