"""Sobe o CB e o CRV como na máquina do mantenedor, para a suíte com banco.

    python tests/ci/preparar_bancos.py <checkout do CB> <checkout do CRV>

Para cada sistema, a partir do checkout do `main` dele:

1. cria os segredos que o Compose dele declara e ainda não existem. A CA de
   build é uma CA efêmera, e os outros recebem valores aleatórios. A lista sai
   da configuração do próprio Compose, sem nada escrito aqui: um segredo novo
   no CB ou no CRV não quebra esta CI;
2. constrói a imagem, sobe o PostgreSQL e roda o `migrate` do próprio Compose.
   O esquema é exatamente o que as migrações do `main` produzem;
3. roda a semente (`semente_<sistema>.py`) no contêiner do `migrate`, com o
   código e as dependências do próprio sistema.

Os nomes dos contêineres e as portas publicadas (5202 e 5302) são os do
Compose de cada um, os mesmos que `python -m financas_mcp.usuario_leitura local`
usa. Depois deste script, a CI roda esse comando e a suíte inteira, sem filtro.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent

# Serviço do PostgreSQL e semente de cada sistema. O resto vem do Compose dele.
SISTEMAS = {
    "cb": {"banco": "postgres", "semente": AQUI / "semente_cb.py"},
    "crv": {"banco": "db", "semente": AQUI / "semente_crv.py"},
}


def compose(checkout: Path, *argumentos: str, **opcoes) -> subprocess.CompletedProcess:
    print(f"[{checkout.name}] docker compose {' '.join(argumentos)}", flush=True)
    return subprocess.run(["docker", "compose", *argumentos], cwd=checkout, check=True, **opcoes)


def criar_segredos(checkout: Path) -> None:
    configuracao = json.loads(compose(checkout, "config", "--format", "json", capture_output=True, text=True).stdout)
    for nome, segredo in sorted(configuracao.get("secrets", {}).items()):
        arquivo = Path(segredo["file"]) if "file" in segredo else None
        if arquivo is None or arquivo.exists():
            continue
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        if nome == "local_ca":
            # O Dockerfile instala esta CA no build, para quem compila atrás de
            # um proxy corporativo. Aqui ela só precisa ser um certificado válido.
            chave = arquivo.parent / "ca-efemera.key"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "1",
                 "-keyout", str(chave), "-out", str(arquivo), "-subj", "/CN=FinancasMCP CI CA efemera"],
                check=True, capture_output=True,
            )
            chave.unlink()
        else:
            arquivo.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
        # Legível pelos usuários dos contêineres (o PostgreSQL roda como uid 70):
        # o Compose fora do Swarm monta o arquivo com o modo do host. Valores
        # efêmeros, que só existem nesta execução.
        os.chmod(arquivo, 0o644)
        print(f"[{checkout.name}] segredo efêmero: {nome}", flush=True)


def preparar(sistema: str, checkout: Path) -> None:
    dados = SISTEMAS[sistema]
    criar_segredos(checkout)
    compose(checkout, "build", "migrate")
    compose(checkout, "up", "--detach", "--wait", dados["banco"])
    compose(checkout, "run", "--rm", "migrate")
    with dados["semente"].open("rb") as semente:
        compose(checkout, "run", "--rm", "-T", "migrate", "python", "-", stdin=semente)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    for sistema, caminho in zip(SISTEMAS, sys.argv[1:], strict=True):
        preparar(sistema, Path(caminho).resolve())


if __name__ == "__main__":
    main()
