"""Finish only pending MITEX tabs/media; checkpoint full aggregate by ID.

No catalog/detail refetch, no retries of already unavailable media. Eight public
read workers; 2 HTTP attempts, bounded tab/file time, 30 million byte transfer
limit, 15 million byte per hosted asset, 200 million byte target for all docs.
Large assets remain source links; completed local assets live outside hosting.
"""
import concurrent.futures as cf
import copy
import hashlib
import json
import mimetypes
import re
import shutil
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh_portal_catalog as base

ROOT, OUT, RAW, MEDIA = base.ROOT, base.OUT, base.RAW, base.MEDIA
LOCAL = ROOT / 'data/attachments/catalog'
PENDING = {'pending', 'not_downloaded', 'queued', 'not_collected'}
LIMIT = 30_000_000
HOST_FILE_LIMIT = 15_000_000
HOST_TOTAL_TARGET = 200_000_000
LOCK = threading.RLock()
TLS = threading.local()
ROWS = {}
ORDER = []
COMPLETED = 0
STARTED = base.now()


def client():
    if not hasattr(TLS, 'session'):
        TLS.session = requests.Session()
        TLS.session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; MITEX-public-catalog-research/1.0)'})
    return TLS.session


def bounded_request(method, url, **kwargs):
    """AJAX only: two attempts; each response has a total streaming deadline."""
    error = None
    for attempt in range(2):
        try:
            start = time.monotonic()
            with client().request(method, url, timeout=(6, 10), stream=True, **kwargs) as response:
                response.raise_for_status()
                chunks = []
                for chunk in response.iter_content(65536):
                    if time.monotonic() - start > 20:
                        raise TimeoutError('AJAX total deadline exceeded (20 seconds)')
                    chunks.append(chunk)
                response._content = b''.join(chunks)
                response._content_consumed = True
                return response
        except Exception as exc:
            error = exc
            if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (400, 401, 403, 404, 410):
                break
            if attempt == 0:
                time.sleep(1)
    raise RuntimeError(f'Public read failed after bounded attempts: {error}')


def cache_info(url):
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = RAW / 'downloads' / (key + '.json')
    if path.exists():
        info = base.read(path)
        status = info.get('download_status')
        dest = info.get('preserved_local_path') if status == 'saved_locally_external_link' else info.get('local_path')
        if status in ('saved', 'saved_locally_external_link') and dest and (ROOT / dest).is_file():
            return info
    return None


def classify(item):
    if base.VIDEO.search(item['url']) or item.get('mime_type', '').startswith('video/'):
        item['type'] = 'video'
    if item['type'] in ('video', 'embed'):
        item.update(download_status='original_link', local_path='')
        return True
    return False


def register_media(item):
    """Discover first; downloading is a separate bounded queue."""
    if classify(item):
        return
    cached = cache_info(item['url'])
    if cached:
        item.update(cached)
    else:
        item.update(download_status='not_downloaded', local_path='')


