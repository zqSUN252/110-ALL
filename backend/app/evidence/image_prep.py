"""Deterministic image derivations for the second read.

Original bytes are never rewritten. Every derived image stays next to the
source and records its trim box, band, scale and sha256, so the second read is
byte-reproducible and no generative enhancement is ever applied. Crop bands are
starting values measured on the 46-image benchmark; retune them only after a
real accuracy run, never by eye.
"""
import math
import re

from PIL import Image, ImageFilter, ImageOps

# A detail image only has to be legible. Past roughly a megapixel the vision
# encoder downscales it again, so a larger copy costs tokens without adding
# detail: measured on a phone photo, four uncapped bands tripled prompt tokens.
MAX_LONG_EDGE = 2048
MAX_PIXELS = 1_500_000
MAX_UPSCALE = 3.0
MAX_DETAILS = 4
INK_THRESHOLD = 246
MIN_KEEP = 0.3

LAYOUTS = ('bank_receipt', 'pos_slip', 'sms', 'handwritten_form', 'business_doc',
           'chat_screenshot', 'unknown')

BANDS = {
    'bank_receipt': ((0.00, 0.02, 1.00, 0.46), (0.00, 0.40, 1.00, 0.82)),
    'pos_slip': ((0.00, 0.00, 1.00, 0.60), (0.00, 0.35, 1.00, 0.90)),
    'sms': ((0.00, 0.00, 1.00, 0.60), (0.00, 0.35, 1.00, 0.85)),
    'handwritten_form': ((0.00, 0.05, 1.00, 0.70), (0.00, 0.25, 1.00, 0.95)),
    'business_doc': ((0.00, 0.00, 1.00, 0.45), (0.00, 0.30, 1.00, 0.92)),
    'chat_screenshot': ((0.00, 0.00, 1.00, 0.45), (0.00, 0.30, 1.00, 0.92)),
    'unknown': ((0.00, 0.00, 1.00, 0.60), (0.00, 0.38, 1.00, 1.00)),
}

HANDWRITING_MARKERS = ('COUNTER CHEQUE', 'AMOUNT IN WORDS', 'APPLICANT NAME', 'CHEQUE NO',
                       'FUNDS TRANSFER FORM', 'PLEASE TICK', 'TYPE OF TRANSFER')
BUSINESS_MARKERS = ('RELEASE INSTRUCTION', 'PACKING LIST', 'WAREHOUSE', 'SHIPMENT DETAILS')
POS_MARKERS = ('POS', 'TERMINAL ID', 'TERMINAL:', 'MERCHANT ID', 'CARD NO', 'STAN', 'RRN',
               'MONIEPOINT', 'BUSINESS BANKING', 'RESPONSE CODE', 'TRANSFER RECEIPT NO')
# Nigerian debit alerts separate the label from the colon by a space.
SMS_PATTERNS = (r'\bDEBIT ALERT', r'\bCREDIT ALERT', r'\bBAL\s*:', r'\bBALANCE\s*:',
                r'\bACCT\s*NO\s*:', r'\bACCT\s*:', r'\bACC\s*:', r'\bAVAILABLE BAL',
                r'\bDR\s*:\s*NGN', r'\bCR\s*:\s*NGN', r'\bDEBIT AMT\b', r'\bDESC\s*:')
RECEIPT_MARKERS = ('TRANSACTION RECEIPT', 'TRANSFER SUCCESSFUL', 'BENEFICIARY', 'NARRATION',
                   'TRANSACTION DATE', 'CREDIT ACCOUNT', 'DEBIT ACCOUNT', 'PAYMENT RECEIPT',
                   'RECIPIENT DETAILS', 'PAYMENT SUMMARY', 'RECEIPT DETAILS', 'SESSION ID')
ENHANCE_ISSUES = ('illegible_handwriting', 'cropped_key_fields')
# Ordered by how much each field decides matching.
LOCATED_FIELDS = ('amount', 'recipient_name', 'transaction_date', 'payer_name')


def _has(text, markers):
    for marker in markers:
        # A trailing boundary keeps STAN from matching STANDARD.
        pattern = r'\b' + re.escape(marker) + (r'\b' if marker[-1:].isalnum() else '')
        if re.search(pattern, text):
            return True
    return False


