"""Convert a reviewed version-1 ledger bundle to a :class:`TaxStatement`."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from opensteuerauszug.importers.common import (
    CashAccountEntry,
    CashPositionData,
    PositionHints,
    SecurityNameRegistry,
    SecurityPositionData,
    augment_list_of_bank_accounts,
    augment_list_of_securities,
    build_client,
    build_security_payment,
    fold_cash_payments,
    parse_swiss_canton,
)
from opensteuerauszug.model.ech0196 import (
    BankAccountPayment,
    ISINType,
    Institution,
    SecurityStock,
    TaxStatement,
    ValorNumber,
)
from opensteuerauszug.model.position import SecurityPosition

from .models import LedgerBundle, LedgerSecurity

logger = logging.getLogger(__name__)


class LedgerImporter:
    """Import one reviewed provider-neutral ``ledger.json`` file.

    ``period_from`` and ``period_to`` are optional so callers outside the
    command line can rely on the ledger's tax year.  When supplied, they must
    be exactly that calendar year; this prevents a CLI flag from silently
    filtering a reviewed ledger to a different period.
    """

    def __init__(
        self,
        period_from: Optional[date] = None,
        period_to: Optional[date] = None,
    ) -> None:
        self.period_from = period_from
        self.period_to = period_to

    def import_file(self, filename: str | Path) -> TaxStatement:
        """Read *filename*, validate its contract, and return a TaxStatement."""
        path = Path(filename)
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(f"Cannot read ledger file {path}: {error}") from error
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON in ledger file {path} at line {error.lineno}, column {error.colno}: {error.msg}"
            ) from error

        try:
            ledger = LedgerBundle.model_validate(raw_data)
        except ValidationError as error:
            logger.critical("Ledger input %s failed validation: %s", path, error)
            raise ValueError(f"Invalid ledger input {path}: {error}") from error

        period_from = date(ledger.statement.tax_year, 1, 1)
        period_to = date(ledger.statement.tax_year, 12, 31)
        if self.period_from is not None and self.period_from != period_from:
            raise ValueError(
                f"Ledger tax year is {ledger.statement.tax_year}, but period_from is {self.period_from}"
            )
        if self.period_to is not None and self.period_to != period_to:
            raise ValueError(
                f"Ledger tax year is {ledger.statement.tax_year}, but period_to is {self.period_to}"
            )

        statement = TaxStatement(
            minorVersion=1,
            periodFrom=period_from,
            periodTo=period_to,
            taxPeriod=ledger.statement.tax_year,
            institution=Institution(name=ledger.statement.institution_name),
        )
        canton = parse_swiss_canton(ledger.statement.canton)
        if ledger.statement.canton is not None and canton is None:
            raise ValueError(
                f"Invalid Swiss canton in ledger statement: {ledger.statement.canton!r}"
            )
        statement.canton = canton
        client = build_client(
            ledger.statement.client.client_number,
            ledger.statement.client.first_name,
            ledger.statement.client.last_name,
        )
        if client is not None:
            statement.client = [client]

        name_registry = SecurityNameRegistry()
        security_positions: dict[SecurityPosition, SecurityPositionData] = defaultdict(
            lambda: SecurityPositionData({"stocks": [], "payments": []})
        )
        security_hints: dict[SecurityPosition, PositionHints] = {}
        for security in ledger.securities:
            self._append_security(
                security,
                period_to,
                security_positions,
                security_hints,
                name_registry,
            )

        augment_list_of_securities(
            statement,
            security_positions,
            name_registry=name_registry,
            hints_for=lambda position: security_hints[position],
            strict_consistency=True,
        )

        cash_positions: dict[tuple, CashPositionData] = defaultdict(
            lambda: CashPositionData({"stocks": [], "payments": []})
        )
        seed_cash_entries: list[CashAccountEntry] = []
        for account in ledger.cash_accounts:
            cash_key = (account.id, account.currency, "MAIN_CASH")
            for payment in account.payments:
                bank_payment = BankAccountPayment(
                    paymentDate=payment.date,
                    name=payment.description,
                    amountCurrency=payment.currency,
                    amount=payment.gross_amount,
                )
                if payment.swiss_withholding_tax is not None:
                    bank_payment.withHoldingTaxClaim = payment.swiss_withholding_tax
                cash_positions[cash_key]["payments"].append(bank_payment)
            seed_cash_entries.append(
                CashAccountEntry(
                    account_id=account.id,
                    currency=account.currency,
                    closing_balance=account.closing_balance.amount,
                    payments=[],
                    country=account.country,
                    name=account.name,
                    number=account.number,
                )
            )
        augment_list_of_bank_accounts(
            statement, fold_cash_payments(seed_cash_entries, cash_positions)
        )
        return statement

    @staticmethod
    def _append_security(
        security: LedgerSecurity,
        period_to: date,
        positions: dict[SecurityPosition, SecurityPositionData],
        hints: dict[SecurityPosition, PositionHints],
        names: SecurityNameRegistry,
    ) -> None:
        position = SecurityPosition(
            depot=security.depot_id,
            isin=ISINType(security.isin) if security.isin is not None else None,
            valor=ValorNumber(security.valor) if security.valor is not None else None,
            symbol=security.id,
            description=security.name,
        )
        if security.isin is None and security.valor is None:
            logger.warning(
                "Security %s has no ISIN or valor and was explicitly marked manual_review",
                security.id,
            )
        names.update(position, security.name, 10)
        hints[position] = PositionHints(
            security_category=security.category, country=security.country
        )

        positions[position]["stocks"].append(
            SecurityStock(
                referenceDate=period_to + timedelta(days=1),
                mutation=False,
                quantity=security.closing_quantity.quantity,
                unitPrice=(
                    security.closing_quantity.value / security.closing_quantity.quantity
                    if security.closing_quantity.value is not None
                    and security.closing_quantity.quantity != 0
                    else None
                ),
                balance=security.closing_quantity.value,
                balanceCurrency=security.currency,
                quotationType="PIECE",
            )
        )
        for stock in security.stocks:
            quantity = stock.quantity if stock.kind.value == "buy" else -stock.quantity
            positions[position]["stocks"].append(
                SecurityStock(
                    referenceDate=stock.date,
                    mutation=True,
                    quantity=quantity,
                    unitPrice=stock.unit_price,
                    balanceCurrency=stock.currency,
                    quotationType="PIECE",
                    name=stock.description,
                    orderId=stock.order_id,
                )
            )

        for payment_entry in security.payments:
            payment = build_security_payment(
                payment_date=payment_entry.payment_date,
                description=payment_entry.description,
                currency=payment_entry.currency,
                amount=payment_entry.gross_amount,
                broker_label=payment_entry.kind.value,
            )
            payment.exDate = payment_entry.ex_date
            payment.quantity = payment_entry.quantity
            positions[position]["payments"].append(payment)
            if payment_entry.foreign_withholding_tax is not None:
                # Keep tax evidence separate from gross income.  The payment
                # reconciliation calculator deliberately treats withholding
                # rows differently from dividend rows, so putting both values
                # on one eCH payment would hide the source gross amount.
                country = payment_entry.foreign_withholding_country
                tax_name = "Foreign withholding tax"
                if country is not None:
                    tax_name += f" ({country})"
                withholding_payment = build_security_payment(
                    payment_date=payment_entry.payment_date,
                    description=tax_name,
                    currency=payment_entry.currency,
                    amount=-payment_entry.foreign_withholding_tax,
                    broker_label="foreign_withholding_tax",
                    is_withholding=True,
                )
                withholding_payment.exDate = payment_entry.ex_date
                withholding_payment.claimDA1 = payment_entry.foreign_withholding_tax_nonrefundable
                positions[position]["payments"].append(withholding_payment)
            if payment_entry.swiss_withholding_tax is not None:
                withholding_payment = build_security_payment(
                    payment_date=payment_entry.payment_date,
                    description="Swiss withholding tax",
                    currency="CHF",
                    amount=-payment_entry.swiss_withholding_tax,
                    broker_label="swiss_withholding_tax",
                    is_withholding=True,
                )
                withholding_payment.exDate = payment_entry.ex_date
                positions[position]["payments"].append(withholding_payment)
