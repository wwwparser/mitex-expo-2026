"""Verify the new service directory without rerunning unrelated portal checks."""
import functools,json,threading
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/services-qa'


class Quiet(SimpleHTTPRequestHandler):
 def log_message(self,*a):pass


def main():
 OUT.mkdir(parents=True,exist_ok=True)
 server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(ROOT/'docs')))
 threading.Thread(target=server.serve_forever,daemon=True).start()
 base=f'http://127.0.0.1:{server.server_port}/';report={'screens':[],'checks':[],'errors':[]}
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch()
   for width,height in [(1440,1000),(390,844)]:
    page=browser.new_page(viewport={'width':width,'height':height},color_scheme='light')
    page.on('pageerror',lambda error:report['errors'].append(str(error)))
    page.goto(base+'services.html',wait_until='domcontentloaded');page.evaluate('document.fonts.ready')
    page.evaluate('''async()=>{const ims=[...document.images];ims.forEach(i=>i.loading='eager');await Promise.all(ims.map(i=>i.complete?Promise.resolve():new Promise(r=>{i.onload=r;i.onerror=r;setTimeout(r,4000)})));}''')
    metrics=page.evaluate('({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,broken:[...document.images].filter(i=>!i.naturalWidth).length})')
    assert metrics['width']==metrics['scrollWidth'],metrics
    assert not metrics['broken'],metrics
    page.screenshot(path=str(OUT/f'services-{width}-first.jpg'),quality=85)
    page.screenshot(path=str(OUT/f'services-{width}-full.jpg'),full_page=True,quality=72)
    report['screens'].append(metrics)
    assert page.locator('.services-grid .filter-item:visible').count()==14
    page.locator('select[data-field=categories]').select_option('it')
    assert page.locator('.services-grid .filter-item:visible').count()==1
    assert page.locator('.services-grid .filter-item:visible').get_attribute('id')=='smart-logistics'
    page.locator('.reset').click()
    page.locator('input[type=search]').fill('oneplat_no_match_9876')
    assert page.locator('.services-grid .filter-item:visible').count()==0
    assert page.locator('.empty').is_visible()
    page.locator('.reset').click()
    page.locator('#oneplat summary').click()
    assert page.locator('#oneplat details[open]').count()==1
    assert page.locator('#oneplat a[href*="catalog.mitexpo.ru/companies/"]').count()==2
    page.locator('select[data-field=categories]').select_option('platforms')
    assert page.locator('.services-grid .filter-item:visible').count()==3
    report['checks'].append({'width':width,'all':14,'it':1,'platforms':3,'empty_search':True,'oneplat_sources':2})
    page.goto(base+'index.html');assert page.locator('a[href="services.html"]').count()>=2
    page.goto(base+'u/172929.html');page.locator('a[href="../services.html#smart-logistics"]').click()
    assert page.locator('#smart-logistics').is_visible()
    report['checks'].append({'width':width,'home_entry':True,'participant_backlink':True})
    page.close()
   browser.close()
 finally:
  server.shutdown();server.server_close()
 (OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 assert not report['errors'],report['errors']
 print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
