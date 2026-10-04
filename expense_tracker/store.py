"""Parameterized persistence helpers; callers own commit/rollback."""
from .classify import classify_transaction
from .models import normalize_transaction, source_hash, validate_date


def insert_transaction(conn, tx, *, ingestion_run_id=None):
    record = normalize_transaction(tx)
    record.update(classify_transaction(conn, record))
    record.update(source_hash=source_hash(tx), ingestion_run_id=ingestion_run_id)
    cursor = conn.execute("""
        INSERT INTO transactions
        (date, merchant, amount_paise, currency, source, source_id,
         parser_confidence, category, category_source, category_confidence,
         status, review_reason, source_hash, ingestion_run_id)
        VALUES (:date, :merchant, :amount_paise, :currency, :source, :source_id,
                :parser_confidence, :category, :category_source, :category_confidence,
                :status, :review_reason, :source_hash, :ingestion_run_id)
        ON CONFLICT(source_hash) DO NOTHING
    """, record)
    return cursor.rowcount == 1


def list_transactions(conn, start_date, end_date):
    validate_date(start_date)
    validate_date(end_date)
    if start_date > end_date:
        raise ValueError("start date must not follow end date")
    cursor = conn.execute(
        "SELECT * FROM transactions WHERE date BETWEEN ? AND ? ORDER BY date, id",
        (start_date, end_date))
    keys = [column[0] for column in cursor.description]
    return [dict(zip(keys, row)) for row in cursor]
