"""Normalize flat OCR extractions using only their visible transcription."""
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re

from .recognition_validation import MONTHS, date_supported, time_supported, compact

OCR_PROMPT_VERSION = 'ocr-text-v1'
OCR_PROMPT = 'Please output only the text content from the image without any additional descriptions or formatting.'


def dates_in(text):
    found = set()
    def add(y, m, d):
        try:
            found.add(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            pass
    for y, m, d in re.findall(r'(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)', text):
        add(y, m, d)
    for a, b, y in re.findall(r'(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](\d{4})(?!\d)', text):
        add(y, b, a)
        add(y, a, b)
    month = r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    for d, m, y in re.findall(r'(\d{1,2})(?:st|nd|rd|th)?[\s,.-]+' + month + r'[\s,.-]+(\d{4})', text, re.I):
        add(y, MONTHS[m[:3].lower()], d)
    for m, d, y in re.findall(month + r'[\s,.-]+(\d{1,2})(?:st|nd|rd|th)?[\s,.-]+(\d{4})', text, re.I):
        add(y, MONTHS[m[:3].lower()], d)
    return found


def money(text):
    text = re.sub(r'(?<=\d)\s+(?=\d)', '', text)
    values = []
    for raw in re.findall(r'(?<![\d.])\d+(?:,\d+)*(?:\.\d{1,2})?(?![\d.])', text):
        if ',' in raw and not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?', raw):
            continue
        try:
            value = Decimal(raw.replace(',', ''))
            if value.is_finite():
                values.append(format(value, 'f'))
        except InvalidOperation:
            continue
    return set(values)


def times_in(text):
    values = set()
    for h, m, s, suffix in re.findall(r'(?<!\d)(\d{1,2})[:.](\d{2})(?::(\d{2}))?\s*(AM|PM)?\b', text, re.I):
        hour = int(h)
        if suffix:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if suffix.upper() == 'PM' else 0)
        if hour <= 23 and int(m) <= 59 and (not s or int(s) <= 59):
            values.add(f'{hour:02}:{m}' + (':' + s if s else ''))
    return values


