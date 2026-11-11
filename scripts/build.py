import json
import shutil
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    out=ROOT/'site'
    out.mkdir(exist_ok=True)
    for name in ('index.html','novinki.html','icon.svg','style.css','portal.js','map.html','map.js'):
        shutil.copyfile(ROOT/'web'/name,out/name)
    shutil.copytree(ROOT/'web/assets/scheme',out/'assets/scheme',dirs_exist_ok=True)
    scheme=json.loads((ROOT/'data/site/scheme.json').read_text(encoding='utf-8'))
    (out/'scheme-data.js').write_text('window.MITEX_SCHEME = '+json.dumps(scheme,ensure_ascii=False).replace('<','\\u003c')+';\n',encoding='utf-8')
    names=('meta','exhibitors','program','feed','feed_report','videos','video_channels','video_report','people','items','novelty_registry')
    data={name:json.loads((ROOT/'data/site'/f'{name}.json').read_text(encoding='utf-8')) for name in names}
    # Внешние строки остаются данными. Никакой разметки источника в UI.
    data['scheme_companies']=list(scheme['companies'])
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    (out/'data.js').write_text('window.MITEX_DATA = '+payload+';\n',encoding='utf-8')
    shutil.copyfile(ROOT/'data/site/exhibitors.csv',out/'exhibitors.csv')
    if (ROOT/'data/site/exhibitors.xlsx').exists():
        shutil.copyfile(ROOT/'data/site/exhibitors.xlsx',out/'exhibitors.xlsx')
    for name in ('contact_report.json','channels.json','feed_report.json','video_report.json','search_discoveries.json','novelty_registry.csv'):
        if (ROOT/'data/site'/name).exists(): shutil.copyfile(ROOT/'data/site'/name,out/name)
    for filename in ('index.html','novinki.html','map.html'):
        path=out/filename
        text=path.read_text(encoding='utf-8')
        for asset in ('data.js','portal.js','style.css','icon.svg','map.js','scheme-data.js'):
            digest=hashlib.sha256((out/asset).read_bytes()).hexdigest()[:12]
            text=text.replace('"'+asset+'"','"'+asset+'?v='+digest+'"')
        path.write_text(text,encoding='utf-8')
    print(f'Built: {out / "index.html"}; {len(data["exhibitors"])} exhibitors')

if __name__=='__main__': main()
