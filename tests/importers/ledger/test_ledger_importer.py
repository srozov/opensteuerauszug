import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opensteuerauszug.importers.ledger import LedgerImporter
from opensteuerauszug.steuerauszug import app

runner = CliRunner()


def _ledger_data() -> dict:
    return {
        "schema_version": 1,
        "statement": {
            "institution_name": "Example Broker",
            "institution_country": "US",
            "statement_id": "example-2025",
            "tax_year": 2025,
            "canton": "ZH",
            "client": {
                "client_number": "customer-1",
                "first_name": "Ada",
                "last_name": "Lovelace",
            },
        },
        "cash_accounts": [
            {
                "id": "cash-usd",
                "number": "cash-001",
                "name": "Example cash account",
                "country": "US",
                "currency": "USD",
                "closing_balance": {"date": "2025-12-31", "amount": "100.25"},
                "payments": [
                    {
                        "date": "2025-06-30",
                        "kind": "interest",
                        "description": "Cash interest",
                        "gross_amount": "1.25",
                        "currency": "USD",
                    }
                ],
            }
        ],
        "securities": [
            {
                "id": "AAPL",
                "depot_id": "main",
                "name": "Apple Inc.",
                "country": "US",
                "currency": "USD",
                "category": "SHARE",
                "isin": "US0378331005",
                "valor": None,
                "closing_quantity": {"date": "2025-12-31", "quantity": "10"},
                "stocks": [
                    {
                        "date": "2025-03-12",
                        "kind": "buy",
                        "quantity": "10",
                        "unit_price": "200.10",
                        "currency": "USD",
                        "description": "Purchase",
                        "order_id": "order-1",
                    }
                ],
                "payments": [
                    {
                        "payment_date": "2025-07-25",
                        "ex_date": "2025-07-12",
                        "kind": "dividend",
                        "description": "Quarterly dividend",
                        "quantity": "10",
                        "gross_amount": "2.50",
                        "currency": "USD",
                        "foreign_withholding_tax": "0.38",
                        "foreign_withholding_country": "US",
                    }
                ],
            }
        ],
    }


def _write_ledger(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_reviewed_ledger_preserves_source_amounts_and_builds_positions(tmp_path: Path):
    statement = LedgerImporter().import_file(_write_ledger(tmp_path, _ledger_data()))

    assert statement.periodFrom == date(2025, 1, 1)
    assert statement.periodTo == date(2025, 12, 31)
    assert statement.institution.name == "Example Broker"
    assert statement.canton == "ZH"

    cash_account = statement.listOfBankAccounts.bankAccount[0]
    assert cash_account.taxValue.balance == Decimal("100.25")
    assert cash_account.payment[0].amount == Decimal("1.25")

    security = statement.listOfSecurities.depot[0].security[0]
    assert security.isin == "US0378331005"
    assert security.stock[-1].referenceDate == date(2026, 1, 1)
    assert security.stock[-1].quantity == Decimal("10")
    assert security.payment[0].amount == Decimal("2.50")
    assert security.payment[1].nonRecoverableTaxAmountOriginal == Decimal("0.38")


def test_ledger_rejects_json_floating_point_amounts(tmp_path: Path):
    ledger = _ledger_data()
    ledger["cash_accounts"][0]["closing_balance"]["amount"] = 100.25

    with pytest.raises(ValueError, match="must be a JSON string"):
        LedgerImporter().import_file(_write_ledger(tmp_path, ledger))


def test_security_without_identifier_requires_explicit_manual_review(tmp_path: Path):
    ledger = _ledger_data()
    ledger["securities"][0]["isin"] = None

    with pytest.raises(ValueError, match="requires manual_review"):
        LedgerImporter().import_file(_write_ledger(tmp_path, ledger))

    ledger["securities"][0]["manual_review"] = True
    statement = LedgerImporter().import_file(_write_ledger(tmp_path, ledger))
    assert statement.listOfSecurities.depot[0].security[0].isin is None


def test_cli_routes_ledger_json_to_the_ledger_importer(tmp_path: Path):
    ledger_path = _write_ledger(tmp_path, _ledger_data())
    project_root = Path(__file__).resolve().parents[3]

    result = runner.invoke(
        app,
        [
            "process",
            str(ledger_path),
            "--importer",
            "ledger",
            "--tax-year",
            "2025",
            "--phases",
            "import",
            "--config",
            str(project_root / "config.template.toml"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Ledger import complete." in result.stdout


def test_cli_rejects_fill_in_calculation_for_ledger(tmp_path: Path):
    ledger_path = _write_ledger(tmp_path, _ledger_data())

    result = runner.invoke(
        app,
        [
            "process",
            str(ledger_path),
            "--importer",
            "ledger",
            "--tax-year",
            "2025",
            "--tax-calculation-level",
            "fillin",
        ],
    )

    assert result.exit_code != 0
