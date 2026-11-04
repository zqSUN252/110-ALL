"""Conservative checks against a model's own transcription, not proof the image was read correctly."""
from datetime import date
from decimal import Decimal
import re
import unicodedata

MONTHS = {name: i for i, name in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun',
                                           'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
PAYER_ROLE = r'\b(?:sender|from|ordering\s+customer|customer\s+name|debit\s+account|originator|payer|applicant\s+details\s*\(debit\))\b'
RECIPIENT_ROLE = r'\b(?:recipient|beneficiary|credit\s+account|transfer\s+to|to)\b'


def role_evidence(value, proof, transcript, role=PAYER_ROLE):
    """Include the actual party section heading when the model cites only its value."""
    if isinstance(proof, str) and re.search(role, proof, re.I):
        # A shortened quotation may omit an intervening account line; use the real section below.
        if compact(proof) in compact(transcript):
            return proof
    if not isinstance(value, str) or not value:
        return proof
    # Locate the value with whitespace differences only; retain exact source bytes in the excerpt.
    pattern = r'\s*'.join(re.escape(c) for c in re.sub(r'\s+', '', value))
    for occurrence in re.finditer(pattern, transcript, re.I):
        start = max(0, occurrence.start() - 400)
        preceding = transcript[start:occurrence.start()]
        markers = list(re.finditer(PAYER_ROLE + '|' + RECIPIENT_ROLE + r'|\b(?:merchant|operator)\b', preceding, re.I))
        if markers and re.fullmatch(role, markers[-1][0], re.I):
            return transcript[start + markers[-1].start():occurrence.end()]
    return proof


def payer_evidence(value, proof, transcript):
    return role_evidence(value, proof, transcript, PAYER_ROLE)


def recipient_evidence(value, proof, transcript):
    return role_evidence(value, proof, transcript, RECIPIENT_ROLE)


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


def amount_evidence(value, proof, transcript):
    """Recover a literal standalone amount line when only its quoted currency sign differs.

    Never search a balance, fee or total for a replacement, and never change the model's amount.
    This is a transcription-consistency check, not image or bank verification.
    """
    if not isinstance(value, str) or not isinstance(proof, str) or compact(proof) in compact(transcript):
        return proof
    pattern = r'\s*(?:(?:[A-Z]{3}|[₦$€£#]|=N=)\s*)?(\d{1,3}(?:,\s*\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*'
    quoted = re.fullmatch(pattern, proof)
    if not quoted or not amount_supported(value, proof, 'amount'):
        return proof
    candidates = []
    previous = ''
    for line in transcript.splitlines():
        if re.fullmatch(pattern, line) and amount_supported(value, line, 'amount'):
            if not re.search(r'\b(?:bal(?:ance)?|loan|limit|fee|charge|commission|total|amount\s+paid)\b', previous, re.I):
                candidates.append(line)
        if line.strip():
            previous = line
    return candidates[0] if len(candidates) == 1 else proof


def date_supported(value, proof):
    """Require a full visible year and an unambiguous date expression, without using file/bank dates."""
    target = date.fromisoformat(value)
    if not re.search(r'(?<!\d)' + str(target.year) + r'(?!\d)', proof):
        return False
    if re.search(r'\b(?:yesterday|today|tomorrow|generated|printed)\b', proof, re.I):
        return False
    candidates = set()
    def add(y, m, d):
        try:
            candidates.add(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for y, m, d in re.findall(r'(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)', proof):
        add(y, m, d)
    for a, b, y in re.findall(r'(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](\d{4})(?!\d)', proof):
        a, b = int(a), int(b)
        if a > 12 or a == b or re.search(r'dd\s*[/.-]\s*mm\s*[/.-]\s*yyyy', proof, re.I):
            add(y, b, a)
        elif b > 12 or re.search(r'mm\s*[/.-]\s*dd\s*[/.-]\s*yyyy', proof, re.I):
            add(y, a, b)
        else:
            # Do not silently choose one interpretation even when another date appears in the citation.
            return False
    month = r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    for d, m, y in re.findall(r'(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s,.-]+' + month + r'[\s,.-]+(\d{4})(?!\d)', proof, re.I):
        add(y, MONTHS[m[:3].lower()], d)
    for m, d, y in re.findall(r'\b' + month + r'[\s,.-]+(\d{1,2})(?:st|nd|rd|th)?[\s,.-]+(\d{4})(?!\d)', proof, re.I):
        add(y, MONTHS[m[:3].lower()], d)
    return candidates == {target}


def time_supported(value, proof):
    if re.search(r'\b(?:yesterday|today|status\s*bar|generated|printed|received\s+by\s+bank)\b', proof, re.I):
        return False
    expected = value.split(':')
    for h, m, s, suffix in re.findall(r'(?<!\d)(\d{1,2})[:.](\d{2})(?::(\d{2}))?\s*(AM|PM)?\b', proof, re.I):
        hour = int(h)
        if suffix:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if suffix.upper() == 'PM' else 0)
        if hour > 23 or int(m) > 59 or (s and int(s) > 59):
            continue
        actual = [f'{hour:02}', m] + ([s] if s else [])
        # Missing seconds cannot be invented as :00, but discarding visible seconds is also not accepted.
        if actual == expected:
            return True
    return False


def amount_supported(value, proof, field):
    if field == 'amount' and re.search(r'\b(?:bal(?:ance)?|loan|credit\s+limit|amount\s+paid|total\s+(?:debit|paid|payable))\b', proof, re.I):
        return False
    if field == 'fee' and not re.search(r'\b(?:fee|charge|commission)\b', proof, re.I):
        return False
    if field == 'total_debit' and not re.search(r'\b(?:amount\s+paid|total|debited)\b', proof, re.I):
        return False
    # Join only whitespace between digits, for SMS line wrapping and grouped account numbers.
    joined = re.sub(r'(?<=\d)\s+(?=\d)', '', proof)
    # A typeset thousands separator may be followed by whitespace or a line break.
    # Retain the comma and still require complete 3-digit grouping below.
    joined = re.sub(r'(?<=\d),\s+(?=\d)', ',', joined)
    for raw in re.findall(r'(?<![\d.])\d+(?:,\d+)*(?:\.\d{1,2})?(?![\d.])', joined):
        if ',' in raw and not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?', raw):
            continue
        if Decimal(raw.replace(',', '')) == Decimal(value):
            return True
    return False


def supported(field, value, proof, transcript, semantic_role=False):
    if compact(proof) not in compact(transcript):
        return False
    if field == 'transaction_date':
        return date_supported(value, proof)
    if field == 'transaction_time':
        return time_supported(value, proof)
    if field in ('amount', 'fee', 'total_debit'):
        return amount_supported(value, proof, field)
    if field.endswith('_account'):
        if re.search(r'[?*…]|[xX]{2,}|[•●]{2,}', proof) or re.search(r'[?*…]|[xX]{2,}|[•●]{2,}', value):
            return False
        normalized = re.sub(r'\s+', '', value)
        return bool(re.fullmatch(r'[A-Za-z0-9]{8,34}', normalized)) and compact(normalized) in compact(proof)
    # Role assignment is a semantic/layout candidate from the model, not a keyword gate.
    # Literal support still prevents expanding a truncated name into an unseen full name.
    if field == 'payer_name' and not semantic_role and re.search(r'\b(?:initiator|merchant|operator)\b', proof, re.I):
        # A contradictory role needs the model's layout explanation. Keep the raw candidate regardless.
        return False
    if field == 'currency':
        if re.search(r'(?<![a-z])' + re.escape(value) + r'(?![a-z])', proof, re.I):
            return True
        if value == 'NGN':
            return '₦' in proof or bool(re.search(r'\bnaira\b', proof, re.I))
        return False
    return compact(value) in compact(proof)
