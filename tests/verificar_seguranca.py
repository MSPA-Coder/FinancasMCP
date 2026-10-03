"""Confere, no ambiente configurado (produção inclusive), que o BANCO recusa escrita
e esconde toda tabela dos sistemas (só as views do esquema `leitura` são legíveis)
mesmo com a barreira do servidor contornada.

    FINANCAS_MCP_AMBIENTE=producao .venv/Scripts/python.exe tests/verificar_seguranca.py

Os comandos de escrita têm `where false` ou criam tabela: se o banco os aceitasse,
nenhum dado existente mudaria, e o teste falharia avisando.
"""

from __future__ import annotations

import asyncio

from financas_mcp.conexao import Conexoes, ConsultaRecusada
from financas_mcp.config import carregar

TENTATIVAS = {
    "cb": ["update leitura.operacao set status = status where false",
           "delete from leitura.operacao where false", "create table invasora (id int)",
           "create table leitura.invasora (id int)",
           "select count(*) from public.cash_flow_entry", "select count(*) from public.app_user",
           "select count(*) from public.audit_log", "select count(*) from public.app_setting"],
    "crv": ["update leitura.operacao set status = status where false",
            "create table leitura.invasora (id int)",
            "select count(*) from public.positions", "select count(*) from public.users",
            "select count(*) from public.app_settings"],
}


async def main() -> None:
    conexoes = Conexoes(carregar())
    print("ambiente:", conexoes.config.ambiente)
    falhas = 0
    for sistema, comandos in TENTATIVAS.items():
        leitura = await conexoes.consultar(sistema, "select current_user, current_setting('transaction_read_only')")
        print(f"{sistema}: conectado como {leitura['linhas'][0][0]}, read_only={leitura['linhas'][0][1]}")
        for comando in comandos:
            try:
                await conexoes.consultar(sistema, comando)  # sem validar_consulta, de propósito
            except ConsultaRecusada as exc:
                print(f"   recusado: {comando[:45]:<45} -> {str(exc)[:60]}")
            else:
                falhas += 1
                print(f"   ACEITO (FALHA): {comando}")
    print("RESULTADO:", "OK" if falhas == 0 else f"{falhas} FALHA(S)")


if __name__ == "__main__":
    asyncio.run(main())
