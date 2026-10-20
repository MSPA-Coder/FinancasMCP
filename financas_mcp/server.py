"""Servidor MCP somente leitura sobre CB e CRV.

Ferramentas prontas (cb_*, crv_*) consultam as views do esquema `leitura`, que
cada sistema publica com as regras do próprio domínio (status efetivo, saldo,
preço que vale); `esquema` e `sql` cobrem o resto. Nenhuma regra é reescrita
aqui, e nenhuma tabela dos sistemas é lida. Tudo passa por `Conexoes.consultar`,
que abre transação READ ONLY com o usuário de leitura.
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

Sistema = Literal["cb", "crv"]


def conexoes() -> Conexoes:
    global _conexoes
    if _conexoes is None:
        _conexoes = Conexoes(carregar())
    return _conexoes


def _data(texto: str | None, padrao: date) -> date:
    return date.fromisoformat(texto) if texto else padrao


def _inicio_do_ano() -> date:
    return date(date.today().year, 1, 1)


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
    """Views e colunas liberadas de um sistema, com as regras para ler os dados certo.

    Chame antes de escrever SQL livre para aquele sistema. As views são as do esquema
    `leitura`, que o próprio sistema publica; cada uma traz a descrição.
    """
    resultado = await conexoes().consultar(
        sistema,
        """
        select c.relname, obj_description(c.oid, 'pg_class'),
               string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum)
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
        where n.nspname = %s and c.relkind = 'v'
        group by c.oid, c.relname order by c.relname
        """,
        (dominio.ESQUEMA,),
    )
    return {
        "regras": dominio.REGRAS[sistema],
        "views": {nome: {"descricao": descricao, "colunas": colunas} for nome, descricao, colunas in resultado["linhas"]},
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
    return await conexoes().consultar("cb", """
        select id, conta, titular, instituicao, moeda, tipo, saldo_inicial, data_saldo_inicial,
               saldo_realizado_hoje, vencido_em_aberto, saldo_com_todo_previsto, ultimo_vencimento_previsto
        from leitura.conta
        order by tipo, titular, conta
    """)


_GRUPOS_CB = {
    "categoria": "l.categoria",
    "conta": "l.conta",
    "tipo": "l.tipo",
    "categoria_e_conta": "l.categoria || ' | ' || l.conta",
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
        "realizado": "l.status = 'realizado'",
        "previsto": "true",
        "em_aberto": "l.status <> 'realizado'",
    }[visao]
    grupo = _GRUPOS_CB[agrupar]
    return await conexoes().consultar("cb", f"""
        select to_char(l.data, 'YYYY-MM') as mes, {grupo} as grupo, l.moeda,
               l.tipo, sum(l.valor) as total, count(*) as lancamentos
        from leitura.lancamento l
        where {filtro_visao}
          and l.data between %(inicio)s and %(fim)s
          and (%(gerenciais)s = false or l.natureza = 'gerencial')
          and (%(conta)s::int is null or l.conta_id = %(conta)s::int)
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
    estimativa da fatura projetada. limite máximo 2000. O status é o efetivo: em aberto
    com vencimento anterior a hoje é 'vencidos', mesmo que o CB ainda o guarde como
    'a_vencer'.
    """
    return await conexoes().consultar("cb", """
        select l.id, l.data, l.status, l.tipo, l.valor, l.descricao, l.categoria, l.natureza,
               l.conta, l.moeda, l.parcela, l.recorrente, l.estimativa_de_fatura,
               l.vencimento, l.realizado_em
        from leitura.lancamento l
        where (%(texto)s::text is null or l.descricao ilike '%%' || %(texto)s::text || '%%')
          and (%(inicio)s::date is null or l.data >= %(inicio)s::date)
          and (%(fim)s::date is null or l.data <= %(fim)s::date)
          and (%(conta)s::int is null or l.conta_id = %(conta)s::int)
          and (%(categoria)s::text is null or l.categoria = %(categoria)s::text)
          and (%(status)s = 'todos' or l.status = %(status)s)
          and (%(tipo)s = 'todos' or l.tipo = %(tipo)s)
          and (%(gerenciais)s = false or l.natureza = 'gerencial')
        order by l.data desc, l.id desc
    """, {
        "texto": texto, "inicio": inicio, "fim": fim, "conta": conta_id, "categoria": categoria,
        "status": status, "tipo": tipo, "gerenciais": somente_gerenciais,
    }, limite=max(1, min(limite, 2000)))


@mcp.tool(annotations=LEITURA)
async def cb_categorias() -> dict:
    """Categorias do CB com natureza e quantos lançamentos cada uma tem."""
    return await conexoes().consultar("cb", """
        select id, categoria, natureza, lancamentos, primeiro, ultimo
        from leitura.categoria
        order by natureza, categoria
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
            select l.categoria_id, sum(l.valor) as realizado
            from leitura.lancamento l
            where l.status = 'realizado' and l.tipo = 'despesa' and l.moeda = 'BRL'
              and l.natureza = 'gerencial'
              and l.realizado_em >= make_date(%(ano)s, %(mes)s, 1)
              and l.realizado_em < make_date(%(ano)s, %(mes)s, 1) + interval '1 month'
            group by 1
        ), meta as (
            select categoria_id, sum(planejado) as planejado
            from leitura.orcamento_mensal where ano = %(ano)s and mes = %(mes)s and ativo
            group by 1
        )
        select c.categoria, m.planejado, coalesce(g.realizado, 0) as realizado,
               m.planejado - coalesce(g.realizado, 0) as disponivel
        from leitura.categoria c
        left join meta m on m.categoria_id = c.id
        left join gasto g on g.categoria_id = c.id
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
        select b.conta as cartao, b.id as importacao, b.arquivo, b.importada_em::date as importada_em,
               sum(-l.valor) filter (where l.valor < 0) as compras,
               sum(l.valor) filter (where l.valor > 0 and l.descricao !~* '^pag(amentos?)?\\y') as creditos,
               sum(l.valor) filter (where l.valor > 0 and l.descricao ~* '^pag(amentos?)?\\y') as pagamentos,
               min(l.data) as primeira_linha, max(l.data) as ultima_linha
        from leitura.extrato_importacao b
        join leitura.extrato_linha l on l.importacao_id = b.id
        where b.tipo_conta = 'cartao_credito'
        group by b.conta, b.id, b.arquivo, b.importada_em
        order by b.conta, b.id desc
    """)
    futuro = await c.consultar("cb", """
        select l.conta as cartao, to_char(l.vencimento, 'YYYY-MM') as mes,
               sum(l.valor_previsto) filter (where l.tipo = 'despesa' and l.tipo_operacao = 'installment') as parcelas,
               sum(l.valor_previsto) filter (where l.tipo = 'despesa' and l.gasto_estimado_do_cartao) as gasto_estimado,
               sum(l.valor_previsto) filter (where l.tipo = 'despesa' and l.tipo_operacao <> 'installment'
                                              and not l.estimativa_de_fatura) as outros,
               sum(l.valor_previsto) filter (where l.tipo = 'receita') as pagamentos_previstos
        from leitura.lancamento l
        join leitura.conta c on c.id = l.conta_id and c.tipo = 'cartao_credito'
        where l.status <> 'realizado'
        group by 1, 2 order by 1, 2
    """)
    return {"importadas": importadas, "futuro": futuro}


@mcp.tool(annotations=LEITURA)
async def cb_projecao_de_caixa(meses: int = 6) -> dict:
    """Saldo de cada conta no fim de cada mês, do mês atual até `meses` à frente.

    Considera realizados pela data de realização e em aberto (inclusive vencidos) pelo
    vencimento, somando ao saldo inicial. Mostra onde e quando uma conta fica negativa.
    """
    return await conexoes().consultar("cb", """
        with fins as (
            select (date_trunc('month', leitura.hoje()) + make_interval(months => n + 1) - interval '1 day')::date as fim
            from generate_series(0, %(meses)s) as n
        ), mov as (
            select l.conta_id, l.data, l.sinal * l.valor as valor from leitura.lancamento l
        )
        select c.conta, c.moeda, c.tipo, f.fim as fim_do_mes,
               c.saldo_inicial + coalesce((select sum(m.valor) from mov m where m.conta_id = c.id and m.data <= f.fim), 0) as saldo
        from leitura.conta c cross join fins f
        order by c.tipo, c.conta, f.fim
    """, {"meses": max(0, min(meses, 36))})


# --- ControleRendaVariavel ------------------------------------------------------


@mcp.tool(annotations=LEITURA)
async def crv_carteira() -> dict:
    """Posições abertas (ações e opções) com custo, última cotação e resultado não realizado.

    instrumento: 'acao' ou 'opcao' (opção traz ativo-objeto, strike e vencimento).
    preco = última cotação, ou o fechamento anterior quando não há; valor_mercado =
    quantidade x preco, com sinal negativo para posição vendida (side 'SELL'), como o CRV
    calcula o valor de desmonte. Moeda na coluna moeda: não some BRL com USD.
    """
    return await conexoes().consultar("crv", """
        select carteira, corretora, instrumento, ativo, objeto, strike, vencimento, mercado, moeda,
               lado, quantidade, custo_medio, custo_total, preco, cotado_em, valor_mercado, resultado_aberto,
               aberta_em
        from leitura.posicao
        order by moeda, instrumento, valor_mercado desc nulls last
    """)


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
        "mes": "to_char(p.pago_em, 'YYYY-MM')",
        "ativo": "p.ativo",
        "mes_e_ativo": "to_char(p.pago_em, 'YYYY-MM') || ' ' || p.ativo",
        "tipo": "p.tipo",
    }[agrupar]
    return await conexoes().consultar("crv", f"""
        select {grupo} as grupo, p.moeda, sum(p.valor) as total, count(*) as pagamentos
        from leitura.provento p
        where p.pago_em between %(inicio)s and %(fim)s
        group by 1, 2 order by 1, 2
    """, {"inicio": inicio_d, "fim": fim_d})


@mcp.tool(annotations=LEITURA)
async def crv_operacoes(
    inicio: str | None = None,
    fim: str | None = None,
    status: Literal["OPEN", "CLOSED", "todos"] = "CLOSED",
) -> dict:
    """Operações do CRV (compra/venda de ações e de opções), com resultado realizado das fechadas.

    Filtra pela data de fechamento (fechadas) ou de abertura (abertas). Padrão: ano corrente.
    Nas operações de opção, e_opcao é verdadeiro, ativo é o código da opção e objeto, o ativo-objeto.
    """
    inicio_d = _data(inicio, _inicio_do_ano())
    fim_d = _data(fim, date.today())
    return await conexoes().consultar("crv", """
        select o.id, o.ativo, o.objeto, o.e_opcao, o.moeda, o.lado, o.quantidade,
               o.preco_de_entrada as preco_entrada, o.preco_de_saida as preco_saida, o.aberta_em,
               o.fechada_em, o.status, o.resultado, o.carteira, o.notas
        from leitura.operacao o
        where (%(status)s = 'todos' or o.status = %(status)s)
          and coalesce(o.fechada_em, o.aberta_em) between %(inicio)s and %(fim)s
        order by coalesce(o.fechada_em, o.aberta_em) desc
    """, {"inicio": inicio_d, "fim": fim_d, "status": status})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
