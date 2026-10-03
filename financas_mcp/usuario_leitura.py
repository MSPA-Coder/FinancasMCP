"""Cria (ou recria) o usuário de leitura `mcp_leitura` nos bancos e grava a configuração.

    python -m financas_mcp.usuario_leitura local
    python -m financas_mcp.usuario_leitura producao --host 100.94.82.91 --chave C:\\...\\chave.key

Para cada sistema: gera uma senha nova, aplica o SQL pelo `psql` de dentro do
contêiner do Postgres (localmente ou por SSH no VPS) e grava a senha em
`~/.financas-mcp/config.toml`. A senha nunca é impressa.

O usuário só tem SELECT nas views do esquema `leitura`, que o CB e o CRV
publicam pelas próprias migrações (nenhuma tabela do `public`), nasce com
`default_transaction_read_only` e limite de 5 conexões, e seu caminho de busca
é o esquema `leitura`. Rodar de novo troca a senha e reaplica as permissões,
então também serve para liberar uma view nova que uma migração do CB ou do CRV
tenha acrescentado ao esquema. Se o esquema ainda não existe (o CB ou o CRV não
foi implantado com ele), o script para antes de mexer em qualquer permissão.
"""

from __future__ import annotations

import argparse
import secrets
import subprocess
import tomllib
from pathlib import Path

from .config import caminho_padrao
from .dominio import ESQUEMA, VIEWS

USUARIO = "mcp_leitura"
CONTEINER = {
    "cb": "controle-bancario-postgres-1",
    "crv": "controle-renda-variavel-db-1",
}
PORTA = {"cb": 5202, "crv": 5302}


def _sql(banco: str, senha: str, views: tuple[str, ...]) -> str:
    esperadas = ", ".join(f"'{v}'" for v in views)
    return f"""
\\set ON_ERROR_STOP on
-- Antes de qualquer permissão: sem o esquema (ou com view faltando) o CB ou o CRV
-- implantado é anterior ao contrato, e tirar o acesso às tabelas deixaria o
-- servidor MCP sem nada para ler.
DO $$
DECLARE faltando text;
BEGIN
  IF NOT EXISTS (SELECT FROM pg_namespace WHERE nspname = '{ESQUEMA}') THEN
    RAISE EXCEPTION 'o esquema {ESQUEMA} não existe neste banco: implante a versão do sistema que o cria';
  END IF;
  SELECT string_agg(v, ', ') INTO faltando
  FROM unnest(ARRAY[{esperadas}]) AS v
  WHERE NOT EXISTS (
    SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = '{ESQUEMA}' AND c.relname = v AND c.relkind = 'v'
  );
  IF faltando IS NOT NULL THEN
    RAISE EXCEPTION 'views ausentes no esquema {ESQUEMA}: %', faltando;
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{USUARIO}') THEN
    CREATE ROLE {USUARIO} LOGIN;
  END IF;
END $$;
ALTER ROLE {USUARIO} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 5 PASSWORD '{senha}';
ALTER ROLE {USUARIO} SET default_transaction_read_only = on;
ALTER ROLE {USUARIO} SET statement_timeout = '30s';
ALTER ROLE {USUARIO} SET search_path = {ESQUEMA}, pg_catalog;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {USUARIO};
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {USUARIO};
REVOKE ALL ON SCHEMA public FROM {USUARIO};
REVOKE ALL ON ALL TABLES IN SCHEMA {ESQUEMA} FROM {USUARIO};
GRANT CONNECT ON DATABASE "{banco}" TO {USUARIO};
GRANT USAGE ON SCHEMA {ESQUEMA} TO {USUARIO};
GRANT SELECT ON ALL TABLES IN SCHEMA {ESQUEMA} TO {USUARIO};
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
    parser.add_argument("--sistemas", default="cb,crv")
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
        # Quem cria e concede é um superusuário, pelo socket local do contêiner. Em
        # todos os bancos é o POSTGRES_USER (dono das tabelas); desde 24/09/2026 as
        # aplicações conectam com um papel restrito à parte (`<app>_app`, só DML).
        superusuario = _executar(args, conteiner, (
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select rolname from pg_roles '
            'where rolsuper and rolcanlogin order by rolname = current_user desc limit 1"'
        ))
        _executar(args, conteiner, f'psql -q -U {superusuario} -d "$POSTGRES_DB"', _sql(banco, senha, VIEWS[sistema]))
        sistemas[sistema] = {"porta": PORTA[sistema], "banco": banco, "usuario": USUARIO, "senha": senha}
        print(f"{args.ambiente}/{sistema}: usuário {USUARIO} pronto em {banco} "
              f"(esquema {ESQUEMA}, {len(VIEWS[sistema])} views esperadas)")

    dados["ambiente"] = args.ambiente  # o último configurado vira o padrão
    _gravar(caminho, dados)
    print(f"configuração gravada em {caminho}")


if __name__ == "__main__":
    main()
