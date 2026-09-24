"""Chama cada ferramenta uma vez contra um ambiente e diz qual quebrou.

    python -m financas_mcp.verificar producao
    python -m financas_mcp.verificar local

As consultas deste servidor dependem do schema de três sistemas que mudam sem
ele saber. Em 24/09/2026 a `crv_carteira` estava quebrada em produção porque a
revisão 0019 do CRV removeu uma coluna que ela lia -- e nada avisou, porque este
repositório não tem CI e a suíte roda contra os bancos locais. Rodar isto logo
depois de implantar o CB, o CRV ou o NetWorth é o que fecha essa janela.

Tudo é leitura: as mesmas transações `READ ONLY` do servidor, com o mesmo
usuário `mcp_leitura`. Nenhum valor é impresso; só o nome da ferramenta, a
quantidade de linhas e o erro, se houver.
"""

from __future__ import annotations

import asyncio
import os
import sys

from financas_mcp import dominio


def _chamadas(server) -> list[tuple[str, object]]:
    """Cada ferramenta com os argumentos que percorrem os ramos de SQL dela."""
    return [
        ("sistemas", server.sistemas()),
        *[(f"esquema({s})", server.esquema(s)) for s in ("cb", "crv", "nw")],
        ("cb_contas", server.cb_contas()),
        ("cb_resumo_mensal", server.cb_resumo_mensal()),
        ("cb_resumo_mensal(previsto, conta)", server.cb_resumo_mensal(visao="previsto", agrupar="conta")),
        ("cb_lancamentos", server.cb_lancamentos(limite=1)),
        ("cb_categorias", server.cb_categorias()),
        ("cb_orcamento", server.cb_orcamento()),
        ("cb_faturas", server.cb_faturas()),
        ("cb_projecao_de_caixa", server.cb_projecao_de_caixa(meses=1)),
        ("crv_carteira", server.crv_carteira()),
        ("crv_carteira(simuladas)", server.crv_carteira(incluir_simuladas=True)),
        ("crv_proventos(ativo)", server.crv_proventos(agrupar="ativo")),
        ("crv_operacoes(todos)", server.crv_operacoes(status="todos")),
        ("nw_historico", server.nw_historico()),
    ]


def _linhas(resultado) -> str:
    if isinstance(resultado, dict) and "quantidade" in resultado:
        return f"{resultado['quantidade']} linha(s)"
    return "ok"


async def _verificar(server) -> list[str]:
    falhas: list[str] = []
    for nome, corrotina in _chamadas(server):
        try:
            resultado = await corrotina
        except Exception as erro:  # noqa: BLE001 -- o ponto é listar todas as quebras
            falhas.append(nome)
            print(f"FALHA  {nome}: {type(erro).__name__}: {str(erro).splitlines()[0][:200]}")
            continue
        if nome.startswith("esquema("):
            sistema = nome[len("esquema("):-1]
            ausentes = sorted(set(dominio.TABELAS[sistema]) - set(resultado["tabelas"]))
            if ausentes:
                falhas.append(nome)
                print(f"FALHA  {nome}: tabelas liberadas que não existem ou sem SELECT: {ausentes}")
                continue
        print(f"ok     {nome}: {_linhas(resultado)}")
    return falhas


def main(argv: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argv is None else argv
    if len(argumentos) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("uso: python -m financas_mcp.verificar <ambiente>", file=sys.stderr)
        return 2
    os.environ["FINANCAS_MCP_AMBIENTE"] = argumentos[0]
    from financas_mcp import server

    falhas = asyncio.run(_verificar(server))
    print(f"\n{len(falhas)} ferramenta(s) com falha" if falhas else "\ntodas as ferramentas responderam")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
