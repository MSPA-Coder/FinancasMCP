"""Túnel SSH e execução de consultas, sempre somente leitura.

Os bancos de produção só escutam em 127.0.0.1 do VPS. Uma conexão SSH (pela
Tailscale) abre um encaminhamento local para cada porta; o túnel é criado na
primeira consulta e refeito se cair.

A proteção de verdade é o usuário do banco: ele só tem SELECT nas views do
esquema `leitura` (nenhuma tabela) e nasce com `default_transaction_read_only`.
Aqui vão as camadas
de cima, para uma consulta errada falhar cedo e barata:

- a transação é aberta como READ ONLY, com tempo máximo;
- só um comando, e começando por SELECT ou WITH;
- o resultado é cortado em `LIMITE_DE_LINHAS`, com aviso.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from decimal import Decimal
from typing import Any

import asyncssh
import psycopg
from mcp.server.mcpserver.exceptions import ToolError

from .config import Banco, Config

# O asyncssh narra cada conexão em INFO; no stderr do Claude Desktop isso é ruído.
logging.getLogger("asyncssh").setLevel(logging.WARNING)

LIMITE_DE_LINHAS = 2000
TEMPO_MAXIMO = "15s"
_COMECO_PERMITIDO = re.compile(r"^\s*(select|with|values|table|explain)\b", re.IGNORECASE)


class ConsultaRecusada(ToolError):
    """Recusa ou erro de consulta; a mensagem chega ao modelo para ele corrigir."""


def validar_consulta(sql: str) -> str:
    """Barreira de cima: um comando só, de leitura. O banco é a de baixo."""
    texto = sql.strip().rstrip(";").strip()
    if not texto:
        raise ConsultaRecusada("Consulta vazia.")
    if ";" in _sem_literais(texto):
        raise ConsultaRecusada("Envie um comando por vez, sem ';' no meio.")
    if not _COMECO_PERMITIDO.match(texto):
        raise ConsultaRecusada("Só consultas de leitura: SELECT, WITH, VALUES, TABLE ou EXPLAIN.")
    return texto


def _sem_literais(texto: str) -> str:
    """O texto sem strings e comentários, para procurar ';' de verdade."""
    saida, i, n = [], 0, len(texto)
    while i < n:
        c = texto[i]
        if c in "'\"":
            # String ('...', com '' como aspa escapada) ou identificador ("...").
            i += 1
            while i < n:
                if texto[i] == c:
                    if i + 1 < n and texto[i + 1] == c:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        elif texto.startswith("--", i):
            fim = texto.find("\n", i)
            i = n if fim < 0 else fim
        elif texto.startswith("/*", i):
            fim = texto.find("*/", i + 2)
            i = n if fim < 0 else fim + 2
        else:
            saida.append(c)
            i += 1
    return "".join(saida)


def _valor(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _executar(banco: Banco, porta: int, sql: str, parametros, limite: int) -> tuple[list[str], list]:
    with psycopg.connect(
        host="127.0.0.1", port=porta, dbname=banco.banco, user=banco.usuario, password=banco.senha,
        connect_timeout=15, application_name="financas-mcp", autocommit=True,
    ) as conexao:
        conexao.execute("BEGIN READ ONLY")
        conexao.execute(f"SET LOCAL statement_timeout = '{TEMPO_MAXIMO}'")
        with conexao.cursor() as cursor:
            cursor.execute(sql, parametros)
            colunas = [c.name for c in cursor.description or []]
            linhas = cursor.fetchmany(limite + 1)
        conexao.execute("ROLLBACK")
    return colunas, linhas


class Conexoes:
    """Um túnel por processo, uma conexão ao banco por consulta."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._ssh: asyncssh.SSHClientConnection | None = None
        self._portas: dict[str, int] = {}
        self._trava = asyncio.Lock()

    async def _porta_local(self, sistema: str, banco: Banco) -> int:
        ssh = self.config.ssh
        if ssh is None:
            return banco.porta
        async with self._trava:
            if self._ssh is None or self._ssh.is_closed():
                self._ssh = await asyncssh.connect(
                    ssh.host,
                    username=ssh.usuario,
                    client_keys=[ssh.chave],
                    known_hosts=asyncssh.import_known_hosts(f"{ssh.host} {ssh.chave_do_host}"),
                    keepalive_interval=30,
                )
                self._portas.clear()
            if sistema not in self._portas:
                ouvinte = await self._ssh.forward_local_port("127.0.0.1", 0, "127.0.0.1", banco.porta)
                self._portas[sistema] = ouvinte.get_port()
            return self._portas[sistema]

    async def consultar(self, sistema: str, sql: str, parametros: dict | tuple | None = None,
                        limite: int = LIMITE_DE_LINHAS) -> dict:
        banco = self.config.sistemas.get(sistema)
        if banco is None:
            raise ConsultaRecusada(f"Sistema '{sistema}' não configurado. Use: {', '.join(self.config.sistemas)}.")
        porta = await self._porta_local(sistema, banco)
        # psycopg síncrono numa thread: o assíncrono não roda no laço padrão do
        # Windows (Proactor), que é o do servidor MCP. O túnel segue no laço.
        try:
            colunas, linhas = await asyncio.to_thread(_executar, banco, porta, sql, parametros, limite)
        except psycopg.errors.InsufficientPrivilege as exc:
            raise ConsultaRecusada(
                "Sem permissão: só as views do esquema `leitura` estão liberadas para leitura (usuários, "
                f"senhas, sessões e auditoria ficam de fora). Veja `esquema('{sistema}')`."
            ) from exc
        except psycopg.errors.UndefinedTable as exc:
            raise ConsultaRecusada(
                f"Essa tabela ou view não existe para leitura. As tabelas dos sistemas não são lidas "
                f"diretamente: consulte as views do esquema `leitura` (veja `esquema('{sistema}')`)."
            ) from exc
        except psycopg.errors.ReadOnlySqlTransaction as exc:
            raise ConsultaRecusada("Somente leitura: o banco recusou a alteração.") from exc
        except psycopg.errors.QueryCanceled as exc:
            raise ConsultaRecusada(f"A consulta passou de {TEMPO_MAXIMO}; agregue ou filtre mais.") from exc
        except psycopg.Error as exc:
            diag = exc.diag
            detalhe = " ".join(filter(None, [diag.message_primary, diag.message_hint and f"Dica: {diag.message_hint}"]))
            raise ConsultaRecusada(f"Erro do banco: {detalhe or exc}") from exc
        truncado = len(linhas) > limite
        return {
            "colunas": colunas,
            "linhas": [[_valor(v) for v in linha] for linha in linhas[:limite]],
            "quantidade": min(len(linhas), limite),
            "truncado": truncado,
        }
