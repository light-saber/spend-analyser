# Expense Tracker

A small local-first Indian expense tracker using Python 3.10+ and only the
standard library. Phase 1 imports already-parsed expenses from local JSONL into
SQLite. No network access or external integrations are included.

## Usage

Run from the repository root. No installation or dependencies are required.

```sh
python3 -m expense_tracker.cli init --db data/expenses.db
python3 -m expense_tracker.cli ingest-json --db data/expenses.db --input transactions.jsonl
python3 -m expense_tracker.cli list --db data/expenses.db --start 2026-01-01 --end 2026-01-31
```

Example `transactions.jsonl` (one object per line):

```json
{"date":"2026-01-02","merchant":"Example Grocery","amount":"125.50","source_id":"example-record-1"}
{"date":"2026-01-03","merchant":"Example Transit","amount":"40.00","parser_confidence":0.9}
```

Required fields: `date` (YYYY-MM-DD), `merchant` (nonempty text), and `amount`
(positive rupees, at most two decimal places). Decimal strings are recommended.
Only INR expenses are supported; refunds and other currencies are out of scope.

Optional fields: `currency` (default INR), `source` (default jsonl), `source_id`
(nonempty text), and `parser_confidence` (number from 0 to 1, default 1).
Unknown fields are rejected. Blank lines are ignored. Input files must be UTF-8.

Imports print inserted and duplicate counts as JSON. Lists print one JSON object
per transaction, ordered by date then ID, with inclusive date bounds. Amounts
are stored and returned as integer `amount_paise`, avoiding floating-point money.

## Identity and review

SHA-256 hashes use versioned canonical JSON. When `source_id` is supplied,
identity is the pair `source` and `source_id`; reimports do not update existing
records. Without an ID, identity uses source, date, trimmed merchant, amount,
and currency. Identical same-day expenses will collapse without distinct source
IDs. Filenames, JSON key order, and confidence do not affect identity.

Unknown merchants receive category `uncategorized`, status `needs_review`,
category source `default`, confidence 0, and reason `unknown_merchant`.
Exact merchant rules use SQLite's case-insensitive matching (ASCII case folding),
category source `merchant_rule`, and category confidence 1. Parser confidence
below 0.8 requires review even when a rule matches; otherwise matched records
are `confirmed`. These are deterministic policy scores, not calibrated probabilities.

The schema includes `transactions`, `merchant_rules`, `review_actions`, and
`ingestion_runs`. Rule editing and review commands are reserved for a later
phase. No merchant rules ship by default.

Each import is atomic. Invalid records or file errors roll back transaction
changes and record a failed run with its line number when available. Errors do
not echo input contents. Successful runs record inserted and duplicate counts.
A process killed mid-import can leave a run marked `running`; SQLite rolls back
its uncommitted transaction changes.

## Python API

- `schema.ensure_db(db_path)` creates parent directories and tables, returning
  a connection with foreign keys enabled. The caller must close it.
- `store.insert_transaction(conn, tx)` accepts the same dictionary contract as
  JSONL and returns True for insertion, False for an existing source hash.
- `store.list_transactions(conn, start_date, end_date)` returns dictionaries.
- `models.source_hash(tx)` exposes stable source hashing.

Callers own commit/rollback, typically using `with conn:`. A connection's
context manager does not close it.

Databases contain plain local data, without encryption. Use appropriate local
file permissions and backups. Database files and JSONL inputs are gitignored.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q expense_tracker tests
```
