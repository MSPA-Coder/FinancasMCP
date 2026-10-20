"""O que cada sistema libera para leitura e como ler os dados sem errar.

O servidor não lê as tabelas dos sistemas: lê as views do esquema `leitura`, que
o CB e o CRV publicam pelas próprias migrações e que carregam as regras do
domínio (status efetivo, saldo, preço que vale, sinal da posição vendida). Assim
uma mudança de schema que quebraria este servidor reprova na suíte de quem
mudou, e uma regra que muda lá muda aqui sem reescrever SQL.

`VIEWS` é a fonte única da lista: o script que cria o usuário de leitura
(`python -m financas_mcp.usuario_leitura`) dá SELECT em todo o esquema `leitura`
e confere que estas views existem. Ficam de fora usuários, senhas, sessões,
permissões e auditoria, que não têm view.
"""

from __future__ import annotations

ESQUEMA = "leitura"

VIEWS: dict[str, tuple[str, ...]] = {
    "cb": (
        "lancamento", "conta", "categoria", "orcamento_mensal", "operacao",
        "extrato_importacao", "extrato_linha", "fechamento_mes",
        "lancamento_etiqueta", "lancamento_projeto",
    ),
    "crv": (
        "posicao", "operacao", "provento", "cotacao", "cotacao_historico", "opcao", "cotacao_opcao",
        "movimento_posicao", "movimento_opcao", "posicao_historica",
        "ativo", "carteira", "corretora", "carteira_ativo",
    ),
}

NOMES = {
    "cb": "ControleBancario (contas, lançamentos, cartões, orçamento)",
    "crv": "ControleRendaVariavel (carteira de ações e opções, operações, proventos)",
}

REGRAS: dict[str, str] = {
    "cb": """\
ControleBancario -- só existem as views do esquema `leitura` (o caminho de busca já aponta para
ele: `select * from lancamento` e `select * from leitura.lancamento` são a mesma coisa). Cada view
traz a descrição dela; as regras abaixo são as que atravessam várias.
- leitura.lancamento: valores SEMPRE positivos (valor, valor_previsto); o sinal vem de `tipo`
  ('receita' soma, 'despesa' subtrai) ou da coluna `sinal` (+1/-1). `status` é o EFETIVO:
  'realizado', 'vencidos' (em aberto e já vencido) ou 'a_vencer'; `status_gravado` é o que está
  guardado e pode estar desatualizado. `data` e `valor` já são os que valem: realização e valor
  realizado nos realizados, vencimento e valor previsto nos em aberto.
- Categorias (`natureza`): 'gerencial' = receita/despesa de verdade; 'transferencia' = movimento
  entre contas próprias (NÃO é gasto nem renda); 'movimentacao' = aporte/resgate de investimento
  (sai do caixa, mas não é despesa). Análise de gastos/renda: só natureza = 'gerencial'.
- leitura.conta: saldo_realizado_hoje = saldo inicial + realizados até hoje; saldo_com_todo_previsto
  inclui tudo o que está previsto; vencido_em_aberto é a soma assinada do que venceu e não foi
  pago. A data de hoje é `leitura.hoje()` (fuso de São Paulo). Moeda é da conta: BRL ou USD, e
  moedas diferentes nunca se somam.
- Cartão de crédito: conta.tipo = 'cartao_credito'. Compras são despesas na conta do cartão;
  saldo negativo = fatura em aberto. Pagar a fatura é transferência de uma conta comum para o
  cartão. 'aplicacao' (CDB, caixinha, Tesouro) é para o saldo uma conta comum.
- Fatura projetada: lancamento.estimativa_de_fatura = true marca gasto estimado pela mediana
  (gasto_estimado_do_cartao) ou pagamento projetado. São estimativas, não fatos.
- leitura.operacao agrupa parcelas, recorrências e transferências. Recorrências são projetadas até
  o horizonte de Parâmetros (6 meses); parcelas vêm todas.
- leitura.extrato_linha: linhas importadas de extratos e faturas (valor com sinal: negativo é
  saída). Em cartão, cada leitura.extrato_importacao é uma fatura; lancamento_conciliado_id liga
  a linha ao lançamento.
- leitura.orcamento_mensal: meta por categoria, ano e mês, em BRL; só `ativo` vale.
- leitura.fechamento_mes: meses fechados por conta (não mudam sem reabertura).
- O sistema começa em 31/12/2025: não há lançamento anterior a 2026-01-01.""",
    "crv": """\
ControleRendaVariavel -- só existem as views do esquema `leitura` (o caminho de busca já aponta
para ele). Cada view traz a descrição dela; as regras abaixo são as que atravessam várias.
- leitura.posicao: posições ABERTAS de ações e opções juntas (instrumento 'acao' ou 'opcao'; a
  opção traz objeto, strike e vencimento). lado 'BUY' é comprada e 'SELL' é vendida. `preco` é a
  última cotação, ou o fechamento anterior quando não há; valor_mercado = quantidade x preco,
  negativo na posição vendida.
- Ativos: `mercado` é 'B3', 'NYSE' ou 'NASDAQ'; `moeda` vem do ativo. Valores monetários na moeda
  do ativo ou da carteira; não some BRL com USD.
- leitura.cotacao: última cotação por ativo (cotado_em mostra a idade do dado);
  leitura.cotacao_historico: a série diária.
- leitura.operacao: compras e vendas, de ações e de opções (e_opcao); status 'OPEN' ou 'CLOSED';
  `resultado` = realizado ao fechar, BRUTO: sem custos nem IR. O CRV tinha um modo de resultado
  líquido (x 0,9996) e o removeu em 25/09/2026: o que já estava gravado, aqui e nos movimentos, não
  foi recalculado. Resultados antigos podem trazer esse fator (0,04%), sem como distingui-los.
- leitura.movimento_posicao / movimento_opcao: extrato de cada posição (OPEN, INCREASE, DECREASE,
  ADJUSTMENT) com o resultado do movimento e o custo médio resultante.
- leitura.provento: proventos recebidos; tipo 'DIVIDENDO', 'JCP' ou 'ALUGUEL'; valor na moeda do
  ativo; pago_em. Opcionais (nulos quando não informados): data_com, cotas, valor_por_cota e o
  retrato do dia do pagamento (investido, valor_de_mercado, preco_medio, preco_cotado).
  yield_on_cost e dividend_yield ficam em PONTOS PERCENTUAIS (0,42 = 0,42%, não 42%); nos
  importados são deste pagamento: valor sobre o investido e sobre o valor de mercado.""",
}

INSTRUCOES = """\
Dados financeiros pessoais do usuário, em dois sistemas, SOMENTE LEITURA:
cb = ControleBancario, crv = ControleRendaVariavel.

Como trabalhar:
1. Prefira as ferramentas prontas (cb_*, crv_*): as somas já saem certas.
2. Para o que elas não cobrem, chame `esquema(sistema)` uma vez para ver as views, as colunas e as
   regras de leitura, e depois `sql(sistema, consulta)` com um SELECT sobre as views do esquema
   `leitura`. As tabelas dos sistemas não são lidas diretamente.
3. Respeite as regras: no CB, gasto/renda só com natureza 'gerencial'; transferências entre
   contas próprias não são gasto; valores são positivos e o tipo dá o sinal; não some moedas
   diferentes.
4. Datas no formato AAAA-MM-DD. Valores em texto decimal exato (ex.: "1234.56").
Nada aqui altera dados: para mudar algo, o usuário usa as telas dos sistemas."""
