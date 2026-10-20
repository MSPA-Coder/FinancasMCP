"""Dados mínimos do CRV para a suíte com banco do FinancasMCP na CI.

Roda no ambiente do próprio CRV, num banco recém-migrado:

    docker compose run --rm -T migrate python - < tests/ci/semente_crv.py

Cria uma carteira real com uma posição aberta em ação e a cotação dela, que é
o que `crv_carteira` precisa para devolver linha. As outras ferramentas do CRV
rodam sobre tabelas vazias: o que a suíte confere nelas é que a consulta
ainda casa com o esquema.

Recusa banco que já tenha algum usuário: não é para rodar fora da CI.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app import create_app, db
from app.models import Broker, Market, Portfolio, Position, Quote, Side, Ticker, User
from sqlalchemy import func, select

app = create_app()
with app.app_context():
    if db.session.scalar(select(func.count()).select_from(User)):
        raise SystemExit("semente_crv: recusada, o banco já tem usuários. Ela só roda num banco recém-migrado.")
    db.session.rollback()

    with db.session.begin():
        # Hash que nenhuma senha produz: este usuário não entra na aplicação.
        dono = User(username="ci-financas-mcp", password_hash="!")
        corretora = Broker(name="Corretora CI", acronym="CI")
        ativo = Ticker(
            symbol="PETR4", trading_name="Petrobras PN", market=Market.B3, rtd_market_code="B", currency="BRL",
        )
        db.session.add_all([dono, corretora, ativo])
        db.session.flush()
        carteira = Portfolio(name="Carteira CI", owner_id=dono.id, currency="BRL")
        db.session.add(carteira)
        db.session.flush()
        db.session.add_all([
            Position(
                owner_id=dono.id, portfolio_id=carteira.id, broker_id=corretora.id, ticker_id=ativo.id,
                quantity=Decimal("100"), average_cost=Decimal("30.00"), side=Side.BUY, opened_on=date.today(),
            ),
            Quote(
                ticker_id=ativo.id, last_price=Decimal("32.50"), previous_close=Decimal("32.00"),
                observed_at=datetime.now(UTC),
            ),
        ])

    posicoes = db.session.scalar(select(func.count()).select_from(Position))
    print(f"semente_crv: {posicoes} posição aberta, com cotação")
