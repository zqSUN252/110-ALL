"""Reproducible synthetic originals for browser acceptance; never customer data."""
from pathlib import Path
from PIL import Image
from openpyxl import Workbook


def main():
    root=Path(__file__).resolve().parents[1]/'private/ui-validation/browser-assets'
    root.mkdir(parents=True,exist_ok=True)
    book=Workbook();sheet=book.active;sheet.title='Access'
    sheet.append(['Date','Credit','Debit','Description','Reference'])
    sheet.append(['01/11/2026',1000,0,'SYNTHETIC BROWSER PAYER','UIFULL-REFERENCE'])
    book.save(root/'bank.xlsx')
    for value in (20,40,60):Image.new('RGB',(120,80),(value,100,180)).save(root/f'evidence-{value}.png')
    print('Synthetic browser originals prepared under private/ui-validation/browser-assets.')


if __name__=='__main__':main()