def _matches(text, patterns):
    return any(re.search(pattern, text) for pattern in patterns)


def classify_layout(raw_text, size, source_issues=()):
    """Infer the document family from the first read and the image shape.

    Classification is a crop hint only. It never changes extracted fields and
    an unknown result stays valid.
    """
    text = (raw_text or '').upper()
    issues = {str(item) for item in (source_issues or ())}
    if _has(text, HANDWRITING_MARKERS):
        return 'handwritten_form'
    if _has(text, BUSINESS_MARKERS):
        return 'business_doc'
    if _has(text, POS_MARKERS):
        return 'pos_slip'
    if _matches(text, SMS_PATTERNS) and not _has(text, RECEIPT_MARKERS):
        return 'sms'
    if _has(text, RECEIPT_MARKERS):
        return 'bank_receipt'
    if 'illegible_handwriting' in issues:
        return 'handwritten_form'
    width, height = size
    if width and height >= width * 1.6:
        return 'chat_screenshot'
    return 'unknown'


def upright(image):
    """Apply EXIF orientation to a derived copy; the stored original is never rewritten."""
    return ImageOps.exif_transpose(image).convert('RGB')


def content_box(image):
    """Trim near-uniform margins around the document content.

    Phone photos of a form and chat screenshots carry large empty borders; the
    same bands applied to the full frame can miss the field entirely.
    """
    width, height = image.size
    table = [255 if value < INK_THRESHOLD else 0 for value in range(256)]
    box = image.convert('L').point(table).getbbox()
    if not box:
        return (0, 0, width, height)
    left, top, right, bottom = box
    if (right - left) < width * MIN_KEEP or (bottom - top) < height * MIN_KEEP:
        return (0, 0, width, height)
    pad_x = max(2, int((right - left) * 0.02))
    pad_y = max(2, int((bottom - top) * 0.02))
    return (max(0, left - pad_x), max(0, top - pad_y),
            min(width, right + pad_x), min(height, bottom + pad_y))


def scale_for(size):
    width, height = size
    if width < 1 or height < 1:
        return 1.0
    factor = min(MAX_UPSCALE, MAX_LONG_EDGE / max(width, height))
    if width * height * factor * factor > MAX_PIXELS:
        factor = math.sqrt(MAX_PIXELS / (width * height))
    return max(1.0, round(factor, 3))


def render(crop, scale, enhanced):
    """Upscale one region; the enhanced copy is a contrast and denoise view, not a redraw."""
    # Flooring keeps the resized copy inside the pixel ceiling above.
    target = (max(1, int(crop.width * scale)), max(1, int(crop.height * scale)))
    part = crop.resize(target, Image.Resampling.LANCZOS)
    if enhanced:
        part = ImageOps.autocontrast(part.convert('L'), cutoff=1)
        part = part.filter(ImageFilter.MedianFilter(3))
        part = part.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
    return part.convert('RGB')


def needs_enhancement(layout, source_issues=()):
    issues = {str(item) for item in (source_issues or ())}
    return layout == 'handwritten_form' or bool(issues.intersection(ENHANCE_ISSUES))


def _profile(region, axis):
    """Ink coverage per row (axis='y') or per column (axis='x')."""
    table = [255 if value < INK_THRESHOLD else 0 for value in range(256)]
    mask = region.convert('L').point(table)
    size = (1, mask.height) if axis == 'y' else (mask.width, 1)
    thin = mask.resize(size, Image.Resampling.BOX)
    count = mask.height if axis == 'y' else mask.width
    return [thin.getpixel((0, y) if axis == 'y' else (y, 0)) for y in range(count)]


def snap_to_gap(profile, target, window):
    """Move a cut to the emptiest nearby position so no line is sliced in half.

    Ties fall back to the closest position, and a window without a real gap
    keeps the original target rather than drifting far from the plan.
    """
    total = len(profile)
    if not total:
        return target
    low = max(0, min(target - window, total - 1))
    high = max(low + 1, min(target + window + 1, total))
    return min(range(low, high), key=lambda index: (profile[index], abs(index - target)))


def _window(size):
    return max(3, int(size * 0.02))


