"""O que cada sistema libera para leitura e como ler os dados sem errar.

`TABELAS` é a fonte única da lista de tabelas: o script que cria o usuário
de leitura (`python -m financas_mcp.usuario_leitura`) dá SELECT exatamente
nelas. Ficam de fora usuários, senhas, sessões, permissões e auditoria.
"""

from __future__ import annotations

TABELAS: dict[str, tuple[str, ...]] = {
    "cb": (
        "account_owner", "financial_institution", "financial_account",
        "cash_flow_category", "cash_flow_entry", "bank_operation",
        "account_month_close", "monthly_budget",
        "management_tag", "cash_flow_entry_tag", "management_project", "cash_flow_entry_project",
        "bank_statement_import", "bank_statement_line", "entry_attachment",
    ),
    "crv": (
        "brokers", "tickers", "portfolios", "portfolio_tickers",
        "positions", "position_movements", "position_ledger_archive",
        "transactions", "dividends", "quotes", "quote_history",
        "option_contracts", "option_expirations", "option_positions",
        "option_position_movements", "option_quotes",
    ),
    "nw": ("foto_do_patrimonio", "valor_da_foto", "taxa_de_cambio"),
}

NOMES = {
    "cb": "ControleBancario (contas, lançamentos, cartões, orçamento)",
    "crv": "ControleRendaVariavel (carteira de ações e opções, operações, proventos)",
    "nw": "NetWorth (fotos diárias do patrimônio consolidado de CB e CRV)",
}

REGRAS: dict[str, str] = {
    "cb": """\
ControleBancario -- regras para ler certo:
- cash_flow_entry: valores SEMPRE positivos (entry_amount, realized_amount); o sinal vem de
  entry_type ('receita' soma, 'despesa' subtrai).
- status: 'a_vencer' (futuro), 'vencidos' (em aberto e já vencido), 'realizado' (pago/recebido).
  Realizado tem realized_date e realized_amount (use estes); em aberto use due_date e entry_amount.
- Categorias (cash_flow_category.kind): 'gerencial' = receita/despesa de verdade;
  'transferencia' = movimento entre contas próprias (NÃO é gasto nem renda, tem as duas pontas
  ligadas por source_entry_id); 'movimentacao' = aporte/resgate de investimento (sai do caixa,
  mas não é despesa). Análise de gastos/renda: só kind='gerencial'.
- Saldo de uma conta = financial_account.initial_balance + soma assinada de TODOS os lançamentos
  dela (inclusive os anteriores a initial_balance_date). Saldo realizado até hoje: só
  status='realizado' com realized_date <= hoje, somando realized_amount.
- Moeda é da conta (financial_account.currency: BRL ou USD). Nunca some moedas diferentes.
- Cartão de crédito: financial_account.account_kind='cartao_credito'. Compras são despesas na
  conta do cartão; saldo negativo = fatura em aberto. Pagar a fatura é transferência de uma conta
  comum para o cartão. card_closing_day/card_due_day = fechamento/vencimento.
- Fatura projetada: lançamentos cuja bank_operation.operation_key começa com
  'cartao-estimativa:' (gasto estimado pela mediana) ou 'cartao-pagamento:' (pagamento projetado).
  São estimativas, não fatos.
- bank_operation agrupa parcelas (operation_type='installment', installments/current_installment),
  recorrências (is_recurring) e transferências. Recorrências são projetadas até o horizonte de
  Parâmetros (6 meses); parcelas vêm todas.
- bank_statement_line: linhas importadas de extratos/faturas. Nas faturas de cartão há
  purchase_date, installment_current/total, card_holder e bank_category (categoria do banco).
  matched_entry_id liga a linha ao lançamento conciliado; import_id liga ao bank_statement_import
  (cada importação de cartão é uma fatura).
- monthly_budget: orçamento (meta) por categoria, ano e mês, em moeda base (BRL).
- account_month_close: meses fechados por conta (não mudam mais sem reabertura).
- Lançamentos com realized_date/due_date antes de 2026-01-01 não existem: o sistema começa em
  31/12/2025 (saldos iniciais nessa data).""",
    "crv": """\
ControleRendaVariavel -- regras para ler certo:
- positions: posições abertas (quantity, average_cost, side 'BUY' comprada / 'SELL' vendida).
  quote_multiplier ajusta preço de cotação; portfolio_id -> portfolios (simulated=true é carteira
  simulada: exclua de análises reais).
- tickers: symbol, market ('B3','NYSE','NASDAQ'), currency. Enums guardam o NOME em maiúsculas. quotes: última cotação por ticker
  (last_price, previous_close, observed_at). quote_history: série histórica.
- transactions: operações; status 'OPEN'/'CLOSED'; result = resultado realizado ao fechar
  (closed_on). result_mode indica como o resultado é medido.
- position_movements: extrato de cada posição (OPEN, INCREASE, DECREASE, ADJUSTMENT) com
  result do movimento e custo médio resultante.
- dividends: proventos recebidos; kind 'DIVIDENDO', 'JCP' ou 'ALUGUEL'; amount na moeda do ativo;
  payment_date.
- option_*: opções (contratos, vencimentos, posições, movimentos, cotações).
- Valores monetários na moeda do ticker/carteira; não some BRL com USD.""",
    "nw": """\
NetWorth -- regras para ler certo:
- foto_do_patrimonio: uma foto por dia (data), só fotos completas.
- valor_da_foto: totais de cada foto por fonte ('CB' ou 'CRV'), papel ('caixa' para contas do CB,
  'investimento' para a carteira do CRV), instituição e moeda. O patrimônio de um dia é a soma dos
  valores daquela foto, convertendo USD para BRL.
- taxa_de_cambio: quanto vale 1 unidade da moeda em BRL naquele dia (fonte yahoo). Para converter
  a foto de uma data use a taxa da mesma data ou a última anterior.
- Os cartões de crédito entram no caixa do CB com saldo negativo.""",
}

INSTRUCOES = """\
Dados financeiros pessoais do usuário, em três sistemas, SOMENTE LEITURA:
cb = ControleBancario, crv = ControleRendaVariavel, nw = NetWorth.

Como trabalhar:
1. Prefira as ferramentas prontas (cb_*, crv_*, nw_*): as somas já saem certas.
2. Para o que elas não cobrem, chame `esquema(sistema)` uma vez para ver tabelas, colunas e as
   regras de leitura, e depois `sql(sistema, consulta)` com um SELECT.
3. Respeite as regras: no CB, gasto/renda só com categorias kind='gerencial'; transferências
   entre contas próprias não são gasto; valores são positivos e o tipo dá o sinal; não some
   moedas diferentes.
4. Datas no formato AAAA-MM-DD. Valores em texto decimal exato (ex.: "1234.56").
Nada aqui altera dados: para mudar algo, o usuário usa as telas dos sistemas."""
