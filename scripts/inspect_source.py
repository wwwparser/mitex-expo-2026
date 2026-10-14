import requests
from bs4 import BeautifulSoup
from pathlib import Path

for name, url in [('all','https://catalog.mitexpo.ru/expositions/exposition/7054-mitex-2026.html?limit=1000'), ('company','https://catalog.mitexpo.ru/companies/company/84488-a-ipower.html'), ('program','https://mitexpo.ru/about/delovaya-programma/')]:
    r=requests.get(url,timeout=35)
    r.raise_for_status()
    Path('data/raw').mkdir(parents=True,exist_ok=True)
    Path(f'data/raw/{name}.html').write_bytes(r.content)
    s=BeautifulSoup(r.content,'html.parser')
    if name=='all':
        cards=[a for a in s.find_all('a',href=True) if '/companies/company/' in a['href']]
        print('CARDS',len(cards),str(cards[0])[:4500])
    else:
        print(name,s.get_text(' ',strip=True)[-12000:] if name=='company' else s.get_text(' ',strip=True)[3000:7000])
        print([(a['href'],a.get_text(' ',strip=True)) for a in s.find_all('a',href=True) if a['href'].startswith(('http','mailto:','tel:'))][:25])
