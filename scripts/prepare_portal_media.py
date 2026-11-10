"""Save small, attributed previews; Telegram originals remain linked."""
import concurrent.futures as cf
import hashlib
import io
import json
from pathlib import Path

import requests
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/portal'
OUT=ROOT/'docs/media/telegram'


def main():
    posts=json.loads((DATA/'telegram_posts.json').read_text(encoding='utf-8'))
    OUT.mkdir(parents=True,exist_ok=True)
    urls=list(dict.fromkeys(u for p in posts for u in p.get('remote_photos',p.get('photos',[]))[:2] if u.startswith('https://')))
    def fetch(url):
        name=hashlib.sha256(url.encode()).hexdigest()[:24]+'.jpg'
        path=OUT/name
        if path.exists():return url,'media/telegram/'+name
        try:
            r=requests.get(url,timeout=(5,12));r.raise_for_status()
            if len(r.content)>8_000_000:return url,None
            im=Image.open(io.BytesIO(r.content));im.thumbnail((900,900))
            im.convert('RGB').save(path,quality=74,optimize=True)
            return url,'media/telegram/'+name
        except Exception:return url,None
    mapped={}
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        for n,(u,p) in enumerate(pool.map(fetch,urls),1):
            mapped[u]=p
            if n%100==0:print(n,len(urls),flush=True)
    for p in posts:
        p['remote_photos']=p.get('remote_photos',p.get('photos',[]))
        p['photos']=[mapped[u] for u in p['remote_photos'][:2] if mapped.get(u)]
        p['photos_scope']='up_to_two_preview_images; original_album_linked'
    (DATA/'telegram_posts.json').write_text(json.dumps(posts,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'requested':len(urls),'saved':sum(bool(p) for p in mapped.values()),'failed':sum(not p for p in mapped.values())}
    (DATA/'telegram_media_coverage.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(report,flush=True)


if __name__=='__main__':main()
