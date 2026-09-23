"""Testes do servidor contra os bancos LOCAIS (dados de teste).

Precisam do `~/.financas-mcp/config.toml` com o ambiente `local`, criado por
`python -m financas_mcp.usuario_leitura local`. A barreira de SQL é testada
sem banco; o resto exercita as ferramentas e, principalmente, confirma que o
banco recusa escrita e esconde as tabelas sensíveis mesmo quando a barreira
de cima é contornada.
"""

from __future__ import annotations

import asyncio
import os

import psycopg
import pytest

os.environ.setdefault("FINANCAS_MCP_AMBIENTE", "local")

from financas_mcp import server  # noqa: E402
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
    "delete from cash_flow_entry",
    "update cash_flow_entry set entry_amount = 0",
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


@pytest.mark.parametrize("comando", [
    "update cash_flow_entry set entry_amount = entry_amount where false",
    "delete from cash_flow_entry where false",
    "create table invasora (id int)",
])
def test_usuario_de_leitura_nao_escreve_nem_com_a_barreira_contornada(comando):
    with _conectar("cb") as conexao, pytest.raises(psycopg.Error):
        conexao.execute(comando)


@pytest.mark.parametrize(("sistema", "tabela"), [
    ("cb", "app_user"), ("cb", "audit_log"), ("cb", "django_session"), ("cb", "app_setting"),
    ("crv", "users"), ("crv", "audit_log"), ("crv", "app_settings"),
    ("nw", "auth_user"), ("nw", "django_session"),
])
def test_tabelas_sensiveis_ficam_invisiveis(sistema, tabela):
    with _conectar(sistema) as conexao, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conexao.execute(f"select count(*) from {tabela}")


def test_ferramenta_sql_recusa_escrita():
    with pytest.raises(ConsultaRecusada):
        rodar(server.sql("cb", "delete from cash_flow_entry"))


# --- Ferramentas ---------------------------------------------------------------


def _ok(resultado, minimo=1):
    assert resultado["quantidade"] >= minimo, resultado
    assert not resultado["truncado"]
    return resultado


def test_sistemas_e_esquema():
    assert rodar(server.sistemas())["ambiente"] == "local"
    for sistema in ("cb", "crv", "nw"):
        esquema = rodar(server.esquema(sistema))
        assert esquema["regras"]
        assert esquema["tabelas"]


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


def test_saldo_do_carbon_bate_com_o_do_sistema():
    # O cartão Carbon local tem saldo realizado conhecido (-10.656,92): a mesma conta
    # que o dashboard do NetWorth mostra.
    contas = rodar(server.cb_contas())
    i = contas["colunas"].index("saldo_realizado_hoje")
    j = contas["colunas"].index("conta")
    saldos = {linha[j]: linha[i] for linha in contas["linhas"]}
    assert saldos.get("Carbon") == "-10656.92"


def test_ferramentas_do_crv_e_do_networth():
    _ok(rodar(server.crv_carteira()))
    rodar(server.crv_proventos(agrupar="ativo"))
    rodar(server.crv_operacoes(status="todos"))
    rodar(server.crv_operacoes())
    historico = _ok(rodar(server.nw_historico()))
    assert "patrimonio_brl" in historico["colunas"]


def test_sql_livre_com_limite():
    resultado = rodar(server.sql("cb", "select generate_series(1, 5000) as n"))
    assert resultado["quantidade"] == 2000
    assert resultado["truncado"] is True
