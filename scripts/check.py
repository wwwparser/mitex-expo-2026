"""Содержательные проверки целостности снимка и автономной сборки."""
import csv
import json
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
records=json.loads((ROOT/'data/site/exhibitors.json').read_text(encoding='utf-8'))
meta=json.loads((ROOT/'data/site/meta.json').read_text(encoding='utf-8'))
assert records and len(records)==meta['catalog_count']
assert len({r['id'] for r in records})==len(records), 'Дубли id'
assert len({r['source_url'] for r in records})==len(records), 'Дубли URL'
assert all(r['name'].strip() and '\ufffd' not in r['name']+r['about'] for r in records), 'Повреждённая кодировка'
assert sum(r['detail_status']=='collected' for r in records)==meta['details_collected']
with (ROOT/'site/exhibitors.csv').open(encoding='utf-8-sig',newline='') as file:
    rows=list(csv.reader(file,delimiter=';'))
assert len(rows)-1==len(records)
html=BeautifulSoup((ROOT/'site/index.html').read_text(encoding='utf-8'),'html.parser')
for tag in html.find_all(['script','link']):
    target=tag.get('src') or tag.get('href')
    if target and not target.startswith('http'): assert (ROOT/'site'/target).is_file(),target
js=(ROOT/'site/data.js').read_text(encoding='utf-8')
payload=json.loads(js.removeprefix('window.MITEX_DATA = ').rstrip(';\n'))
assert len(payload['exhibitors'])==len(records)
assert payload['program']['events']==[], 'Непроверенная программа не должна попасть в MVP'
print(f'PASS: {len(records)} unique records; UTF-8; coverage; CSV; assets; embedded data; program status')
