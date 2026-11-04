"""Source-linked context for candidate extraction, scoped to one WhatsApp message."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

CONTEXT_VERSION = 'message-context-v1'
# Nigerian bank business dates use WAT, which has no daylight-saving transition.
BUSINESS_ZONE = timezone(timedelta(hours=1), 'Africa/Lagos')
BEFORE_SECONDS = 3600
AFTER_SECONDS = 900


def encode(context):
    return json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(context):
    return hashlib.sha256(encode(context).encode()).hexdigest()


def message_context(message, texts=()):
    sent = datetime.fromtimestamp(message['sent_at'], timezone.utc)
    business_day = sent.astimezone(BUSINESS_ZONE).date()
    return {
        'version': CONTEXT_VERSION,
        'source_message': {key: message.get(key) for key in (
            'id', 'message_id', 'group_id', 'group_name', 'sender', 'sender_name', 'sender_phone',
            'reply_to_id', 'caption')},
        'sent_at_utc': sent.isoformat(),
        'sent_at_business': sent.astimezone(BUSINESS_ZONE).isoformat(),
        'message_sent_date': business_day.isoformat(),
        'message_sent_date_utc': sent.date().isoformat(),
        'business_timezone': 'Africa/Lagos (UTC+01:00)',
        'date_guidance': {
            'basis': 'User confirmed: usually sent on payment day; sometimes 2-3 days later, normally at most 3-5 days.',
            'normal_delay_days': 5,
            'likely_start': (business_day - timedelta(days=5)).isoformat(),
            'likely_end': business_day.isoformat(),
            'is_hard_cutoff': False,
            'instruction': 'Use this window to resolve missing years, ambiguous months and relative dates. '
                           'Allow previous month/year at boundaries. Preserve clear older dates; '
                           'a plausible inferred date is a candidate for bank comparison, not confirmation.'},
        'related_messages': list(texts),
    }


def date_assessment(candidate, origin, context):
    """Keep dates as candidates; report the likely window without fabricating a day."""
    if not context or not context.get('date_guidance'):
        return None
    guidance = context['date_guidance']
    result = {'likely_start': guidance['likely_start'], 'likely_end': guidance['likely_end'],
              'business_timezone': context['business_timezone'], 'inferred': origin.get('kind') == 'context_inferred',
              'source_message_id': context['source_message']['message_id'], 'within_likely_window': None}
    try:
        day = datetime.strptime(candidate, '%Y-%m-%d').date()
        start = datetime.strptime(guidance['likely_start'], '%Y-%m-%d').date()
        end = datetime.strptime(guidance['likely_end'], '%Y-%m-%d').date()
        result['within_likely_window'] = start <= day <= end
    except (TypeError, ValueError):
        pass
    return result
