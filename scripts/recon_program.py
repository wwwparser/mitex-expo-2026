import requests
from bs4 import BeautifulSoup
from pathlib import Path
for name,url in [('programjs','https://mitexpo.ru/js/program.js'),('conference','https://mitexpo.ru/press/novosti/14229/'),('novelties','https://mitexpo.ru/press/novosti/14220/')]:
    r=requests.get(url,timeout=30);r.raise_for_status();Path('data/raw/'+name+'.html').write_bytes(r.content)
    if name=='programjs':print(name,r.content.decode('utf-8')[:18000])
    else:
        s=BeautifulSoup(r.content,'html.parser');print(name)
        print([(t.get('class'),t.get('id'),t.get_text(' ',strip=True)[:180]) for t in s.select('h1')])
        print(s.get_text(' ',strip=True)[-16000:-1000])
