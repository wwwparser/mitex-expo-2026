"""XMLRiver: отдельный клиент, кэш и ограничение платных попыток."""
import hashlib
import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[2]
CACHE=ROOT/'data/raw/xmlriver'
CACHE.mkdir(parents=True,exist_ok=True)
LOCK=threading.Lock()
ATTEMPTS=0
MAX_ATTEMPTS=200
class NoResults(RuntimeError): pass
for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key,value=line.split('=',1)
        os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))

def search(query,engine='google'):
    global ATTEMPTS
    spec={'query':query,'engine':engine,'region':'RU','language':'ru','page':1 if engine=='google' else 0,'ai':False}
    key=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
    path=CACHE/(key+'.json')
    if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
    user,token=os.getenv('XMLRIVER_USER'),os.getenv('XMLRIVER_KEY')
    if not user or not token: raise RuntimeError('XMLRIVER_USER/XMLRIVER_KEY не заданы в .env')
    params={'user':user,'key':token,'query':query,'groupby':10,'page':spec['page']}
    params.update({'country':'2643','lr':'ru'} if engine=='google' else {'lr':'225','lang':'ru'})
    endpoint='https://xmlriver.com/'+('search/xml' if engine=='google' else 'search_yandex/xml')
    for attempt in range(3):
        with LOCK:
            if ATTEMPTS>=MAX_ATTEMPTS: raise RuntimeError('Достигнут потолок 200 платных попыток')
            ATTEMPTS+=1
        try:
            response=requests.get(endpoint,params=params,timeout=(10,120))
            response.raise_for_status()
        except requests.RequestException as error:
            if attempt==2: raise RuntimeError('Ошибка XMLRiver HTTP: '+type(error).__name__) from None
            time.sleep(4);continue
        root=ET.fromstring(response.content)
        error=root.find('.//error')
        if error is not None:
            text=' '.join(error.itertext()).strip()
            if 'отсутствуют результаты' in text.lower(): raise NoResults(text)
            if any(word in text.lower() for word in ('баланс','денег','превышено','ключ','доступ запрещ')):
                raise RuntimeError('XMLRiver: '+text)
            if attempt==2: raise RuntimeError('XMLRiver: '+text)
            time.sleep(15 if 'канал' in text.lower() else 4);continue
        results=[{'url':doc.findtext('url',''),'title':' '.join(doc.find('title').itertext()) if doc.find('title') is not None else '',
            'snippet':' '.join(' '.join(p.itertext()) for p in doc.iter('passage'))} for doc in root.iter('doc')]
        path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        return results
