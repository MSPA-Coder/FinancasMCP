"""Precisão, parametrização e recusa são verificadas contra PostgreSQL real."""

import asyncio
from datetime import date

import pytest

from financas_mcp import server, verificar
from financas_mcp.conexao import ConsultaRecusada, validar_consulta
from financas_mcp.config import carregar


@pytest.fixture(autouse=True)
def conexoes_independentes():
    server._conexoes = None
    yield
    server._conexoes = None


@pytest.mark.banco_local
def test_serializacao_preserva_centavos_grandes_datas_e_nulos():
    resultado = asyncio.run(server.conexoes().consultar(
        "cb", "select %s::numeric(30, 8) as valor, %s::date as dia, null as ausente",
        ("12345678901234567890.12345678", date(2026, 10, 18)),
    ))
    assert resultado["linhas"] == [["12345678901234567890.12345678", "2026-10-18", None]]
    assert resultado["quantidade"] == 1
    assert not resultado["truncado"]


@pytest.mark.banco_local
@pytest.mark.parametrize(("quantidade", "limite", "truncado"), [(0, 2, False), (2, 2, False), (3, 2, True)])
def test_limite_distingue_resultado_completo_de_cortado(quantidade, limite, truncado):
    resultado = asyncio.run(server.conexoes().consultar(
        "cb", "select generate_series(1, %s) as n", (quantidade,), limite=limite,
    ))
    assert resultado["quantidade"] == min(quantidade, limite)
    assert resultado["truncado"] is truncado
    assert resultado["linhas"] == [[i] for i in range(1, min(quantidade, limite) + 1)]


@pytest.mark.banco_local
def test_parametro_parece_sql_mas_continua_sendo_dado():
    texto = "'; DELETE FROM leitura.lancamento; --"
    resultado = asyncio.run(server.conexoes().consultar("cb", "select %s::text", (texto,)))
    assert resultado["linhas"] == [[texto]]


@pytest.mark.banco_local
@pytest.mark.parametrize("consulta", [
    "select * from leitura.view_inexistente_validacao",
    "select 1 / 0",
    "select 1 into public.tabela_invasora_validacao",
    "select * from public.app_user",
])
def test_erro_ou_escrita_recusada_nao_envenena_consulta_seguinte(consulta):
    async def cenario():
        conexoes = server.conexoes()
        with pytest.raises(ConsultaRecusada):
            await conexoes.consultar("cb", consulta)
        return await conexoes.consultar("cb", "select 7 as valido")

    assert asyncio.run(cenario())["linhas"] == [[7]]


def test_sistema_nao_configurado_e_recusado_sem_abrir_conexao():
    with pytest.raises(ConsultaRecusada):
        asyncio.run(server.conexoes().consultar("inexistente", "select 1"))


@pytest.mark.parametrize("sql", [
    "select 'a'';b' as valor;",
    'select "identificador;com;separador" from leitura.conta',
    "select 1 /* ; apenas comentário */",
    "select 1 -- ; comentário\n",
])
def test_sql_com_separador_em_literal_ou_comentario_e_aceito(sql):
    assert validar_consulta(sql)


@pytest.mark.parametrize("sql", [
    "select 'literal;seguro'; delete from leitura.conta",
    "select 1 /* ; comentário */; select 2",
    "select 1 -- ; comentário\n; select 2",
])
def test_literais_nao_escondem_segundo_comando(sql):
    with pytest.raises(ConsultaRecusada):
        validar_consulta(sql)


def test_configuracao_escolhe_ambiente_e_ignora_sistema_nao_publicado(tmp_path, monkeypatch):
    caminho = tmp_path / "config.toml"
    caminho.write_text('''ambiente = "local"
[ambientes.local.sistemas.cb]
porta = 1
banco = "local"
usuario = "leitor"
senha = "sintetica"
[ambientes.ensaio.sistemas.cb]
porta = 5432
banco = "ensaio"
usuario = "leitor"
senha = "sintetica"
[ambientes.ensaio.sistemas.interno]
porta = 9999
banco = "nao-publicado"
usuario = "leitor"
senha = "sintetica"
''')
    monkeypatch.setenv("FINANCAS_MCP_AMBIENTE", "ensaio")
    config = carregar(caminho)
    assert config.ambiente == "ensaio"
    assert config.ssh is None
    assert set(config.sistemas) == {"cb"}
    assert config.sistemas["cb"].banco == "ensaio"


@pytest.mark.banco_local
def test_verificador_exercita_todas_as_ferramentas_sem_falha(capsys):
    assert asyncio.run(verificar._verificar(server)) == []
    saida = capsys.readouterr().out
    assert "FALHA" not in saida
    assert "12345678901234567890" not in saida
