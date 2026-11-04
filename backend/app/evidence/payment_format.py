"""Canonical storage values; source readings and inference evidence stay unchanged."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import unicodedata

STORAGE_SCHEMA_VERSION = 'payment-fields-v1'
MONEY_FIELDS = ('amount', 'fee', 'total_debit')


def money_value(value):
    if not isinstance(value, str):
        raise ValueError('money_must_be_text')
    value = value.strip()
    if not re.fullmatch(r'(?:\d{1,16}|\d{1,3}(?:,\d{3}){1,5})(?:\.\d{1,2})?', value):
        raise ValueError('invalid_money_format')
    try:
        number = Decimal(value.replace(',', ''))
        if number > Decimal('9999999999999999.99'):
            raise ValueError('money_out_of_range')
        canonical = format(number.quantize(Decimal('0.01')), 'f')
        # JSON integers above 2**53 lose precision in browsers, so minor units are text too.
        return canonical, str(int(number * 100))
    except InvalidOperation:
        raise ValueError('invalid_money_format') from None


def canonical_fields(fields):
    normalized, issues, minor_units = {}, [], {}
    time_precision = None
    for name, value in fields.items():
        normalized[name] = None
        if value is None or value == '':
            if name in MONEY_FIELDS:
                minor_units[name] = None
            continue
        try:
            if not isinstance(value, str):
                raise ValueError('value_must_be_text')
            value = unicodedata.normalize('NFC', value).strip()
            if not value:
                if name in MONEY_FIELDS:
                    minor_units[name] = None
                continue
            if name in MONEY_FIELDS:
                normalized[name], minor_units[name] = money_value(value)
            elif name == 'transaction_date':
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError('invalid_date_format')
                normalized[name] = date.fromisoformat(value).isoformat()
            elif name == 'transaction_time':
                if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?', value):
                    raise ValueError('invalid_time_format')
                time_precision = 'second' if len(value) == 8 else 'minute'
                normalized[name] = value if time_precision == 'second' else value + ':00'
            elif name == 'currency':
                if not re.fullmatch(r'[A-Za-z]{3}', value):
                    raise ValueError('invalid_currency_format')
                normalized[name] = value.upper()
            elif name.endswith('_account'):
                # Account IDs are text, including their leading zeros and any visible masking.
                normalized[name] = re.sub(r'\s+', '', value)
            else:
                normalized[name] = re.sub(r'\s+', ' ', value)
        except ValueError as error:
            issues.append({'field': name, 'code': str(error) if str(error).isascii() else 'invalid_format'})
            if name in MONEY_FIELDS:
                minor_units[name] = None
    return {'storage_schema_version': STORAGE_SCHEMA_VERSION, 'normalized_fields': normalized,
            'money_minor_units': minor_units, 'transaction_time_precision': time_precision,
            'format_issues': issues}


def for_storage(result):
    fields = result.get('candidate_fields') or result.get('fields')
    if not isinstance(fields, dict):
        return result
    return {**result, **canonical_fields(fields)}
