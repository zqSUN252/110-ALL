"""Server export: append typed rows while preserving the user's original XLSX package.

The production Python container has no desktop Artifact Tool runtime. This writer
changes only the target worksheet and its added styles. openpyxl is read-only.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
import time
import uuid
import zipfile
from xml.sax.saxutils import escape, quoteattr

from defusedxml import ElementTree as SafeET
from xml.etree import ElementTree as ET
import openpyxl



NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
Q = lambda name: '{' + NS + '}' + name
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def inspect_template(data):
    if not 0 < len(data) <= 25 * 1024 * 1024:
        raise ValueError('归档模板为空或超过 25 MiB')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > 150 * 1024 * 1024 or len(z.infolist()) > 10000:
            raise ValueError('归档模板展开后过大')
        if any('vbaproject' in n.lower() for n in z.namelist()):
            raise ValueError('归档模板不能包含宏')
        if 'xl/worksheets/sheet1.xml' not in z.namelist() or z.testzip():
            raise ValueError('归档模板内容损坏')
        workbook = SafeET.fromstring(z.read('xl/workbook.xml'))
        if workbook.find(Q('workbookPr')) is not None and workbook.find(Q('workbookPr')).get('date1904') in ('1', 'true'):
            raise ValueError('目前归档模板需要使用标准 1900 日期系统')
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    try:
        sheet = book.worksheets[0]
        head = list(sheet.iter_rows(min_row=1, max_row=3, max_col=10, values_only=True))
        columns = [' '.join(str(row[n] or '') for row in head) for n in range(10)]
        if not ('日期' in columns[1] and 'Reference' in columns[2] and '付款人' in columns[3] and '汇率' in columns[6]):
            raise ValueError('归档模板列不匹配，需要原表的编号、日期、Reference、付款人、金额、汇率等列')
        if sheet.max_row > 100000 or sheet.max_column > 80:
            raise ValueError('归档模板规模超过限制')
        return {'sheet': sheet.title, 'last_row': sheet.max_row, 'sheet_names': book.sheetnames}
    finally:
        book.close()


def serialize_plain(element):
    e = deepcopy(element)
    for item in e.iter():
        if isinstance(item.tag, str):
            item.tag = item.tag.split('}')[-1]
    return ET.tostring(e, encoding='unicode', short_empty_elements=True)


def appended_styles(styles, template_styles):
    raw = styles.decode('utf-8')
    root = SafeET.fromstring(styles)
    xfs = root.find(Q('cellXfs'))
    numfmts = root.find(Q('numFmts'))
    fmt_id = max([163] + [int(n.get('numFmtId')) for n in list(numfmts) if numfmts is not None]) + 1 if numfmts is not None else 164
    custom = f'<numFmt numFmtId="{fmt_id}" formatCode="yyyy-mm-dd"/>'
    if numfmts is None:
        raw = re.sub(r'(<(?:\w+:)?styleSheet\b[^>]*>)', r'\1<numFmts count="1">' + custom + '</numFmts>', raw, count=1)
    else:
        replacement = '<numFmts count="' + str(len(numfmts) + 1) + '">' + ''.join(serialize_plain(x) for x in numfmts) + custom + '</numFmts>'
        raw = re.sub(r'<numFmts\b[^>]*(?:/>|>.*?</numFmts>)', lambda _: replacement, raw, count=1, flags=re.S)
    ids, extra = {}, []
    for column in 'ABCDEFGHIJ':
        base = int(template_styles.get(column, '0'))
        xf = deepcopy(xfs[base])
        if column in 'BF GHIJ'.replace(' ', ''):
            xf.set('numFmtId', str(fmt_id if column == 'B' else 4 if column in 'FJ' else 2))
            xf.set('applyNumberFormat', '1')
        if column in 'CD':
            alignment = xf.find(Q('alignment'))
            if alignment is None:
                alignment = ET.SubElement(xf, Q('alignment'))
            alignment.set('wrapText', '1')
            xf.set('applyAlignment', '1')
        ids[column] = len(xfs) + len(extra)
        extra.append(serialize_plain(xf))
    raw = re.sub(r'(<cellXfs\b[^>]*\bcount=")[0-9]+(")', lambda m: m[1] + str(len(xfs) + len(extra)) + m[2], raw, count=1)
    raw = raw.replace('</cellXfs>', ''.join(extra) + '</cellXfs>', 1)
    return raw.encode('utf-8'), ids


def cell(col, row, value, style, formula=None):
    attrs = f'r="{col}{row}" s="{style}"'
    if formula is not None:
        kind = ' t="str"' if value is None else ''
        return f'<c {attrs}{kind}><f>{escape(formula)}</f><v>{"" if value is None else escape(str(value))}</v></c>'
    if value is None:
        return f'<c {attrs}/>'
    if isinstance(value, (Decimal, int)):
        return f'<c {attrs}><v>{value}</v></c>'
    # Explicit inline strings preserve account IDs and cannot become formulas.
    return f'<c {attrs} t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def write_archive(template, records, output, label):
    original = Path(template).read_bytes()
    info = inspect_template(original)
    before = openpyxl.load_workbook(io.BytesIO(original), read_only=True, data_only=False, keep_links=False)
    sheet = before.worksheets[0]
    existing = {str(r[0]): r for r in sheet.iter_rows(min_row=3, max_col=10, values_only=True) if r[0]}
    wanted, repeated = [], []
    for record in records:
        if record['currency'] != 'NGN':
            raise ValueError('当前归档模板为 NGN，其他币种不能混入同一金额列')
        if record['code'] in existing:
            old = existing[record['code']]
            old_date = old[1].date().isoformat() if isinstance(old[1], datetime) else str(old[1])
            if Decimal(str(old[5])) != Decimal(record['amount']) or old_date != record['date'] or str(old[3]).strip() != record['description'].strip():
                raise ValueError('归档编号已存在但金额、日期或付款摘要不同：' + record['code'])
            repeated.append(record['code'])
        else:
            wanted.append(record)
            existing[record['code']] = [record['code'], record['date'], None, record['description'], None, record['amount']]
    with zipfile.ZipFile(io.BytesIO(original)) as z:
        xml = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
        root = SafeET.fromstring(xml)
        rows = root.find(Q('sheetData'))
        source_row = next((r for r in rows if r.get('r') == '24'), list(rows)[-1])
        styles = {re.sub('[0-9]', '', c.get('r')): c.get('s', '0') for c in source_row}
        new_styles, style_ids = appended_styles(z.read('xl/styles.xml'), styles)
        start = info['last_row'] + 1
        strings, written = [], []
        for index, r in enumerate(wanted, start):
            serial = (date.fromisoformat(r['date']) - date(1899, 12, 30)).days
            g = Decimal(str(r['fx_ngn_usd'])) if r.get('fx_ngn_usd') else None
            h = Decimal(str(r['fx_cny_usd'])) if r.get('fx_cny_usd') else None
            if (g is not None and g <= 0) or (h is not None and h <= 0):
                raise ValueError('结算汇率必须大于零')
            ratio = g / h if g and h else None
            values = [r['code'], serial, r.get('business_reference'), r['description'], None,
                      Decimal(r['amount']), g, h, ratio, Decimal(r['amount']) / ratio if ratio else None]
            content = []
            for col, value in zip('ABCDEFGHIJ', values):
                formula = f'IF(OR(G{index}="",H{index}=""),"",G{index}/H{index})' if col == 'I' else f'IF(I{index}="","",F{index}/I{index})' if col == 'J' else None
                content.append(cell(col, index, value, style_ids[col], formula))
            strings.append(f'<row r="{index}" ht="26" customHeight="1">' + ''.join(content) + '</row>')
            written.append({'code': r['code'], 'row': index, 'amount': r['amount']})
        total = sum((Decimal(r['amount']) for r in wanted), Decimal('0.00'))
        last = info['last_row']
        if written:
            last = start + len(written)
            strings.append(f'<row r="{last}" ht="26" customHeight="1">' + cell('D', last, label + '合计（NGN）', style_ids['D']) +
                           cell('F', last, total, style_ids['F'], f'SUM(F{start}:F{last-1})') + '</row>')
            xml = xml.replace('</sheetData>', ''.join(strings) + '</sheetData>', 1)
            xml = re.sub(r'(<dimension\b[^>]*ref=")[^"]+("[^>]*/>)', lambda m: m[1] + f'A1:J{last}' + m[2], xml, count=1)
        replacements = {'xl/worksheets/sheet1.xml': xml.encode(), 'xl/styles.xml': new_styles} if written else {}
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        part = output.with_suffix('.' + uuid.uuid4().hex + '.part')
        with zipfile.ZipFile(part, 'w', zipfile.ZIP_DEFLATED) as out:
            for item in z.infolist():
                out.writestr(deepcopy(item), replacements.get(item.filename, z.read(item.filename)))
        part.replace(output)
        with zipfile.ZipFile(output) as checked:
            preserved = [n for n in z.namelist() if n not in replacements]
            if checked.testzip() or any(checked.read(n) != z.read(n) for n in preserved):
                raise ValueError('归档文件结构校验失败')
    after = openpyxl.load_workbook(output, read_only=True, data_only=False, keep_links=False)
    cached = openpyxl.load_workbook(output, read_only=True, data_only=True, keep_links=False)
    try:
        if before.sheetnames != after.sheetnames:
            raise ValueError('原工作表没有完整保留')
        for old_sheet, new_sheet in zip(before.worksheets, after.worksheets):
            for old_row, new_row in zip(old_sheet.iter_rows(), new_sheet.iter_rows(max_row=old_sheet.max_row, max_col=old_sheet.max_column)):
                if any((a.value, a.number_format) != (b.value, b.number_format) for a, b in zip(old_row, new_row)):
                    raise ValueError('原有单元格内容或格式发生变化')
        for position, record in zip(written, wanted):
            row = position['row']
            cells = list(after.worksheets[0].iter_rows(min_row=row, max_row=row, max_col=10))[0]
            if cells[0].value != record['code'] or Decimal(str(cells[5].value)) != Decimal(record['amount']) or cells[1].value.date().isoformat() != record['date']:
                raise ValueError('归档日期或金额回读不一致')
        if written and Decimal(str(cached.worksheets[0].cell(last, 6).value)) != total:
            raise ValueError('归档合计校验失败')
    finally:
        before.close(); after.close(); cached.close()
    return {'written': len(written), 'existing': len(repeated), 'total_ngn': str(total),
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'original_parts_preserved': len(preserved),
            'round_trip_verified': True, 'missing_rates': sum(not r.get('fx_ngn_usd') or not r.get('fx_cny_usd') for r in wanted),
            'missing_reference': sum(not r.get('business_reference') for r in wanted)}
