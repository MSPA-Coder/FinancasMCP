"""Servidor MCP somente leitura sobre CB, CRV e NetWorth.

Ferramentas prontas (cb_*, crv_*, nw_*) fazem as contas no banco, com as regras
de cada domínio; `esquema` e `sql` cobrem o resto. Tudo passa por
`Conexoes.consultar`, que abre transação READ ONLY com o usuário de leitura.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import dominio
from .conexao import Conexoes, validar_consulta
from .config import carregar

mcp = MCPServer("financas", instructions=dominio.INSTRUCOES)
# Tudo aqui só lê: o cliente pode chamar sem pedir confirmação de escrita.
LEITURA = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_conexoes: Conexoes | None = None

Sistema = Literal["cb", "crv", "nw"]


def conexoes() -> Conexoes:
    global _conexoes
    if _conexoes is None:
        _conexoes = Conexoes(carregar())
    return _conexoes


def _data(texto: str | None, padrao: date) -> date:
    return date.fromisoformat(texto) if texto else padrao


def _inicio_do_ano() -> date:
    return date(date.today().year, 1, 1)


# Expressões do CB reutilizadas: a data e o valor que valem para cada lançamento.
_DATA_REF = "case when e.status = 'realizado' then e.realized_date else e.due_date end"
_VALOR_REF = "case when e.status = 'realizado' then coalesce(e.realized_amount, e.entry_amount) else e.entry_amount end"
_SINAL = "case when e.entry_type = 'receita' then 1 else -1 end"
_PROJECAO_DO_CARTAO = "coalesce(o.operation_key, '') ~ '^cartao-(estimativa|pagamento):'"


# --- Genéricas ------------------------------------------------------------------


@mcp.tool(annotations=LEITURA)
async def sistemas() -> dict:
    """Os sistemas disponíveis, o ambiente (produção ou local) e a data de hoje."""
    config = conexoes().config
    return {
        "ambiente": config.ambiente,
        "hoje": date.today().isoformat(),
        "sistemas": {nome: dominio.NOMES[nome] for nome in config.sistemas},
    }


@mcp.tool(annotations=LEITURA)
async def esquema(sistema: Sistema) -> dict:
    """Tabelas e colunas liberadas de um sistema, com as regras para ler os dados certo.

    Chame antes de escrever SQL livre para aquele sistema.
    """
    resultado = await conexoes().consultar(
        sistema,
        """
        select c.table_name, string_agg(c.column_name || ' ' || c.data_type, ', ' order by c.ordinal_position)
        from information_schema.columns c
        where c.table_schema = 'public' and c.table_name = any(%s)
        group by c.table_name order by c.table_name
        """,
        (list(dominio.TABELAS[sistema]),),
    )
    return {
        "regras": dominio.REGRAS[sistema],
        "tabelas": {tabela: colunas for tabela, colunas in resultado["linhas"]},
    }


@mcp.tool(annotations=LEITURA)
async def sql(sistema: Sistema, consulta: str) -> dict:
    """Executa um SELECT (ou WITH ... SELECT) somente leitura no sistema indicado.

    Um comando por vez; até 2000 linhas e 15 s. Para resultados grandes, agregue no SQL
    (group by, date_trunc) em vez de trazer linhas soltas.
    """
    return await conexoes().consultar(sistema, validar_consulta(consulta))


# --- ControleBancario -----------------------------------------------------------


@mcp.tool(annotations=LEITURA)
async def cb_contas() -> dict:
    """Contas do CB com saldo realizado hoje, total vencido em aberto e saldo com todo o previsto.

    Cartões de crédito aparecem com saldo negativo (fatura em aberto).
    """
    return await conexoes().consultar("cb", f"""
        select a.id, a.account_name as conta, ow.owner_name as titular, i.institution_name as instituicao,
               a.currency as moeda, a.account_kind as tipo, a.initial_balance as saldo_inicial,
               a.initial_balance_date as data_saldo_inicial,
               a.initial_balance + coalesce(sum({_SINAL} * {_VALOR_REF})
                   filter (where e.status = 'realizado' and e.realized_date <= current_date), 0) as saldo_realizado_hoje,
               coalesce(sum({_SINAL} * e.entry_amount)
                   filter (where e.status <> 'realizado' and e.due_date < current_date), 0) as vencido_em_aberto,
               a.initial_balance + coalesce(sum({_SINAL} * {_VALOR_REF}), 0) as saldo_com_todo_previsto,
               max(e.due_date) filter (where e.status <> 'realizado') as ultimo_vencimento_previsto
        from financial_account a
        join account_owner ow on ow.id = a.owner_id
        join financial_institution i on i.id = a.institution_id
        left join cash_flow_entry e on e.account_id = a.id
        group by a.id, ow.owner_name, i.institution_name
        order by a.account_kind, ow.owner_name, a.account_name
    """)


_GRUPOS_CB = {
    "categoria": "c.category_name",
    "conta": "a.account_name",
    "tipo": "e.entry_type",
    "categoria_e_conta": "c.category_name || ' | ' || a.account_name",
}


@mcp.tool(annotations=LEITURA)
async def cb_resumo_mensal(
    inicio: str | None = None,
    fim: str | None = None,
    visao: Literal["realizado", "previsto", "em_aberto"] = "realizado",
    agrupar: Literal["categoria", "conta", "tipo", "categoria_e_conta"] = "categoria",
    conta_id: int | None = None,
    somente_gerenciais: bool = True,
) -> dict:
    """Receitas e despesas somadas por mês e pelo agrupamento escolhido.

    visao: 'realizado' (o que aconteceu, pela data de realização), 'previsto' (realizado +
    em aberto, pela data que vale), 'em_aberto' (só o que ainda vai acontecer ou está vencido).
    somente_gerenciais=True exclui transferências entre contas e aplicações (não são gasto).
    Datas AAAA-MM-DD; padrão: do início do ano até hoje (ou até o fim do ano no previsto).
    """
    hoje = date.today()
    inicio_d = _data(inicio, _inicio_do_ano())
    fim_d = _data(fim, hoje if visao == "realizado" else date(hoje.year, 12, 31))
    filtro_visao = {
        "realizado": "e.status = 'realizado'",
        "previsto": "true",
        "em_aberto": "e.status <> 'realizado'",
    }[visao]
    grupo = _GRUPOS_CB[agrupar]
    return await conexoes().consultar("cb", f"""
        select to_char({_DATA_REF}, 'YYYY-MM') as mes, {grupo} as grupo, a.currency as moeda,
               e.entry_type as tipo, sum({_VALOR_REF}) as total, count(*) as lancamentos
        from cash_flow_entry e
        join cash_flow_category c on c.id = e.category_id
        join financial_account a on a.id = e.account_id
        left join bank_operation o on o.id = e.bank_operation_id
        where {filtro_visao}
          and {_DATA_REF} between %(inicio)s and %(fim)s
          and (%(gerenciais)s = false or c.kind = 'gerencial')
          and (%(conta)s::int is null or e.account_id = %(conta)s::int)
        group by 1, 2, 3, 4
        order by 1, 4, total desc
    """, {"inicio": inicio_d, "fim": fim_d, "gerenciais": somente_gerenciais, "conta": conta_id})


@mcp.tool(annotations=LEITURA)
async def cb_lancamentos(
    texto: str | None = None,
    inicio: str | None = None,
    fim: str | None = None,
    conta_id: int | None = None,
    categoria: str | None = None,
    status: Literal["realizado", "a_vencer", "vencidos", "todos"] = "todos",
    tipo: Literal["receita", "despesa", "todos"] = "todos",
    somente_gerenciais: bool = False,
    limite: int = 300,
) -> dict:
    """Lançamentos individuais do CB com filtros; mais recentes primeiro.

    texto busca na descrição (sem diferenciar maiúsculas); categoria pelo nome exato.
    Cada linha traz a data e o valor que valem (realizado ou previsto), parcela e se é
    estimativa da fatura projetada. limite máximo 2000.
    """
    return await conexoes().consultar("cb", f"""
        select e.id, {_DATA_REF} as data, e.status, e.entry_type as tipo, {_VALOR_REF} as valor,
               e.description as descricao, c.category_name as categoria, c.kind as natureza,
               a.account_name as conta, a.currency as moeda,
               case when e.installments > 1 then e.current_installment || '/' || e.installments end as parcela,
               e.is_recurring as recorrente, {_PROJECAO_DO_CARTAO} as estimativa_de_fatura,
               e.due_date as vencimento, e.realized_date as realizado_em
        from cash_flow_entry e
        join cash_flow_category c on c.id = e.category_id
        join financial_account a on a.id = e.account_id
        left join bank_operation o on o.id = e.bank_operation_id
        where (%(texto)s::text is null or e.description ilike '%%' || %(texto)s::text || '%%')
          and (%(inicio)s::date is null or {_DATA_REF} >= %(inicio)s::date)
          and (%(fim)s::date is null or {_DATA_REF} <= %(fim)s::date)
          and (%(conta)s::int is null or e.account_id = %(conta)s::int)
          and (%(categoria)s::text is null or c.category_name = %(categoria)s::text)
          and (%(status)s = 'todos' or e.status = %(status)s)
          and (%(tipo)s = 'todos' or e.entry_type = %(tipo)s)
          and (%(gerenciais)s = false or c.kind = 'gerencial')
        order by {_DATA_REF} desc, e.id desc
    """, {
        "texto": texto, "inicio": inicio, "fim": fim, "conta": conta_id, "categoria": categoria,
        "status": status, "tipo": tipo, "gerenciais": somente_gerenciais,
    }, limite=max(1, min(limite, 2000)))


@mcp.tool(annotations=LEITURA)
async def cb_categorias() -> dict:
    """Categorias do CB com natureza e quantos lançamentos cada uma tem."""
    return await conexoes().consultar("cb", """
        select c.id, c.category_name as categoria, c.kind as natureza, count(e.id) as lancamentos,
               min(coalesce(e.realized_date, e.due_date)) as primeiro, max(coalesce(e.realized_date, e.due_date)) as ultimo
        from cash_flow_category c left join cash_flow_entry e on e.category_id = c.id
        group by c.id order by c.kind, c.category_name
    """)


@mcp.tool(annotations=LEITURA)
async def cb_orcamento(ano: int | None = None, mes: int | None = None) -> dict:
    """Orçamento (metas) do mês por categoria contra as despesas realizadas em BRL.

    Inclui categorias com gasto e sem meta (planejado vazio). Padrão: mês atual.
    """
    hoje = date.today()
    ano = ano or hoje.year
    mes = mes or hoje.month
    return await conexoes().consultar("cb", """
        with gasto as (
            select e.category_id, sum(coalesce(e.realized_amount, e.entry_amount)) as realizado
            from cash_flow_entry e
            join financial_account a on a.id = e.account_id
            join cash_flow_category c on c.id = e.category_id
            where e.status = 'realizado' and e.entry_type = 'despesa' and a.currency = 'BRL'
              and c.kind = 'gerencial'
              and e.realized_date >= make_date(%(ano)s, %(mes)s, 1)
              and e.realized_date < make_date(%(ano)s, %(mes)s, 1) + interval '1 month'
            group by 1
        ), meta as (
            select category_id, sum(planned_amount) as planejado
            from monthly_budget where year = %(ano)s and month = %(mes)s and active
            group by 1
        )
        select c.category_name as categoria, m.planejado, coalesce(g.realizado, 0) as realizado,
               m.planejado - coalesce(g.realizado, 0) as disponivel
        from cash_flow_category c
        left join meta m on m.category_id = c.id
        left join gasto g on g.category_id = c.id
        where m.planejado is not null or g.realizado is not null
        order by m.planejado is null, coalesce(g.realizado, 0) desc
    """, {"ano": ano, "mes": mes})


@mcp.tool(annotations=LEITURA)
async def cb_faturas() -> dict:
    """Cartões de crédito: faturas importadas (totais) e o que vem pela frente por mês.

    'futuro' separa, por mês de vencimento, parcelas já contratadas, gasto estimado (mediana
    das últimas faturas) e outros lançamentos, além dos pagamentos que chegam ao cartão.
    """
    c = conexoes()
    importadas = await c.consultar("cb", """
        select a.account_name as cartao, b.id as importacao, b.source_filename as arquivo,
               b.created_at::date as importada_em,
               sum(-l.amount) filter (where l.amount < 0) as compras,
               sum(l.amount) filter (where l.amount > 0 and l.description !~* '^pag(amentos?)?\\y') as creditos,
               sum(l.amount) filter (where l.amount > 0 and l.description ~* '^pag(amentos?)?\\y') as pagamentos,
               min(l.statement_date) as primeira_linha, max(l.statement_date) as ultima_linha
        from bank_statement_import b
        join financial_account a on a.id = b.account_id and a.account_kind = 'cartao_credito'
        join bank_statement_line l on l.import_id = b.id
        group by a.account_name, b.id order by a.account_name, b.id desc
    """)
    futuro = await c.consultar("cb", f"""
        select a.account_name as cartao, to_char(e.due_date, 'YYYY-MM') as mes,
               sum(e.entry_amount) filter (where e.entry_type = 'despesa' and e.operation_type = 'installment') as parcelas,
               sum(e.entry_amount) filter (where e.entry_type = 'despesa' and o.operation_key like 'cartao-estimativa:%%') as gasto_estimado,
               sum(e.entry_amount) filter (where e.entry_type = 'despesa' and e.operation_type <> 'installment'
                                            and not {_PROJECAO_DO_CARTAO}) as outros,
               sum(e.entry_amount) filter (where e.entry_type = 'receita') as pagamentos_previstos
        from cash_flow_entry e
        join financial_account a on a.id = e.account_id and a.account_kind = 'cartao_credito'
        left join bank_operation o on o.id = e.bank_operation_id
        where e.status <> 'realizado'
        group by 1, 2 order by 1, 2
    """)
    return {"importadas": importadas, "futuro": futuro}


@mcp.tool(annotations=LEITURA)
async def cb_projecao_de_caixa(meses: int = 6) -> dict:
    """Saldo de cada conta no fim de cada mês, do mês atual até `meses` à frente.

    Considera realizados pela data de realização e em aberto (inclusive vencidos) pelo
    vencimento, somando ao saldo inicial. Mostra onde e quando uma conta fica negativa.
    """
    return await conexoes().consultar("cb", f"""
        with fins as (
            select (date_trunc('month', current_date) + make_interval(months => n + 1) - interval '1 day')::date as fim
            from generate_series(0, %(meses)s) as n
        ), mov as (
            select e.account_id, {_DATA_REF} as data, {_SINAL} * {_VALOR_REF} as valor from cash_flow_entry e
        )
        select a.account_name as conta, a.currency as moeda, a.account_kind as tipo, f.fim as fim_do_mes,
               a.initial_balance + coalesce((select sum(m.valor) from mov m where m.account_id = a.id and m.data <= f.fim), 0) as saldo
        from financial_account a cross join fins f
        order by a.account_kind, a.account_name, f.fim
    """, {"meses": max(0, min(meses, 36))})


# --- ControleRendaVariavel ------------------------------------------------------


@mcp.tool(annotations=LEITURA)
async def crv_carteira(incluir_simuladas: bool = False) -> dict:
    """Posições abertas (ações e opções) com custo, última cotação e resultado não realizado.

    instrumento: 'acao' ou 'opcao' (opção traz ativo-objeto, strike e vencimento).
    preco = última cotação, ou o fechamento anterior quando não há; valor_mercado =
    quantidade x preco, com sinal negativo para posição vendida (side 'SELL'), como o CRV
    calcula o valor de desmonte. Moeda na coluna moeda: não some BRL com USD.
    """
    return await conexoes().consultar("crv", """
        with abertas as (
            select 'acao' as instrumento, p.portfolio_id, p.broker_id, t.symbol as ativo,
                   null::text as objeto, null::numeric as strike, null::date as vencimento,
                   t.market::text as mercado, t.currency as moeda, p.side, p.quantity, p.average_cost,
                   coalesce(q.last_price, q.previous_close) as preco, q.observed_at as cotado_em,
                   p.opened_on
            from positions p
            join tickers t on t.id = p.ticker_id
            left join quotes q on q.ticker_id = p.ticker_id
            union all
            select 'opcao', op.portfolio_id, op.broker_id, t.symbol, obj.symbol, c.strike, v.exercise_date,
                   t.market::text, t.currency, op.side, op.quantity, op.average_cost,
                   coalesce(oq.last_price, oq.previous_close), oq.observed_at, op.opened_on
            from option_positions op
            join option_contracts c on c.id = op.contract_id
            join tickers t on t.id = c.ticker_id
            join tickers obj on obj.id = c.underlying_ticker_id
            join option_expirations v on v.id = c.expiration_id
            left join option_quotes oq on oq.contract_id = op.contract_id
        )
        select pf.name as carteira, pf.simulated as simulada, br.name as corretora, a.instrumento,
               a.ativo, a.objeto, a.strike, a.vencimento, a.mercado, a.moeda, a.side as lado,
               a.quantity as quantidade, a.average_cost as custo_medio,
               a.quantity * a.average_cost as custo_total, a.preco, a.cotado_em,
               case when a.side = 'BUY' then 1 else -1 end * a.quantity * a.preco as valor_mercado,
               case when a.side = 'BUY' then 1 else -1 end * (a.preco - a.average_cost) * a.quantity
                   as resultado_aberto,
               a.opened_on as aberta_em
        from abertas a
        join portfolios pf on pf.id = a.portfolio_id
        left join brokers br on br.id = a.broker_id
        where a.quantity <> 0 and (%(simuladas)s or not pf.simulated)
        order by a.moeda, a.instrumento, valor_mercado desc nulls last
    """, {"simuladas": incluir_simuladas})


@mcp.tool(annotations=LEITURA)
async def crv_proventos(
    inicio: str | None = None,
    fim: str | None = None,
    agrupar: Literal["mes", "ativo", "mes_e_ativo", "tipo"] = "mes",
) -> dict:
    """Proventos recebidos (dividendos, JCP, aluguel) somados por mês, ativo ou tipo.

    Padrão: últimos 12 meses. Valores na moeda do ativo.
    """
    hoje = date.today()
    inicio_d = _data(inicio, date(hoje.year - 1, hoje.month, 1))
    fim_d = _data(fim, hoje)
    grupo = {
        "mes": "to_char(d.payment_date, 'YYYY-MM')",
        "ativo": "t.symbol",
        "mes_e_ativo": "to_char(d.payment_date, 'YYYY-MM') || ' ' || t.symbol",
        "tipo": "d.kind::text",
    }[agrupar]
    return await conexoes().consultar("crv", f"""
        select {grupo} as grupo, t.currency as moeda, sum(d.amount) as total, count(*) as pagamentos
        from dividends d join tickers t on t.id = d.ticker_id
        where d.payment_date between %(inicio)s and %(fim)s
        group by 1, 2 order by 1, 2
    """, {"inicio": inicio_d, "fim": fim_d})


@mcp.tool(annotations=LEITURA)
async def crv_operacoes(
    inicio: str | None = None,
    fim: str | None = None,
    status: Literal["OPEN", "CLOSED", "todos"] = "CLOSED",
) -> dict:
    """Operações do CRV (compra/venda), com resultado realizado das fechadas.

    Filtra pela data de fechamento (fechadas) ou de abertura (abertas). Padrão: ano corrente.
    """
    inicio_d = _data(inicio, _inicio_do_ano())
    fim_d = _data(fim, date.today())
    return await conexoes().consultar("crv", """
        select tr.id, t.symbol as ativo, t.currency as moeda, tr.side as lado, tr.quantity as quantidade,
               tr.average_cost as preco_entrada, tr.exit_price as preco_saida, tr.opened_on as aberta_em,
               tr.closed_on as fechada_em, tr.status, tr.result as resultado, pf.name as carteira,
               pf.simulated as simulada, tr.notes as notas
        from transactions tr
        join tickers t on t.id = tr.ticker_id
        left join portfolios pf on pf.id = tr.portfolio_id
        where (%(status)s = 'todos' or tr.status::text = %(status)s)
          and coalesce(tr.closed_on, tr.opened_on) between %(inicio)s and %(fim)s
        order by coalesce(tr.closed_on, tr.opened_on) desc
    """, {"inicio": inicio_d, "fim": fim_d, "status": status})


# --- NetWorth -------------------------------------------------------------------


@mcp.tool(annotations=LEITURA)
async def nw_historico(
    inicio: str | None = None,
    fim: str | None = None,
    frequencia: Literal["dia", "semana", "mes"] = "mes",
) -> dict:
    """Evolução do patrimônio: caixa (CB) e investimentos (CRV) em BRL, convertendo USD.

    Usa a última foto de cada período e a taxa de câmbio da data da foto (ou a anterior mais
    próxima); com duas fontes no mesmo dia, vale a preferida do NetWorth (ptax, depois yahoo).
    Padrão: tudo, por mês.
    """
    inicio_d = _data(inicio, date(2000, 1, 1))
    fim_d = _data(fim, date.today())
    periodo = {"dia": "day", "semana": "week", "mes": "month"}[frequencia]
    return await conexoes().consultar("nw", f"""
        with fotos as (
            select distinct on (date_trunc('{periodo}', f.data)) f.id, f.data
            from foto_do_patrimonio f
            where f.data between %(inicio)s and %(fim)s
            order by date_trunc('{periodo}', f.data), f.data desc
        ), valores as (
            select fo.data, v.papel, v.moeda, v.total,
                   case when v.moeda = 'BRL' then 1 else (
                       select t.taxa from taxa_de_cambio t
                       where t.moeda = v.moeda and t.data <= fo.data
                       -- Mesma escolha de consolidado/cambio.py (PREFERENCIA_DE_FONTE).
                       order by t.data desc, case t.fonte when 'ptax' then 0 when 'yahoo' then 1 else 99 end,
                                t.fonte
                       limit 1
                   ) end as taxa
            from fotos fo join valor_da_foto v on v.foto_id = fo.id
        )
        select data,
               sum(total * taxa) filter (where papel = 'caixa') as caixa_brl,
               sum(total * taxa) filter (where papel = 'investimento') as investimentos_brl,
               sum(total * taxa) as patrimonio_brl,
               sum(total) filter (where moeda = 'USD') as parte_em_usd,
               bool_or(taxa is null) as faltou_cambio
        from valores group by data order by data
    """, {"inicio": inicio_d, "fim": fim_d})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
