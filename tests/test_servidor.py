"""Testes do servidor contra os bancos LOCAIS (dados de teste).

Precisam do `~/.financas-mcp/config.toml` com o ambiente `local`, criado por
`python -m financas_mcp.usuario_leitura local`. A barreira de SQL é testada
sem banco; o resto exercita as ferramentas e, principalmente, confirma que o
banco recusa escrita e esconde toda tabela dos sistemas (só as views do esquema
`leitura` são legíveis) mesmo quando a barreira de cima é contornada.

Os que precisam de banco levam `@pytest.mark.banco_local`. Localmente rode a
suíte inteira, sem filtro. Na CI, o job "Testes com banco" monta o CB e o CRV a
partir do `main` de cada um, com os dados mínimos de `tests/ci/`, e roda todos.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date

import psycopg
import pytest

os.environ.setdefault("FINANCAS_MCP_AMBIENTE", "local")

from financas_mcp import dominio, server  # noqa: E402
from financas_mcp.conexao import ConsultaRecusada, validar_consulta  # noqa: E402


def rodar(corrotina):
    return asyncio.run(corrotina)


@pytest.fixture(autouse=True)
def conexoes_novas():
    # Cada teste com seu próprio laço de eventos: o túnel/conexão não atravessa laços.
    server._conexoes = None
    yield
    server._conexoes = None


# --- Barreira de SQL (sem banco) ----------------------------------------------


@pytest.mark.parametrize("consulta", [
    "select 1",
    "  WITH x AS (select 1) select * from x;",
    "select 'a;b' as texto",
    "select 1 -- comentário; com ponto e vírgula",
    'select "coluna;estranha" from t',
    "explain select 1",
])
def test_barreira_aceita_leitura(consulta):
    assert validar_consulta(consulta)


@pytest.mark.parametrize("consulta", [
    "",
    "delete from lancamento",
    "update lancamento set valor = 0",
    "select 1; delete from cash_flow_entry",
    "insert into x values (1)",
    "drop table cash_flow_entry",
    "create table x (id int)",
    "grant all on cash_flow_entry to public",
])
def test_barreira_recusa_o_resto(consulta):
    with pytest.raises(ConsultaRecusada):
        validar_consulta(consulta)


# --- O banco é a proteção de verdade ------------------------------------------


def _conectar(sistema: str):
    banco = server.conexoes().config.sistemas[sistema]
    return psycopg.connect(
        host="127.0.0.1", port=banco.porta, dbname=banco.banco, user=banco.usuario,
        password=banco.senha, autocommit=True,
    )


@pytest.mark.banco_local
@pytest.mark.parametrize("comando", [
    "update leitura.operacao set status = status where false",
    "delete from leitura.operacao where false",
    "update leitura.categoria set categoria = categoria where false",
    "create table invasora (id int)",
    "create table leitura.invasora (id int)",
    "create or replace view leitura.lancamento as select 1 as id",
])
def test_usuario_de_leitura_nao_escreve_nem_com_a_barreira_contornada(comando):
    with _conectar("cb") as conexao, pytest.raises(psycopg.Error):
        conexao.execute(comando)


@pytest.mark.banco_local
@pytest.mark.parametrize(("sistema", "tabela"), [
    ("cb", "app_user"), ("cb", "audit_log"), ("cb", "django_session"), ("cb", "app_setting"),
    ("crv", "users"), ("crv", "audit_log"), ("crv", "app_settings"),
    # As tabelas financeiras também: o servidor lê as views, não elas.
    ("cb", "cash_flow_entry"), ("cb", "financial_account"), ("crv", "positions"), ("crv", "dividends"),
])
def test_nenhuma_tabela_dos_sistemas_e_legivel(sistema, tabela):
    with _conectar(sistema) as conexao, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conexao.execute(f"select count(*) from public.{tabela}")


@pytest.mark.banco_local
@pytest.mark.parametrize("sistema", ["cb", "crv"])
def test_todas_as_views_esperadas_existem_e_respondem(sistema):
    with _conectar(sistema) as conexao:
        for view in dominio.VIEWS[sistema]:
            conexao.execute(f"select * from leitura.{view} limit 1")


@pytest.mark.banco_local
def test_o_caminho_de_busca_aponta_para_o_esquema_leitura():
    # `sql` aceita o nome da view sem prefixo.
    com_prefixo = rodar(server.sql("cb", "select count(*) from leitura.lancamento"))["linhas"]
    sem_prefixo = rodar(server.sql("cb", "select count(*) from lancamento"))["linhas"]
    assert com_prefixo == sem_prefixo


@pytest.mark.banco_local
def test_tabela_dos_sistemas_vira_um_erro_que_aponta_para_as_views():
    with pytest.raises(ConsultaRecusada, match="leitura"):
        rodar(server.sql("cb", "select * from cash_flow_entry"))


@pytest.mark.banco_local
def test_ferramenta_sql_recusa_escrita():
    with pytest.raises(ConsultaRecusada):
        rodar(server.sql("cb", "delete from lancamento"))


# --- Ferramentas ---------------------------------------------------------------


def _hoje_no_banco() -> date:
    # O status efetivo sai de `leitura.hoje()`, a data do fuso de São Paulo. A data
    # do Python é a da máquina que roda a suíte, que pode estar em outro fuso: das
    # 21h à meia-noite em São Paulo, um runner em UTC já está no dia seguinte.
    linhas = rodar(server.conexoes().consultar("cb", "select leitura.hoje()"))["linhas"]
    return date.fromisoformat(str(linhas[0][0]))


def _ok(resultado, minimo=1):
    assert resultado["quantidade"] >= minimo, resultado
    assert not resultado["truncado"]
    return resultado


@pytest.mark.banco_local
def test_sistemas_e_esquema():
    assert rodar(server.sistemas())["ambiente"] == "local"
    for sistema in ("cb", "crv"):
        esquema = rodar(server.esquema(sistema))
        assert esquema["regras"]
        # Todas as views esperadas existem, cada uma com descrição e colunas.
        assert set(dominio.VIEWS[sistema]) <= set(esquema["views"])
        assert all(v["colunas"] for v in esquema["views"].values())
        # As que carregam regra de negócio dizem qual é.
        principal = {"cb": "lancamento", "crv": "posicao"}[sistema]
        assert esquema["views"][principal]["descricao"]


@pytest.mark.banco_local
def test_ferramentas_do_cb():
    contas = _ok(rodar(server.cb_contas()))
    assert "saldo_realizado_hoje" in contas["colunas"]
    _ok(rodar(server.cb_resumo_mensal()))
    _ok(rodar(server.cb_resumo_mensal(visao="previsto", agrupar="conta")))
    assert rodar(server.cb_lancamentos(limite=5))["quantidade"] == 5
    _ok(rodar(server.cb_categorias()))
    rodar(server.cb_orcamento())
    faturas = rodar(server.cb_faturas())
    _ok(faturas["importadas"])
    _ok(faturas["futuro"])
    _ok(rodar(server.cb_projecao_de_caixa(meses=3)))


@pytest.mark.banco_local
def test_status_efetivo_vem_da_view_e_obedece_a_regra():
    # A regra mora no CB (`leitura.lancamento`), e é lá que ela é testada à mão. Aqui
    # só se confere que o que o servidor lê a respeita em todas as linhas reais.
    hoje = _hoje_no_banco().isoformat()
    violacoes = rodar(server.conexoes().consultar("cb", """
        select count(*) filter (where (status = 'realizado') <> (status_gravado = 'realizado')),
               count(*) filter (where status = 'vencidos' and vencimento >= %(hoje)s),
               count(*) filter (where status = 'a_vencer' and vencimento < %(hoje)s)
        from leitura.lancamento
    """, {"hoje": hoje}))["linhas"][0]
    assert violacoes == [0, 0, 0]


@pytest.mark.banco_local
def test_filtro_de_status_dos_lancamentos_usa_o_efetivo():
    por_status = {
        s: rodar(server.cb_lancamentos(status=s, limite=2000))
        for s in ("realizado", "a_vencer", "vencidos", "todos")
    }
    assert all(not r["truncado"] for r in por_status.values())
    parcial = sum(por_status[s]["quantidade"] for s in ("realizado", "a_vencer", "vencidos"))
    assert parcial == por_status["todos"]["quantidade"]
    hoje = _hoje_no_banco().isoformat()
    for s in ("realizado", "a_vencer", "vencidos"):
        col = por_status[s]["colunas"].index("status")
        assert {linha[col] for linha in por_status[s]["linhas"]} <= {s}, s
    for s, nome in (("vencidos", "vencimento"), ("a_vencer", "vencimento")):
        col = por_status[s]["colunas"].index(nome)
        datas = [linha[col] for linha in por_status[s]["linhas"]]
        assert all((d < hoje) if s == "vencidos" else (d >= hoje) for d in datas), s


@pytest.mark.banco_local
def test_saldo_do_carbon_bate_com_o_do_sistema():
    # O cartão Carbon local tem saldo realizado conhecido (-10.656,92): a mesma conta
    # que a tela de contas do CB mostra. Na CI, a semente do CB cria um Carbon com
    # esse saldo e o confere pela função das telas do CB; aqui, o mesmo número sai
    # do SQL do MCP.
    contas = rodar(server.cb_contas())
    i = contas["colunas"].index("saldo_realizado_hoje")
    j = contas["colunas"].index("conta")
    saldos = {linha[j]: linha[i] for linha in contas["linhas"]}
    assert saldos.get("Carbon") == "-10656.92"


@pytest.mark.banco_local
def test_ferramentas_do_crv():
    _ok(rodar(server.crv_carteira()))
    rodar(server.crv_proventos(agrupar="ativo"))
    rodar(server.crv_operacoes(status="todos"))
    rodar(server.crv_operacoes())


@pytest.mark.banco_local
def test_sql_livre_com_limite():
    resultado = rodar(server.sql("cb", "select generate_series(1, 5000) as n"))
    assert resultado["quantidade"] == 2000
    assert resultado["truncado"] is True
