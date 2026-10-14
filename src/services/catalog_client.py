import re
from urllib.parse import urljoin
from .http_client import fetch

BASE = 'https://catalog.mitexpo.ru'
CATALOG = BASE + '/expositions/exposition/7054'

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
    block = soup.select_one('#tab_contacts_flat')
    if block is None:
        raise ValueError('Не найден блок контактов')
    for a in block.find_all('a', href=True):
        href = a['href'].strip()
        if href.startswith('mailto:'):
            record['contacts']['emails'].append(href[7:].split('?')[0])
        elif href.startswith(('https://','http://')):
            record['website'] = href
    text = block.get_text(' ', strip=True)
    record['contacts']['phones'] = sorted(set(re.findall(r'\+\d[\d\s()\-]{8,}\d', text)))
    record['contacts']['emails'] = sorted(set(record['contacts']['emails']))
    record['detail_status'] = 'collected'
    return record
