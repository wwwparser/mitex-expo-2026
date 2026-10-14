import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.xmlriver_client import ROOT,search,NoResults

EXTRA=[
 ('program','MITEX 2026 деловая программа спикеры'),
 ('program2','site:mitexpo.ru 2026 конференция программа спикеры'),
 ('youtube','site:youtube.com/watch MITEX 2026'),
 ('youtube2','site:youtube.com МИТЕКС 2026'),
 ('novelties','site:mitexpo.ru 2026 новинки представит'),
 ('speakers','MITEX 2026 "Современный крепёж" спикеры')]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');args=parser.parse_args()
    records=json.loads((ROOT/'data/site/exhibitors.json').read_text(encoding='utf-8'))
    tasks=[]
    for x in records:
        if x['country']=='Россия' and not any(s['network']=='Telegram' for s in x['contacts']['social']):
            name=re.sub(r'\s*[,/-]?\s*(ООО|ЗАО|АО|ПАО)\b','',x['name'],flags=re.I).strip(' ,')
            name=re.sub(r'\s*\([^)]*\)','',name).replace('"','').strip(' ,')
            tasks.append({'id':x['id'],'name':x['name'],'query':f'"{name}" site:t.me','kind':'company'})
    tasks.extend({'id':key,'query':query,'kind':'topic'} for key,query in EXTRA)
    if args.probe: tasks=tasks[:1]
    output=ROOT/'data/research/search.jsonl';output.parent.mkdir(parents=True,exist_ok=True)
    done={}
    if output.exists():
        for line in output.read_text(encoding='utf-8').splitlines():
            row=json.loads(line);done[row['query']]=row
    todo=[t for t in tasks if t['query'] not in done or done[t['query']].get('error')]
    print('SEARCH pending',len(todo),'planned total',len(tasks),flush=True)
    def work(task):
        try:return {**task,'results':search(task['query']),'status':'collected'}
        except NoResults:return {**task,'results':[],'status':'no_results'}
        except RuntimeError as error:return {**task,'results':[],'error':str(error),'status':'error'}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for n,future in enumerate(as_completed([pool.submit(work,t) for t in todo]),1):
            row=future.result()
            with output.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
            if args.probe: print(json.dumps(row,ensure_ascii=False,indent=2),flush=True)
            elif n%10==0 or n==len(todo):print(f'SEARCH {n}/{len(todo)}',flush=True)

if __name__=='__main__':main()
