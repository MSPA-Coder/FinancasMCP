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