def checkpoint(pid, patch, event):
    """Merge fields by ID into latest full file, preserving other workers' fields."""
    global COMPLETED, ROWS
    with LOCK:
        latest = base.read(OUT / 'participants.json')
        if len(latest) != len(ORDER) or {str(x['id']) for x in latest} != set(ORDER):
            raise ValueError('Refusing to overwrite an incomplete/different participant aggregate')
        current = {str(r['id']): r for r in latest}
        current[pid].update(copy.deepcopy(patch))
        ROWS = current
        cp = OUT / 'cards' / (pid + '.json')
        card = base.read(cp)
        card.update(copy.deepcopy(patch))
        base.dump(cp, card)
        base.dump(OUT / 'participants.json', [current[i] for i in ORDER])
        COMPLETED += 1
        with (OUT / 'pending_progress.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'id': pid, 'event': event, 'checked_at': base.now(), 'full_count': len(latest)}, ensure_ascii=False) + '\n')
        if COMPLETED % 10 == 0:
            print(f'CHECKPOINTS {COMPLETED} full_participants={len(latest)} event={event}', flush=True)


def save_tab(row, section, event):
    keys = [section, section + '_status', section + '_checked_at', section + '_errors', 'media']
    for extra in ('materials_menu_raw', 'news_list_raw'):
        if extra in row:
            keys.append(extra)
    checkpoint(row['id'], {k: row[k] for k in keys if k in row}, event)


def failure(ident, kind, exc):
    return {'id': str(ident), 'type': kind, 'status': 'unavailable',
            'checked_at': base.now(), 'error': str(exc), 'attempt_policy': 'at most 2 HTTP attempts; 20 second response deadline'}


def crawl_materials(row, root):
    row.setdefault('materials', [])
    errors = row.setdefault('materials_errors', [])
    index = {(str(x['type']), str(x['id'])): x for x in row['materials']}
    started = time.monotonic()
    try:
        service = root.select_one('#tab_items #service[data-parent]')
        if service is None:
            raise ValueError('Materials tab lacks its public service root in saved card')
        parent = service['data-parent']
        menu, _, path = base.ajax_content('items.ajaxLoadMenu', int(row['id']), query='&itemsroot=' + parent + '&ishow=' + service.get('data-ish', '0'))
        row['materials_menu_raw'] = base.rel(path)
        queue = [('cat', parent)] + [(n['data-typ'], n['data-typid']) for n in menu.select('[data-typ][data-typid]')]
        seen = set()
        while queue:
            kind, ident = queue.pop(0)
            ident = str(ident)
            key = (kind, ident)
            if key in seen:
                continue
            seen.add(key)
            try:
                if time.monotonic() - started > 180:
                    raise TimeoutError('Materials tab 180-second deadline exhausted after public read attempts')
                content, meta, raw = base.ajax_content('items.ajaxLoadItem', [kind, int(ident), int(row['id'])])
                children = [(n['data-typ'], n['data-typid']) for n in content.select('[data-typ][data-typid]')]
                queue.extend(children)
                for n in content.select('script,style'):
                    n.decompose()
                item = {'id': ident, 'type': kind, 'title': base.text(content.select_one('h3')),
                    'text': base.text(content), 'raw_path': base.rel(raw), 'checked_at': meta['checked_at'],
                    'source_url': row['source_url'], 'status': 'collected'}
                old = index.get(key)
                index[key] = item
                before = len(row['media'])
                base.extract_media(content, row['source_url'], meta['checked_at'], row['media'])
                if old is None or before != len(row['media']) or old.get('status') != 'collected':
                    row['materials'] = list(index.values())
                    save_tab(row, 'materials', 'material_node:' + ident)
            except Exception as exc:
                entry = failure(ident, kind, exc)
                errors.append(entry)
                if key not in index:
                    index[key] = entry
                row['materials'] = list(index.values())
                save_tab(row, 'materials', 'material_node_unavailable:' + ident)
        row['materials_status'] = 'unavailable' if errors else 'collected'
    except Exception as exc:
        errors.append(failure(row['id'], 'materials_tab', exc))
        row['materials_status'] = 'unavailable'
    row['materials_checked_at'] = base.now()
    save_tab(row, 'materials', 'materials_tab_finished')


def crawl_news(row):
    row.setdefault('news', [])
    errors = row.setdefault('news_errors', [])
    items = {str(n['id']): n for n in row['news']}
    try:
        page, meta, path = base.ajax_content('news.updateNewsList', ['eid=7054', 'cid=' + row['id'], 'canedit='])
        row['news_list_raw'] = base.rel(path)
        base.extract_media(page, row['source_url'], meta['checked_at'], row['media'])
        started = time.monotonic()
        for ident in base.unique(re.findall(r'ed\.news\.newsFull\((\d+)\)', str(page))):
            try:
                if time.monotonic() - started > 180:
                    raise TimeoutError('News tab 180-second deadline exhausted')
                content, cmeta, raw = base.ajax_content('news.newsFull', int(ident))
                for n in content.select('script,style'):
                    n.decompose()
                items[ident] = {'id': ident, 'text': base.text(content), 'raw_path': base.rel(raw),
                    'checked_at': cmeta['checked_at'], 'source_url': row['source_url'], 'status': 'collected'}
                base.extract_media(content, row['source_url'], cmeta['checked_at'], row['media'])
            except Exception as exc:
                entry = failure(ident, 'news_item', exc)
                items[ident] = entry
                errors.append(entry)
            row['news'] = list(items.values())
            save_tab(row, 'news', 'news_item:' + ident)
        row['news_status'] = 'unavailable' if errors else 'collected'
    except Exception as exc:
        errors.append(failure(row['id'], 'news_tab', exc))
        row['news_status'] = 'unavailable'
    row['news_checked_at'] = base.now()
    save_tab(row, 'news', 'news_tab_finished')


def crawl(row):
    try:
        root = BeautifulSoup((ROOT / row['raw_html']).read_bytes(), 'html.parser')
    except Exception as exc:
        for kind in ('materials', 'news'):
            if row.get(kind + '_status') in PENDING:
                row[kind + '_status'] = 'unavailable'
                row[kind + '_checked_at'] = base.now()
                row[kind + '_errors'] = [failure(row['id'], kind + '_tab', exc)]
                save_tab(row, kind, kind + '_unavailable')
        return
    if row.get('materials_status') in PENDING:
        crawl_materials(row, root)
    if row.get('news_status') in PENDING:
        crawl_news(row)


def checked_path(path, parent):
    resolved = path.resolve()
    resolved.relative_to(parent.resolve())
    return resolved


def download(item):
    """At most two attempts; partials are always outside docs, never published."""
    item = copy.deepcopy(item)
    if classify(item):
        item['download_checked_at'] = base.now()
        return item
    cached = cache_info(item['url'])
    if cached:
        item.update(cached)
        return item
    key = hashlib.sha256(item['url'].encode()).hexdigest()[:24]
    suffix = Path(unquote(urlparse(item['url']).path)).suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}', suffix):
        suffix = '.bin'
    part = checked_path(LOCAL / (key + suffix + '.part'), LOCAL)
    # Previously interrupted transfers already prove the file exceeds the cap.
    if part.exists() and part.stat().st_size > LIMIT:
        item.update(download_status='large_external_link', local_path='', preserved_partial_path=base.rel(part),
                    bytes_lower_bound=part.stat().st_size, download_checked_at=base.now(),
                    note='Previously received bytes exceed 30 million byte hosting transfer cap; original link retained')
        return item
    last_error = None
    for attempt in range(2):
        started = time.monotonic()
        try:
            offset = part.stat().st_size if part.exists() else 0
            headers = {'Range': f'bytes={offset}-'} if offset else {}
            with client().get(item['url'], headers=headers, stream=True, timeout=(6, 8)) as response:
                response.raise_for_status()
                mime = response.headers.get('Content-Type', '').split(';')[0].lower()
                if mime.startswith('video/'):
                    item.update(type='video', download_status='original_link', local_path='', mime_type=mime, download_checked_at=base.now())
                    return item
                if mime in ('text/html', 'application/xhtml+xml'):
                    raise ValueError('Attachment returned HTML instead of file')
                resume = offset > 0 and response.status_code == 206
                size = offset if resume else 0
                length = response.headers.get('Content-Length', '')
                total = int(length) + size if length.isdigit() else None
                if total is not None and total > LIMIT:
                    item.update(download_status='large_external_link', local_path='', bytes=total,
                        mime_type=mime, download_checked_at=base.now(), note='Content-Length exceeds 30 million byte transfer limit')
                    if part.exists():
                        item['preserved_partial_path'] = base.rel(part)
                    return item
                with part.open('ab' if resume else 'wb') as f:
                    for chunk in response.iter_content(65536):
                        if time.monotonic() - started > 45:
                            raise TimeoutError('Attachment total attempt deadline exceeded (45 seconds)')
                        if not chunk:
                            continue
                        if size + len(chunk) > LIMIT:
                            item.update(download_status='large_external_link', local_path='',
                                bytes_lower_bound=size + len(chunk), preserved_partial_path=base.rel(part),
                                mime_type=mime, download_checked_at=base.now(), note='Streaming body exceeds 30 million byte transfer limit')
                            return item
                        f.write(chunk)
                        size += len(chunk)
                if not size or (total is not None and size != total):
                    raise ValueError(f'Incomplete attachment: received {size}, expected {total}')
                if suffix == '.bin':
                    suffix = mimetypes.guess_extension(mime) or '.bin'
                dest = checked_path(LOCAL / (key + suffix), LOCAL)
                part.replace(dest)
                digest = hashlib.sha256(dest.read_bytes()).hexdigest()
                with LOCK:
                    docs_total = sum(p.stat().st_size for p in (ROOT / 'docs').rglob('*') if p.is_file())
                    if size <= HOST_FILE_LIMIT and docs_total + size <= HOST_TOTAL_TARGET:
                        hosted = checked_path(MEDIA / dest.name, MEDIA)
                        if hosted.exists():
                            if hashlib.sha256(hosted.read_bytes()).hexdigest() != digest:
                                raise ValueError('Hosting filename collision')
                            dest.unlink()
                        else:
                            shutil.move(str(dest), str(hosted))
                        info = {'download_status': 'saved', 'local_path': base.rel(hosted)}
                    else:
                        info = {'download_status': 'saved_locally_external_link', 'local_path': '', 'preserved_local_path': base.rel(dest)}
                info.update(bytes=size, sha256=digest, mime_type=mime, final_url=response.url,
                            download_checked_at=base.now(), download_attempts=attempt + 1)
                base.dump(RAW / 'downloads' / (key + '.json'), info)
                item.update(info)
                item.pop('error', None)
                item.pop('note', None)
                return item
        except Exception as exc:
            last_error = str(exc)
            if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (400, 401, 403, 404, 410):
                break
            if attempt == 0:
                time.sleep(1)
    item.update(download_status='unavailable', local_path='', download_checked_at=base.now(),
                download_attempts=attempt + 1, error=last_error, note='Bounded attempts exhausted; no automatic retry of unavailable items')
    if part.exists():
        item['preserved_partial_path'] = base.rel(part)
    return item


