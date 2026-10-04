"""Exact merchant rules with conservative review defaults."""


def classify_transaction(conn, tx):
    rule = conn.execute(
        "SELECT category FROM merchant_rules WHERE merchant = ? COLLATE NOCASE",
        (tx['merchant'],)).fetchone()
    reason = 'low_parser_confidence' if tx['parser_confidence'] < 0.8 else None
    if rule is None:
        reason = reason or 'unknown_merchant'
    return dict(category=rule[0] if rule else 'uncategorized',
                category_source='merchant_rule' if rule else 'default',
                category_confidence=1.0 if rule else 0.0,
                status='needs_review' if reason else 'confirmed',
                review_reason=reason)
