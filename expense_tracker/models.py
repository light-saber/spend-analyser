"""Transaction normalization and stable, versioned source identity."""
import hashlib
import json
import math
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext


def validate_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("date must use YYYY-MM-DD")
    return value


def normalize_transaction(tx):
    if not isinstance(tx, dict):
        raise ValueError("transaction must be an object")
    allowed = {'date', 'merchant', 'amount', 'currency', 'source', 'source_id',
               'parser_confidence', 'category', 'category_source', 'category_confidence',
               'raw_category', 'parser_reason'}
    if set(tx) - allowed:
        raise ValueError("unsupported transaction fields")
    day = validate_date(tx.get('date'))
    merchant = tx.get('merchant')
    if not isinstance(merchant, str) or not merchant.strip():
        raise ValueError("merchant must be nonempty text")
    try:
        amount = Decimal(str(tx.get('amount')))
        if not amount.is_finite() or not Decimal('0.01') <= amount <= Decimal('92233720368547758.07'):
            raise ValueError("amount must be positive INR with at most two decimal places")
        with localcontext() as context:
            context.prec = max(28, len(amount.as_tuple().digits) + 2)
            paise = amount * 100
        if paise != paise.to_integral_value():
            raise ValueError("amount must be positive INR with at most two decimal places")
    except InvalidOperation:
        raise ValueError("invalid amount") from None
    if tx.get('currency', 'INR') != 'INR':
        raise ValueError("only INR is supported")
    source = tx.get('source', 'jsonl')
    source_id = tx.get('source_id')
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be nonempty text")
    if source_id is not None and (not isinstance(source_id, str) or not source_id.strip()):
        raise ValueError("source_id must be nonempty text")
    confidence = tx.get('parser_confidence', 1.0)
    if (isinstance(confidence, bool) or
            not isinstance(confidence, (int, float, Decimal)) or
            (isinstance(confidence, Decimal) and not confidence.is_finite()) or
            not 0 <= confidence <= 1):
        raise ValueError("parser_confidence must be between zero and one")
    return dict(date=day, merchant=merchant.strip(), amount_paise=int(paise),
                currency='INR', source=source.strip(), source_id=source_id,
                parser_confidence=float(confidence), **classification_metadata(tx))


def source_hash(tx):
    """Hash canonical identity; explicit source IDs distinguish identical expenses."""
    normalized = normalize_transaction(tx)
    if normalized['source_id'] is not None:
        identity = {k: normalized[k] for k in ('source', 'source_id')}
    else:
        identity = {k: normalized[k] for k in ('source', 'date', 'merchant', 'amount_paise', 'currency')}
    payload = json.dumps(identity, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(('v1:' + payload).encode('utf-8')).hexdigest()


def classification_metadata(tx):
    """Validate metadata supplied by a trusted importer without echoing values."""
    fields = {'category', 'category_source', 'category_confidence'}
    supplied = fields.intersection(tx)
    result = {}
    if supplied:
        if supplied != fields:
            raise ValueError("category metadata must include category, source and confidence")
        for name in ('category', 'category_source'):
            value = tx[name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError("category and category_source must be nonempty text")
            result[name] = value.strip()
        confidence = tx['category_confidence']
        if (isinstance(confidence, bool) or
                not isinstance(confidence, (int, float, Decimal)) or
                (isinstance(confidence, Decimal) and not confidence.is_finite()) or
                not 0 <= confidence <= 1):
            raise ValueError("category_confidence must be between zero and one")
        result['category_confidence'] = float(confidence)
    for name in ('raw_category', 'parser_reason'):
        value = tx.get(name)
        if value is not None and not isinstance(value, str):
            raise ValueError("raw_category and parser_reason must be text or null")
        result[name] = value
    return result