def finalize():
    rows = base.read(OUT / 'participants.json')
    media = [dict(m, participant_id=r['id']) for r in rows for m in r.get('media', [])]
    errors = [{'id': m['participant_id'], 'stage': 'media', 'url': m['url'], 'error': m.get('error'), 'checked_at': m.get('download_checked_at')} for m in media if m.get('download_status') == 'unavailable']
    for row in rows:
        for kind in ('materials', 'news'):
            errors.extend(dict(e, participant_id=row['id'], stage=kind) for e in row.get(kind + '_errors', []))
    coverage = base.read(OUT / 'coverage_catalog.json')
    tabs = {kind: dict(Counter(r.get(kind + '_status') for r in rows)) for kind in ('materials', 'news')}
    pending_media = sum(m.get('download_status') in PENDING for m in media)
    pending_tabs = sum(r.get(k + '_status') in PENDING for r in rows for k in ('materials', 'news'))
    coverage.update(finished_at=base.now(), pending_resume_started_at=STARTED,
        cards_collected=len(rows), detail_errors=sum(r['detail_status'] != 'collected' for r in rows),
        media_by_type=dict(Counter(m['type'] for m in media)), download_statuses=dict(Counter(m['download_status'] for m in media)),
        media_errors=sum(m.get('download_status') == 'unavailable' for m in media),
        materials_tabs_collected=tabs['materials'].get('collected', 0), materials_tabs_pending=tabs['materials'].get('pending', 0),
        materials_tabs_unavailable=tabs['materials'].get('unavailable', 0),
        news_tabs_collected=tabs['news'].get('collected', 0), news_tabs_pending=tabs['news'].get('pending', 0),
        news_tabs_unavailable=tabs['news'].get('unavailable', 0),
        material_nodes_collected=sum(len(r.get('materials', [])) for r in rows), news_items_collected=sum(len(r.get('news', [])) for r in rows),
        pending_media=pending_media, pending_tabs=pending_tabs,
        all_pending_attempted=not pending_media and not pending_tabs,
        all_available_attachments_collected=not pending_media and not pending_tabs and not errors,
        aggregate_status='stable', network_pass_stopped=False,
        docs_bytes=sum(p.stat().st_size for p in (ROOT / 'docs').rglob('*') if p.is_file()),
        local_attachments_bytes=sum(p.stat().st_size for p in LOCAL.glob('*') if p.is_file()),
        screenshots_saved=sum(r.get('screenshot_status') == 'saved' and (ROOT / r['source_screenshot']).is_file() for r in rows),
        screenshots_pending=sum(r.get('screenshot_status') == 'pending' for r in rows),
        screenshots_source_warnings_report='artifacts/source-screenshots-report.txt',
        hosting_policy={'target_docs_bytes': HOST_TOTAL_TARGET, 'max_docs_bytes': 500_000_000, 'per_hosted_file_bytes': HOST_FILE_LIMIT, 'transfer_cap_bytes': LIMIT},
        notes=['Main 745 cards unchanged; only pending dynamic tabs and attachments were resumed.',
               'Unavailable URLs are terminal with dated errors; large assets stay external links, not unavailable.',
               'Completed large/local-only files have preserved_local_path outside docs. No part files in docs.',
               'Video remains an original source link. Screenshot workflow is separate. Historical contacts remain dated.'])
    base.dump(OUT / 'media_catalog.json', media)
    base.dump(OUT / 'errors_catalog.json', errors)
    base.dump(OUT / 'coverage_catalog.json', coverage)
    print('DONE ' + json.dumps(coverage, ensure_ascii=False), flush=True)
    if pending_media or pending_tabs:
        raise ValueError('Nonterminal pending data remains')


