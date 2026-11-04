"""Read only a bounded provider error code; never expose upstream response text."""
import json


async def is_arrearage(response):
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        if len(raw) + len(chunk) > 16384:
            return False
        raw.extend(chunk)
    try:
        value = json.loads(raw)
        error = value.get('error', value)
        return isinstance(error, dict) and (error.get('code') == 'Arrearage' or error.get('type') == 'Arrearage')
    except (ValueError, TypeError, AttributeError):
        return False
