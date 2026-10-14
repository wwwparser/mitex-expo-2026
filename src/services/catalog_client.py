import re
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone
from .http_client import fetch

BASE = 'https://catalog.mitexpo.ru'
CATALOG = BASE + '/expositions/exposition/7054'

def normalize_website(value):
    value=value.strip()
    scheme='http' if value.lower().startswith('http:') else 'https'
    # В каталоге есть http://Http://example и http://HTTP//:EXAMPLE.
    while re.match(r'^https?[:/]+',value,re.I):
        value=re.sub(r'^https?[:/]+','',value,flags=re.I)
    value=re.sub(r'\s+','',value).strip('/')
    return scheme+'://'+value

def website_urls(value):
    value=normalize_website(value)
    return [normalize_website(part.strip(' ,;；，/')) for part in re.split(r'(?=https?://)',value,flags=re.I) if part.strip(' ,;；，/')]

def collect_catalog(refresh=False):
    """Параметр limit проверяется по последней странице, а не принимается на веру."""
    soup = fetch(CATALOG + '?limit=1000', refresh)
    pagination = soup.select_one('.pagination')
    if pagination and pagination.select('a[data-href]'):
        raise RuntimeError('Источник больше не отдаёт весь список через limit=1000; адаптеру нужна пагинация.')
    records = {}
    for a in soup.find_all('a', href=True):
        if '/companies/company/' not in a['href'] or not a.select_one('.exh_name'):
            continue
        url = urljoin(BASE, a['href'])
        stand = a.select_one('.numb_stand b')
        country = a.select_one('.img_country')
        about = a.select_one('.short_opis')
        records[url] = {
            'id': re.search(r'/company/(\d+)', url).group(1),
            'name': a.get('title') or a.select_one('.exh_name').get_text(' ', strip=True),
            'stands': [stand.get_text(strip=True)] if stand else [],
            'country': country.get('title', '') if country else '',
            'about': about.get_text(' ', strip=True) if about else '',
            'source_url': url, 'website': '',
            'contacts': {'emails': [], 'phones': [], 'social': []},
            'detail_status': 'not_collected',
        }
    if not records:
        raise RuntimeError('Не найдено ни одного участника — вероятно, изменилась разметка.')
    return list(records.values())

def collect_detail(record, refresh=False):
    soup = fetch(record['source_url'], refresh)
    record['detail_checked_at'] = datetime.now(timezone.utc).isoformat()
    record['detail_version'] = 2
    desc=soup.select_one('.company_desc')
    if desc:
        for item in desc.select('.top_title, .company_icon, .company_image_expo'):
            item.decompose()
        about=desc.get_text(' ',strip=True)
        if about and about!='-': record['about']=about
    record['categories']=sorted(set(x.get_text(' ',strip=True) for x in soup.select('.rubr_cat .rubr_box .rubr')))
    block = soup.select_one('#tab_contacts_flat')
    if block is None:
        record['detail_status']='no_contacts'
        record['detail_note']='В карточке нет блока контактов'
        record['websites']=[]
        return record
    record['websites']=[]
    for a in block.select('.company_site a[href]'):
        for href in website_urls(a['href']):
            if urlparse(href).hostname and href not in record['websites']: record['websites'].append(href)
    record['website']=record['websites'][0] if record['websites'] else ''
    for a in block.find_all('a', href=True):
        href = a['href'].strip()
        if href.startswith('mailto:'):
            record['contacts']['emails'].append(href[7:].split('?')[0])
    text = block.get_text(' ', strip=True)
    record['contacts']['phones'] = sorted(set(record['contacts']['phones']+re.findall(r'\+\d[\d\s()\-]{8,}\d', text)))
    record['contacts']['emails'] = sorted(set(record['contacts']['emails']))
    sources=record.setdefault('contact_sources',[])
    for kind in ('emails','phones'):
        for value in record['contacts'][kind]:
            evidence={'kind':kind,'value':value,'source_url':record['source_url']}
            if evidence not in sources: sources.append(evidence)
    record['detail_status'] = 'collected'
    return record
