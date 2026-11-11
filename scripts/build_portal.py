"""Build the independent MITEX portal. Only docs/ is generated; docs/media is read-only.

python scripts/build_portal.py              # permits explicitly labelled legacy inputs
python scripts/build_portal.py --production # requires current participant/program/people inputs
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape as xml_escape

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError as exc:
    raise SystemExit('Для сборки необходим Jinja2: python -m pip install Jinja2') from exc

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs'
BASE = 'https://wwwparser.github.io/mitex-expo-2026/'
NOW = datetime.now(ZoneInfo('Europe/Moscow'))
FALLBACKS: list[str] = []


def string(value):
    if value is None:
        return ''
    if isinstance(value, dict):
        for key in ('name', 'title', 'text', 'value', 'label', 'url'):
            if value.get(key) is not None:
                return string(value[key])
        return '; '.join(f'{k}: {string(v)}' for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return '; '.join(string(v) for v in value if v is not None)
    return str(value)


def seq(value):
    return value if isinstance(value, list) else ([] if value is None or value == '' else [value])


def strings(value):
    return list(dict.fromkeys(string(x).strip() for x in seq(value) if string(x).strip()))


def records(value, *keys):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return records(value[key], *keys)
    return []


def read_json(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError) as exc:
        raise SystemExit(f'Не удалось прочитать {path.relative_to(ROOT)}: {exc}') from exc


def load(name, legacy=None, default=None, production=False):
    path = ROOT / 'data/portal' / name
    if path.exists():
        return read_json(path, default)
    if legacy:
        if production:
            raise SystemExit(f'Нет обязательного файла {path.relative_to(ROOT)} для production-сборки')
        FALLBACKS.append(name)
        return read_json(ROOT / 'data/site' / legacy, default)
    return default


def safe_url(value):
    """Only web, mail and phone URLs. No protocol-relative URLs or executable schemes."""
    value = string(value).strip()
    if not value or re.search(r'[\x00-\x20\x7f]', value):
        return ''
    parsed = urlsplit(value)
    if parsed.scheme.lower() in ('http', 'https') and parsed.netloc and not parsed.username:
        return value
    if parsed.scheme.lower() in ('mailto', 'tel') and parsed.path:
        return value
    return ''


def asset(value):
    value = string(value).replace('\\', '/').strip()
    if safe_url(value).startswith(('https://', 'http://')):
        return safe_url(value)
    if value.startswith('docs/'):
        value = value[5:]
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or ':' in value or value.startswith('//'):
        return ''
    # These paths are provided by the catalogue worker. Never alter or copy docs/media.
    if value.startswith('media/'):
        return quote(value, safe='/.-_')
    if (OUT / value).is_file():
        return quote(value, safe='/.-_')
    return ''


def date_text(value):
    value = string(value)
    if not value:
        return ''
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone(ZoneInfo('Europe/Moscow'))
        months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
        result = f'{dt.day} {months[dt.month - 1]} {dt.year}'
        return result + (f', {dt:%H:%M}' if 'T' in value or re.search(r'\d \d\d:', value) else '')
    except ValueError:
        return value


def excerpt(value, words=25):
    tokens = re.sub(r'\s+', ' ', string(value)).strip().split()
    return ' '.join(tokens[:words]) + ('…' if len(tokens) > words else '')


def slug(value):
    value = string(value)
    if re.fullmatch(r'[\w-]+', value, re.UNICODE):
        return value
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def contacts_for(item):
    result = []
    seen = set()

    def add(label, value, url='', source='', note=''):
        value = string(value)
        url = safe_url(url)
        if value and (value, url) not in seen:
            seen.add((value, url))
            result.append(dict(label=label, value=value, url=url, source=safe_url(source), note=note))

    for website in seq(item.get('websites')) + seq(item.get('website')):
        raw = string(website)
        add('Сайт', raw, raw)
    contacts = item.get('contacts', {})
    if isinstance(contacts, dict):
        for key, values in contacts.items():
            for value in seq(values):
                if key in ('social', 'socials') and isinstance(value, dict):
                    note = 'Канал организации' if value.get('kind') == 'organization' else ''
                    add(string(value.get('network')) or 'Соцсеть', value.get('url'), value.get('url'), value.get('source_url'), note)
                elif key in ('emails', 'email'):
                    raw = string(value)
                    add('E-mail', raw, 'mailto:' + raw)
                elif key in ('phones', 'phone'):
                    raw = string(value)
                    add('Телефон', raw, 'tel:' + re.sub(r'[^+\d]', '', raw))
                else:
                    add(key, value, string(value), item.get('source_url'))
    else:
        for value in records(contacts):
            add(string(value.get('kind')) or 'Контакт', value.get('value'), value.get('url'), value.get('source_url'))
    for value in records(item.get('social')):
        note = 'Канал организации' if value.get('kind') == 'organization' else ''
        add(string(value.get('network')) or 'Соцсеть', value.get('url'), value.get('url'), value.get('source_url'), note)
    return result


def participant(item):
    p = dict(item)
    p['id'] = string(p.get('id'))
    p['page'] = 'u/' + slug(p['id']) + '.html'
    p['name'] = string(p.get('name')) or p['id']
    p['about'] = string(p.get('about') or p.get('description'))
    p['stands'] = strings(p.get('stands') or p.get('stand'))
    p['country'] = string(p.get('country'))
    p['categories'] = strings(p.get('categories'))
    rubrics = records(p.get('rubrics'))
    for r in rubrics:
        for value in strings(r.get('values')):
            if value not in p['categories']:
                p['categories'].append(value)
    p['rubrics'] = rubrics
    product_rubrics=[r for r in rubrics if 'рубрикатор' in string(r.get('name')).lower()]
    if product_rubrics:
        p['categories']=list(dict.fromkeys(c for r in product_rubrics for c in strings(r.get('values'))))
    p['halls'] = strings(p.get('halls') or p.get('hall'))
    for c in strings(item.get('categories'))+[v for r in rubrics for v in strings(r.get('values'))]:
        if re.search(r'\bзал\b', c, re.I) and c not in p['halls']:
            p['halls'].append(c)
    p['links'] = contacts_for(p)
    p['logo_view'] = asset(p.get('logo_local')) or asset(p.get('logo'))
    media = []
    for m in seq(p.get('media')):
        m = dict(m) if isinstance(m, dict) else {'url': string(m)}
        m['href'] = asset(m.get('local_path')) or safe_url(m.get('url'))
        m['source'] = safe_url(m.get('source_url') or p.get('source_url'))
        m['image'] = m.get('type') in ('image', 'photo', 'logo') or string(m.get('mime_type')).startswith('image/')
        if not m.get('title'):
            m['title'] = {'video': 'Видео', 'logo': 'Логотип', 'image': 'Изображение', 'pdf': 'PDF', 'document': 'Документ', 'presentation': 'Презентация', 'catalog': 'Каталог'}.get(m.get('type'), 'Материал участника')
        media.append(m)
        if m.get('type') == 'logo' and not p['logo_view']:
            p['logo_view'] = m['href']
    p['media_view'] = media
    p['cover'] = next((m['href'] for m in media if m['image'] and m.get('type') != 'logo' and m['href']), '')
    p['screenshot_view'] = asset(p.get('source_screenshot'))
    p['screenshot_ready'] = bool(p['screenshot_view'] and (OUT / string(p.get('source_screenshot')).removeprefix('docs/')).is_file())
    p['checked'] = p.get('catalog_checked_at') or p.get('detail_checked_at') or p.get('checked_at')
    return p


def public_posts(raw):
    result = []
    for post in records(raw, 'posts', 'items'):
        url = safe_url(post.get('url'))
        # Explicit allowlist: never send a full Telegram text into public output.
        p = {k: post.get(k) for k in ('id', 'date', 'channel', 'channel_title', 'participant_ids', 'people_ids')}
        p.update(url=url, channel_url=safe_url(post.get('channel_url')), excerpt=excerpt(post.get('text') or post.get('telegram_excerpt')))
        p['participant_ids'] = strings(p.get('participant_ids'))
        p['people_ids'] = strings(p.get('people_ids'))
        p['photos'] = []
        for photo in seq(post.get('photos')):
            path = (photo.get('local_path') or photo.get('url') or photo.get('src')) if isinstance(photo, dict) else photo
            if asset(path):
                p['photos'].append(asset(path))
        match = re.fullmatch(r'https://(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,})/(\d+)(?:\?[^\s]*)?', url)
        p['embed'] = '/'.join(match.groups()) if match else ''
        result.append(p)
    return sorted(result, key=lambda x: string(x.get('date')), reverse=True)


STATUS = {'cancelled': 'Отменён', 'canceled': 'Отменён', 'completed': 'Завершён', 'past': 'Прошёл', 'ended': 'Завершён', 'scheduled': 'Запланирован', 'announced': 'Анонс', 'active': 'Идёт', 'upcoming': 'Предстоит', 'postponed': 'Перенесён', 'unknown': 'Статус не уточнён'}


def giveaway(item):
    g = dict(item)
    for key in ('prizes', 'conditions', 'source_urls', 'source_post_ids', 'participant_ids'):
        g[key] = strings(g.get(key))
    # Never infer a draw date from the publication or entry deadline.
    g['when'] = string(g.get('draw_at') or g.get('date'))
    has_time = bool(g.get('draw_at') and re.search(r'\d{1,2}:\d{2}', string(g['draw_at'])))
    g['schedule_group'] = 'dated' if g['when'] and has_time else ('date_only' if g['when'] else 'undated')
    state = string(g.get('status')).lower()
    g['status_view'] = STATUS.get(state, string(g.get('status')) or 'Статус не уточнён')
    if not state and re.match(r'^\d{4}-\d{2}-\d{2}', g['when']) and g['when'][:10] < NOW.date().isoformat():
        g['status_view'] = 'Прошёл по дате; итоги не подтверждены'
    g['inactive'] = state in ('cancelled', 'canceled', 'completed', 'past', 'ended')
    return g


def build(production=False):
    OUT.mkdir(exist_ok=True)
    meta = read_json(ROOT / 'data/site/meta.json', {})
    meta = meta if isinstance(meta, dict) else {}
    raw_participants = records(load('participants.json', 'exhibitors.json', [], production), 'participants', 'exhibitors', 'items')
    participants = [participant(p) for p in raw_participants if p.get('id') is not None]
    participants.sort(key=lambda x: x['name'].casefold())
    scheme=read_json(ROOT/'data/site/scheme.json')
    for p in participants:
        p['has_scheme']=p['id'] in scheme['companies']
    service_catalog=load('service_catalog.json',default={})
    service_items=[]
    participant_lookup={p['id']:p for p in participants}
    for group in service_catalog.get('items',[]):
        members=[participant_lookup[i] for i in group['participant_ids'] if i in participant_lookup]
        if not members:
            continue
        group=dict(group)
        group['members']=members
        group['primary']=members[0]
        service_items.append(group)
        for p in members:
            p['service_group']=group['id']
    service_priority={'it':0,'platforms':1,'analytics':2,'marketing':3,'media':4}
    service_items.sort(key=lambda group:min(service_priority.get(k,9) for k in group['categories']))
    program = load('program.json', 'program.json', {}, production)
    events = records(program, 'events', 'items')
    people = records(load('people.json', 'people.json', [], production), 'people', 'items')
    for p in people:
        p['page'] = 'p/' + slug(p.get('id')) + '.html'
        p['name'] = string(p.get('name'))
        p['links'] = contacts_for(p)
        p['photo_view'] = asset(p.get('photo_local')) or asset(p.get('photo'))
    posts = public_posts(load('telegram_posts.json', default=[]))
    coverage = load('telegram_coverage.json', default={})
    giveaways = [giveaway(g) for g in records(load('giveaways.json', default=[]), 'giveaways', 'events', 'items')]
    giveaways = list({string(g.get('id') or (g.get('title'), g.get('when'), g.get('participant_ids'))): g for g in giveaways}.values())
    analysis = []
    analysis_path = ROOT / 'data/portal/post_analysis.jsonl'
    if analysis_path.exists():
        for line in analysis_path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        analysis.append(row)
                except ValueError as exc:
                    raise SystemExit(f'Ошибка JSONL в {analysis_path}: {exc}') from exc
    related = {string(pid) for g in giveaways for pid in g['source_post_ids']}
    for row in analysis:
        label = string(row.get('classification')).lower()
        if row.get('events') or any(w in label for w in ('giveaway', 'contest', 'розыгрыш', 'конкурс')):
            related.add(string(row.get('post_id')))
    announcements = [p for p in posts if string(p.get('id')) in related or p['url'] in {url for g in giveaways for url in g['source_urls']}]
    analysis_by_id={string(a.get('post_id')):a for a in analysis}
    classifications={'giveaway':'Розыгрыш','prize_contest':'Призовой конкурс','gift_for_visit':'Подарок за визит','discount':'Скидка','unclear':'Условия уточняются'}
    relevances={'current_expo':'Выставка MITEX','other_event':'Другое событие','online_only':'Онлайн','unclear':'Связь не уточнена'}
    for p in posts:
        a=analysis_by_id.get(string(p.get('id')), {})
        p['classification']=a.get('classification','other')
        p['relevance']=a.get('relevance','unclear')
        p['classification_label']=classifications.get(p['classification'],'')
        p['relevance_label']=relevances.get(p['relevance'],'') if p['classification']!='other' else ''
    editorial = load('program_editorial.json', default=None)
    videos = records(read_json(ROOT / 'data/site/videos.json', []), 'videos', 'items')
    video_groups = defaultdict(list)
    for v in videos:
        v['thumb_view'] = asset(v.get('thumb_local')) or asset(v.get('thumb') or v.get('thumbnail'))
        year = string(v.get('exhibition_year') or string(v.get('date'))[:4]) or 'Год не указан'
        video_groups[year].append(v)
    day_groups = defaultdict(list)
    for e in events:
        e['title'] = string(e.get('title') or e.get('name'))
        day_groups[string(e.get('date')) or 'Дата не указана'].append(e)
    for day in day_groups:
        day_groups[day].sort(key=lambda e: string(e.get('start') or e.get('time')))
    env = Environment(loader=FileSystemLoader(ROOT / 'portal_web'), autoescape=select_autoescape(['html', 'xml']))
    env.filters.update(date_text=date_text, excerpt=excerpt, text=string, url=safe_url, asset=asset, seq=seq)
    env.globals.update(strings=strings, isinstance=isinstance, dict=dict, list=list)
    nav = [('catalog.html', 'Участники'), ('services.html','IT и услуги'), ('program.html', 'Программа'), ('people.html', 'Спикеры'), ('feed.html', 'Лента'), ('giveaways.html', 'Розыгрыши'), ('videos.html', 'Видео')]
    pmap = {string(p.get('id')): p for p in participants}
    person_map = {string(p.get('id')): p for p in people}
    common = dict(meta=meta, participants=participants, people=people, events=events, posts=posts, giveaways=giveaways,
                  nav=nav, built_at=date_text(NOW.isoformat()), base=BASE, fallbacks=FALLBACKS,
                  coverage=coverage, analyzed_count=len(analysis), channel_count=len({string(p.get('channel_url') or p.get('channel')) for p in posts}),
                  participant_map=pmap, person_map=person_map, videos=videos,
                  service_catalog=service_catalog,service_items=service_items)
    generated = []

    def render(path, template, title, description, active='', **context):
        rel = '../' if '/' in path else ''
        destination = OUT / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        identity=BASE + ('' if path=='index.html' else quote(path,safe='/.-_'))
        structured={'@context':'https://schema.org','@type':'WebPage','name':title,'url':identity,'description':description,'inLanguage':'ru'}
        entity=context.get('item')
        if entity and path.startswith(('u/','p/')):
            structured['mainEntity']={'@type':'Organization' if path.startswith('u/') else 'Person','name':entity['name'],'url':identity}
            if safe_url(entity.get('source_url')):structured['mainEntity']['sameAs']=[entity['source_url']]
        if path=='index.html':
            structured['about']={'@type':'ExhibitionEvent','name':'MITEX 2026','startDate':'2026-11-10','endDate':'2026-11-13','url':'https://mitexpo.ru/','location':{'@type':'Place','name':'МВЦ «Крокус Экспо», павильон 2','address':'Международная улица, 18, Красногорск, Россия'}}
        html = env.get_template(template).render(**common, **context, prefix=rel, title=title, description=description,
                                                  active=active or path, canonical=identity,structured=structured)
        destination.write_text(html, encoding='utf-8')
        generated.append(path)

    render('index.html', 'index.html', 'MITEX 2026 — участники, программа и навигатор выставки', 'Независимый портал MITEX 2026: каталог участников со стендами и контактами, программа по дням, спикеры, Telegram и розыгрыши.')
    render('catalog.html', 'catalog.html', 'Участники MITEX 2026 — каталог, стенды и контакты', 'Найдите участника MITEX 2026 по названию, стенду или описанию. Фильтры по стране, рубрике и залу; все контакты и материалы компаний.',
           countries=sorted({p['country'] for p in participants if p['country']}), categories=sorted({c for p in participants for c in p['categories']}), halls=sorted({h for p in participants for h in p['halls']}))
    render('map.html','map.html','Стенд участника — MITEX 2026','Размещение компании на официальном плане MITEX 2026.')
    render('services.html','services.html','IT, маркетинг и медиа на MITEX 2026 — каталог услуг',
           'Цифровые платформы, автоматизация, маркетинг, отраслевые медиа и аналитика среди участников MITEX: услуги, контакты, стенды и подтверждающие источники.')
    for p in participants:
        render(p['page'], 'participant.html', p['name'] + ' — MITEX 2026', excerpt(p['about'], 30) or f"Стенды, контакты и материалы участника {p['name']} на MITEX 2026.", 'catalog.html',
               item=p, item_posts=[post for post in posts if p['id'] in post['participant_ids']], item_giveaways=[g for g in giveaways if p['id'] in g['participant_ids']])
    render('program.html', 'program.html', 'Программа MITEX 2026 по дням', 'Деловая программа MITEX 2026: даты, время, площадки, темы и спикеры со ссылками на организатора.', program=program, days=sorted(day_groups.items()), editorial=editorial)
    render('people.html', 'people.html', 'Спикеры MITEX 2026', 'Эксперты деловой программы MITEX 2026: темы выступлений, публичные контакты и публикации.')
    for p in people:
        pid = string(p.get('id'))
        own_events = [e for e in events if pid in strings(e.get('people_ids')) or any(pid == string(ep.get('id')) for ep in records(e.get('people') or e.get('speakers')))]
        render(p['page'], 'person.html', p['name'] + ' — спикер MITEX 2026', excerpt(p.get('role') or p.get('about'), 30) or f"Выступления и публикации спикера {p['name']}.", 'people.html', item=p,
               item_events=own_events or records(p.get('events')), item_posts=[post for post in posts if pid in post['people_ids']])
    render('feed.html', 'feed.html', 'Лента участников и спикеров MITEX 2026', 'Короткие выдержки из публичных Telegram-каналов участников и спикеров MITEX, фотографии и ссылки на оригиналы.', feed_posts=posts)
    render('giveaways.html', 'giveaways.html', 'Розыгрыши MITEX 2026 — расписание и объявления', 'Розыгрыши и призовые конкурсы: отдельно время розыгрыша, дедлайны участия, условия и исходные объявления.',
           dated=sorted([g for g in giveaways if g['schedule_group'] == 'dated'], key=lambda g: g['when']),
           date_only=[g for g in giveaways if g['schedule_group'] == 'date_only'], undated=[g for g in giveaways if g['schedule_group'] == 'undated'], announcements=announcements,
           analysis_present=analysis_path.exists(), giveaways_present=(ROOT / 'data/portal/giveaways.json').exists())
    render('videos.html', 'videos.html', 'Видео о MITEX — анонсы 2026 и архив', 'Проверенная подборка видео: приглашения MITEX 2026 и отдельно обозначенные записи прошлых выставок. Даты и авторы у каждого ролика.', video_groups=sorted(video_groups.items(), reverse=True))
    render('404.html', '404.html', 'Страница не найдена — MITEX 2026', 'Перейдите к каталогу участников или программе MITEX 2026.')
    assets = OUT / 'portal-assets'
    assets.mkdir(exist_ok=True)
    for name in ('style.css', 'portal.js'):
        shutil.copyfile(ROOT / 'portal_web' / name, assets / name)
    shutil.copytree(ROOT/'web/assets/scheme',OUT/'assets/scheme',dirs_exist_ok=True)
    shutil.copyfile(ROOT/'web/map.js',assets/'map.js')
    (assets/'scheme-data.js').write_text('window.MITEX_SCHEME = '+json.dumps(scheme,ensure_ascii=False).replace('<','\\u003c')+';\n',encoding='utf-8')
    map_companies=[{k:p[k] for k in ('id','name','stands','source_url')} for p in participants]
    (assets/'map-companies.js').write_text('window.MITEX_DATA = '+json.dumps({'exhibitors':map_companies},ensure_ascii=False).replace('<','\\u003c')+';\n',encoding='utf-8')
    exports = OUT / 'exports'
    exports.mkdir(exist_ok=True)
    (exports/'services.json').write_text(json.dumps({k:v for k,v in service_catalog.items()},ensure_ascii=False,indent=2),encoding='utf-8')
    with (exports/'services.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['Название','Направления','Услуги','Задача клиента','Стенды','Сайт','Карточки MITEX','Источники'])
        for service in service_items:
            writer.writerow([service['name'],', '.join(service_catalog['categories'][k] for k in service['categories']),service['summary'],service['business_task'],
                ', '.join(s for p in service['members'] for s in p['stands']),service.get('website',''),', '.join(service['participant_ids']),', '.join(service['source_urls'])])
    # Participant exports do not include Telegram post bodies or analysis evidence.
    (exports / 'participants.json').write_text(json.dumps(raw_participants, ensure_ascii=False, indent=2), encoding='utf-8')
    with (exports / 'participants.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['id', 'name', 'country', 'stands', 'halls', 'categories', 'about', 'contacts', 'source_url', 'checked_at'])
        for p in participants:
            row = [p['id'], p['name'], p['country'], '; '.join(p['stands']), '; '.join(p['halls']), '; '.join(p['categories']), p['about'], '; '.join(c['value'] for c in p['links']), string(p.get('source_url')), string(p['checked'])]
            writer.writerow(["'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value for value in row])
    urls = [f'<url><loc>{xml_escape(BASE + ("" if p == "index.html" else quote(p, safe="/.-_")))}</loc><lastmod>{NOW.date()}</lastmod></url>' for p in generated if p != '404.html']
    (OUT / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(urls) + '</urlset>', encoding='utf-8')
    (OUT / 'robots.txt').write_text(f'User-agent: *\nAllow: /\nSitemap: {BASE}sitemap.xml\n', encoding='utf-8')
    # Clean only obsolete files owned by this builder, never other agents' output.
    manifest = assets / 'generated.json'
    previous = read_json(manifest, [])
    for old in previous if isinstance(previous, list) else []:
        if old not in generated and re.fullmatch(r'(?:u|p)/[\w-]+\.html', string(old)):
            (OUT / old).unlink(missing_ok=True)
    manifest.write_text(json.dumps(generated, ensure_ascii=False), encoding='utf-8')
    (OUT/'.nojekyll').touch()
    print(f'Portal: {len(generated)} pages; {len(participants)} participants; {len(events)} events; {len(people)} people; {len(posts)} posts; {len(giveaways)} giveaways. Output: {OUT}')
    if FALLBACKS:
        print('Development fallback (labelled in footer): ' + ', '.join(FALLBACKS))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production', action='store_true')
    build(parser.parse_args().production)
