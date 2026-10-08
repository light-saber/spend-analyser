"""Exact merchant rules, Jev fallback, and conservative review defaults."""
from .jev import CACHE_PATH, CATEGORY_RULES, cached_answer, request_jev, safe_merchant
from .models import classification_metadata


def classify_transaction(conn, tx):
    metadata = classification_metadata(tx)
    rule = conn.execute(
        "SELECT category FROM merchant_rules WHERE merchant = ? COLLATE NOCASE",
        (tx['merchant'],)).fetchone()
    builtin = next((category for keywords, category in CATEGORY_RULES
                    if any(word in tx['merchant'].lower() for word in keywords)), None)
    if rule is not None:
        result = decision(rule[0], 'rule', 1.0)
    elif builtin:
        result = decision(builtin, 'rule', 1.0)
    elif 'category' in metadata:
        result = decision(metadata['category'], metadata['category_source'], metadata['category_confidence'])
    else:
        result = classify_merchant(tx['merchant'])
    if (result['category_source'] == 'default' or
            result['category'].lower() in ('unknown', 'uncategorized')):
        result.update(status='needs_review', review_reason='unknown_merchant')
    if tx.get('parser_confidence', 1.0) < 0.8:
        result.update(status='needs_review', review_reason='low_parser_confidence')
    result.update(raw_category=metadata['raw_category'], parser_reason=metadata['parser_reason'])
    return result


def classify_merchant(merchant, *, cache_path=CACHE_PATH):
    for keywords, category in CATEGORY_RULES:
        if any(word in merchant.lower() for word in keywords):
            return decision(category, 'rule', 1.0)
    safe = safe_merchant(merchant)
    answer = cached_answer(safe, request_jev, cache_path) if safe else None
    if answer is None:
        return decision('Other', 'unknown', 0.0)
    return decision(answer['choice'], 'jev', answer['confidence'])


def decision(category, source, confidence):
    reason = None
    if source == 'jev' and confidence < 0.60:
        category, source = 'Other', 'unknown'
        reason = f'Jev confidence {confidence:.2f} below 0.60'
    elif source == 'jev' and confidence < 0.80:
        reason = f'Jev confidence {confidence:.2f} below 0.80'
    elif source == 'unknown':
        reason = 'unknown_merchant'
    return dict(category=category, category_source=source, category_confidence=confidence,
                status='needs_review' if reason else 'confirmed', review_reason=reason)
