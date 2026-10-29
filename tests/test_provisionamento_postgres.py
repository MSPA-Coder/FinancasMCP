"""Executa o SQL de provisionamento em PostgreSQL descartável, sem fontes reais."""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from financas_mcp import conexao
from financas_mcp import usuario_leitura as u
from financas_mcp.config import Banco, Config


@pytest.fixture
def provisionamento(monkeypatch):
    url = os.environ.get("FINANCAS_MCP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("use o serviço postgres-permissoes do Compose quality")
    dados = conninfo_to_dict(url)
    # Impede executar este teste destrutivo contra qualquer banco configurado do operador.
    assert dados["dbname"] == "qa_permissoes" and dados["host"] == "postgres-permissoes"
    usuario = "qa_mcp_" + uuid4().hex[:12]
    monkeypatch.setattr(u, "USUARIO", usuario)
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute("CREATE SCHEMA IF NOT EXISTS leitura")
        admin.execute("CREATE TABLE public.qa_privado (id serial PRIMARY KEY, valor numeric(24,8))")
        admin.execute("INSERT INTO public.qa_privado(valor) VALUES (123.45678901)")
        admin.execute("CREATE VIEW leitura.qa_saldos AS SELECT valor FROM public.qa_privado")
        try:
            yield admin, dados, usuario
        finally:
            admin.execute("DROP VIEW IF EXISTS leitura.qa_saldos")
            admin.execute("DROP TABLE public.qa_privado CASCADE")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (usuario,)).fetchone():
                admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(usuario)))
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(usuario)))


def aplicar(admin, senha="senha-sintetica-inicial", views=("qa_saldos",)):
    # \set é metacomando do psql; o restante é o SQL de produto, sem reescrita.
    texto = "\n".join(linha for linha in u._sql("qa_permissoes", senha, views).splitlines() if not linha.startswith("\\"))
    try:
        admin.execute(texto)
    except psycopg.Error:
        admin.execute("ROLLBACK")
        raise


def test_provisionamento_concede_so_view_e_remove_privilegios_anteriores(provisionamento):
    admin, dados, usuario = provisionamento
    admin.execute(sql.SQL(
        "CREATE ROLE {} LOGIN SUPERUSER CREATEDB CREATEROLE REPLICATION BYPASSRLS"
    ).format(sql.Identifier(usuario)))
    admin.execute(sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(usuario)))
    admin.execute(sql.SQL("GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(usuario)))
    aplicar(admin)
    atributos = admin.execute(
        "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolconnlimit "
        "FROM pg_roles WHERE rolname=%s", (usuario,),
    ).fetchone()
    assert atributos == (False, False, False, False, False, 5)
    with psycopg.connect(**{**dados, "user": usuario, "password": "senha-sintetica-inicial"}, autocommit=True) as leitura:
        assert str(leitura.execute("SELECT valor FROM leitura.qa_saldos").fetchone()[0]) == "123.45678901"
        assert leitura.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
        for comando in ("SELECT * FROM public.qa_privado", "SELECT nextval('public.qa_privado_id_seq')",
                        "CREATE TABLE public.invasao(id int)", "DELETE FROM leitura.qa_saldos"):
            with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
                leitura.execute(comando)
        # Mesmo que o cliente desligue o padrão de transação somente leitura,
        # não recebe os privilégios sobre as tabelas ou sobre a view.
        leitura.execute("SET default_transaction_read_only=off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            leitura.execute("UPDATE leitura.qa_saldos SET valor=0")
    assert admin.execute("SELECT valor FROM public.qa_privado").fetchone()[0] != 0


def test_view_ausente_recusa_antes_de_alterar_papel(provisionamento):
    admin, _, usuario = provisionamento
    admin.execute(sql.SQL("CREATE ROLE {} LOGIN CONNECTION LIMIT 7").format(sql.Identifier(usuario)))
    admin.execute(sql.SQL("GRANT SELECT ON public.qa_privado TO {}").format(sql.Identifier(usuario)))
    with pytest.raises(psycopg.errors.RaiseException):
        aplicar(admin, views=("qa_saldos", "qa_ausente"))
    assert admin.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname=%s", (usuario,)).fetchone()[0] == 7
    assert admin.execute("SELECT has_table_privilege(%s, 'public.qa_privado', 'SELECT')", (usuario,)).fetchone()[0]


def test_reprovisionamento_troca_senha_sem_ampliar_acesso(provisionamento):
    admin, dados, usuario = provisionamento
    aplicar(admin)
    aplicar(admin, senha="senha-sintetica-nova")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(**{**dados, "user": usuario, "password": "senha-sintetica-inicial"})
    with psycopg.connect(**{**dados, "user": usuario, "password": "senha-sintetica-nova"}) as leitura:
        assert leitura.execute("SELECT count(*) FROM leitura.qa_saldos").fetchone()[0] == 1


def test_provisionamento_nao_concede_acesso_a_tabela_extra_no_esquema_leitura(provisionamento):
    admin, _, usuario = provisionamento
    admin.execute("CREATE TABLE leitura.qa_tabela_privada (valor integer)")
    try:
        aplicar(admin)
        assert not admin.execute(
            "SELECT has_table_privilege(%s, 'leitura.qa_tabela_privada', 'SELECT')", (usuario,)
        ).fetchone()[0]
        assert admin.execute("SELECT has_table_privilege(%s, 'leitura.qa_saldos', 'SELECT')", (usuario,)).fetchone()[0]
    finally:
        admin.execute("DROP TABLE leitura.qa_tabela_privada")


def test_provisionamento_recusa_usuario_com_papel_herdado(provisionamento):
    admin, _, usuario = provisionamento
    grupo = usuario + "_grupo"
    admin.execute(sql.SQL("CREATE ROLE {} LOGIN CONNECTION LIMIT 7").format(sql.Identifier(usuario)))
    admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(grupo)))
    admin.execute(sql.SQL("GRANT SELECT ON public.qa_privado TO {}").format(sql.Identifier(grupo)))
    admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(grupo), sql.Identifier(usuario)))
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            aplicar(admin)
        assert admin.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname=%s", (usuario,)).fetchone()[0] == 7
    finally:
        admin.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(grupo), sql.Identifier(usuario)))
        admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(grupo)))
        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(grupo)))


