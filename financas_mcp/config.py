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


@dataclass(frozen=True)
class Ambientes:
    """Todos os ambientes do arquivo e qual vale quando a chamada não escolhe."""

    padrao: str
    configs: dict[str, Config]


def _ler(caminho: Path | None) -> dict:
    return tomllib.loads((caminho or caminho_padrao()).read_text(encoding="utf-8"))


def _montar(dados: dict, ambiente: str) -> Config:
    bloco = dados["ambientes"][ambiente]
    ssh = Ssh(**bloco["ssh"]) if "ssh" in bloco else None
    sistemas = {nome: Banco(**valores) for nome, valores in bloco["sistemas"].items() if nome in SISTEMAS}
    return Config(ambiente=ambiente, ssh=ssh, sistemas=sistemas)


def carregar(caminho: Path | None = None) -> Config:
    dados = _ler(caminho)
    return _montar(dados, os.environ.get("FINANCAS_MCP_AMBIENTE") or dados["ambiente"])


def carregar_todos(caminho: Path | None = None) -> Ambientes:
    """Todos os ambientes do arquivo, para a chamada escolher entre local e produção.

    O padrão continua sendo `ambiente` (ou `FINANCAS_MCP_AMBIENTE`); só a chamada
    que pede outro ambiente por nome o troca, e a resposta diz qual leu.
    """
    dados = _ler(caminho)
    padrao = os.environ.get("FINANCAS_MCP_AMBIENTE") or dados["ambiente"]
    configs = {nome: _montar(dados, nome) for nome in dados["ambientes"]}
    if padrao not in configs:
        raise KeyError(padrao)
    return Ambientes(padrao=padrao, configs=configs)
