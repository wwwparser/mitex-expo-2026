"""Refresh public MITEX cards into an isolated portal dataset.

python scripts/refresh_portal_catalog.py --workers 8
Same-day raw HTML is reused, but reparsed (the acceptance parser missed CSS
logos and AJAX galleries). --refresh bypasses HTML cache. Files are streamed
without a document size cap; video remains a link. No site/build/Telegram writes.
All local paths are relative to the project root.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import mimetypes
import re
import shutil
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.catalog_client import website_urls

BASE = 'https://catalog.mitexpo.ru'
CATALOG = BASE + '/expositions/exposition/7054'
AJAX = BASE + '/index.php?option=com_expo&tmpl=component&no_html=1&format=ajax&lang=ru'
OUT = ROOT / 'data/portal'
RAW = ROOT / 'data/raw/portal'
MEDIA = ROOT / 'docs/media/catalog'
SEED = ROOT / 'skill-test-20261110'
VERSION = 3
TLS = threading.local()
LOCK = threading.Lock()
SOCIAL = {'t.me', 'telegram.me', 'vk.com', 'vk.ru', 'youtube.com', 'youtu.be',
          'rutube.ru', 'max.ru', 'max.me', 'wa.me', 'api.whatsapp.com',
          'instagram.com', 'facebook.com', 'ok.ru', 'dzen.ru', 'vkvideo.ru', 'wechat.com'}
DOC = re.compile(r'\.(?:pdf|docx?|pptx?|xlsx?|zip|rar|7z|odt|ods|odp|rtf|txt|csv)(?:[?#]|$)', re.I)
VIDEO = re.compile(r'youtube\.com/(?:watch|embed|shorts)|youtu\.be/|rutube\.ru/(?:video|play)|vimeo\.com/|vk(?:video)?\.(?:ru|com)/video|\.(?:mp4|webm|mov|m3u8)(?:[?#]|$)', re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows readers/indexers may hold the previous aggregate briefly.
    for attempt in range(20):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.1 * (attempt + 1))


def text(node):
    return node.get_text(' ', strip=True) if node else ''


def unique(items):
    return list(dict.fromkeys(x for x in items if x))


def rel(path):
    return path.relative_to(ROOT).as_posix()


def fresh(meta):
    try:
        return datetime.fromisoformat(meta['checked_at']).astimezone(timezone.utc).date() == datetime.now(timezone.utc).date() and meta['status'] == 200
    except (KeyError, ValueError, TypeError):
        return False


def session():
    if not hasattr(TLS, 'session'):
        TLS.session = requests.Session()
        TLS.session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; MITEX-public-catalog-research/1.0)'})
    return TLS.session


def request(method, url, **kwargs):
    for attempt in range(4):
        try:
            response = session().request(method, url, timeout=(12, 45), **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1 + attempt * 2)


def html(url, refresh=False, allow_seed=False):
    key = hashlib.sha256(url.encode()).hexdigest()
    path = RAW / 'html' / (key + '.html')
    meta_path = path.with_suffix('.json')
    if not refresh:
        for candidate in [path] + ([SEED / 'raw' / path.name] if allow_seed else []):
            mp = candidate.with_suffix('.json')
            if candidate.exists() and mp.exists() and fresh(read(mp)):
                meta = read(mp)
                if candidate != path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(candidate, path)
                    dump(meta_path, {**meta, 'reused_from': rel(candidate)})
                    meta = read(meta_path)
                return BeautifulSoup(path.read_bytes(), 'html.parser'), meta, path
    r = request('GET', url)
    if 'html' not in r.headers.get('Content-Type', ''):
        raise ValueError('Expected HTML: ' + url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    meta = {'url': url, 'final_url': r.url, 'status': r.status_code, 'checked_at': now()}
    dump(meta_path, meta)
    return BeautifulSoup(r.content, 'html.parser'), meta, path


def catalog_rows(soup):
    rows = {}
    for a in soup.select('a[href]'):
        match = re.search(r'/companies/company/(\d+)', a['href'])
        if not match or not a.select_one('.exh_name'):
            continue
        flag = a.select_one('.img_country')
        rows[match[1]] = {'id': match[1], 'name': a.get('title') or text(a.select_one('.exh_name')),
            'stands': unique(text(x) for x in a.select('.numb_stand b')),
            'country': flag.get('title', '') if flag else '',
            'about': text(a.select_one('.short_opis')), 'source_url': urljoin(BASE, a['href'])}
    return rows


def pagination(soup):
    return unique(urljoin(BASE, a.get('data-href') or a.get('href'))
                  for a in soup.select('.pagination a[data-href],.pagination a[href]'))


def discover(workers):
    # Always fresh discovery, even during resume. Independently walk normal pages.
    full, meta, _ = html(CATALOG + '?limit=1000', True)
    expected = catalog_rows(full)
    if not expected:
        raise ValueError('Empty catalog')
    first, _, _ = html(CATALOG, True)
    seen, pending, paged, evidence = set(), [CATALOG], {}, []
    pages = {CATALOG: first}
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        while pending:
            batch = unique(u for u in pending if u not in seen)
            pending = []
            futures = {pool.submit(html, u, True): u for u in batch if u not in pages}
            for f in cf.as_completed(futures):
                pages[futures[f]] = f.result()[0]
            for u in batch:
                if u in seen:
                    continue
                seen.add(u)
                soup = pages.pop(u)
                rows = catalog_rows(soup)
                paged.update(rows)
                evidence.append({'url': u, 'count': len(rows), 'ids': list(rows)})
                pending.extend(v for v in pagination(soup) if v not in seen)
    missing = sorted(set(paged) - set(expected))
    extra = sorted(set(expected) - set(paged))
    beyond_url = CATALOG + '?limit=1000&start=' + str(len(expected))
    beyond, _, _ = html(beyond_url, True)
    proof = {'checked_at': meta['checked_at'], 'limit_1000_count': len(expected),
        'limit_1000_pagination': pagination(full), 'pagination_unique_count': len(paged),
        'pagination_pages': evidence, 'missing_from_limit': missing, 'missing_from_pages': extra,
        'beyond_url': beyond_url, 'beyond_count': len(catalog_rows(beyond)),
        'verified': not missing and not extra and not catalog_rows(beyond)}
    dump(OUT / 'catalog_discovery.json', proof)
    if not proof['verified']:
        raise ValueError('Discovery mismatch; see catalog_discovery.json')
    print(f'CATALOG verified={len(expected)} pagination_pages={len(evidence)}', flush=True)
    return expected, proof


def gallery(pid, refresh=False):
    path = RAW / 'gallery' / (pid + '.json')
    meta_path = path.with_suffix('.meta.json')
    if not refresh and path.exists() and meta_path.exists() and fresh(read(meta_path)):
        data, meta = read(path), read(meta_path)
    else:
        params = {'namespace': 'gallery.updateGalleryList', 'args': json.dumps(['eid=7054', 'cid=' + pid, 'canedit='])}
        r = request('POST', AJAX, data=params)
        data = r.json()  # Parse JSON only; never execute server-supplied scripts.
        meta = {'url': AJAX, 'request_data': params, 'checked_at': now(), 'status': r.status_code}
        dump(path, data)
        dump(meta_path, meta)
    blocks = [a[2] for a in data if isinstance(a, list) and len(a) >= 3 and a[0] in ('assign', 'append') and a[1] == 'gallery_list_ajax']
    if not blocks:
        raise ValueError('Gallery response missing target HTML')
    return BeautifulSoup('\n'.join(blocks), 'html.parser'), meta


def ajax_content(namespace, args, refresh=False, query=''):
    """Only public read endpoints observed in the source JS; no eval or mutation."""
    allowed = {'items.ajaxLoadMenu', 'items.ajaxLoadItem', 'news.updateNewsList', 'news.newsFull'}
    if namespace not in allowed:
        raise ValueError('Unsupported read endpoint')
    params = {'namespace': namespace, 'args': json.dumps(args)}
    key = hashlib.sha256((namespace + params['args'] + query).encode()).hexdigest()
    path = RAW / 'ajax' / (key + '.json')
    mp = path.with_suffix('.meta.json')
    if not refresh and path.exists() and mp.exists() and fresh(read(mp)):
        data, meta = read(path), read(mp)
    else:
        r = request('POST', AJAX + query, data=params)
        data = r.json()
        meta = {'url': AJAX + query, 'request_data': params, 'status': r.status_code, 'checked_at': now()}
        dump(path, data)
        dump(mp, meta)
    chunks = []
    for action in data:
        if isinstance(action, list) and len(action) >= 3 and action[0] in ('assign', 'append', 'dialog', 'replace'):
            chunks.append(action[2])
    if not chunks:
        raise ValueError('No content in ' + namespace)
    return BeautifulSoup('\n'.join(chunks), 'html.parser'), meta, path


def dynamic_tabs(row, root, refresh):
    pid = row['id']
    row['materials'] = []
    row['news'] = []
    service = root.select_one('#tab_items #service[data-parent]')
    if service:
        parent = service['data-parent']
        menu, meta, path = ajax_content('items.ajaxLoadMenu', int(pid), refresh,
            '&itemsroot=' + parent + '&ishow=' + service.get('data-ish', '0'))
        row['materials_menu_raw'] = rel(path)
        queue = [('cat', parent)]
        queue.extend((a['data-typ'], a['data-typid']) for a in menu.select('[data-typ][data-typid]'))
        seen = set()
        while queue:
            kind, ident = queue.pop(0)
            if (kind, ident) in seen:
                continue
            seen.add((kind, ident))
            content, cmeta, cpath = ajax_content('items.ajaxLoadItem', [kind, int(ident), int(pid)], refresh)
            for script in content.select('script,style'):
                script.decompose()
            row['materials'].append({'id': ident, 'type': kind, 'title': text(content.select_one('h3')),
                'text': text(content), 'raw_path': rel(cpath), 'checked_at': cmeta['checked_at'],
                'source_url': row['source_url']})
            extract_media(content, row['source_url'], cmeta['checked_at'], row['media'])
            queue.extend((a['data-typ'], a['data-typid']) for a in content.select('[data-typ][data-typid]'))
        row['materials_status'] = 'collected'
    else:
        row['materials_status'] = 'not_present_in_card'
    if root.select_one('#tab_news_flat'):
        news, nmeta, npath = ajax_content('news.updateNewsList', ['eid=7054', 'cid=' + pid, 'canedit='], refresh)
        row['news_list_raw'] = rel(npath)
        extract_media(news, row['source_url'], nmeta['checked_at'], row['media'])
        for ident in unique(re.findall(r'ed\.news\.newsFull\((\d+)\)', str(news))):
            content, cmeta, cpath = ajax_content('news.newsFull', int(ident), refresh)
            for script in content.select('script,style'):
                script.decompose()
            row['news'].append({'id': ident, 'text': text(content), 'raw_path': rel(cpath),
                'checked_at': cmeta['checked_at'], 'source_url': row['source_url']})
            extract_media(content, row['source_url'], cmeta['checked_at'], row['media'])
        row['news_status'] = 'collected'
    else:
        row['news_status'] = 'not_present_in_card'


def download(item):
    url = item['url']
    download_url = url
    if item['type'] == 'document_link':
        host = urlparse(url).hostname or ''
        if host in ('disk.yandex.ru', 'yadi.sk'):
            try:
                info = request('GET', 'https://cloud-api.yandex.net/v1/disk/public/resources/download', params={'public_key': url}).json()
                download_url = info['href']
            except Exception as exc:
                item.update(download_status='unavailable', local_path='', error='Public catalog download: ' + str(exc), download_checked_at=now())
                return
        elif host == 'drive.google.com' and re.search(r'/file/d/([^/]+)', url):
            ident = re.search(r'/file/d/([^/]+)', url)[1]
            download_url = 'https://drive.google.com/uc?export=download&id=' + ident
        else:
            item.update(download_status='original_link', local_path='', note='Online catalog/website supplied instead of a direct file')
            return
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    meta_path = RAW / 'downloads' / (key + '.json')
    if meta_path.exists():
        saved = read(meta_path)
        if saved.get('download_status') == 'saved' and (ROOT / saved['local_path']).is_file():
            item.update(saved)
            return
    for attempt in range(3):
        dest = None
        try:
            with request('GET', download_url, stream=True) as r:
                mime = r.headers.get('Content-Type', '').split(';')[0].lower()
                if mime in ('text/html', 'application/xhtml+xml'):
                    raise ValueError('Attachment returned HTML')
                if mime.startswith('video/'):
                    item.update(download_status='original_link', local_path='', mime_type=mime)
                    return
                ext = Path(unquote(urlparse(r.url).path)).suffix.lower()
                if not re.fullmatch(r'\.[a-z0-9]{1,8}', ext):
                    ext = mimetypes.guess_extension(mime) or '.bin'
                dest = MEDIA / (key + ext)
                temp = dest.with_suffix(dest.suffix + '.part')
                size, digest = 0, hashlib.sha256()
                with temp.open('wb') as f:
                    for chunk in r.iter_content(131072):
                        if chunk:
                            f.write(chunk)
                            size += len(chunk)
                            digest.update(chunk)
                if not size:
                    raise ValueError('Empty attachment')
                temp.replace(dest)
                info = {'download_status': 'saved', 'local_path': rel(dest), 'mime_type': mime,
                    'bytes': size, 'sha256': digest.hexdigest(), 'download_checked_at': now(), 'final_url': r.url}
                dump(meta_path, info)
                item.update(info)
                return
        except Exception as exc:
            if dest:
                dest.with_suffix(dest.suffix + '.part').unlink(missing_ok=True)
            if attempt == 2:
                item.update(download_status='unavailable', local_path='', error=str(exc), download_checked_at=now())
                return
            time.sleep(attempt + 1)


def extract_media(root, source_url, checked_at, items):
    seen = {m['url'] for m in items}

    def add(kind, value, title='', **extra):
        if not value or not value.strip() or value.strip().startswith(('#', 'javascript:')):
            return
        url = urljoin(source_url, value.strip())
        if url in seen or not url.startswith(('https://', 'http://')):
            return
        seen.add(url)
        item = {'type': kind, 'title': title or unquote(Path(urlparse(url).path).name), 'url': url, 'source_url': source_url,
                'fetched_at': checked_at, 'local_path': '', **extra}
        if kind == 'logo':
            item['is_placeholder'] = '/images/no-logo.' in url or '/repository/mitex_china_logo_' in url
        if kind in ('video', 'embed'):
            item['download_status'] = 'original_link'
        else:
            download(item)
        items.append(item)

    for node in root.select('[style]'):
        for value in re.findall(r'url\([\s\'"]*([^\)\'"]+)', node['style']):
            if '/flags/' in value or '/directory/' in value or '/images/icons/' in value:
                continue
            add('logo' if 'company_logo' in node.get('class', []) else 'image', value, node.get('title', '') or text(node.select_one('.title-image-gallery')))
    for image in root.select('img[src],img[data-src]'):
        value = image.get('data-src') or image.get('src')
        if '/flags/' not in value and '/images/directory/' not in value and '/images/icons/' not in value:
            parent = image.find_parent('a', href=True)
            if parent and re.search(r'\.(jpg|jpeg|png|webp|gif)(?:[?#]|$)', parent['href'], re.I):
                value = parent['href']
            add('image', value, image.get('alt') or image.get('title', ''))
    for video in root.select('video,audio'):
        poster = urljoin(source_url, video['poster']) if video.get('poster') else ''
        if poster:
            add('image', poster, 'Video poster', role='poster')
        for node in [video] + video.select('source[src]'):
            if node.get('src'):
                add('video', node['src'], video.get('title', ''), poster=poster)
    for frame in root.select('iframe[src],embed[src],object[data]'):
        value = frame.get('src') or frame.get('data')
        if value:
            if DOC.search(value):
                kind = 'document'
            elif 'present' in frame.get('class', []):
                kind = 'document_link'
            elif frame.name == 'embed' or VIDEO.search(value):
                kind = 'video'
            else:
                kind = 'embed'
            add(kind, value, frame.get('title', ''))
    for a in root.select('a[href]'):
        value = a['href']
        if DOC.search(value) or a.has_attr('download') or ('efiles' in a.get('class', []) and not re.search(r'\.(jpg|jpeg|png|webp|gif)(?:[?#]|$)', value, re.I)):
            add('document', value, text(a.select_one('.files_title')) or a.get('title') or text(a))
        elif VIDEO.search(value):
            add('video', value, text(a))
        elif re.search(r'\.(jpg|jpeg|png|webp|gif)(?:[?#]|$)', value, re.I):
            add('image', value, text(a) or a.get('title', ''))


def card(record, old, refresh=False):
    pid = record['id']
    path = OUT / 'cards' / (pid + '.json')
    if not refresh and path.exists():
        prev = read(path)
        if prev.get('parser_version') == VERSION and prev.get('detail_status') == 'collected' and prev.get('detail_checked_at', '')[:10] == now()[:10] and not any(m.get('download_status') == 'unavailable' for m in prev.get('media', [])):
            prev['catalog_checked_at'] = record['catalog_checked_at']
            dump(path, prev)
            return prev
    row = {**record, 'website': '', 'websites': [], 'contacts': {'emails': [], 'phones': [], 'social': []},
        'categories': [], 'media': [], 'logo_local': '', 'parser_version': VERSION,
        'source_screenshot': f'docs/media/source-cards/{pid}.jpg', 'screenshot_status': 'pending'}
    try:
        soup, meta, raw_path = html(row['source_url'], refresh, allow_seed=True)
        root = soup.select_one('.expo_inner')
        if not root or not root.select_one('h1'):
            raise ValueError('Missing card root/title')
        row.update(detail_checked_at=meta['checked_at'], detail_parsed_at=now(), raw_html=rel(raw_path),
            detail_cache_origin=meta.get('reused_from', 'portal_or_live'), name=text(root.select_one('h1')))
        row['country'] = text(root.select_one('.company_img_country')) or row['country']
        row['stands'] = unique(text(n) for n in root.select('.company_stand_num')) or row['stands']
        desc = root.select_one('.company_desc .description')
        if desc is None:
            desc = root.select_one('.company_desc')
            if desc:
                desc = BeautifulSoup(str(desc), 'html.parser')
                for n in desc.select('.top_title,.company_icon,.roll_up_description,.chitatdalee_description_btn,.roll_up_description_btn'):
                    n.decompose()
        row['about'] = text(desc)
        row['rubrics'] = [{'id': n.get('id'), 'name': text(n.select_one('.rubr_name')).rstrip(' :'),
            'values': unique(text(v) for v in n.select('.rubr_box .rubr'))} for n in root.select('.rubr_cat')]
        # Preserve legacy flattened categories as well as separately typed fields.
        row['categories'] = unique(v for group in row['rubrics'] for v in group['values'])
        row['halls'] = unique(v for g in row['rubrics'] if g['name'].lower() == 'зал' for v in g['values'])
        row['product_categories'] = unique(v for g in row['rubrics'] if 'рубрикатор' in g['name'].lower() for v in g['values'])
        row['participation_types'] = unique(v for g in row['rubrics'] if 'участник выставки' in g['name'].lower() for v in g['values'])
        contacts = root.select_one('#tab_contacts_flat')
        row['contacts_text'] = text(contacts)
        row['contact_blocks'] = [text(n) for n in root.select('.company_contacts')]
        row['card_text'] = text(root)
        row['tabs'] = [{'id': n.get('id'), 'title': text(n.select_one('h2,h3')), 'text': text(n)} for n in root.select('[id^="tab_"]')]
        row['contact_sources'] = []

        def contact(kind, value):
            value = value.strip()
            if value and value not in row['contacts'][kind]:
                row['contacts'][kind].append(value)
                row['contact_sources'].append({'kind': kind, 'value': value, 'source_url': row['source_url'], 'checked_at': meta['checked_at']})

        for a in root.select('a[href]'):
            href = a['href'].strip()
            absolute = urljoin(row['source_url'], href)
            host = (urlparse(absolute).hostname or '').lower().removeprefix('www.')
            if href.lower().startswith('mailto:'):
                for value in re.split('[,;]', unquote(href[7:].split('?')[0])):
                    contact('emails', value)
            elif href.lower().startswith('tel:'):
                contact('phones', unquote(href[4:]))
            elif host in SOCIAL or any(host.endswith('.' + h) for h in SOCIAL):
                contact('social', absolute)
        for email in re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', row['card_text']):
            contact('emails', email)
        phone_text = text(contacts)
        # Each source field also remains verbatim in contacts_text/contact_blocks.
        for phone in re.findall(r'(?<!\d)\+?\d[\d ()\-]{7,}\d', phone_text):
            contact('phones', phone)
        for a in root.select('.company_site a[href]'):
            row['websites'].extend(website_urls(a['href']))
        row['websites'] = unique(row['websites'])
        row['website'] = next(iter(row['websites']), '')
        extract_media(root, row['source_url'], meta['checked_at'], row['media'])
        if 'gallery.updateGalleryList' in str(soup):
            gal, gmeta = gallery(pid, refresh)
            extract_media(gal, row['source_url'], gmeta['checked_at'], row['media'])
            row['gallery_status'] = 'collected'
            row['gallery_checked_at'] = gmeta['checked_at']
            row['gallery_raw'] = rel(RAW / 'gallery' / (pid + '.json'))
        else:
            row['gallery_status'] = 'not_present_in_card'
        dynamic_tabs(row, root, refresh)
        row['logo_local'] = next((m['local_path'] for m in row['media'] if m['type'] == 'logo' and m['download_status'] == 'saved'), '')
        row['logo_is_placeholder'] = any(m.get('is_placeholder') for m in row['media'] if m['type'] == 'logo')
        row['video_status'] = 'present' if any(m['type'] == 'video' for m in row['media']) else 'not_present_in_card'
        row['document_status'] = 'present' if any(m['type'] in ('document', 'document_link') for m in row['media']) else 'not_present_in_card'
        row['detail_status'] = 'collected'
        if old:
            row['historical_findings'] = {'source_file': 'data/site/exhibitors.json',
                'verification_status': 'historical_not_rechecked', 'detail_checked_at': old.get('detail_checked_at'),
                'website_checks': old.get('website_checks', []), 'contacts': old.get('contacts', {}),
                'website': old.get('website', ''), 'contact_sources': old.get('contact_sources', [])}
        if (ROOT / row['source_screenshot']).is_file():
            row['screenshot_status'] = 'saved'
    except Exception as exc:
        row['detail_status'] = 'error'
        row['error'] = str(exc)
    dump(path, row)
    with LOCK:
        with (OUT / 'progress.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'id': pid, 'status': row['detail_status'], 'at': now()}, ensure_ascii=False) + '\n')
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8, choices=range(6, 9))
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--finalize-cached', action='store_true', help='Publish stable 745-card snapshot from existing files only; no network or downloads')
    args = parser.parse_args()
    if args.finalize_cached:
        return finalize_cached()
    for p in (OUT, RAW, MEDIA):
        p.mkdir(parents=True, exist_ok=True)
    started = now()
    records, discovery = discover(args.workers)
    previous_path = ROOT / 'data/site/exhibitors.json'
    previous = {str(r['id']): r for r in read(previous_path)} if previous_path.exists() else {}
    for record in records.values():
        record['catalog_checked_at'] = discovery['checked_at']
    rows = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(card, r, previous.get(r['id']), args.refresh): r['id'] for r in records.values()}
        for future in cf.as_completed(futures):
            pid = futures[future]
            row = future.result()
            rows[pid] = row
            # Per-card files are the durable resume journal; aggregate is also atomic.
            if len(rows) % 25 == 0 or len(rows) == len(records):
                target = 'participants.json' if len(rows) == len(records) else 'participants_progress.json'
                dump(OUT / target, [rows[k] for k in records if k in rows])
            errors = [{'id': x['id'], 'stage': 'detail', 'error': x['error']} for x in rows.values() if x.get('error')]
            errors += [{'id': x['id'], 'stage': 'media', 'url': m['url'], 'error': m.get('error')} for x in rows.values() for m in x.get('media', []) if m.get('download_status') == 'unavailable']
            dump(OUT / 'errors_catalog.json', errors)
            if len(rows) % 25 == 0 or len(rows) == len(records):
                print(f'CARDS {len(rows)}/{len(records)} errors={len(errors)} media={sum(len(x.get("media", [])) for x in rows.values())}', flush=True)
    ordered = [rows[k] for k in records]
    media = [dict(m, participant_id=r['id']) for r in ordered for m in r.get('media', [])]
    coverage = {'started_at': started, 'finished_at': now(), 'source_url': CATALOG,
        'catalog_discovered': len(records), 'pagination_verified': discovery['verified'],
        'pagination_pages': len(discovery['pagination_pages']),
        'cards_collected': sum(x['detail_status'] == 'collected' for x in ordered),
        'detail_errors': sum(x['detail_status'] != 'collected' for x in ordered),
        'media_by_type': dict(Counter(m['type'] for m in media)),
        'download_statuses': dict(Counter(m['download_status'] for m in media)),
        'media_errors': sum(m['download_status'] == 'unavailable' for m in media),
        'screenshots_saved': sum((ROOT / x['source_screenshot']).is_file() for x in ordered),
        'screenshots_pending': sum(not (ROOT / x['source_screenshot']).is_file() for x in ordered),
        'raw_html_reused_from_today': sum(x.get('detail_cache_origin', '').startswith('skill-test-') for x in ordered),
        'participants_with_email': sum(bool(x['contacts']['emails']) for x in ordered),
        'participants_with_phone': sum(bool(x['contacts']['phones']) for x in ordered),
        'participants_with_website': sum(bool(x['website']) for x in ordered),
        'participants_with_logo': sum(bool(x['logo_local']) for x in ordered),
        'participants_with_individual_logo': sum(bool(x['logo_local']) and not x.get('logo_is_placeholder') for x in ordered),
        'materials_tabs_collected': sum(x.get('materials_status') == 'collected' for x in ordered),
        'material_nodes_collected': sum(len(x.get('materials', [])) for x in ordered),
        'news_tabs_collected': sum(x.get('news_status') == 'collected' for x in ordered),
        'news_items_collected': sum(len(x.get('news', [])) for x in ordered),
        'new_ids_vs_historical': sorted(set(records) - set(previous)),
        'historical_ids_absent_today': sorted(set(previous) - set(records)),
        'errors_file': 'data/portal/errors_catalog.json', 'discovery_file': 'data/portal/catalog_discovery.json',
        'notes': ['Video/embed URLs are links, not downloaded videos.',
                  'Historical contacts are isolated in historical_findings and not checked today.',
                  'Screenshot files are managed separately; source_screenshot names their target path.']}
    dump(OUT / 'media_catalog.json', media)
    dump(OUT / 'coverage_catalog.json', coverage)
    print('PARTICIPANTS_READY ' + rel(OUT / 'participants.json'), flush=True)
    print(json.dumps(coverage, ensure_ascii=False), flush=True)
    return 1 if coverage['detail_errors'] or coverage['media_errors'] else 0


def finalize_cached():
    """Recover complete aggregate without repeating discovery or heavy downloads."""
    discovery = read(OUT / 'catalog_discovery.json')
    ids = unique(pid for page in discovery['pagination_pages'] for pid in page['ids'])
    rows = {pid: read(OUT / 'cards' / (pid + '.json')) for pid in ids}
    if len(rows) != discovery['limit_1000_count']:
        raise ValueError('Missing cached cards; refusing to replace the full snapshot')

    def cached_download(item):
        key = hashlib.sha256(item['url'].encode()).hexdigest()[:24]
        mp = RAW / 'downloads' / (key + '.json')
        if mp.exists() and read(mp).get('download_status') == 'saved' and (ROOT / read(mp)['local_path']).is_file():
            item.update(read(mp))
        else:
            item.update(download_status='not_downloaded', local_path='', note='Heavy download pass stopped; original URL preserved')
    # Offline extraction of responses that arrived before the pass was stopped.
    original_download = globals()['download']
    globals()['download'] = cached_download
    try:
        for path in (RAW / 'ajax').glob('*.json'):
            if path.name.endswith('.meta.json'):
                continue
            meta = read(path.with_suffix('.meta.json'))
            params = meta['request_data']
            args = json.loads(params['args'])
            ns = params['namespace']
            if ns != 'items.ajaxLoadItem':
                continue
            pid = str(args[2])
            if pid not in rows:
                continue
            row = rows[pid]
            chunks = [a[2] for a in read(path) if len(a) >= 3 and a[0] == 'assign']
            content = BeautifulSoup('\n'.join(chunks), 'html.parser')
            for node in content.select('script,style'):
                node.decompose()
            entry = {'id': str(args[1]), 'type': args[0], 'title': text(content.select_one('h3')),
                'text': text(content), 'raw_path': rel(path), 'checked_at': meta['checked_at'], 'source_url': row['source_url']}
            materials = row.setdefault('materials', [])
            if not any(m['id'] == entry['id'] and m['type'] == entry['type'] for m in materials):
                materials.append(entry)
            extract_media(content, row['source_url'], meta['checked_at'], row['media'])
    finally:
        globals()['download'] = original_download
    for row in rows.values():
        row['source_screenshot'] = f'docs/media/source-cards/{row["id"]}.jpg'
        row['screenshot_status'] = 'pending'  # Owned by the separate screenshot worker.
        if 'materials_status' not in row:
            row['materials_status'] = 'pending' if any(t['id'] == 'tab_items' for t in row.get('tabs', [])) else 'not_present_in_card'
        if 'news_status' not in row:
            row['news_status'] = 'pending' if any(t['id'] == 'tab_news_flat' for t in row.get('tabs', [])) else 'not_present_in_card'
        for m in row['media']:
            if m['type'] == 'logo':
                m['is_placeholder'] = '/images/no-logo.' in m['url'] or '/repository/mitex_china_logo_' in m['url']
            if m['type'] == 'document' and m.get('title'):
                m['title'] = re.split(r'\s+\d{2}\.\d{2}\.\d{4}\b', m['title'])[0]
        row['logo_is_placeholder'] = any(m.get('is_placeholder') for m in row['media'] if m['type'] == 'logo')
        dump(OUT / 'cards' / (row['id'] + '.json'), row)
    ordered = list(rows.values())
    media = [dict(m, participant_id=r['id']) for r in ordered for m in r['media']]
    errors = [{'id': r['id'], 'stage': 'detail', 'error': r['error']} for r in ordered if r.get('error')]
    errors += [{'id': m['participant_id'], 'stage': 'media', 'url': m['url'], 'error': m.get('error')} for m in media if m['download_status'] == 'unavailable']
    coverage = {'finished_at': now(), 'source_url': CATALOG, 'catalog_discovered': len(rows),
        'pagination_verified': discovery['verified'], 'pagination_pages': len(discovery['pagination_pages']),
        'cards_collected': sum(r['detail_status'] == 'collected' for r in ordered),
        'detail_errors': sum(r['detail_status'] != 'collected' for r in ordered),
        'media_errors': sum(m['download_status'] == 'unavailable' for m in media),
        'media_by_type': dict(Counter(m['type'] for m in media)),
        'download_statuses': dict(Counter(m['download_status'] for m in media)),
        'materials_tabs_collected': sum(r['materials_status'] == 'collected' for r in ordered),
        'materials_tabs_pending': sum(r['materials_status'] == 'pending' for r in ordered),
        'material_nodes_collected': sum(len(r.get('materials', [])) for r in ordered),
        'news_tabs_collected': sum(r['news_status'] == 'collected' for r in ordered),
        'news_tabs_pending': sum(r['news_status'] == 'pending' for r in ordered),
        'participants_with_logo': sum(bool(r['logo_local']) for r in ordered),
        'participants_with_individual_logo': sum(bool(r['logo_local']) and not r['logo_is_placeholder'] for r in ordered),
        'screenshots_status': 'managed_by_separate_worker',
        'all_available_attachments_collected': False,
        'aggregate_status': 'stable', 'network_pass_stopped': True,
        'notes': ['All catalog IDs and main detailed cards collected today.',
                  'Heavy dynamic materials pass stopped at user request; cached results merged offline.',
                  'Pending dynamic materials/news and not_downloaded attachments are explicitly incomplete.',
                  'Historical contacts are isolated and not verified today. Videos remain links.']}
    dump(OUT / 'errors_catalog.json', errors)
    dump(OUT / 'media_catalog.json', media)
    dump(OUT / 'coverage_catalog.json', coverage)
    dump(OUT / 'participants.json', ordered)
    print('STABLE_PARTICIPANTS_READY ' + json.dumps(coverage, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