def literal_fields(transcript):
    """Extract labelled literals; never use filenames, bank candidates or known customer identities."""
    data = {'raw_text': transcript}
    lines = [line.strip().strip('|').strip() for line in transcript.splitlines() if line.strip()]
    def labelled(patterns):
        found = []
        for pattern in patterns:
            for i, line in enumerate(lines):
                match = re.match(r'^(?:' + pattern + r')\s*(?:[:：|]\s*|\s+|$)(.*)', line, re.I)
                if match:
                    value = match[1].strip()
                    if not value and i + 1 < len(lines):
                        value = lines[i + 1]
                    if value:
                        found.append(value)
            if found:
                return found
        return []
    specs = {
        'payer_name': [r'Sender\s+Name', r'Sender\s+Details', r'Ordering\s+Customer', r'Customer\s+Name', r'Debit\s+Account', r'From', r'Payer(?:\s+Name)?'],
        'recipient_name': [r'(?:Beneficiary|Recipient)\s+(?:Account\s+)?(?:Name|NA\.\.\.)', r'Beneficiary', r'Recipient\s+Details', r'Transfer\s+to', r'To'],
        'recipient_account': [r'Credit\s+Account', r'(?:Recipient|Beneficiary)\s+Account(?:\s*(?:Number|No\.?|NU\.\.\.))?', r'Account\s+(?:No\.?|Number)'],
        'payer_account': [r'Debit\s+Account', r'(?:Sender|Payer)\s+Account(?:\s*(?:Number|No\.?))?', r'From'],
        'bank_name': [r'(?:Recipient|Beneficiary)\s+Bank(?:\s+Name)?', r'Bank'],
        'transaction_reference': [r'Transaction\s+(?:No\.?|ID|Reference)', r'Reference\s+(?:No\.?|ID)', r'Session\s+ID'],
        'business_reference': [r'(?:Order|Container|Mark)(?:\s+(?:Number|No\.?|Reference))?'],
        'fee': [r'(?:Transaction\s+)?(?:Fee|Charge|Commission)'],
        'total_debit': [r'Amount\s+Paid', r'Total(?:\s+(?:Debit|Paid|Amount))?'],
        'amount': [r'Transaction\s+Amount', r'Amount(?!\s+(?:in|paid|due|words))', r'Amt'],
        'transaction_date': [r'Transaction\s+Date(?:\s*(?:/|and)\s*Time)?', r'Date(?:\s*/\s*Time)?'],
        'transaction_time': [r'Transaction\s+Time', r'Time', r'Date\s*/\s*Time', r'Transaction\s+Date'],
    }
    for field, patterns in specs.items():
        values = labelled(patterns)
        if field in ('payer_name', 'recipient_name'):
            values = [re.split(r'\s*\(|\s+-\s+\d|\s*\|', v)[0].strip() for v in values]
            values = [v for v in values if re.search('[A-Za-z]', v) and not re.search(r'\d{5}|\b(?:account|bank|transfer|receipt)\b', v, re.I)]
        elif field.endswith('_account'):
            values = [m for v in values for m in re.findall(r'(?<![\d*Xx])\d{8,34}(?![\d*Xx])', v)]
        if len(set(values)) == 1:
            data[field] = values[0]
    # SMS fields are often on one line. Take only text inside an explicit field boundary.
    for field, label, ending in (
            ('amount', r'Amt', r'Date|Desc|Bal'),
            ('transaction_date', r'Date', r'Desc|Bal'),
            ('transaction_time', r'Date', r'Desc|Bal')):
        match = re.search(r'\b' + label + r'\s*:\s*(.*?)(?=\b(?:' + ending + r')\s*:|$)', transcript, re.I | re.S)
        if match:
            data[field] = match[1].strip()
    if not data.get('amount'):
        amounts = []
        for i, line in enumerate(lines):
            if re.fullmatch(r'[-+]?\s*(?:NGN|[N₦#₹₵])\s*\d[\d,. ]*', line, re.I):
                surrounding = ' '.join(lines[max(0, i-2):i+2])
                if not re.search(r'\b(?:loan|overdraft|balance|credit\s+limit|up\s+to)\b', surrounding, re.I):
                    amounts.append(line)
        if amounts and len(set().union(*(money(v) for v in amounts))) == 1:
            data['amount'] = amounts[0]
    if not data.get('transaction_date'):
        candidates = [line for line in lines if dates_in(line) and not re.search(r'\b(?:generated|printed|yesterday|today)\b', line, re.I)]
        if candidates and len(set().union(*(dates_in(v) for v in candidates))) == 1:
            data['transaction_date'] = candidates[0]
    if not data.get('transaction_time'):
        candidate = data.get('transaction_date', '')
        if times_in(candidate):
            data['transaction_time'] = candidate
    # Recipient Details blocks contain name, bank and account in that order in many e-wallet receipts.
    block = re.search(r'(?im)^\s*Recipient\s+Details\s*\n(.+?)(?=\n\s*(?:Sender|Transaction|Session)\b|\Z)', transcript, re.S)
    if block:
        accounts = re.findall(r'(?<![\d*Xx])\d{8,34}(?![\d*Xx])', block[1])
        if len(set(accounts)) == 1:
            data['recipient_account'] = accounts[0]
    for i, line in enumerate(lines):
        if re.search(r'\b(?:loan|overdraft|credit\s+limit)\b', ' '.join(lines[max(0, i-2):i+1]), re.I):
            continue
        if '₦' in line:
            data['currency'] = '₦'
            break
        if re.search(r'\bNGN\b|NGN(?=\d)', line, re.I):
            data['currency'] = 'NGN'
            break
        if re.search(r'\bnaira\b', line, re.I):
            data['currency'] = 'naira'
            break
    return data


