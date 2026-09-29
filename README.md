# FinancasMCP

Servidor MCP **somente leitura** que dá ao Claude acesso aos dados do
ControleBancario (`cb`) e do ControleRendaVariavel (`crv`).

## Como funciona

- Roda no Windows, iniciado pelo Claude Desktop (transporte stdio).
- Em produção, abre um túnel SSH pela Tailscale até o VPS e fala com os bancos
  que escutam só em `127.0.0.1` de lá (portas 5202, 5302 e 5702). Nada novo fica
  exposto na internet.
- Cada banco tem um usuário `mcp_leitura` com `SELECT` apenas nas tabelas
  financeiras (`financas_mcp/dominio.py`, `TABELAS`) e
  `default_transaction_read_only`. Usuários, senhas, sessões, permissões e
  auditoria ficam de fora. Toda consulta roda numa transação `READ ONLY`, com
  tempo máximo e limite de linhas.
- As credenciais ficam fora do repositório, em `%USERPROFILE%\.financas-mcp\config.toml`.

## Ferramentas

| Ferramenta | O que devolve |
|---|---|
| `sistemas` | ambiente, data de hoje e sistemas disponíveis |
| `esquema(sistema)` | tabelas, colunas e regras de leitura do domínio |
| `sql(sistema, consulta)` | qualquer `SELECT`, até 2.000 linhas e 15 s |
| `cb_contas` | saldo realizado hoje, vencido em aberto e saldo com todo o previsto |
| `cb_resumo_mensal` | receitas e despesas por mês e categoria/conta |
| `cb_lancamentos` | lançamentos com filtros (texto, período, conta, categoria, status) |
| `cb_categorias` | categorias com natureza e uso |
| `cb_orcamento` | metas do mês contra o realizado |
| `cb_faturas` | faturas importadas e futuro dos cartões (parcelas, estimativa, pagamentos) |
| `cb_projecao_de_caixa` | saldo de cada conta no fim de cada mês |
| `crv_carteira` | posições com custo, cotação e resultado aberto |
| `crv_proventos` | dividendos, JCP e aluguel por mês, ativo ou tipo |
| `crv_operacoes` | operações e resultado realizado |

## Instalação

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# usuário de leitura + config (senhas geradas e gravadas, nunca impressas)
.\.venv\Scripts\python.exe -m financas_mcp.usuario_leitura local
.\.venv\Scripts\python.exe -m financas_mcp.usuario_leitura producao `
    --host 100.94.82.91 --chave C:\caminho\da\chave.key --chave-do-host "ssh-ed25519 AAAA..."
```

`ambiente` no `config.toml` escolhe local ou produção; `FINANCAS_MCP_AMBIENTE`
sobrepõe. Para liberar uma tabela nova, acrescente-a a `TABELAS` e rode o
`usuario_leitura` de novo (ele troca a senha e reaplica as permissões).

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

Os testes confirmam que o próprio banco recusa escrita e esconde as tabelas
sensíveis mesmo com a barreira do servidor contornada. O `cliente_stdio.py`
sobe o servidor como o Claude Desktop e imprime só contagens, nunca valores.

## Depois de implantar o CB ou o CRV

```powershell
.\.venv\Scripts\python.exe -m financas_mcp.verificar producao
```

Chama cada ferramenta uma vez, só leitura, e sai com código 1 se alguma
quebrar ou se uma tabela liberada em `dominio.TABELAS` sumir. As consultas
daqui dependem do schema de três sistemas que mudam sem este repositório
saber: em 24/09/2026 a `crv_carteira` estava quebrada em produção porque uma
revisão do CRV removeu uma coluna, e nada avisou. Este repositório não tem CI
e o VPS não alcança o Windows, então o passo é manual: rode logo depois do
`deploy.sh` desses três. Imprime só nomes e contagens, nunca valores.
