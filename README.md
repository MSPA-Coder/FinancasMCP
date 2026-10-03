# FinancasMCP

Servidor MCP **somente leitura** que dá ao Claude acesso aos dados do
ControleBancario (`cb`) e do ControleRendaVariavel (`crv`).

## Como funciona

- Roda no Windows, iniciado pelo Claude Desktop (transporte stdio).
- Em produção, abre um túnel SSH pela Tailscale até o VPS e fala com os bancos
  que escutam só em `127.0.0.1` de lá (portas 5202 e 5302). Nada novo fica
  exposto na internet.
- Cada banco tem um usuário `mcp_leitura` com `SELECT` apenas nas **views do
  esquema `leitura`**, que o CB e o CRV publicam pelas próprias migrações
  (`financas_mcp/dominio.py`, `VIEWS`), e `default_transaction_read_only`.
  Nenhuma tabela dos sistemas é legível: usuários, senhas, sessões, permissões e
  auditoria não têm view. Toda consulta roda numa transação `READ ONLY`, com
  tempo máximo e limite de linhas.
- As regras do domínio (status efetivo do lançamento, saldo, o preço que vale, o
  sinal da posição vendida) moram nas views, uma vez, e não são reescritas aqui.
  O PostgreSQL recusa remover ou trocar o tipo de uma coluna que uma view lê, então
  uma mudança de schema que quebraria este servidor reprova na suíte do sistema
  que mudou, e não em produção.
- As credenciais ficam fora do repositório, em `%USERPROFILE%\.financas-mcp\config.toml`.

## Ferramentas

| Ferramenta | O que devolve |
|---|---|
| `sistemas` | ambiente, data de hoje e sistemas disponíveis |
| `esquema(sistema)` | views, descrições, colunas e regras de leitura do domínio |
| `sql(sistema, consulta)` | qualquer `SELECT` sobre as views, até 2.000 linhas e 15 s |
| `cb_contas` | saldo realizado hoje, vencido em aberto e saldo com todo o previsto |
| `cb_resumo_mensal` | receitas e despesas por mês e categoria/conta |
| `cb_lancamentos` | lançamentos com filtros (texto, período, conta, categoria, status) |
| `cb_categorias` | categorias com natureza e uso |
| `cb_orcamento` | metas do mês contra o realizado |
| `cb_faturas` | faturas importadas e futuro dos cartões (parcelas, estimativa, pagamentos) |
| `cb_projecao_de_caixa` | saldo de cada conta no fim de cada mês |
| `crv_carteira` | posições com custo, cotação e resultado aberto |
| `crv_proventos` | dividendos, JCP e aluguel por mês, ativo ou tipo |
| `crv_operacoes` | operações de ações e de opções e resultado realizado |

## Instalação

As versões ficam travadas no `uv.lock`, como nos outros repositórios. O uv
entra uma vez pelo pip e cria o `.venv` no mesmo lugar de antes, então a
configuração do Claude Desktop abaixo não muda:

```powershell
py -3.14 -m pip install --user "uv==0.12.10"
py -3.14 -m uv sync --locked --extra dev

# usuário de leitura + config (senhas geradas e gravadas, nunca impressas)
.\.venv\Scripts\python.exe -m financas_mcp.usuario_leitura local
.\.venv\Scripts\python.exe -m financas_mcp.usuario_leitura producao `
    --host 100.94.82.91 --chave C:\caminho\da\chave.key --chave-do-host "ssh-ed25519 AAAA..."
```

`ambiente` no `config.toml` escolhe local ou produção; `FINANCAS_MCP_AMBIENTE`
sobrepõe. Quando uma migração do CB ou do CRV acrescentar uma view ao esquema
`leitura`, acrescente o nome dela a `VIEWS` e rode o `usuario_leitura` de novo
(ele troca a senha e reaplica as permissões). O script confere antes de mexer em
qualquer permissão: se o esquema ou alguma view esperada não existir no banco
(o CB ou o CRV implantado é anterior ao contrato), ele para com uma mensagem.

**Ordem ao adotar o esquema `leitura`:** primeiro implantar o CB e o CRV com o
esquema; depois atualizar este repositório (`git pull`) e rodar o
`usuario_leitura producao`; por fim reiniciar o Claude Desktop. Antes do segundo
passo o servidor antigo continua lendo as tabelas.

No Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "financas": {
      "command": "C:\\Dev\\VSCodeProjects\\FinancasMCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "financas_mcp.server"],
      "cwd": "C:\\Dev\\VSCodeProjects\\FinancasMCP"
    }
  }
}
```

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # contra os bancos locais
.\.venv\Scripts\python.exe -m ruff check .
$env:FINANCAS_MCP_AMBIENTE = "producao"; .\.venv\Scripts\python.exe tests\cliente_stdio.py
```

Os testes confirmam que o próprio banco recusa escrita e esconde toda tabela dos
sistemas (só as views de `leitura` são legíveis) mesmo com a barreira do servidor
contornada. O `cliente_stdio.py`
sobe o servidor como o Claude Desktop e imprime só contagens, nunca valores.

Na CI, o job "Testes com banco" sobe o CB e o CRV a partir do `main` de cada
um, pelos próprios Compose, cria dados mínimos (`tests/ci/`) e roda a suíte
inteira, em cada PR e toda segunda-feira. O saldo do cartão Carbon é conferido
dos dois lados: a semente o calcula pela função das telas do CB, e o teste,
pelo SQL do MCP.

## Depois de implantar o CB ou o CRV

```powershell
.\.venv\Scripts\python.exe -m financas_mcp.verificar producao
```

Chama cada ferramenta uma vez, só leitura, e sai com código 1 se alguma
quebrar ou se uma view esperada em `dominio.VIEWS` sumir do esquema `leitura`.
Em 24/09/2026 a `crv_carteira` estava quebrada em produção porque uma revisão do
CRV removeu uma coluna que ela lia, e nada avisou. Com as views, essa quebra
reprova na suíte do sistema que mudou. A CI daqui ainda roda a suíte com banco
contra o `main` do CB e do CRV em cada PR e toda segunda-feira, mas a CI testa o
`main`, e a produção pode estar em outra versão. Como o VPS não alcança o
Windows, este passo continua manual: rode logo depois do `deploy.sh` desses dois.
Imprime só nomes e contagens, nunca valores.
