"""Dados mínimos do CB para a suíte com banco do FinancasMCP na CI.

Roda no ambiente do próprio CB, num banco recém-migrado:

    docker compose run --rm -T migrate python - < tests/ci/semente_cb.py

Cria o pouco que os testes exigem: um titular, uma conta comum, o cartão
"Carbon" com compras realizadas e uma fatura importada, e lançamentos em
aberto. O nome Carbon e o saldo vêm da base local do mantenedor, onde
`test_saldo_do_carbon_bate_com_o_do_sistema` nasceu: aqui as compras somam o
mesmo -10.656,92.

Antes de terminar, a semente confere esse saldo pela função que as telas do CB
usam (`decimal_balance_before`, a mesma do contrato v4). O teste do FinancasMCP
confere o mesmo número pela consulta dele. Juntos, provam que o SQL do MCP e a
regra do CB contam igual; se a regra do CB mudar, quem reprova é esta semente,
com a mensagem dizendo onde.

Recusa banco que já tenha alguma conta: não é para rodar fora da CI.
"""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from decimal import Decimal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "financeiro.settings")
django.setup()

from accounts.models import AccountOwner  # noqa: E402
from bank_statements.models import BankStatementImport, BankStatementLine  # noqa: E402
from banking.models import FinancialAccount, FinancialInstitution  # noqa: E402
from core.domain.finance import (  # noqa: E402
    ACCOUNT_KIND_CREDIT_CARD,
    CATEGORY_KIND_MANAGERIAL,
    ENTRY_TYPE_EXPENSE,
    ENTRY_TYPE_INCOME,
    STATUS_PROJECTED,
    STATUS_REALIZED,
    VIEW_REALIZED,
)
from django.db import transaction  # noqa: E402
from django.utils.timezone import localdate  # noqa: E402
from reports.services import decimal_balance_before  # noqa: E402
from transactions.models import CashFlowCategory, CashFlowEntry  # noqa: E402

SALDO_DO_CARBON = Decimal("-10656.92")
COMPRAS_DO_CARBON = (Decimal("4000.00"), Decimal("3656.92"), Decimal("3000.00"))

if FinancialAccount.objects.exists():
    raise SystemExit("semente_cb: recusada, o banco já tem contas. Ela só roda num banco recém-migrado.")

hoje = localdate()
daqui_a_um_mes = hoje + timedelta(days=30)


def realizado(conta, categoria, tipo, valor, descricao):
    return CashFlowEntry(
        account=conta, category=categoria, entry_type=tipo, description=descricao,
        entry_amount=valor, due_date=hoje, status=STATUS_REALIZED,
        realized_date=hoje, realized_amount=valor,
    )


def em_aberto(conta, categoria, valor, descricao):
    return CashFlowEntry(
        account=conta, category=categoria, entry_type=ENTRY_TYPE_EXPENSE, description=descricao,
        entry_amount=valor, due_date=daqui_a_um_mes, status=STATUS_PROJECTED,
    )


with transaction.atomic():
    titular = AccountOwner.objects.create(name="Titular CI")
    banco = FinancialInstitution.objects.create(institution_name="Banco CI", institution_type="Banco")
    conta = FinancialAccount.objects.create(
        owner=titular, institution=banco, account_name="Conta CI", initial_balance=Decimal("5000.00"),
    )
    carbon = FinancialAccount.objects.create(
        owner=titular, institution=banco, account_name="Carbon", account_kind=ACCOUNT_KIND_CREDIT_CARD,
        card_closing_day=5, card_due_day=12, card_payment_account=conta,
    )
    mercado = CashFlowCategory.objects.create(category_name="Mercado CI", kind=CATEGORY_KIND_MANAGERIAL)
    salario = CashFlowCategory.objects.create(category_name="Salário CI", kind=CATEGORY_KIND_MANAGERIAL)

    CashFlowEntry.objects.bulk_create([
        realizado(conta, salario, ENTRY_TYPE_INCOME, Decimal("8000.00"), "Salário"),
        realizado(conta, mercado, ENTRY_TYPE_EXPENSE, Decimal("350.00"), "Mercado do mês"),
        *(realizado(carbon, mercado, ENTRY_TYPE_EXPENSE, valor, f"Compra {i}")
          for i, valor in enumerate(COMPRAS_DO_CARBON, start=1)),
        em_aberto(carbon, mercado, Decimal("500.00"), "Compra a vencer no cartão"),
        em_aberto(conta, mercado, Decimal("1200.00"), "Conta a vencer"),
    ])

    fatura = BankStatementImport.objects.create(
        account=carbon, source_filename="fatura-carbon-ci.csv", row_count=len(COMPRAS_DO_CARBON),
    )
    BankStatementLine.objects.bulk_create([
        BankStatementLine(
            import_batch=fatura, account=carbon, statement_date=hoje, description=f"COMPRA {i}",
            amount=-valor, line_hash=hashlib.sha256(f"ci-carbon-{i}".encode()).hexdigest(),
        )
        for i, valor in enumerate(COMPRAS_DO_CARBON, start=1)
    ])

saldo = decimal_balance_before([carbon.id], hoje + timedelta(days=1), VIEW_REALIZED)
if saldo != SALDO_DO_CARBON:
    raise SystemExit(
        f"semente_cb: o CB calcula {saldo} para o Carbon, e a suíte do MCP espera {SALDO_DO_CARBON}. "
        "A regra do saldo realizado mudou no CB: revise a semente e o teste juntos."
    )
print(f"semente_cb: {CashFlowEntry.objects.count()} lançamentos; saldo realizado do Carbon pelo CB = {saldo}")
