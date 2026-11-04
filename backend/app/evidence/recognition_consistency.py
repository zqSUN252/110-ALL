"""Normalize explicit date/time text while retaining the model's original candidates."""
from datetime import date
import json
import re

from .ocr_fields import dates_in, times_in
from .recognition_validation import compact, date_supported, time_supported


def normalize(data, context=None):
    data = json.loads(json.dumps(data))
    changes = []
    fields, evidence, transcript = data['fields'], data['evidence'], data['raw_text']
    origins = data.get('field_origins') if isinstance(data.get('field_origins'), dict) else {}
    origin = origins.get('transaction_date') or {}
    candidate = fields.get('transaction_date')
    # Do not accept an invented old year in place of a fully transcribed transaction date.
    # This is an optional consistency correction, not a label requirement for extraction.
    if isinstance(candidate, str) and isinstance(origin, dict) and origin.get('kind') == 'context_inferred':
        options = {}
        lines = transcript.splitlines()
        for index, line in enumerate(lines):
            if not re.search(r'\bdate(?:\s*(?:/|&|and)\s*time)?\b', line, re.I):
                continue
            snippet = line if dates_in(line) else '\n'.join(lines[index:index+2])
            for value in dates_in(snippet):
                if date_supported(value, snippet):
                    options[value] = snippet
        if len(options) == 1:
            literal, proof = next(iter(options.items()))
            try:
                a, b = date.fromisoformat(candidate), date.fromisoformat(literal)
                guide = context.get('date_guidance', {}) if context else {}
                prefer_literal = (not guide or (guide['likely_start'] <= literal <= guide['likely_end']
                                               and not guide['likely_start'] <= candidate <= guide['likely_end']))
                if a.year != b.year and (a.month, a.day) == (b.month, b.day) and prefer_literal:
                    fields['transaction_date'] = literal
                    evidence['transaction_date'] = proof
                    origins['transaction_date'] = {'kind': 'visible', 'basis': '原文已有完整交易日期，保留票面年份；模型另外推测的旧年份未作为主候选。', 'source_ids': []}
                    changes.append({'field': 'transaction_date', 'from': candidate, 'to': literal,
                                    'reason': 'preserve_explicit_year', 'evidence': proof})
            except (ValueError, KeyError):
                pass
    value, proof = fields.get('transaction_time'), evidence.get('transaction_time')
    if isinstance(value, str) and isinstance(proof, str):
        for match in re.finditer(r'(?<!\d)\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:AM|PM)\b', proof, re.I):
            literal = match[0]
            possibilities = times_in(literal)
            given = times_in(value)
            if len(possibilities) != 1 or len(given) != 1:
                continue
            normalized = next(iter(possibilities)); original = next(iter(given))
            if normalized == original or normalized[2:] != original[2:] or abs(int(normalized[:2])-int(original[:2])) != 12:
                continue
            if compact(literal) not in compact(transcript) or not time_supported(normalized, proof):
                continue
            pattern = r'\s*'.join(re.escape(c) for c in re.sub(r'\s+', '', literal))
            actual = re.search(pattern, transcript, re.I)
            if not actual:
                continue
            fields['transaction_time'] = normalized
            evidence['transaction_time'] = actual[0]
            origins['transaction_time'] = {'kind': 'visible', 'basis': '按原文 AM/PM 转为 24 小时制，保留相同的分和秒。', 'source_ids': []}
            changes.append({'field': 'transaction_time', 'from': value, 'to': normalized,
                            'reason': 'explicit_meridiem', 'evidence': actual[0]})
            break
    data['field_origins'] = origins
    return data, changes
