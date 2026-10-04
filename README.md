# Expense Tracker

A small local-first Indian expense tracker using Python 3.10+ and only the
standard library. Imports already-parsed expenses from local JSONL into
SQLite. No network access or external integrations are included.

## Usage

Run from the repository root. No installation or dependencies are required.

```sh
python3 -m expense_tracker.cli review --db data/expenses.db --limit 5
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
Trusted importers may also supply all three of `category`, `category_source`
(nonempty text), and `category_confidence` (finite number from 0 to 1).
The Python API and local JSONL input are trusted caller boundaries; they do not
authenticate classification provenance. Merchant rules override supplied categories.
Optional `raw_category` and `parser_reason` accept text or null and are preserved.
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
are `confirmed`. Valid caller categories can also be confirmed; category
`unknown` or `uncategorized` (case-insensitive), or source `default`, requires
review with reason `unknown_merchant`. Low parser confidence takes reason precedence. These are deterministic policy scores, not calibrated probabilities.

The schema includes `transactions`, `merchant_rules`, `review_actions`, and
`ingestion_runs`. Opening a database migrates it to `PRAGMA user_version = 2`, preserving
Phase 1 rows and adding nullable metadata columns if missing.
The `review` command lists only `needs_review` rows, newest import first
(`created_at DESC, id DESC`), as one JSON object per line. Its limit defaults
to 5; integers outside 1..50 are rejected. Rule editing and review resolution
are reserved for a later phase. No merchant rules ship by default.

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
- `store.list_review_transactions(conn, limit=5)` returns bounded review dictionaries.
- `classify.classify_transaction(conn, tx)` returns category, source, confidence,
  status, review reason, raw category, and parser reason.
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
