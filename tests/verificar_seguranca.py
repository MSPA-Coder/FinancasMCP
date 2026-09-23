"""Confere, no ambiente configurado (produção inclusive), que o BANCO recusa escrita
e esconde as tabelas sensíveis mesmo com a barreira do servidor contornada.

    FINANCAS_MCP_AMBIENTE=producao .venv/Scripts/python.exe tests/verificar_seguranca.py

Os comandos de escrita têm `where false` ou criam tabela: se o banco os aceitasse,
nenhum dado existente mudaria, e o teste falharia avisando.
"""

from __future__ import annotations

import asyncio

from financas_mcp.conexao import Conexoes, ConsultaRecusada
from financas_mcp.config import carregar

TENTATIVAS = {
    "cb": ["update cash_flow_entry set entry_amount = entry_amount where false",
           "delete from cash_flow_entry where false", "create table invasora (id int)",
           "select count(*) from app_user", "select count(*) from audit_log", "select count(*) from app_setting"],
    "crv": ["update positions set quantity = quantity where false", "select count(*) from users",
            "select count(*) from app_settings"],
    "nw": ["delete from valor_da_foto where false", "select count(*) from auth_user"],
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
