"""Exact merchant rules with conservative review defaults."""
from .models import classification_metadata


def classify_transaction(conn, tx):
    metadata = classification_metadata(tx)
    rule = conn.execute(
        "SELECT category FROM merchant_rules WHERE merchant = ? COLLATE NOCASE",
        (tx['merchant'],)).fetchone()
    category = metadata.get('category', 'uncategorized')
    source = metadata.get('category_source', 'default')
    confidence = metadata.get('category_confidence', 0.0)
    if rule is not None:
        category, source, confidence = rule[0], 'merchant_rule', 1.0
    reason = 'low_parser_confidence' if tx.get('parser_confidence', 1.0) < 0.8 else None
    if source == 'default' or category.strip().lower() in ('unknown', 'uncategorized'):
        reason = reason or 'unknown_merchant'
    return dict(category=category, category_source=source,
                category_confidence=confidence,
                status='needs_review' if reason else 'confirmed',
                review_reason=reason, raw_category=metadata['raw_category'],
                parser_reason=metadata['parser_reason'])
