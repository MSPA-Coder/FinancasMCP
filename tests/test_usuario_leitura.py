import subprocess
import sys
import tomllib

import pytest

from financas_mcp import usuario_leitura as u
from financas_mcp.usuario_leitura import _comando


def test_comando_local_chama_docker_sem_shell_no_host():
    assert _comando("local", [], "controle-bancario-postgres-1", "printenv POSTGRES_DB") == [
        "docker", "exec", "-i", "controle-bancario-postgres-1", "sh", "-c", "printenv POSTGRES_DB",
    ]


def test_comando_remoto_usa_shell_no_servidor():
    ssh = ["ssh", "-i", "chave.key", "usuario@host"]
    assert _comando("producao", ssh, "postgres-1", "printenv POSTGRES_DB") == [
        *ssh, "docker exec -i postgres-1 sh -c 'printenv POSTGRES_DB'",
    ]


@pytest.mark.parametrize("texto", [r"C:\Users\O'Neil\chave.key", 'aspas " e\nlinha', "ação e ç"])
def test_serializacao_toml_preserva_caminho_e_conteudo(texto):
    assert tomllib.loads("valor = " + u._toml(texto))["valor"] == texto


@pytest.mark.parametrize("falhar", [False, True])
@pytest.mark.parametrize("ambiente", ["local", "producao"])
def test_provisionamento_cli_so_grava_config_apos_sucesso_sem_imprimir_senha(tmp_path, monkeypatch, capsys, falhar, ambiente):
    caminho = tmp_path / "config.toml"
    anterior = {"ambiente": "preservado", "ambientes": {"preservado": {"sistemas": {
        "crv": {"porta": 5302, "banco": "anterior", "usuario": "leitura", "senha": "sintetica-anterior"},
    }}}}
    u._gravar(caminho, anterior)
    original = caminho.read_bytes()
    monkeypatch.setenv("FINANCAS_MCP_CONFIG", str(caminho))
    argumentos = ["provisionar", ambiente, "--sistemas", "cb"]
    if ambiente == "producao":
        argumentos += ["--host", "qa.invalid", "--chave", "qa.key", "--chave-do-host", "ssh-ed25519 AAAA"]
    monkeypatch.setattr(sys, "argv", argumentos)
    entradas = []

    def executar(comando, **opcoes):
        remoto = comando[-1]
        entrada = opcoes.get("input")
        if entrada:
            entradas.append(entrada)
            return subprocess.CompletedProcess(comando, int(falhar), b"", b"Falha sintetica" if falhar else b"")
        saida = b"qa_sintetico" if "printenv POSTGRES_DB" in remoto else b"qa_admin"
        return subprocess.CompletedProcess(comando, 0, saida, b"")

    monkeypatch.setattr(subprocess, "run", executar)
    if falhar:
        with pytest.raises(SystemExit):
            u.main()
        assert caminho.read_bytes() == original
    else:
        u.main()
        gravado = tomllib.loads(caminho.read_text())
        assert gravado["ambientes"]["preservado"] == anterior["ambientes"]["preservado"]
        novo = gravado["ambientes"][ambiente]["sistemas"]["cb"]
        assert novo["banco"] == "qa_sintetico" and novo["usuario"] == "mcp_leitura"
        assert novo["senha"] not in capsys.readouterr().out
        assert novo["senha"].encode() in entradas[0]


def test_provisionamento_remoto_exige_parametros_antes_de_qualquer_comando(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["provisionar", "producao"])
    with pytest.raises(SystemExit) as erro:
        u.main()
    assert erro.value.code == 2
