"""Teste manual ponta a ponta: sobe o servidor por stdio, como o Claude Desktop, e chama ferramentas.

    FINANCAS_MCP_AMBIENTE=producao .venv/Scripts/python.exe tests/cliente_stdio.py

Imprime só formas e contagens, não valores: serve para validar a produção sem
despejar dados financeiros no terminal.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

RAIZ = Path(__file__).resolve().parents[1]


async def main() -> None:
    parametros = StdioServerParameters(
        command=sys.executable, args=["-m", "financas_mcp.server"], cwd=str(RAIZ),
        env={**os.environ},
    )
    async with stdio_client(parametros) as (leitura, escrita), ClientSession(leitura, escrita) as sessao:
        inicio = await sessao.initialize()
        print("servidor:", inicio.server_info.name, "| instruções:", bool(inicio.instructions))
        ferramentas = await sessao.list_tools()
        print("ferramentas:", len(ferramentas.tools))
        chamadas = [
            ("sistemas", {}),
            ("cb_contas", {}),
            ("cb_resumo_mensal", {"agrupar": "categoria"}),
            ("cb_faturas", {}),
            ("cb_projecao_de_caixa", {"meses": 6}),
            ("crv_carteira", {}),
            ("crv_proventos", {"agrupar": "mes"}),
            ("nw_historico", {"frequencia": "mes"}),
            ("sql", {"sistema": "cb", "consulta": "select count(*) as lancamentos from cash_flow_entry"}),
            ("sql", {"sistema": "cb", "consulta": "delete from cash_flow_entry"}),
            ("sql", {"sistema": "cb", "consulta": "select count(*) from app_user"}),
            ("sql", {"sistema": "cb", "consulta": "select coluna_que_nao_existe from cash_flow_entry"}),
        ]
        for nome, argumentos in chamadas:
            resposta = await sessao.call_tool(nome, argumentos)
            texto = resposta.content[0].text if resposta.content else ""
            if resposta.is_error:
                print(f"  {nome}: ERRO (esperado nas duas últimas) -> {texto[:110]}")
                continue
            dados = json.loads(texto)
            if nome == "sistemas":
                print(f"  sistemas: ambiente={dados['ambiente']} {list(dados['sistemas'])}")
            elif "quantidade" in dados:
                print(f"  {nome}: {dados['quantidade']} linha(s), colunas={len(dados['colunas'])}")
            else:
                print(f"  {nome}: " + ", ".join(f"{k}={v['quantidade']}" for k, v in dados.items()))


if __name__ == "__main__":
    asyncio.run(main())
