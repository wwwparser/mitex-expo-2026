"""Воспроизводимый сбор каталога; ограниченное обогащение для MVP."""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT, fetch
from src.services.catalog_client import collect_catalog, collect_detail, CATALOG
from src.services.contact_client import enrich

def write(name, data):
    path = ROOT / 'data/site' / (name + '.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--details',type=int,default=12)
    parser.add_argument('--enrich',type=int,default=6)
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    previous=ROOT/'data/site/exhibitors.json'
    old={x['id']:x for x in json.loads(previous.read_text(encoding='utf-8'))} if previous.exists() else {}
    records=collect_catalog(args.refresh)
    for x in records:
        if x['id'] in old and not args.refresh:
            for key in ('website','contacts','detail_status','enrichment_status'):
                if key in old[x['id']]: x[key]=old[x['id']][key]
    write('exhibitors',records)
    errors=[]
    for i,record in enumerate(records[:args.details]):
        try:
            if record['detail_status']!='collected' or not record['website'] or args.refresh: collect_detail(record,args.refresh)
            if i<args.enrich and record['website'] and (record.get('enrichment_status')!='collected' or args.refresh): enrich(record,args.refresh)
        except Exception as error:
            errors.append({'id':record['id'],'name':record['name'],'error':str(error)})
            print('ERROR',record['name'],str(error),flush=True)
        write('exhibitors',records)
        print(f'{i+1}/{args.details}: {record["name"]}',flush=True)
    program_url='https://mitexpo.ru/about/delovaya-programma/'
    program=fetch(program_url,args.refresh)
    heading=program.find('h1')
    write('program',{'status':'requires_review','source_url':program_url,
        'observed_heading':heading.get_text(' ',strip=True) if heading else '',
        'note':'Страница источника не подтверждает программу 2026 года. События и спикеры не импортированы.', 'events':[]})
    write('meta',{'title':'MITEX 2026','start':'2026-11-10','end':'2026-11-13',
        'venue':'МВЦ «Крокус Экспо», павильон №2',
        'date_source':'https://mitexpo.ru/about/venue/', 'catalog_source':CATALOG,
        'checked_at':datetime.now(timezone.utc).isoformat(),
        'catalog_count':len(records),'catalog_scope':'Все карточки, возвращённые каталогом через limit=1000 на момент сбора',
        'details_collected':sum(x['detail_status']=='collected' for x in records),
        'sites_enriched':sum(x.get('enrichment_status')=='collected' for x in records), 'errors':errors})
    for name in ('feed','videos','people','items'):
        if not (ROOT/'data/site'/f'{name}.json').exists(): write(name,[])
    with (ROOT/'data/site/exhibitors.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file,delimiter=';')
        writer.writerow(['Компания','Страна','Стенд','Сайт','Email','Телефон','Соцсети','Источник','Статус карточки'])
        for x in records:
            c=x['contacts']; writer.writerow([x['name'],x['country'],', '.join(x['stands']),x['website'],', '.join(c['emails']),', '.join(c['phones']),', '.join(s['url'] for s in c['social']),x['source_url'],x['detail_status']])
    print('Catalog:',len(records))

if __name__=='__main__': main()