def parse_flat_ocr(raw, field_names):
    text = raw.strip()
    if text.startswith('```') and text.endswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, count=1).removesuffix('```').strip()
    if text.startswith('{'):
        source = json.loads(text)
        if not isinstance(source, dict) or not isinstance(source.get('raw_text'), str):
            raise ValueError('Invalid OCR structure')
        data = literal_fields(source['raw_text'])
    else:
        data = literal_fields(raw)
    transcript = data['raw_text']
    lines = transcript.splitlines()
    fields, evidence, uncertain = {}, {}, []
    # Consecutive snippets preserve line-wrapped labels and numbers; never synthesize evidence.
    snippets = []
    for width in (1, 2, 3):
        for index in range(len(lines)):
            snippet = '\n'.join(lines[index:index + width]).strip()
            if snippet and len(snippet) <= 2000:
                snippets.append(snippet)
    receipt_markers = re.search(r'\b(?:transfer|transaction|beneficiary|receipt|credit|debit|amt)\b', transcript, re.I)
    for field in field_names:
        candidate = data.get(field)
        fields[field] = None
        if not isinstance(candidate, str) or candidate.strip().lower() in ('', 'null', 'none', 'n/a', 'unknown'):
            continue
        candidate = candidate.strip()
        proof = None
        normalized = candidate
        if field == 'transaction_date':
            possible = dates_in(candidate)
            for snippet in snippets:
                if re.search(r'\b(?:generated|printed|yesterday|today)\b', snippet, re.I):
                    continue
                matches = [v for v in possible if date_supported(v, snippet)]
                if len(matches) == 1:
                    normalized, proof = matches[0], snippet
                    break
        elif field == 'transaction_time':
            possible = times_in(candidate)
            # Prefer explicit transaction-time labels or a date/time line over an unlabeled clock.
            for snippet in snippets:
                if not re.search(r'\b(?:time|date|payment|transfer|transaction)\b|\d{4}', snippet, re.I):
                    continue
                matches = [v for v in possible if time_supported(v, snippet)]
                if len(matches) == 1:
                    normalized, proof = matches[0], snippet
                    break
        elif field in ('amount', 'fee', 'total_debit'):
            possible = money(candidate)
            if len(possible) == 1:
                normalized = next(iter(possible))
                role = (r'\b(?:fee|charge|commission)\b' if field == 'fee' else
                        r'\b(?:amount\s+paid|total|debited)\b' if field == 'total_debit' else
                        r'\b(?:amount|amt|transfer|payment|credit|debit)\b|(?:^|\s)[₦#N₹₵]\s*\d')
                for snippet in snippets:
                    if re.search(role, snippet, re.I) and any(Decimal(v) == Decimal(normalized) for v in money(snippet)):
                        proof = snippet
                        break
        elif field == 'currency':
            if candidate in ('₦', '=N=') or candidate.casefold() == 'naira':
                normalized = 'NGN'
            elif re.fullmatch(r'[a-zA-Z]{3}', candidate):
                normalized = candidate.upper()
            else:
                normalized = None
            proof = next((s for s in snippets if compact(candidate) in compact(s)), None)
        else:
            proof = next((s for s in snippets if compact(candidate) in compact(s)), None)
            if field == 'payer_name' and proof:
                roles = r'\b(?:sender|from|debit\s+account|ordering\s+customer|customer\s+name|payer)\b'
                proof = next((s for s in snippets if compact(candidate) in compact(s) and re.search(roles, s, re.I)), None)
        if proof and normalized and '?' not in candidate:
            fields[field] = normalized
            evidence[field] = proof
        else:
            uncertain.append(field)
    return {'is_receipt': bool(receipt_markers), 'raw_text': transcript,
            'fields': fields, 'evidence': evidence, 'uncertain_fields': uncertain,
            'source_issues': [], 'transaction_count': None}
