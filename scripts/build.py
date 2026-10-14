import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    out=ROOT/'site'
    out.mkdir(exist_ok=True)
    for name in ('index.html','style.css','app.js'):
        shutil.copyfile(ROOT/'web'/name,out/name)
    names=('meta','exhibitors','program','feed','videos','people','items')
    data={name:json.loads((ROOT/'data/site'/f'{name}.json').read_text(encoding='utf-8')) for name in names}
    # Внешние строки остаются данными. Никакой разметки источника в UI.
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    (out/'data.js').write_text('window.MITEX_DATA = '+payload+';\n',encoding='utf-8')
    shutil.copyfile(ROOT/'data/site/exhibitors.csv',out/'exhibitors.csv')
    if (ROOT/'data/site/exhibitors.xlsx').exists():
        shutil.copyfile(ROOT/'data/site/exhibitors.xlsx',out/'exhibitors.xlsx')
    for name in ('contact_report.json','channels.json'):
        if (ROOT/'data/site'/name).exists(): shutil.copyfile(ROOT/'data/site'/name,out/name)
    print(f'Built: {out / "index.html"}; {len(data["exhibitors"])} exhibitors')

if __name__=='__main__': main()
