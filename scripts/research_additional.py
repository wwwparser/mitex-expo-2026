import json,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.xmlriver_client import ROOT,search,NoResults
QUERIES={
'sharonin':'"Александр Шаронин" "sharonin_alexander"',
'lyapchev':'"Владимир Ляпчев" соцсети Telegram',
'darusenkov':'"Андрей Дарусенков" "dao.bonsai"',
'ekaterina':'"Екатерина Васильева" топиарная стрижка Telegram',
'denis_suk':'"Денис Сук" Greenhouse Telegram',
'emstudio':'"Елена Мельник" "EM Studio" Telegram',
'aipower':'"a-ipower.ru" Telegram',
'condtrol':'"condtrol.ru" Telegram',
'kvt':'"kvt.su" "kvt_su"',
'dvizhok':'"dvizhok_journal" журнал официальный',
'stroygaz':'"stroygazeta" "stroygaz.ru"',
'video_invites':'site:youtube.com "MITEX 2026" "приглашаем"',
}
path=ROOT/'data/research/additional.json'
def work(item):
    key,query=item
    try:return {'id':key,'query':query,'results':search(query)}
    except NoResults:return {'id':key,'query':query,'results':[]}
    except RuntimeError as error:return {'id':key,'query':query,'results':[],'error':str(error)}
with ThreadPoolExecutor(max_workers=8) as pool:rows=list(pool.map(work,QUERIES.items()))
path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
for r in rows:print(r['id'],json.dumps(r['results'],ensure_ascii=False)[:2300],flush=True)
