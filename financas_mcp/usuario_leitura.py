"""Cria (ou recria) o usuário de leitura `mcp_leitura` nos bancos e grava a configuração.

    python -m financas_mcp.usuario_leitura local
    python -m financas_mcp.usuario_leitura producao --host 100.94.82.91 --chave C:\\...\\chave.key

Para cada sistema: gera uma senha nova, aplica o SQL pelo `psql` de dentro do
contêiner do Postgres (localmente ou por SSH no VPS) e grava a senha em
`~/.financas-mcp/config.toml`. A senha nunca é impressa.

O usuário só tem SELECT nas tabelas de `dominio.TABELAS`, nasce com
`default_transaction_read_only` e limite de 5 conexões. Rodar de novo troca a
senha e reaplica as permissões, então também serve para liberar uma tabela
nova depois de acrescentá-la a `TABELAS`.
"""

from __future__ import annotations

import argparse
import secrets
import subprocess
import tomllib
from pathlib import Path

from .config import caminho_padrao
from .dominio import TABELAS

USUARIO = "mcp_leitura"
CONTEINER = {
    "cb": "controle-bancario-postgres-1",
    "crv": "controle-renda-variavel-db-1",
    "nw": "networth-postgres-1",
}
PORTA = {"cb": 5202, "crv": 5302, "nw": 5702}


def _sql(banco: str, senha: str, tabelas: tuple[str, ...]) -> str:
    lista = ", ".join(f'public."{t}"' for t in tabelas)
    return f"""
\\set ON_ERROR_STOP on
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{USUARIO}') THEN
    CREATE ROLE {USUARIO} LOGIN;
  END IF;
END $$;
ALTER ROLE {USUARIO} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 5 PASSWORD '{senha}';
ALTER ROLE {USUARIO} SET default_transaction_read_only = on;
ALTER ROLE {USUARIO} SET statement_timeout = '30s';
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {USUARIO};
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {USUARIO};
REVOKE CREATE ON SCHEMA public FROM {USUARIO};
GRANT CONNECT ON DATABASE "{banco}" TO {USUARIO};
GRANT USAGE ON SCHEMA public TO {USUARIO};
GRANT SELECT ON {lista} TO {USUARIO};
"""


def _comando(ambiente: str, ssh: list[str], conteiner: str, remoto: str) -> list[str]:
    base = f"docker exec -i {conteiner} sh -c '{remoto}'"
    return [*ssh, base] if ambiente == "producao" else ["sh", "-c", base]


def _executar(args, conteiner: str, remoto: str, entrada: str | None = None) -> str:
    ssh = ["ssh", "-i", args.chave, "-o", "BatchMode=yes", f"{args.usuario}@{args.host}"] if args.ambiente == "producao" else []
    # Bytes, não texto: no Windows o modo texto trocaria \n por \r\n na entrada,
    # e o psql leria o \r como parte dos comandos.
    feito = subprocess.run(
        _comando(args.ambiente, ssh, conteiner, remoto),
        input=entrada.encode("utf-8") if entrada else None, capture_output=True, check=False,
    )
    if feito.returncode != 0:
        raise SystemExit(f"falhou em {conteiner}: {feito.stderr.decode('utf-8', 'replace').strip()[-500:]}")
    return feito.stdout.decode("utf-8", "replace").strip()


def _toml(valor) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, int):
        return str(valor)
    return "'" + str(valor).replace("'", "") + "'" if "\\" in str(valor) else '"' + str(valor).replace('"', '\\"') + '"'


def _gravar(caminho: Path, dados: dict) -> None:
    linhas = [f"ambiente = {_toml(dados['ambiente'])}", ""]
    for nome, bloco in dados["ambientes"].items():
        if "ssh" in bloco:
            linhas.append(f"[ambientes.{nome}.ssh]")
            linhas += [f"{k} = {_toml(v)}" for k, v in bloco["ssh"].items()]
            linhas.append("")
        for sistema, valores in bloco.get("sistemas", {}).items():
            linhas.append(f"[ambientes.{nome}.sistemas.{sistema}]")
            linhas += [f"{k} = {_toml(v)}" for k, v in valores.items()]
            linhas.append("")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("\n".join(linhas), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ambiente", choices=["local", "producao"])
    parser.add_argument("--sistemas", default="cb,crv,nw")
    parser.add_argument("--host")
    parser.add_argument("--usuario", default="ubuntu")
    parser.add_argument("--chave")
    parser.add_argument("--chave-do-host", help="linha 'tipo base64' da chave pública do servidor")
    args = parser.parse_args()
    if args.ambiente == "producao" and not (args.host and args.chave and args.chave_do_host):
        parser.error("produção exige --host, --chave e --chave-do-host")

    caminho = caminho_padrao()
    vazio = {"ambiente": args.ambiente, "ambientes": {}}
    dados = tomllib.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else vazio
    bloco = dados["ambientes"].setdefault(args.ambiente, {})
    if args.ambiente == "producao":
        # Só "tipo base64": a saída do ssh-keyscan traz comentários e quebras de linha.
        chave_do_host = " ".join(args.chave_do_host.split()[-2:])
        bloco["ssh"] = {"host": args.host, "usuario": args.usuario, "chave": args.chave, "chave_do_host": chave_do_host}
    sistemas = bloco.setdefault("sistemas", {})

    for sistema in args.sistemas.split(","):
        conteiner = CONTEINER[sistema]
        banco = _executar(args, conteiner, 'printenv POSTGRES_DB')
        senha = secrets.token_urlsafe(32)
        # Quem cria e concede é um superusuário, pelo socket local do contêiner.
        # No CB o dono das tabelas não é superusuário (endurecimento) e existe o
        # `postgres`; no CRV e no NetWorth o próprio POSTGRES_USER é o superusuário.
        superusuario = _executar(args, conteiner, (
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select rolname from pg_roles '
            'where rolsuper and rolcanlogin order by rolname = current_user desc limit 1"'
        ))
        _executar(args, conteiner, f'psql -q -U {superusuario} -d "$POSTGRES_DB"', _sql(banco, senha, TABELAS[sistema]))
        sistemas[sistema] = {"porta": PORTA[sistema], "banco": banco, "usuario": USUARIO, "senha": senha}
        print(f"{args.ambiente}/{sistema}: usuário {USUARIO} pronto em {banco} ({len(TABELAS[sistema])} tabelas)")

    dados["ambiente"] = args.ambiente  # o último configurado vira o padrão
    _gravar(caminho, dados)
    print(f"configuração gravada em {caminho}")


if __name__ == "__main__":
    main()
