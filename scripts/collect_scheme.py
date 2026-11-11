"""Extract booth geometry by official company ID; never execute source scripts."""
import json
import re
import shutil
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]

def main():
    raw = ROOT / 'data/raw/scheme'
    soup = BeautifulSoup((raw / 'app.html').read_text(encoding='utf-8'), 'html.parser')
    names = {a['href'].removeprefix('#tab-'): a.get_text(' ', strip=True) for a in soup.select('#schemsHallsTab a')}
    assets = ROOT / 'web/assets/scheme'
    assets.mkdir(parents=True, exist_ok=True)
    halls, companies = {}, {}
    for svg in soup.select('svg.workspace-svg'):
        hid = svg['id'].removeprefix('workspace-svg')
        source = svg.select_one('image.img-scheme')['xlink:href']
        image_path = raw / (svg['id'] + '.svg')
        image = BeautifulSoup(image_path.read_bytes(), 'xml')
        assert not image.find_all(['script', 'foreignObject'])
        assert not any(any(k.lower().startswith('on') or k.lower().endswith('href') for k in tag.attrs) for tag in image.find_all())
        dimensions = [float(v) for v in image.svg['viewBox'].split()]
        shutil.copyfile(image_path, assets / f'{hid}.svg')
        halls[hid] = dict(name=names[hid], image=f'assets/scheme/{hid}.svg', viewBox=dimensions, source_url=source)
        for path in svg.select('path[class*=path-company]'):
            match = re.search(r'path-company(\d+)', ' '.join(path.get('class', [])))
            if not match:
                continue
            d = path.get('d', '')
            assert re.fullmatch(r'[\s\d.,eE+\-MmLlHhVvCcSsQqTtAaZz]+', d), d
            companies.setdefault(match[1], []).append(dict(hall=hid, d=d))
    scheme = dict(checked_at='2026-11-11', source_url='https://catalog.mitexpo.ru/mitex-scheme.html', expo_id=7054, halls=halls, companies=companies)
    (ROOT / 'data/site/scheme.json').write_text(json.dumps(scheme, ensure_ascii=False, indent=2), encoding='utf-8')
    records = json.loads((ROOT / 'data/site/exhibitors.json').read_text(encoding='utf-8'))
    matched = [r['id'] for r in records if str(r['id']) in companies]
    print(f'Mapped {len(matched)}/{len(records)} exhibitors; {len(companies)} official company IDs; {len(halls)} halls')

if __name__ == '__main__':
    main()
