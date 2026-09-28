"""Strict input contract for version 1 ``ledger.json`` files.

The ledger is intentionally a small, source-neutral audit artefact.  It is
not a broker export format: source-specific extraction belongs before this
schema.  In particular, accepting JSON numbers here would silently turn a
source amount into a binary floating-point value, so all Decimal values are
required to be JSON strings.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class LedgerModel(BaseModel):
    """Base model that rejects undeclared fields in the auditable contract."""

    model_config = ConfigDict(extra="forbid")


def _decimal_from_string(value: Any, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON string, never a JSON number")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{field_name} must be a valid decimal string") from error


def _validate_currency(value: str, field_name: str) -> str:
    if not _CURRENCY_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a three-letter uppercase ISO-4217 code")
    return value


def _validate_country(value: str, field_name: str) -> str:
    if not _COUNTRY_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a two-letter uppercase country code")
    return value


class ClosingBalance(LedgerModel):
    date: date
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def amount_is_decimal_string(cls, value: Any) -> Decimal:
        return _decimal_from_string(value, "closing_balance.amount")


class ClosingQuantity(LedgerModel):
    date: date
    quantity: Decimal
    value: Optional[Decimal] = None

    @field_validator("quantity", "value", mode="before")
    @classmethod
    def values_are_decimal_strings(cls, value: Any, info: Any) -> Optional[Decimal]:
        if value is None:
            return None
        return _decimal_from_string(value, f"closing_quantity.{info.field_name}")

    @field_validator("quantity")
    @classmethod
    def quantity_is_not_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("closing_quantity.quantity cannot be negative")
        return value

    @field_validator("value")
    @classmethod
    def value_is_not_negative(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and value < 0:
            raise ValueError("closing_quantity.value cannot be negative")
        return value

    @model_validator(mode="after")
    def zero_quantity_requires_zero_value(self) -> "ClosingQuantity":
        if self.quantity == 0 and self.value not in (None, Decimal("0")):
            raise ValueError("closing_quantity.value must be zero when quantity is zero")
        return self


class CashPaymentKind(str, Enum):
    INTEREST = "interest"


class SecurityPaymentKind(str, Enum):
    DIVIDEND = "dividend"
    DISTRIBUTION = "distribution"
    INTEREST = "interest"


class StockKind(str, Enum):
    BUY = "buy"
    SELL = "sell"


class CashPayment(LedgerModel):
    date: date
    kind: CashPaymentKind
    description: str = Field(min_length=1, max_length=200)
    gross_amount: Decimal
    currency: str
    swiss_withholding_tax: Optional[Decimal] = None

    @field_validator("gross_amount", "swiss_withholding_tax", mode="before")
    @classmethod
    def amounts_are_decimal_strings(cls, value: Any, info: Any) -> Optional[Decimal]:
        if value is None:
            return None
        return _decimal_from_string(value, info.field_name)

    @field_validator("currency")
    @classmethod
    def currency_is_valid(cls, value: str) -> str:
        return _validate_currency(value, "currency")

    @field_validator("gross_amount", "swiss_withholding_tax")
    @classmethod
    def amounts_are_not_negative(cls, value: Optional[Decimal], info: Any) -> Optional[Decimal]:
        if value is not None and value < 0:
            raise ValueError(f"{info.field_name} cannot be negative")
        return value


class CashAccount(LedgerModel):
    id: str = Field(min_length=1)
    number: Optional[str] = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=40)
    country: str
    currency: str
    closing_balance: ClosingBalance
    payments: list[CashPayment] = Field(default_factory=list)

    @field_validator("country")
    @classmethod
    def country_is_valid(cls, value: str) -> str:
        return _validate_country(value, "country")

    @field_validator("currency")
    @classmethod
    def currency_is_valid(cls, value: str) -> str:
        return _validate_currency(value, "currency")

    @model_validator(mode="after")
    def payment_currencies_match_account(self) -> "CashAccount":
        for index, payment in enumerate(self.payments):
            if payment.currency != self.currency:
                raise ValueError(
                    f"payments[{index}].currency must equal cash account currency {self.currency}"
                )
        return self


class SecurityStockEntry(LedgerModel):
    date: date
    kind: StockKind
    quantity: Decimal
    unit_price: Decimal
    currency: str
    description: str = Field(min_length=1, max_length=200)
    order_id: Optional[str] = Field(default=None, min_length=1)

    @field_validator("quantity", "unit_price", mode="before")
    @classmethod
    def values_are_decimal_strings(cls, value: Any, info: Any) -> Decimal:
        return _decimal_from_string(value, info.field_name)

    @field_validator("quantity", "unit_price")
    @classmethod
    def values_are_positive(cls, value: Decimal, info: Any) -> Decimal:
        if value <= 0:
            raise ValueError(f"{info.field_name} must be greater than zero")
        return value

    @field_validator("currency")
    @classmethod
    def currency_is_valid(cls, value: str) -> str:
        return _validate_currency(value, "currency")


class SecurityPaymentEntry(LedgerModel):
    payment_date: date
    ex_date: Optional[date] = None
    kind: SecurityPaymentKind
    description: str = Field(min_length=1, max_length=200)
    quantity: Optional[Decimal] = None
    gross_amount: Decimal
    currency: str
    foreign_withholding_tax: Optional[Decimal] = None
    foreign_withholding_country: Optional[str] = None
    foreign_withholding_tax_nonrefundable: bool = False
    swiss_withholding_tax: Optional[Decimal] = None

    @field_validator(
        "quantity",
        "gross_amount",
        "foreign_withholding_tax",
        "swiss_withholding_tax",
        mode="before",
    )
    @classmethod
    def values_are_decimal_strings(cls, value: Any, info: Any) -> Optional[Decimal]:
        if value is None:
            return None
        return _decimal_from_string(value, info.field_name)

    @field_validator("quantity", "gross_amount", "foreign_withholding_tax", "swiss_withholding_tax")
    @classmethod
    def values_are_not_negative(cls, value: Optional[Decimal], info: Any) -> Optional[Decimal]:
        if value is not None and value < 0:
            raise ValueError(f"{info.field_name} cannot be negative")
        return value

    @field_validator("currency")
    @classmethod
    def currency_is_valid(cls, value: str) -> str:
        return _validate_currency(value, "currency")

    @field_validator("foreign_withholding_country")
    @classmethod
    def foreign_withholding_country_is_valid(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            return _validate_country(value, "foreign_withholding_country")
        return value

    @model_validator(mode="after")
    def withholding_country_requires_tax(self) -> "SecurityPaymentEntry":
        if self.foreign_withholding_country is not None and self.foreign_withholding_tax is None:
            raise ValueError("foreign_withholding_country requires foreign_withholding_tax")
        if self.foreign_withholding_tax_nonrefundable and self.foreign_withholding_tax is None:
            raise ValueError(
                "foreign_withholding_tax_nonrefundable requires foreign_withholding_tax"
            )
        return self


class LedgerSecurity(LedgerModel):
    id: str = Field(min_length=1, pattern=r"^\S+$")
    depot_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    country: str
    currency: str
    category: str
    isin: Optional[str] = None
    valor: Optional[int] = Field(default=None, ge=100, le=999999999999)
    manual_review: bool = False
    closing_quantity: ClosingQuantity
    stocks: list[SecurityStockEntry] = Field(default_factory=list)
    payments: list[SecurityPaymentEntry] = Field(default_factory=list)

    @field_validator("country")
    @classmethod
    def country_is_valid(cls, value: str) -> str:
        return _validate_country(value, "country")

    @field_validator("currency")
    @classmethod
    def currency_is_valid(cls, value: str) -> str:
        return _validate_currency(value, "currency")

    @field_validator("category")
    @classmethod
    def category_is_supported(cls, value: str) -> str:
        if value not in {"SHARE", "FUND", "BOND"}:
            raise ValueError("category must be one of SHARE, FUND, or BOND")
        return value

    @field_validator("isin")
    @classmethod
    def isin_is_valid(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _ISIN_RE.fullmatch(value):
            raise ValueError("isin must be a 12-character uppercase ISIN")
        return value

    @model_validator(mode="after")
    def requires_identifier_or_review(self) -> "LedgerSecurity":
        if self.isin is None and self.valor is None and not self.manual_review:
            raise ValueError(
                "security without an ISIN or valor requires manual_review: true before import"
            )
        return self


class LedgerClient(LedgerModel):
    client_number: str = Field(min_length=1, max_length=40)
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class LedgerStatement(LedgerModel):
    institution_name: str = Field(min_length=1)
    institution_country: str
    statement_id: str = Field(min_length=1)
    tax_year: int = Field(ge=1900, le=9999)
    canton: Optional[str] = None
    client: LedgerClient

    @field_validator("institution_country")
    @classmethod
    def institution_country_is_valid(cls, value: str) -> str:
        return _validate_country(value, "institution_country")


class LedgerBundle(LedgerModel):
    schema_version: int
    statement: LedgerStatement
    cash_accounts: list[CashAccount] = Field(default_factory=list)
    securities: list[LedgerSecurity] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def supports_only_schema_version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only ledger schema_version 1 is supported")
        return value

    @model_validator(mode="after")
    def validate_bundle_consistency(self) -> "LedgerBundle":
        tax_year = self.statement.tax_year
        closing_date = date(tax_year, 12, 31)

        self._validate_unique_ids("cash_accounts", [account.id for account in self.cash_accounts])
        self._validate_unique_ids("securities", [security.id for security in self.securities])
        self._validate_unique_ids(
            "cash_accounts and securities",
            [account.id for account in self.cash_accounts]
            + [security.id for security in self.securities],
        )

        for account in self.cash_accounts:
            if account.closing_balance.date != closing_date:
                raise ValueError(
                    f"cash account {account.id!r} closing_balance.date must be {closing_date.isoformat()}"
                )
            self._validate_dates(
                f"cash account {account.id!r}",
                [payment.date for payment in account.payments],
                tax_year,
            )

        for security in self.securities:
            if security.closing_quantity.date != closing_date:
                raise ValueError(
                    f"security {security.id!r} closing_quantity.date must be {closing_date.isoformat()}"
                )
            self._validate_dates(
                f"security {security.id!r}", [stock.date for stock in security.stocks], tax_year
            )
            self._validate_dates(
                f"security {security.id!r}",
                [payment.payment_date for payment in security.payments],
                tax_year,
            )
            self._validate_dates(
                f"security {security.id!r}",
                [payment.ex_date for payment in security.payments if payment.ex_date is not None],
                tax_year,
            )
        return self

    @staticmethod
    def _validate_unique_ids(section: str, ids: list[str]) -> None:
        duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate stable IDs in {section}: {', '.join(duplicates)}")

    @staticmethod
    def _validate_dates(context: str, dates: list[date], tax_year: int) -> None:
        for value in dates:
            if value.year != tax_year:
                raise ValueError(f"{context} contains a date outside tax year {tax_year}: {value}")
