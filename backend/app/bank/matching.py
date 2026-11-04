"""Deterministic receipt/bank comparison. No model calls or production mutations.

An amount/date hit is a candidate; automatic selection also requires account,
reference or payer evidence. Historical user confirmations are exact-file scoped.
"""
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher

from app.evidence.payment_format import canonical_fields

RULE_VERSION = 'unified-exact-v2'


def compact(value):
    return re.sub(r'[^A-Z0-9]', '', unicodedata.normalize('NFKC', value or '').upper())


def amount(value):
    try:
        number = Decimal(str(value).replace(',', ''))
        if not number.is_finite() or number < 0 or number != number.quantize(Decimal('.01')):
            return None
        return number
    except (InvalidOperation, ValueError):
        return None


def bank_name(value):
    v = compact(value)
    if 'FCMB' in v or 'FIRSTCITYMONUMENT' in v:
        return 'FCMB'
    if 'FIDEL' in v:
        return 'FIDELITY'
    if 'ACCESS' in v:
        return 'ACCESS'
    return v


def prepare_bank(parsed, file_sha256, currencies, date_parser):
    """Resolve a sheet's slash date convention only with unambiguous source evidence.

    Original raw values and original issues remain attached. Sheet suffixes are
    account aliases, never represented as verified complete bank account numbers.
    """
    rows, policies = [], []
    for sheet in parsed['sheets']:
        evidence = Counter()
        for r in sheet['rows']:
            match = re.fullmatch(r'(\d{1,2})/(\d{1,2})/\d{4}', r['raw_date'])
            if match:
                a, b = map(int, match.groups())
                if a > 12 and b <= 12:
                    evidence['dmy'] += 1
                elif b > 12 and a <= 12:
                    evidence['mdy'] += 1
        order = next(iter(evidence)) if len(evidence) == 1 else 'auto'
        policies.append({'sheet': sheet['name'], 'date_order': order,
                         'unambiguous_source_rows': dict(evidence)})
        suffix = re.search(r'\b(\d{4})-\(', sheet['name'])
        for r in sheet['rows']:
            resolved, error = date_parser(r['raw_date'], order)
            issues = [x for x in r['issues'] if '日月顺序' not in x]
            if error and error not in issues:
                issues.append(error)
            identity = [sheet['bank_suggestion'], sheet['name'], currencies.get(sheet['name']),
                        resolved or r['raw_date'], r['credit'], r['debit'], r['description'],
                        r['reference'], r['source_code']]
            fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            rows.append({**r, 'id': f"{file_sha256}:{sheet['name']}:{r['row']}",
                         'sheet': sheet['name'], 'bank': sheet['bank_suggestion'],
                         'account_suffix': suffix[1] if suffix else None,
                         'currency': currencies.get(sheet['name']), 'date': resolved,
                         'issues': issues, 'original_issues': r['issues'],
                         'file_sha256': file_sha256, 'fingerprint': fingerprint,
                         'date_order': order})
    return rows, policies


def account_evidence(receipt, row):
    raw, suffix = receipt.get('recipient_account'), row.get('account_suffix')
    if not raw or not suffix:
        return 'missing'
    # The bank workbook only names the last four digits. State this weaker evidence.
    v = re.sub(r'\s+', '', raw)
    tail = re.search(r'(\d{3,})$', v)
    if not tail:
        return 'missing'
    visible = tail[1][-4:]
    return 'suffix_match' if suffix.endswith(visible) else 'conflict'


def payer_evidence(payer, description):
    if not payer:
        return 'missing'
    name, haystack = compact(payer), compact(description)
    if len(name) < 4:
        return 'missing'
    if name in haystack:
        return 'match'
    tokens = [t for t in re.findall(r'[A-Z]+', payer.upper())
              if len(t) >= 4 and t not in {'LIMITED', 'LTD', 'BUSINESS', 'ENTERPRISE', 'SERVICES', 'INTERNATIONAL'}]
    hits = [t for t in tokens if t in haystack]
    if len(hits) >= 2 or any(len(t) >= 6 for t in hits):
        return 'partial_match'
    # A single long company token sometimes contains one OCR character error.
    if len(tokens) == 1 and len(tokens[0]) >= 10:
        for word in re.findall(r'[A-Z]+', description.upper()):
            if SequenceMatcher(None, tokens[0], word).ratio() >= .92:
                return 'ocr_near_match'
    return 'different_text'