@pytest.mark.parametrize("coluna", [False, True])
def test_provisionamento_recusa_acesso_publico_a_tabela_privada(provisionamento, coluna):
    admin, _, usuario = provisionamento
    privilegio = "SELECT (valor)" if coluna else "SELECT"
    admin.execute(f"GRANT {privilegio} ON public.qa_privado TO PUBLIC")
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            aplicar(admin)
        assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (usuario,)).fetchone()
    finally:
        admin.execute(f"REVOKE {privilegio} ON public.qa_privado FROM PUBLIC")


def test_provisionamento_recusa_funcao_privilegiada_acessivel(provisionamento):
    admin, _, usuario = provisionamento
    admin.execute("""CREATE FUNCTION public.qa_revelar() RETURNS numeric
        LANGUAGE sql SECURITY DEFINER AS $$ SELECT valor FROM public.qa_privado LIMIT 1 $$""")
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            aplicar(admin)
        assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (usuario,)).fetchone()
    finally:
        admin.execute("DROP FUNCTION public.qa_revelar()")


def test_provisionamento_aceita_funcao_de_view_sem_ampliar_privilegios(provisionamento):
    admin, dados, usuario = provisionamento
    admin.execute("CREATE FUNCTION leitura.hoje() RETURNS date LANGUAGE sql STABLE "
                  "AS $$ SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date $$")
    admin.execute("CREATE FUNCTION public.qa_invoker() RETURNS numeric LANGUAGE sql "
                  "AS $$ SELECT valor FROM public.qa_privado LIMIT 1 $$")
    try:
        aplicar(admin)
        with psycopg.connect(**{**dados, "user": usuario, "password": "senha-sintetica-inicial"}) as leitura:
            assert leitura.execute("SELECT leitura.hoje() IS NOT NULL").fetchone()[0]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                leitura.execute("SELECT public.qa_invoker()")
    finally:
        admin.execute("DROP FUNCTION leitura.hoje()")
        admin.execute("DROP FUNCTION public.qa_invoker()")


def test_consulta_cancelada_no_banco_vira_recusa_controlada_e_recupera(provisionamento, monkeypatch):
    admin, dados, usuario = provisionamento
    aplicar(admin)
    monkeypatch.setenv("FINANCAS_MCP_LOCAL_HOST", dados["host"])
    monkeypatch.setattr(conexao, "TEMPO_MAXIMO", "10ms")
    banco = Banco(int(dados.get("port", 5432)), dados["dbname"], usuario, "senha-sintetica-inicial")
    gerente = conexao.Conexoes(Config("sintetico", None, {"cb": banco}))

    async def executar():
        with pytest.raises(conexao.ConsultaRecusada, match="10ms"):
            await gerente.consultar("cb", "SELECT pg_sleep(1)")
        assert (await gerente.consultar("cb", "SELECT count(*) FROM leitura.qa_saldos"))["linhas"] == [[1]]

    asyncio.run(executar())


def test_partes_da_resposta_compartilham_snapshot_durante_escrita(provisionamento, monkeypatch):
    admin, dados, usuario = provisionamento
    aplicar(admin)
    monkeypatch.setenv("FINANCAS_MCP_LOCAL_HOST", dados["host"])
    banco = Banco(int(dados.get("port", 5432)), dados["dbname"], usuario, "senha-sintetica-inicial")
    gerente = conexao.Conexoes(Config("sintetico", None, {"cb": banco}))

    def consultar():
        return asyncio.run(gerente.consultar_conjunto("cb", [
            ("SELECT valor, pg_sleep(2) FROM leitura.qa_saldos", None),
            ("SELECT valor FROM leitura.qa_saldos", None),
        ]))

    with ThreadPoolExecutor(max_workers=1) as pool:
        futuro = pool.submit(consultar)
        prazo = time.monotonic() + 10
        while time.monotonic() < prazo:
            em_leitura = admin.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE datname = current_database() AND application_name = 'financas-mcp' "
                "AND wait_event = 'PgSleep')"
            ).fetchone()[0]
            if em_leitura:
                break
            time.sleep(0.02)
        assert em_leitura, "A consulta precisa estar em curso antes da escrita."
        admin.execute("UPDATE public.qa_privado SET valor = 42")
        primeira, segunda = futuro.result(timeout=10)

    assert primeira["linhas"][0][0] == "123.45678901"
    assert segunda["linhas"] == [["123.45678901"]]
    assert admin.execute("SELECT valor FROM public.qa_privado").fetchone()[0] == 42
