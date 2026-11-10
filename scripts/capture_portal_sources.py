"""Capture original MITEX cards only; never writes portal datasets or participants.

Run: python scripts/capture_portal_sources.py --workers 6
Successful, valid JPEGs from the checkpoint are resumed. No networkidle wait,
analytics/chat are blocked, but image, stylesheet, font and gallery requests load.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / 'artifacts/source-screenshots.json'
OUTPUT = ROOT / 'docs/media/source-cards'
BLOCKED = ('mc.yandex.', 'mc.yandex.com', 'google-analytics.com',
           'googletagmanager.com', 'doubleclick.net', 'jivosite.com',
           'jivo.ru', 'top-fwz1.mail.ru', 'top.mail.ru', 'connect.facebook.net')

EXPAND = r"""() => {
  const root = document.querySelector('.expo_inner');
  if (!root || !root.querySelector('h1')) throw Error('Original card root/h1 absent');
  for (const el of root.querySelectorAll('[id^="tab_"]')) {
    // Only source content; never expose applications, participation or messaging.
    if (/desc|contact|photo|video|media|gallery|catalog|product|document|present/i.test(el.id)) {
      el.style.setProperty('display', 'block', 'important');
      el.style.setProperty('opacity', '1', 'important');
      el.style.setProperty('height', 'auto', 'important');
      el.style.setProperty('visibility', 'visible', 'important');
      el.removeAttribute('hidden');
    }
  }
  for (const el of root.querySelectorAll('.company_desc .description')) {
    el.classList.add('active');
    el.style.setProperty('display', 'block', 'important');
    el.style.setProperty('max-height', 'none', 'important');
    el.style.setProperty('height', 'auto', 'important');
    el.style.setProperty('overflow', 'visible', 'important');
  }
  for (const el of root.querySelectorAll('.roll_up_description,.chitatdalee_description_btn,.roll_up_description_btn'))
    el.style.setProperty('display', 'none', 'important');
  for (const img of document.images) {
    img.loading = 'eager';
    if (img.dataset.src) img.src = img.dataset.src;
  }
  return {title: root.querySelector('h1').innerText, textLength: root.innerText.length,
    galleryItems: root.querySelectorAll('.gallery_block').length};
}"""

READY = r"""async () => {
  const urls = new Set();
  const images = [...document.images].map(img => ({url: img.currentSrc || img.src, el: img}));
  for (const el of document.querySelectorAll('*')) {
    if (el.getClientRects().length) {
      const bg = getComputedStyle(el).backgroundImage;
      for (const m of bg.matchAll(/url\(["']?(.*?)["']?\)/g)) urls.add(m[1]);
    }
  }
  const wait = img => new Promise(resolve => {
    if (img.complete) return resolve();
    img.addEventListener('load', resolve, {once: true});
    img.addEventListener('error', resolve, {once: true});
  });
  const backgrounds = [...urls].map(url => {
    const el = new Image(); el.src = url; return {url, el};
  });
  await Promise.race([
    Promise.all([document.fonts.ready, ...images.map(x => wait(x.el)), ...backgrounds.map(x => wait(x.el))]),
    new Promise(resolve => setTimeout(resolve, 5000))
  ]);
  const visibleImages = images.filter(x => x.el.getClientRects().length);
  return {images: visibleImages.length, backgrounds: backgrounds.length,
    failedImages: [...visibleImages, ...backgrounds].filter(x => !x.el.complete || !x.el.naturalWidth).map(x => x.url),
    fontStatus: document.fonts.status,
    failedFonts: [...document.fonts].filter(x => x.status === 'error').map(x => x.family)};
}"""


def load_cards():
    files = sorted((ROOT / 'skill-test-20261110/data/cards').glob('*.json'))
    if files:
        records = [json.loads(p.read_text(encoding='utf-8')) for p in files]
    else:
        data = json.loads((ROOT / 'data/portal/catalog_discovery.json').read_text(encoding='utf-8'))
        records = next((v for v in data.values() if isinstance(v, list) and v
                        and isinstance(v[0], dict) and 'source_url' in v[0]), [])
    participants = ROOT / 'data/portal/participants.json'
    if participants.exists():
        portal = json.loads(participants.read_text(encoding='utf-8'))
        records.extend(portal if isinstance(portal, list) else portal['participants'])
    cards = {}
    for row in records:
        ident, url = str(row['id']), row['source_url']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
            raise ValueError(f'Unsafe ID: {ident}')
        if urlparse(url).hostname != 'catalog.mitexpo.ru':
            raise ValueError(f'Unexpected source host: {url}')
        if ident in cards and cards[ident]['source_url'] != url:
            raise ValueError(f'Conflicting source URLs: {ident}')
        cards[ident] = {'id': ident, 'source_url': url}
    if not cards:
        raise ValueError('No input source cards')
    return list(cards.values())


def valid_jpeg(path):
    try:
        if not 1000 < path.stat().st_size < 20_000_000:
            return False
        with Image.open(path) as im:
            if im.format != 'JPEG' or im.width != 1280 or im.height < 500:
                return False
            im.verify()
        return True
    except (OSError, ValueError):
        return False


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


async def screenshot(page, dest, capture_lock):
    async with capture_lock:
        height = await page.evaluate('Math.ceil(Math.max(document.documentElement.scrollHeight, document.body.scrollHeight))')
        if height > 60000:
            raise ValueError(f'Unsafe page height {height}; no truncated success saved')
        tmp = dest.with_suffix('.tmp.jpg')
        if height <= 14000:
            await page.screenshot(path=str(tmp), type='jpeg', quality=60, full_page=True,
                                  animations='disabled', timeout=45000)
        else:
            # Bounded raster: one tall image at a time, 60000px / 231 million bytes max.
            # Individual Chromium captures stay small, avoiding huge browser surfaces.
            canvas = Image.new('RGB', (1280, height), 'white')
            try:
                for y in range(0, height, 4000):
                    raw = await page.screenshot(type='jpeg', quality=60,
                        clip={'x': 0, 'y': y, 'width': 1280, 'height': min(4000, height-y)},
                        animations='disabled', timeout=45000)
                    with Image.open(BytesIO(raw)) as tile:
                        canvas.paste(tile, (0, y))
                canvas.save(tmp, 'JPEG', quality=60)
            finally:
                canvas.close()
        if not valid_jpeg(tmp):
            raise ValueError('Invalid/oversized screenshot (limit 20 million bytes, width 1280)')
        tmp.replace(dest)
        return height


async def main(args):
    cards = load_cards()
    if args.limit:
        cards = cards[:args.limit]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    old = json.loads(CHECKPOINT.read_text(encoding='utf-8')) if CHECKPOINT.exists() else []
    old = old.get('records', []) if isinstance(old, dict) else old
    previous = {str(r['id']): r for r in old}
    rows = {}
    for c in cards:
        rel = f'docs/media/source-cards/{c["id"]}.jpg'
        prev = previous.get(c['id'], {})
        resume = (prev.get('source_url') == c['source_url'] and prev.get('status') == 'success'
                  and valid_jpeg(ROOT / rel))
        # Earlier screenshots clipped horizontal source galleries. Refresh only
        # cards with background media; simple existing captures stay untouched.
        if prev.get('assets', {}).get('backgrounds', 0) > 1 and prev.get('layout_version') != 2:
            resume = False
        rows[c['id']] = dict(prev, **c, path=rel) if resume else dict(c, path=rel, status='pending', error=None)
    # Keep unrelated previous records if a deliberate --limit probe is used.
    def checkpoint():
        atomic_json(CHECKPOINT, list({**previous, **rows}.values()))
    checkpoint()
    queue = asyncio.Queue()
    for c in cards:
        if rows[c['id']]['status'] != 'success':
            queue.put_nowait(c)
    print(f'Input {len(cards)}; resumed {len(cards)-queue.qsize()}; pending {queue.qsize()}', flush=True)
    start = time.monotonic()
    capture_lock = asyncio.Semaphore(1)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width':1280, 'height':1000},
            device_scale_factor=1, locale='ru-RU', reduced_motion='reduce')
        async def route_handler(route):
            host = (urlparse(route.request.url).hostname or '').lower()
            if any(host == h or host.endswith('.'+h) or (h.endswith('.') and host.startswith(h)) for h in BLOCKED):
                await route.abort()
            else:
                await route.continue_()
        await context.route('**/*', route_handler)
        async def worker(number):
            while not queue.empty():
                c = queue.get_nowait()
                row = rows[c['id']]
                for attempt in range(1, args.attempts+1):
                    page = await context.new_page()
                    page.set_default_timeout(15000)
                    active = set()
                    def track(req):
                        if req.resource_type in ('xhr', 'fetch') and urlparse(req.url).hostname == 'catalog.mitexpo.ru':
                            active.add(req)
                    page.on('request', track)
                    page.on('requestfinished', lambda req: active.discard(req))
                    page.on('requestfailed', lambda req: active.discard(req))
                    try:
                        async with asyncio.timeout(110):
                            response = await page.goto(c['source_url'], wait_until='domcontentloaded', timeout=40000)
                            if not response or response.status != 200:
                                raise ValueError(f'HTTP {response.status if response else "no response"}')
                            await page.wait_for_selector('.expo_inner h1')
                            # Give the source's automatic gallery AJAX time, without waiting for analytics.
                            await page.wait_for_timeout(1000)
                            for _ in range(75):
                                if not active:
                                    break
                                await page.wait_for_timeout(200)
                            if active:
                                raise ValueError('Source gallery/XHR still pending after 15 seconds')
                            content = await page.evaluate(EXPAND)
                            if content['textLength'] < 60:
                                raise ValueError('Suspiciously empty source card')
                            await page.add_style_tag(content='''
                                #jivo-iframe-container,jdiv,.jivo-iframe-container {display:none!important}
                                .expo_inner img {max-width:100%!important}
                                html,body {overflow-x:hidden!important}
                                .expo_inner .slider_gallery {display:grid!important;
                                  grid-template-columns:repeat(3,minmax(0,1fr))!important;
                                  overflow:visible!important; height:auto!important;
                                  max-height:none!important; transform:none!important}
                                .expo_inner .gallery_block {min-width:0!important;
                                  width:auto!important; display:block!important}
                                .expo_inner .gallery-btn {display:none!important}
                            ''')
                            # Scroll through the full content to trigger lazy image/media loading.
                            await page.evaluate('''async () => {
                              for(let y=0;y<Math.min(document.body.scrollHeight,60000);y+=850){
                                scrollTo(0,y); await new Promise(r=>setTimeout(r,35));
                              } scrollTo(0,0);
                            }''')
                            assets = await page.evaluate(READY)
                            if assets['fontStatus'] != 'loaded' or assets['failedFonts']:
                                raise ValueError('Font load incomplete: '+str(assets))
                            if assets['failedImages'] and attempt < args.attempts:
                                raise ValueError('Image load failed: '+str(assets['failedImages'])[:1200])
                            height = await screenshot(page, ROOT / row['path'], capture_lock)
                            row.update(status='success', error=None, attempts=attempt,
                                captured_at=datetime.now(timezone.utc).isoformat(),
                                final_url=page.url, title=content['title'], height=height,
                                bytes=(ROOT/row['path']).stat().st_size, assets=assets,
                                gallery_items=content['galleryItems'], layout_version=2,
                                warnings=['Source images unavailable after retries'] if assets['failedImages'] else [])
                        break
                    except Exception as exc:
                        row.update(status='error', error=f'{type(exc).__name__}: {exc}'[:3000], attempts=attempt)
                        print(f'RETRY {c["id"]} {attempt}: {row["error"][:180]}', flush=True)
                    finally:
                        await page.close()
                    if attempt < args.attempts:
                        await asyncio.sleep(attempt * 2)
                checkpoint()
                counts = Counter(r['status'] for r in rows.values())
                print(f'{c["id"]} {row["status"]} | {dict(counts)} | {int(time.monotonic()-start)}s', flush=True)
                queue.task_done()
        await asyncio.gather(*(worker(i) for i in range(args.workers)))
        await browser.close()
    counts = Counter(r['status'] for r in rows.values())
    successful = [r for r in rows.values() if r['status'] == 'success']
    summary = {'finished_at': datetime.now(timezone.utc).isoformat(), 'input_count':len(cards),
        'counts':dict(counts), 'workers':args.workers, 'width':1280, 'jpeg_quality':60,
        'total_bytes':sum(r.get('bytes',0) for r in successful),
        'max_file_bytes':max((r.get('bytes',0) for r in successful),default=0),
        'cards_with_source_asset_warnings':[r['id'] for r in successful if r.get('warnings')],
        'errors':[{'id':r['id'],'error':r['error']} for r in rows.values() if r['status']=='error']}
    atomic_json(ROOT/'artifacts/source-screenshots-summary.json',summary)
    report = (
        f"MITEX source screenshots — {summary['finished_at']}\n"
        f"Current source IDs (fresh cards + portal participants): {len(cards)}\n"
        f"Saved JPEG screenshots: {counts.get('success', 0)}\n"
        f"Errors: {counts.get('error', 0)}; pending: {counts.get('pending', 0)}\n"
        f"Format: JPEG quality 60, width 1280, full page; substantive sections expanded.\n"
        f"Total: {summary['total_bytes'] / 1_000_000:.2f} megabytes; "
        f"largest file: {summary['max_file_bytes'] / 1_000_000:.2f} megabytes.\n"
        f"Cards with unavailable source images after bounded retries: "
        f"{', '.join(summary['cards_with_source_asset_warnings']) or 'none'}.\n"
        "Per-card URLs, image issues and paths: artifacts/source-screenshots.json\n"
        "No participants, build, templates or data files modified by this script.\n"
    )
    (ROOT/'artifacts/source-screenshots-report.txt').write_text(report,encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    return 0 if counts.get('success',0)==len(cards) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,choices=range(4,7),default=6)
    parser.add_argument('--attempts',type=int,default=3)
    parser.add_argument('--limit',type=int,default=0,help='Optional initial visual probe only')
    raise SystemExit(asyncio.run(main(parser.parse_args())))
