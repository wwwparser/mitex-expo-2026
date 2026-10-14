"""Публикуются только проверенные находки; остальные остаются в досье."""
import json,re,sys
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT,fetch

FOLDER=ROOT/'data/site'
records=json.loads((FOLDER/'exhibitors.json').read_text(encoding='utf-8'))
companies={x['id']:x for x in records}
approved=[
 ('135005','https://t.me/vseinstrumentirudiy','https://t.me/vseinstrumentirudiy','В описании канала указан корпоративный email vseinstrumenti.ru; название и профиль деятельности совпадают.'),
 ('84498','https://t.me/kvt_su','https://expodat.by/companies/company/75909-kvt-elektrotekhnicheskij-zavod.html','Канал и домен kvt.su указаны вместе в карточке завода КВТ.'),
 ('171345','https://t.me/stroygazeta','https://stroygaz.ru/news/official/stroygazeta-voshla-v-top-100-telegram-kanalov-o-nedvizhimosti/','Публикация самого издания о его Telegram-канале; название совпадает с участником.'),
 ('171339','https://t.me/dvizhok_journal','https://dvizhok.su/','Канал автомобильного журнала; совпадают издательское название и тематика.'),
]
added=[]
for id,url,source,note in approved:
    page=fetch(url);title=page.select_one('.tgme_page_title');extra=page.select_one('.tgme_page_extra')
    if not extra or 'subscribers' not in extra.get_text().lower():continue
    social=companies[id]['contacts']['social']
    if not any(s['url'].rstrip('/').lower()==url.lower() for s in social):
        social.append({'network':'Telegram','url':url,'source_url':source,'verification_note':note,'discovery':'XMLRiver','telegram_type':'channel','telegram_title':title.get_text(' ',strip=True),'canonical_url':url})
    added.append({'company_id':id,'company':companies[id]['name'],'url':url,'source_url':source,'note':note})
(FOLDER/'exhibitors.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
(FOLDER/'search_discoveries.json').write_text(json.dumps({'companies_searched':131,'added_channels':added,'scope':'Российские участники без ранее найденного Telegram; кандидаты проверены по названию, тематике и источникам.'},ensure_ascii=False,indent=2),encoding='utf-8')

people=json.loads((FOLDER/'people.json').read_text(encoding='utf-8'))
by_id={p['id']:p for p in people}
personal=[
 ('alyena-melnik','Telegram','https://t.me/be_trained','https://t.me/be_trained','Имя и специализация по продажам совпадают с карточкой спикера.'),
 ('tatyana-gnilitskaya','Instagram','https://www.instagram.com/stav_landshaft/','https://www.instagram.com/stav_landshaft/','В публичном профиле указаны имя и специализация ландшафтного дизайнера.'),
 ('roman-golomolzin-26','Instagram','https://www.instagram.com/roma_niwaki/','https://www.instagram.com/roma_niwaki/','Профиль указывает полное имя и работу с растениями/ниваки.'),
 ('andrey-darusenkov-26','Instagram','https://www.instagram.com/dao.bonsai/','https://daobonsai.ru/','Мастерская автора подтверждена его сайтом.'),
 ('andrey-darusenkov-26','VK','https://vk.com/dao.bonsai','https://daobonsai.ru/','Мастерская автора подтверждена его сайтом.'),
 ('vladimir-lyapchev-26','Instagram','https://www.instagram.com/vladimirliapchev/','https://www.владимирляпчев.рф/kontakty/','Соцсеть указана на личном сайте специалиста.'),
 ('vladimir-lyapchev-26','Telegram','https://t.me/liapchev','https://www.владимирляпчев.рф/kontakty/','Канал указан на личном сайте специалиста.'),
 ('anastasiya-valova-26','Telegram','https://t.me/sinoruss','https://t.me/sinoruss','Корпоративный канал Sinoruss, аффилиация подтверждена ролью в программе и публикацией канала.'),
]
for id,network,url,source,note in personal:
    p=by_id[id]
    entry={'network':network,'url':url,'source_url':source,'verification_note':note,'kind':'organization' if id=='anastasiya-valova-26' else 'personal'}
    if not any(s['url']==url for s in p['social']):p['social'].append(entry)
# Ссылки с сайтов специалистов — только опубликованные, без конструирования username.
for id,website in [('andrey-darusenkov-26','https://daobonsai.ru/telegram'),('vladimir-lyapchev-26','https://www.владимирляпчев.рф/kontakty/')]:
    try:
        page=fetch(website)
        for a in page.find_all('a',href=True):
            url=a['href'];host=(urlparse(url).hostname or '').removeprefix('www.')
            if host in ('t.me','telegram.me','vk.com','instagram.com','youtube.com') and '/share' not in url:
                p=by_id[id]
                if not any(s['url']==url for s in p['social']):p['social'].append({'network':{'t.me':'Telegram','telegram.me':'Telegram','vk.com':'VK','instagram.com':'Instagram','youtube.com':'YouTube'}[host],'url':url,'kind':'personal','source_url':website})
    except Exception as error:by_id[id]['link_collection_note']=type(error).__name__
employers=[('elitech',None,'elitech'),('интерскол','84542',None),('caiman','84582',None),('всеинструменты','135005',None),('тсс','84628',None),('plantic','84624',None)]
for p in people:
    # Удаляем старые привязки по общим словам «компании», «холдинг» и т.п.
    p['social']=[s for s in p['social'] if not s.get('affiliation_source')]
    for employer,company_id,title in employers:
        if not re.search(r'(?<![а-яёa-z])'+re.escape(employer)+r'(?![а-яёa-z])',p['role'].lower()):continue
        for x in records:
            if company_id and x['id']!=company_id:continue
            for s in x['contacts']['social']:
                if title and re.sub(r'[^a-z]','',s.get('telegram_title','').lower())!=title:continue
                if s.get('telegram_type')=='channel' and not any(a['url']==s['url'] for a in p['social']):
                    p['social'].append({**s,'kind':'organization','organization':x['name'],'affiliation_source':p['source_url'],'employer_match':employer})
    p['social_status']='found' if p['social'] else 'not_confirmed'
(FOLDER/'people.json').write_text(json.dumps(people,ensure_ascii=False,indent=2),encoding='utf-8')
print('Added exhibitor channels',len(added),'speakers with links',sum(bool(p['social']) for p in people),flush=True)