def enforce_hosting_and_screenshots():
    """Local final pass only: hosting budget and already captured screenshot evidence."""
    global ORDER, ROWS
    rows = base.read(OUT / 'participants.json')
    ORDER = [str(r['id']) for r in rows]
    ROWS = {str(r['id']): r for r in rows}
    total = sum(p.stat().st_size for p in (ROOT / 'docs').rglob('*') if p.is_file())
    moves = {}
    for path in sorted(MEDIA.glob('*'), key=lambda p: p.stat().st_size, reverse=True):
        if not path.is_file():
            continue
        size = path.stat().st_size
        if total <= HOST_TOTAL_TARGET and size <= HOST_FILE_LIMIT and not path.name.endswith('.part'):
            continue
        source = checked_path(path, MEDIA)
        target = checked_path(LOCAL / path.name, LOCAL)
        if target.exists():
            if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                raise ValueError('Local attachment filename collision: ' + str(target))
            source.unlink()
        else:
            shutil.move(str(source), str(target))
        moves[base.rel(source)] = base.rel(target)
        total -= size
    screenshots = ROOT / 'artifacts/source-screenshots.json'
    evidence = {str(x['id']): x for x in base.read(screenshots)} if screenshots.exists() else {}
    screenshot_patches = {}
    for row in rows:
        patch = {}
        media = copy.deepcopy(row['media'])
        changed = False
        for item in media:
            if item.get('local_path') in moves:
                item.update(preserved_local_path=moves[item['local_path']], local_path='', download_status='saved_locally_external_link')
                changed = True
        if changed:
            patch['media'] = media
        if row.get('logo_local') in moves:
            patch.update(logo_preserved_local_path=moves[row['logo_local']], logo_local='')
        screen = evidence.get(row['id'])
        if patch:
            checkpoint(row['id'], patch, 'local_hosting_and_screenshot_evidence')
        if row.get('source_screenshot') and (ROOT / row['source_screenshot']).is_file():
            screenshot_patches[row['id']] = {'screenshot_status': 'saved',
                'screenshot_evidence': 'artifacts/source-screenshots.json'}
            if screen:
                screenshot_patches[row['id']]['screenshot_checked_at'] = screen.get('captured_at')
    # All screenshots are already completed by their worker; integrate metadata
    # in one full atomic snapshot, preserving latest data from other producers.
    latest = base.read(OUT / 'participants.json')
    for row in latest:
        if row['id'] in screenshot_patches:
            row.update(screenshot_patches[row['id']])
            cp = OUT / 'cards' / (row['id'] + '.json')
            card = base.read(cp)
            card.update(screenshot_patches[row['id']])
            base.dump(cp, card)
    base.dump(OUT / 'participants.json', latest)
    for path in (RAW / 'downloads').glob('*.json'):
        info = base.read(path)
        if info.get('local_path') in moves:
            info.update(preserved_local_path=moves[info['local_path']], local_path='', download_status='saved_locally_external_link')
            base.dump(path, info)
    assert not list((ROOT / 'docs').rglob('*.part'))
    if total > 500_000_000:
        raise ValueError('Other docs content exceeds the 500 million byte hosting budget')
    print(f'HOSTING_FINAL docs_bytes={total} moved={len(moves)}', flush=True)