def band_crop(region, band, landscape):
    """One layout band with both cut edges snapped into a gap between lines."""
    width, height = region.size
    if landscape:
        profile = _profile(region, 'x')
        window = _window(width)
        left = snap_to_gap(profile, int(width * band[0]), window)
        right = snap_to_gap(profile, int(width * band[2]), window)
        right = min(width, max(right, left + 8))
        return region.crop((left, int(height * band[1]), right, int(height * band[3])))
    profile = _profile(region, 'y')
    window = _window(height)
    top = snap_to_gap(profile, int(height * band[1]), window)
    bottom = snap_to_gap(profile, int(height * band[3]), window)
    bottom = min(height, max(bottom, top + 8))
    return region.crop((int(width * band[0]), top, int(width * band[2]), bottom))


def field_crop(region, trim, full_size, box, landscape):
    """Magnify one located field with padding, keeping its line intact.

    The model's box is relative to the full frame, so the trim offset is
    removed before cropping and the vertical edges are snapped to a gap.
    """
    width, height = full_size
    left = box[0] * width - trim[0]
    top = box[1] * height - trim[1]
    right = box[2] * width - trim[0]
    bottom = box[3] * height - trim[1]
    if right - left < 2 or bottom - top < 2:
        return None
    pad_x = max(8.0, (right - left) * 0.4)
    pad_y = max(6.0, (bottom - top) * 0.6)
    left, right = left - pad_x, right + pad_x
    top, bottom = top - pad_y, bottom + pad_y
    region_width, region_height = region.size
    left = max(0.0, min(left, region_width - 1))
    right = max(left + 1.0, min(right, float(region_width)))
    top = max(0.0, min(top, region_height - 1))
    bottom = max(top + 1.0, min(bottom, float(region_height)))
    if landscape:
        profile = _profile(region, 'x')
        window = _window(region_width)
        left = snap_to_gap(profile, int(left), window)
        right = snap_to_gap(profile, int(right), window)
        right = min(region_width, max(right, left + 8))
    else:
        profile = _profile(region, 'y')
        window = _window(region_height)
        top = snap_to_gap(profile, int(top), window)
        bottom = snap_to_gap(profile, int(bottom), window)
        bottom = min(region_height, max(bottom, top + 8))
    crop = region.crop((int(left), int(top), int(right), int(bottom)))
    return crop if crop.width >= 8 and crop.height >= 8 else None


def orient(band, landscape):
    """Landscape sources split left/right; portrait sources split top/bottom."""
    left, top, right, bottom = band
    return (top, left, bottom, right) if landscape else band


def build_details(source, layout, source_issues=(), boxes=None):
    """Return up to four derived detail images plus the trim box that produced them.

    Located fields come first because they are the fields that decide matching;
    fixed bands follow as a fallback and as context around a located field.
    """
    trim = content_box(source)
    region = source.crop(trim)
    enhanced = needs_enhancement(layout, source_issues)
    landscape = source.width > source.height
    groups = []
    for name in LOCATED_FIELDS:
        box = (boxes or {}).get(name)
        if not box:
            continue
        crop = field_crop(region, trim, source.size, box, landscape)
        if crop is not None:
            groups.append({'target': name, 'crop': crop})
    # With three located fields the budget buys more coverage than a second
    # view of the same two fields.
    doubled = len(groups) <= 2
    for index, band in enumerate(BANDS.get(layout, BANDS['unknown']), 1):
        oriented = orient(band, landscape)
        crop = band_crop(region, oriented, landscape)
        if crop.width >= 8 and crop.height >= 8:
            groups.append({'target': 'band-%d' % index, 'band': list(oriented), 'crop': crop})
    details = []
    for group in groups:
        scale = scale_for(group['crop'].size)
        for kind in (('plain', 'enhanced') if enhanced and doubled else ('plain',)):
            if len(details) >= MAX_DETAILS:
                break
            details.append({'kind': kind, 'target': group['target'], 'band': group.get('band'),
                            'trim': list(trim), 'scale': scale,
                            'image': render(group['crop'], scale, kind == 'enhanced')})
    return {'layout': layout, 'trim': list(trim), 'enhanced': enhanced, 'details': details}
