"""Read bank workbooks as evidence; never infer that a receipt has been paid."""
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import html
from pathlib import Path
import re
import zipfile

import openpyxl
import xlrd

MAX_ROWS = 100_000
MAX_COLS = 80
MAX_EXPANDED = 150 * 1024 * 1024


class BankFileError(ValueError):
    pass


def text(value):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return html.unescape(str(value)).strip()[:4000]


def date_value(value, order='auto'):
    value = text(value)
    if not value:
        return None, '缺少日期'
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%b-%Y', '%d-%b-%y'):
        try:
            parsed = datetime.strptime(value, fmt).date()
            if not 2000 <= parsed.year <= 2100:
                return None, '日期年份异常，请核实原始单元格'
            return parsed.isoformat(), None
        except ValueError:
            pass
    match = re.fullmatch(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', value)
    if match:
        a, b, year = map(int, match.groups())
        if order == 'auto' and a <= 12 and b <= 12 and a != b:
            return None, '日月顺序不明确，请选择日期格式'
        month, day = (a, b) if order == 'mdy' or (order == 'auto' and b > 12) else (b, a)
        try:
            parsed = date(year, month, day)
            if not 2000 <= year <= 2100:
                return None, '日期年份异常，请核实原始单元格'
            return parsed.isoformat(), None
        except ValueError:
            pass
    return None, '日期无法识别'


def money(value):
    raw = text(value).replace(',', '').replace(' ', '').replace('₦', '')
    if raw in ('', '-', '—'):
        return Decimal('0.00')
    if raw.startswith('(') and raw.endswith(')'):
        raw = '-' + raw[1:-1]
    try:
        number = Decimal(raw)
        if not number.is_finite() or abs(number) > Decimal('999999999999999'):
            raise InvalidOperation
        rounded = number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if abs(number - rounded) > Decimal('0.000001'):
            raise InvalidOperation
        return rounded
    except InvalidOperation:
        raise BankFileError('金额不是有效的两位小数数值')


def canonical(value):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text(value).lower())


ALIASES = {
    'date': ['valuedate', 'bookingdate', 'postingdate', '入账日期', '记账日期', 'trandate', 'transactiondate', '交易日期', '日期', 'date'],
    'credit': ['deposit', 'deposits', 'credit', 'creditamount', '入账金额', '收入金额', '贷方发生额', '收入', '存入'],
    'debit': ['withdrawal', 'withdrawals', 'debit', 'debitamount', '支出金额', '借方发生额', '支出', '取出'],
    'description': ['details', 'description', 'narration', 'narrative', '摘要', '交易说明', '备注'],
    'reference': ['reference', 'transactionreference', '参考号', '交易参考号', '流水号'],
}


def header_mapping(rows):
    for index, values in enumerate(rows[:60]):
        keys = [canonical(v) for v in values]
        mapping = {}
        for field, aliases in ALIASES.items():
            for alias in aliases:
                if alias in keys:
                    mapping[field] = keys.index(alias)
                    break
        if 'date' in mapping and ('credit' in mapping or 'debit' in mapping):
            return index, mapping
    return None, {}


def workbook_rows(path: Path):
    signature = path.read_bytes()[:8]
    if signature == bytes.fromhex('d0cf11e0a1b11ae1'):
        book = xlrd.open_workbook(str(path), on_demand=True)
        try:
            if sum(s.nrows for s in book.sheets()) > MAX_ROWS or len(book.sheet_names()) > 40:
                raise BankFileError('工作簿过大，请按账户或期间拆分上传')
            for sheet in book.sheets():
                if sheet.ncols > MAX_COLS:
                    raise BankFileError('工作表列数超过限制')
                rows = []
                for i in range(sheet.nrows):
                    values = []
                    for cell in sheet.row(i):
                        value = cell.value
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            try:
                                value = xlrd.xldate_as_datetime(value, book.datemode).date().isoformat()
                            except (ValueError, OverflowError):
                                pass
                        values.append(value)
                    rows.append(values)
                yield sheet.name, rows, book.datemode
        finally:
            book.release_resources()
    elif signature[:4] == b'PK\x03\x04':
        with zipfile.ZipFile(path) as package:
            infos = package.infolist()
            if len(infos) > 10_000 or sum(p.file_size for p in infos) > MAX_EXPANDED:
                raise BankFileError('Excel 解压后的内容超过限制')
            if any('vbaProject' in p.filename for p in infos):
                raise BankFileError('请上传不含宏的银行工作簿')
        book = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            if len(book.sheetnames) > 40:
                raise BankFileError('工作表数量超过限制')
            used = 0
            for sheet in book:
                if (sheet.max_column or 0) > MAX_COLS:
                    raise BankFileError('工作表列数超过限制')
                rows = []
                for values in sheet.iter_rows(values_only=True):
                    used += 1
                    if used > MAX_ROWS:
                        raise BankFileError('工作簿行数超过限制')
                    rows.append(list(values))
                yield sheet.title, rows, 1 if book.epoch.year == 1904 else 0
        finally:
            book.close()
    else:
        raise BankFileError('文件内容不是有效的 XLS 或 XLSX 工作簿')


