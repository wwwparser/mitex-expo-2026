import json,re,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
ROOT=Path(__file__).resolve().parents[1]
rows=json.loads((ROOT/'data/research/youtube.json').read_text(encoding='utf-8'))
by_id={x['id']:x for x in rows if '2026' in x['title'] and not re.search(r'202[0-5]',x['title'])}
by_id['61gIB6cYPFM']={'id':'61gIB6cYPFM','url':'https://www.youtube.com/shorts/61gIB6cYPFM','title':'Getting ready for MITEX 2026? Start test-driving the tools now!'}
def get(x):
    try:
        r=requests.get(x['url'],timeout=30);r.raise_for_status()
        (ROOT/'data/raw'/('yt_'+x['id']+'.html')).write_bytes(r.content)
        m=re.search(r'ytInitialPlayerResponse\s*=\s*',r.text)
        if m:
            p=json.JSONDecoder().raw_decode(r.text[m.end():])[0]
            x.update(details=p.get('videoDetails',{}),micro=p.get('microformat',{}).get('playerMicroformatRenderer',{}))
        m=re.search(r'ytInitialData\s*=\s*',r.text)
        if m:
            initial=json.JSONDecoder().raw_decode(r.text[m.end():])[0]
            found=[]
            def walk(obj):
                if isinstance(obj,dict):
                    for key,value in obj.items():
                        if key in ('videoDescriptionHeaderRenderer','videoPrimaryInfoRenderer','videoOwnerRenderer','videoMetadataRenderer','attributedDescription','dateText','publishDate'):found.append({key:value})
                        walk(value)
                elif isinstance(obj,list):
                    for item in obj:walk(item)
            walk(initial);x['public_metadata']=found
    except Exception as error:x['error']=type(error).__name__
    return x
with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(get,by_id.values()))
(ROOT/'data/research/video_meta.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
for x in results:
    print(x['id'],x['title'],x.get('micro',{}).get('publishDate'),x.get('details',{}).get('author'),x.get('details',{}).get('shortDescription','')[:1200],x.get('error',''),flush=True)
    print(json.dumps(x.get('public_metadata',[]),ensure_ascii=False)[:2100],flush=True)