def main():
    global ROWS, ORDER
    LOCAL.mkdir(parents=True, exist_ok=True)
    initial = base.read(OUT / 'participants.json')
    ROWS = {str(r['id']): r for r in initial}
    ORDER = list(ROWS)
    expected = base.read(OUT / 'catalog_discovery.json')['limit_1000_count']
    if len(initial) != expected or len(ROWS) != expected:
        raise ValueError('A complete unique participant snapshot is required')
    # Preserve today's full starting snapshot locally, without touching hosting.
    backup = RAW / 'before_pending_resume.json'
    if not backup.exists():
        base.dump(backup, initial)
    base.request = bounded_request
    base.download = register_media
    targets = [copy.deepcopy(r) for r in initial if any(r.get(k + '_status') in PENDING for k in ('materials', 'news'))]
    print(f'START full={len(initial)} target_cards={len(targets)} materials={sum(r.get("materials_status") in PENDING for r in initial)} news={sum(r.get("news_status") in PENDING for r in initial)}', flush=True)
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(crawl, r) for r in targets]
        for i, f in enumerate(cf.as_completed(futures), 1):
            f.result()
            print(f'TABS_CARDS {i}/{len(targets)}', flush=True)
    # One download per unique URL; patch all corresponding participant records.
    jobs = {}
    for row in base.read(OUT / 'participants.json'):
        for item in row.get('media', []):
            if item.get('download_status') in PENDING:
                jobs.setdefault(item['url'], {'item': item, 'ids': set()})['ids'].add(row['id'])
    print(f'DOWNLOADS unique_pending={len(jobs)}', flush=True)
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(download, v['item']): v for v in jobs.values()}
        for i, future in enumerate(cf.as_completed(futures), 1):
            job = futures[future]
            result = future.result()
            for pid in job['ids']:
                row = ROWS[pid]
                items = copy.deepcopy(row['media'])
                for item in items:
                    if item['url'] == result['url'] and item.get('download_status') in PENDING:
                        item.update(result)
                checkpoint(pid, {'media': items, 'video_status': 'present' if any(m['type'] == 'video' for m in items) else row.get('video_status', 'not_present_in_card'),
                    'document_status': 'present' if any(m['type'] in ('document', 'document_link') for m in items) else row.get('document_status', 'not_present_in_card')}, 'media:' + result['download_status'])
            if i % 10 == 0 or i == len(jobs):
                print(f'DOWNLOADS {i}/{len(jobs)} last={result["download_status"]}', flush=True)
    enforce_hosting_and_screenshots()
    finalize()


if __name__ == '__main__':
    main()
