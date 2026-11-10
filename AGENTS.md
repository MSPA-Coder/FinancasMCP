# FinancasMCP — guia de trabalho

<!-- comum: início. Cópia de _manutencao/compartilhados/AGENTS-comum.md; mude lá e rode sincronizar.py --aplicar. -->
## Regras comuns aos repositórios

**O que espera o "sim" do mantenedor:** deploy ou publicação, qualquer ação no
VPS, e apagar dados, volumes ou backups (`docker compose down -v` inclusive).
Commit e push não esperam. Na dúvida sobre algo irreversível, pergunte; no
resto, decida e diga o que decidiu.

**Ciclo.** Altere, rode a verificação completa deste repositório (seção
"Validação"), commite direto no `main` quando o bloco de trabalho estiver
pronto e dê `git push`: sem PR, sem push forçado, sem rebase do `main`
publicado. A CI roda depois, só como aviso, mas vermelho no `main` se
investiga. O Dependabot chega por PR, com merge manual. Ao terminar, diga o que
rodou, o resultado e o que deixou de validar.

**Dados e segredos.** Os bancos desta máquina são de teste: não provam nada
sobre valores reais e não pedem backup. Segredos ficam fora do Git (`.secrets/`
ou configuração do usuário) e não aparecem em código, imagem, log, diff,
documento nem relatório.

**Dependências.** Alargue o teto e preserve o piso já verificado: elevar o piso
afirma uma incompatibilidade, e isso exige prova. Vulnerabilidade se corrige
atualizando; exceção só explícita, com motivo e data. Enquanto o Dependabot não
aceitar Python 3.15, ele não propõe atualização de `uv` e o grafo do GitHub fica
parado no lock de 13/10/2026: confira alerta pela versão travada no repositório
e atualize à mão (`uv lock --upgrade-package <pacote>`, onde há `uv.lock`)
quando o `pip-audit` ou o Trivy apontarem.

**Testes.** `docs/TESTES.md`, onde existe, diz o que testar e como. Diante de
vermelho, decida de quem é o defeito; nunca escreva código para um teste
passar. Não rode duas verificações do mesmo repositório ao mesmo tempo: a
segunda recria o ambiente de teste e derruba a primeira.

**Cópias.** Este bloco, `docs/TESTES.md` e os arquivos listados em
`_manutencao/compartilhados/sincronizar.py` são cópias: mude a canônica lá e
rode `python compartilhados/sincronizar.py --aplicar` no `_manutencao`.

**Este arquivo.** Guarde aqui só o que o código não diz: limites, decisões com
o motivo, armadilhas e comandos. Cada regra mora num lugar só: o que este bloco
ou `docs/` já dizem não se repete. História fica no `git log`; decisão com
alternativas vira ADR, e ADR superado ganha a linha "Substituído por"; detalhe
de implementação fica no código. Para acrescentar uma regra, retire ou funda
outra: `sincronizar.py` reprova este arquivo acima de 900 palavras fora deste
bloco.
<!-- comum: fim -->

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

## Validação

| Mudança | Validação mínima antes de concluir |
| --- | --- |
| Documentação | Conferir comandos, caminhos e links alterados; `python ../_manutencao/compartilhados/sincronizar.py`. |
| Código Python, consulta ou regra de leitura | `.\scripts\quality.ps1` (build, suíte e `pip-audit`; passos em `scripts/quality.passos`). O Compose sobe o PostgreSQL descartável de provisionamento. |
| Compose, Dockerfile ou dependências | `docker compose --profile quality config --quiet`, build e validação de qualidade. |
| Credenciais, túnel SSH, permissões ou views | Além da suíte, revisar o isolamento de leitura; em ambiente autorizado, rodar `tests/cliente_stdio.py` ou `python -m financas_mcp.verificar <ambiente>` sem registrar valores. |
| Mudança no CB ou CRV que afete `leitura` | Validar os três repositórios; após deploy autorizado das fontes, rodar `python -m financas_mcp.verificar producao`. |

Em qualquer mudança de código, a linha de código Python da tabela roda completa
antes do commit. Gerar credenciais também espera o "sim" do mantenedor.

