# Generic Ledger Importer

The generic ledger importer turns a reviewed, provider-neutral `ledger.json`
file into an eCH-0196 tax statement. Use it for institutions without a native
OpenSteuerAuszug importer, such as a simple cash account or a broker whose
export you have already reconciled into a small ledger.

It is deliberately not an IBKR Flex XML substitute and it does not parse
provider-specific exports or bank PDFs directly. Extract and review source
data first, then create `ledger.json`. This keeps the input auditable and
avoids inventing broker-specific identifiers or event semantics.

The resulting statement goes through the normal validation, ESTV Kursliste
calculation, payment reconciliation, XML generation, and PDF rendering flow.
Always inspect the generated output and retain the original statements as
evidence before submitting a tax return.

## Quick start

Create a `ledger.json` file, then run the normal processing command. The
`--tax-year` must agree with `statement.tax_year` in the JSON file.

```console
opensteuerauszug process ledger.json --importer ledger --tax-year 2025 \
  --kursliste-dir data/kursliste \
  --xml-output generic-ledger-2025.xml \
  -o generic-ledger-2025-e-steuerauszug.pdf
```

The normal calculation level is `kursliste`. The ledger importer rejects
`--tax-calculation-level fillin`: a security that cannot be valued from the
Kursliste must be reviewed or entered manually, rather than assigned an
invented value.

No importer-specific `config.toml` broker/account entry is required. The
ledger itself supplies the institution, client, canton, account, and depot
data. General render settings in `config.toml` still apply.

## Complete anonymized example

All values that represent money or a quantity are JSON strings. Never use a
JSON number such as `100.25`; it is rejected to prevent binary floating-point
rounding from entering the audit trail.

```json
{
  "schema_version": 1,
  "statement": {
    "institution_name": "Example Broker",
    "institution_country": "US",
    "statement_id": "example-2025-main",
    "tax_year": 2025,
    "canton": "ZH",
    "client": {
      "client_number": "example-client-1",
      "first_name": "Ada",
      "last_name": "Lovelace"
    }
  },
  "cash_accounts": [
    {
      "id": "example-cash-usd",
      "number": "cash-001",
      "name": "Example cash account",
      "country": "US",
      "currency": "USD",
      "closing_balance": {
        "date": "2025-12-31",
        "amount": "100.25"
      },
      "payments": [
        {
          "date": "2025-06-30",
          "kind": "interest",
          "description": "Cash interest",
          "gross_amount": "1.25",
          "currency": "USD"
        }
      ]
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
      "valor": null,
      "closing_quantity": {
        "date": "2025-12-31",
        "quantity": "10"
      },
      "stocks": [
        {
          "date": "2025-03-12",
          "kind": "buy",
          "quantity": "10",
          "unit_price": "200.10",
          "currency": "USD",
          "description": "Purchase",
          "order_id": "purchase-1"
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
          "foreign_withholding_country": "US"
        }
      ]
    }
  ]
}
```

The example contains invented data only. Do not commit real account numbers,
transaction data, or statements to the repository.

## Input rules

`schema_version` must be `1`. Unknown fields and unknown event kinds are
rejected, so a changed extraction cannot be silently ignored.

Every `id` is a stable, local provider identifier. IDs must be unique across
cash accounts and securities. A security ID cannot contain spaces because it
becomes the eCH security symbol.

Dates use ISO `YYYY-MM-DD`, currencies use three-letter uppercase ISO-4217
codes, and country fields use two-letter uppercase country codes. All event
dates must fall in `statement.tax_year`; every closing balance and closing
quantity must be dated `YYYY-12-31` for that year.

Each security needs `closing_quantity`, even when it was sold completely and
its closing quantity is `"0"`. This lets the importer report income and stock
mutations while retaining the year-end zero balance.

Securities normally need either an `isin` or a numeric `valor`. If neither
identifier exists, set `"manual_review": true` explicitly on that security.
The import then emits a warning and the user must review its Kursliste matching
and final output. Missing identifiers without that flag are errors.

The allowed security `category` values are `SHARE`, `FUND`, and `BOND`. A bond
should be included only when the reviewed data has all required source data.
Options, futures, crypto, stock lending, margin, structured products, and
complex corporate actions are intentionally rejected in version 1.

## Record types and mapping

| Ledger record | Required fields | Result |
| --- | --- | --- |
| Cash account | ID, country, currency, 31 December `closing_balance` | eCH bank account and tax value |
| Cash payment | `kind: "interest"`, date, gross amount, currency | bank-account payment |
| Security | ID, depot, name, country, category, year-end quantity | eCH security in the named depot |
| Stock | `kind: "buy"` or `"sell"`, positive quantity and unit price | security stock mutation; sells are represented as negative quantities |
| Security payment | `kind: "dividend"`, `"distribution"`, or `"interest"` | security payment |
| Foreign withholding | `foreign_withholding_tax` and optional country on a security payment | separate source-tax evidence; it does not assert a recoverable Swiss credit |
| Swiss withholding | optional positive `swiss_withholding_tax` on an interest/dividend payment | separate CHF withholding-tax evidence |

`order_id` is optional on stock records. When present, same-day partial fills
with the same order ID can be aggregated by the shared importer pipeline.

The payment `quantity`, `ex_date`, `foreign_withholding_tax`,
`foreign_withholding_country`, and `swiss_withholding_tax` fields are optional.
Withholding values are positive source amounts. The importer writes withholding
as evidence separately from the gross payment, because the Kursliste/DA-1
calculation must determine whether, and how much, foreign withholding is
recoverable.

## Multiple accounts and depots

One ledger can contain several `cash_accounts` and securities across several
`depot_id` values. Each cash-account entry produces one bank account in the
output; all securities with the same `depot_id` are placed in one depot.

For straightforward reconciliation, prefer one ledger per provider/legal
account. When combining accounts, make identifiers, account numbers, and depot
IDs unambiguous and reconcile every year-end balance independently.

## Review checklist

Before running the importer, reconcile the ledger against the source records:

- Every cash currency has its 31 December closing balance.
- Every security has its 31 December quantity, including zero-close positions.
- Purchases and sales agree with the account history.
- Every dividend/distribution has the correct gross amount, date, and source
  withholding tax.
- No broker-provided CHF estimate has replaced an original amount or currency.
- Deposits, withdrawals, and internal transfers are excluded: they are not
  taxable payments.

Standalone fee rows are not modelled by version 1 of this input contract. Keep
them in the extraction audit and handle them manually where they are relevant
to the tax return; do not label them as interest or dividends merely to make
the JSON validate.

After processing, inspect the PDF and the Wertschriftenverzeichnis/DA-1 result
in the tax software. OpenSteuerAuszug never submits the declaration or drives
ZHprivateTax automatically.

## Troubleshooting

Validation errors include the JSON path of the offending field. Common causes
are a numeric value instead of a quoted decimal string, an unsupported event
kind, a lowercase currency/country code, a duplicate ID, a date outside the
tax year, or an omitted year-end quantity.

If a security cannot be found in the Kursliste, do not switch to `fillin` mode
for this importer. Review the ISIN/valor and currency first. If no official
valuation is available, correct or enter that item manually according to the
tax authority's guidance.
