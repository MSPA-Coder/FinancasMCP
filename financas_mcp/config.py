"""Configuração: onde estão os bancos e com que credencial se lê.

O arquivo fica FORA do repositório, em `%USERPROFILE%\\.financas-mcp\\config.toml`
(ou no caminho de `FINANCAS_MCP_CONFIG`), porque guarda as senhas dos usuários
de leitura. Formato:

    ambiente = "producao"          # qual bloco abaixo vale

    [ambientes.producao.ssh]       # sem este bloco, conexão direta
    host = "100.94.82.91"
    usuario = "ubuntu"
    chave = 'C:\\caminho\\da\\chave.key'
    chave_do_host = "ssh-ed25519 AAAA..."   # fixa o servidor: sem MITM

    [ambientes.producao.sistemas.cb]
    porta = 5202                   # porta do Postgres no host (127.0.0.1)
    banco = "controle_bancario"
    usuario = "mcp_leitura"
    senha = "..."

`FINANCAS_MCP_AMBIENTE` sobrepõe `ambiente`, para testar no local sem editar.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

SISTEMAS = ("cb", "crv")


@dataclass(frozen=True)
class Ssh:
    host: str
    usuario: str
    chave: str
    chave_do_host: str


@dataclass(frozen=True)
class Banco:
    porta: int
    banco: str
    usuario: str
    senha: str


@dataclass(frozen=True)
class Config:
    ambiente: str
    ssh: Ssh | None
    sistemas: dict[str, Banco]


def caminho_padrao() -> Path:
    return Path(os.environ.get("FINANCAS_MCP_CONFIG") or Path.home() / ".financas-mcp" / "config.toml")


def carregar(caminho: Path | None = None) -> Config:
    dados = tomllib.loads((caminho or caminho_padrao()).read_text(encoding="utf-8"))
    ambiente = os.environ.get("FINANCAS_MCP_AMBIENTE") or dados["ambiente"]
    bloco = dados["ambientes"][ambiente]
    ssh = Ssh(**bloco["ssh"]) if "ssh" in bloco else None
    sistemas = {nome: Banco(**valores) for nome, valores in bloco["sistemas"].items() if nome in SISTEMAS}
    return Config(ambiente=ambiente, ssh=ssh, sistemas=sistemas)