def compare(receipt, rows, confirmation=None):
    """Return evidence-bearing candidates, without writing financial ledger entries."""
    f = canonical_fields(receipt['fields'])['normalized_fields']
    value = amount(f.get('amount'))
    date_iso = f.get('transaction_date')
    currency = f.get('currency')
    output = {'source_sha256': receipt['sha256'], 'fields': f,
              'rule_version': RULE_VERSION, 'state': 'review', 'reason': None,
              'selected': None, 'candidates': [], 'confirmation': None}
    if value is None or value == 0 or not currency:
        return {**output, 'reason': 'missing_amount_or_currency'}
    if confirmation:
        matches = [r for r in rows if r['file_sha256'] == confirmation['bank_file_sha256']
                   and r['sheet'] == confirmation['bank_sheet'] and r['row'] == confirmation['bank_row']
                   and r['source_code'] == confirmation['bank_source_code']
                   and r['date'] == confirmation['transaction_date']
                   and amount(r['credit']) == amount(confirmation['bank_amount'])
                   and r['currency'] == confirmation['currency']
                   and not r['issues'] and amount(r['debit']) == 0]
        if (confirmation['source_sha256'] != receipt['sha256'] or len(matches) != 1
                or value != amount(confirmation['image_amount'])
                or date_iso != confirmation['transaction_date'] or currency != confirmation['currency']):
            return {**output, 'reason': 'confirmation_source_changed'}
        return {**output, 'state': 'matched', 'reason': 'user_confirmed_exact_transaction',
                'selected': matches[0], 'confirmation': confirmation,
                'candidates': [{'bank': matches[0], 'eligible': True, 'reason': 'user_confirmed'}]}
    for row in rows:
        if row['currency'] != currency or amount(row['credit']) is None or amount(row['credit']) <= 0 or amount(row['debit']) != 0:
            continue
        difference = value - amount(row['credit'])
        if difference != 0 and not (currency == 'NGN' and difference == Decimal('53.75')):
            continue
        date_match = date_iso is not None and row['date'] == date_iso
        acc = account_evidence(f, row)
        payer = payer_evidence(f.get('payer_name'), row['description'])
        # POS slips show an operator alongside the customer. Keep the customer's
        # field intact and use separately visible operator text as corroboration.
        source_text = compact(receipt.get('source_text'))
        words = [x for x in re.findall(r'[A-Z]+', row['description'].upper())
                 if len(x) >= 7 and x not in {'LIMITED', 'COMPANY', 'TRANSFER', 'PAYMENT', 'SERVICES', 'BUSINESS', 'ENTERPRISE'}]
        operator_match = len([x for x in set(words) if x in source_text]) >= 2
        ref = compact(f.get('transaction_reference'))
        reference_match = bool(len(ref) >= 8 and (ref in compact(row['reference']) or ref in compact(row['description'])))
        bank_conflict = bool(f.get('bank_name') and bank_name(f['bank_name']) != bank_name(row['bank']))
        recipient = compact(f.get('recipient_name'))
        recipient_match = bool(len(recipient) >= 10 and any(
            len(compact(n)) >= 10 and (recipient.startswith(compact(n)) or compact(n).startswith(recipient))
            for n in row.get('recipient_names', [])))
        recipient_from_source = any(len(core) >= 10 and core in source_text for core in
            (re.sub(r'(LIMITED|LTD)$', '', compact(n)) for n in row.get('recipient_names', [])))
        recipient_match = recipient_match or recipient_from_source
        bank_label_resolved = (bank_conflict and acc == 'suffix_match' and recipient_match
                               and payer in ('match', 'partial_match', 'ocr_near_match'))
        supporting = acc == 'suffix_match' or payer in ('match', 'partial_match', 'ocr_near_match') or reference_match or recipient_match
        reason = ('bank_row_issue' if row['issues'] else 'date_missing' if not date_iso else 'date_difference' if not date_match
                  else 'recipient_account_conflict' if acc == 'conflict' else 'recipient_bank_conflict' if bank_conflict and not bank_label_resolved
                  else 'payer_difference' if payer == 'different_text' and not reference_match and not operator_match
                  else 'needs_identity_evidence' if not supporting else 'exact_amount_date_with_identity')
        eligible = reason == 'exact_amount_date_with_identity'
        if difference:
            eligible = False  # Historical 53.75 authorizations are not a global policy.
            reason = 'amount_difference_needs_review'
        # Keep same-value candidates even if no date; they remain pending, never silently booked.
        output['candidates'].append({'bank': row, 'date_match': date_match, 'account': acc, 'payer': payer,
                                    'reference_match': reference_match, 'bank_conflict': bank_conflict,
                                    'operator_text_match': operator_match, 'recipient_name_match': recipient_match,
                                    'recipient_name_visible_in_source': recipient_from_source,
                                    'bank_label_conflict_resolved_by_other_evidence': bank_label_resolved,
                                    'difference': format(difference, '.2f'), 'eligible': eligible, 'reason': reason})
    def rank(x):
        gap=abs((date.fromisoformat(date_iso)-date.fromisoformat(x['bank']['date'])).days) if date_iso and x['bank']['date'] else 100000
        return (not x['eligible'],not x['date_match'],x['account']!='suffix_match',
                x['payer'] not in ('match','partial_match','ocr_near_match'),x['bank_conflict'],gap,x['bank']['id'])
    output['candidates'].sort(key=rank)
    valid = [c for c in output['candidates'] if c['eligible']]
    if len(valid) == 1:
        output.update(state='matched', reason=valid[0]['reason'], selected=valid[0]['bank'])
    elif len(valid) > 1:
        output['reason'] = 'multiple_bank_candidates'
    elif not output['candidates']:
        output['reason'] = 'no_bank_amount_candidate'
    else:
        output['reason'] = output['candidates'][0]['reason']
    return output