def read_workbook(path: Path):
    sheets = []
    try:
        for name, rows, datemode in workbook_rows(path):
            start, mapping = header_mapping(rows)
            bank = 'FCMB' if 'FCMB' in name.upper() else ('Fidelity' if re.search(r'FIDEL', name, re.I) else ('Access' if 'ACCESS' in name.upper() else ''))
            summary = {'name': name, 'bank_suggestion': bank, 'account_suggestion': name,
                       'header_row': start + 1 if start is not None else None,
                       'headers': [text(v) for v in rows[start]] if start is not None else [],
                       'rows': [], 'issues': [], 'skipped_rows': 0}
            if start is None:
                summary['error'] = '未找到日期与收入／支出列，原文件已保留'
                sheets.append(summary)
                continue
            for index in range(start + 1, len(rows)):
                values = rows[index]
                def cell(field):
                    column = mapping.get(field)
                    return values[column] if column is not None and column < len(values) else None
                if not any(text(v) for v in values):
                    continue
                try:
                    credit, debit = money(cell('credit')), money(cell('debit'))
                except BankFileError as exc:
                    summary['issues'].append({'row': index + 1, 'reason': str(exc)})
                    continue
                if credit == 0 and debit == 0:
                    summary['skipped_rows'] += 1
                    continue
                raw_date = cell('date')
                if isinstance(raw_date, (int, float)) and 10_000 < raw_date < 80_000:
                    raw_date = xlrd.xldate_as_datetime(raw_date, datemode).date().isoformat()
                date_iso, date_error = date_value(raw_date)
                errors = [date_error] if date_error else []
                if credit < 0 or debit < 0 or (credit and debit):
                    errors.append('收入／支出方向需要核实')
                summary['rows'].append({'row': index + 1, 'raw_date': text(raw_date), 'date': date_iso,
                    'credit': str(credit), 'debit': str(debit), 'description': text(cell('description')),
                    'reference': text(cell('reference')),
                    'source_code': text(values[0]) if mapping['date'] != 0 else '',
                    'raw_values': [text(v) for v in values], 'issues': errors})
            sheets.append(summary)
    except BankFileError:
        raise
    except Exception as exc:
        raise BankFileError('Excel 无法读取，可能已损坏、加密或格式不受支持') from exc
    if not sheets or not any(s['rows'] for s in sheets):
        raise BankFileError('没有识别到可导入的银行交易。需要包含日期和收入／支出列')
    return {'sheets': sheets}


def sheet_preview(sheet):
    rows = sheet['rows']
    dates = [r['date'] for r in rows if r['date']]
    return {k: v for k, v in sheet.items() if k != 'rows'} | {
        'row_count': len(rows), 'preview': rows[:8],
        'credit_total': str(sum((Decimal(r['credit']) for r in rows), Decimal('0.00'))),
        'debit_total': str(sum((Decimal(r['debit']) for r in rows), Decimal('0.00'))),
        'first_transaction_date': min(dates) if dates else None,
        'last_transaction_date': max(dates) if dates else None,
        'ambiguous_dates': sum(bool(r['issues']) for r in rows),
        'issue_count': len(sheet['issues']), 'issues': sheet['issues'][:30]}
