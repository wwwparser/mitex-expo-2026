"""Content and generated-link checks for the complete MITEX portal."""
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/portal'
DOCS=ROOT/'docs'


def main():
    errors=[]
    ps=json.loads((DATA/'participants.json').read_text(encoding='utf-8'))
    coverage=json.loads((DATA/'coverage_catalog.json').read_text(encoding='utf-8'))
    assert len(ps)==coverage['catalog_discovered']
    assert len({p['id'] for p in ps})==len(ps)
    files=list(DOCS.rglob('*.html'))
    for file in files:
        text=file.read_text(encoding='utf-8')
        if '????' in text:errors.append([str(file.relative_to(DOCS)),'invalid encoding'])
        soup=BeautifulSoup(text,'html.parser')
        if not soup.title:errors.append([str(file.relative_to(DOCS)),'no title'])
        for el in soup.select('[href],[src]'):
            target=el.get('href') or el.get('src') or ''
            parsed=urlsplit(target)
            if not parsed.path or parsed.scheme or target.startswith('//'):continue
            path=unquote(parsed.path)
            if path.startswith('/mitex-expo-2026/'):
                dest=DOCS/path.removeprefix('/mitex-expo-2026/')
            elif path.startswith('/'):
                continue
            else:dest=file.parent/path
            if dest.is_dir():dest=dest/'index.html'
            if not dest.is_file():errors.append([str(file.relative_to(DOCS)),target])
    missing=[]
    for p in ps:
        path=DOCS/'media/source-cards'/f"{p['id']}.jpg"
        if not path.is_file():missing.append(p['id'])
    summary={'participants':len(ps),'html_pages':len(files),'source_screenshots':len(ps)-len(missing),
             'missing_source_screenshots':missing,'broken_internal_links':errors}
    folder=ROOT/'artifacts/portal-qa';folder.mkdir(parents=True,exist_ok=True)
    (folder/'static-check.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:(len(v) if isinstance(v,list) else v) for k,v in summary.items()},ensure_ascii=False))
    if errors:print(json.dumps(errors[:20],ensure_ascii=False))
    return 1 if errors or missing else 0


if __name__=='__main__':raise SystemExit(main())
