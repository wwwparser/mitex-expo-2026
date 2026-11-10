"""Local, reproducible browser checks and desktop/mobile visual references."""
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/portal-qa'


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    handler=functools.partial(Quiet,directory=str(ROOT/'docs'))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}/'
    participants=json.loads((ROOT/'data/portal/participants.json').read_text(encoding='utf-8'))
    rich=next((p for p in participants if p.get('video_status')=='present' and p.get('document_status')=='present'),participants[0])
    routes=['index.html','catalog.html','program.html','people.html','feed.html','giveaways.html','videos.html',f"u/{rich['id']}.html"]
    results=[];errors=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            for width,height in ((1440,1000),(390,844)):
                context=browser.new_context(viewport={'width':width,'height':height},color_scheme='light')
                page=context.new_page()
                page.on('pageerror',lambda exc:errors.append(str(exc)))
                for route in routes:
                    response=page.goto(base+route,wait_until='domcontentloaded')
                    page.evaluate('document.fonts.ready')
                    page.evaluate('''async () => {
                      const images=[...document.images].filter(i=>!i.closest('[hidden]'));
                      for(const im of images)im.loading='eager';
                      await Promise.all(images.map(im=>im.complete?Promise.resolve():new Promise(r=>{im.addEventListener('load',r,{once:true});im.addEventListener('error',r,{once:true});setTimeout(r,4000)})));
                    }''')
                    page.wait_for_timeout(250)
                    metrics=page.evaluate('''() => ({width:innerWidth,height:innerHeight,
                      scrollWidth:document.documentElement.scrollWidth,
                      scrollHeight:document.documentElement.scrollHeight,
                      title:document.title, h1:document.querySelector('h1')?.innerText,
                      loadedImages:[...document.images].filter(i=>i.complete&&i.naturalWidth).length,
                      failedImages:[...document.images].filter(i=>i.complete&&!i.naturalWidth&&i.src).map(i=>i.getAttribute('src'))})''')
                    name=route.replace('/','--').replace('.html','')+f'-{width}'
                    page.screenshot(path=str(OUT/(name+'-first.jpg')),type='jpeg',quality=85,animations='disabled')
                    # Full screenshot for six-role audit; paginate giant lists in the UI.
                    if metrics['scrollHeight']<45000:
                        page.screenshot(path=str(OUT/(name+'-full.jpg')),full_page=True,type='jpeg',quality=70,animations='disabled')
                    else:
                        metrics['full_screenshot']='omitted_above_45000px; review_pagination'
                    results.append({'route':route,'status':response.status,'viewport':width,**metrics})
                if width==1440:
                    page.goto(base+'catalog.html')
                    search=page.locator('input[type=search]').first
                    if search.count():
                        search.fill('ADA')
                        page.wait_for_timeout(200)
                        page.screenshot(path=str(OUT/'catalog-search-ADA.jpg'),quality=80)
                        results.append({'interaction':'catalog_search_ADA','body_excerpt':page.locator('main').inner_text()[:2200]})
                        search.fill('zzzz_nonexistent_987654321')
                        page.wait_for_timeout(150)
                        results.append({'interaction':'catalog_empty_search','body_excerpt':page.locator('main').inner_text()[:700]})
                        search.fill('')
                    page.goto(base+'index.html')
                    page.keyboard.press('Tab')
                    results.append({'interaction':'keyboard_tab','focused':page.evaluate('document.activeElement.outerHTML')[:300]})
                    button=page.get_by_role('button',name='Сменить тему')
                    if button.count():
                        button.click()
                        results.append({'interaction':'theme_toggle','theme':page.evaluate('document.documentElement.dataset.theme')})
                        page.screenshot(path=str(OUT/'index-dark.jpg'),quality=80)
                context.close()
            browser.close()
    finally:
        server.shutdown();server.server_close()
    (OUT/'browser-check.json').write_text(json.dumps({'results':results,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pages':len([r for r in results if 'route' in r]),'overflows':[(r['route'],r['viewport']) for r in results if r.get('scrollWidth',0)>r.get('width',0)],'page_errors':errors},ensure_ascii=False))


if __name__=='__main__':main()
