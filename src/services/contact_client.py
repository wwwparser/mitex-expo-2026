"""Обогащение только опубликованными ссылками главной и страницы контактов."""
from urllib.parse import urlparse, urljoin
from .http_client import fetch

NETWORKS = {'t.me': 'Telegram', 'telegram.me': 'Telegram', 'vk.com': 'VK',
            'youtube.com': 'YouTube', 'youtu.be': 'YouTube', 'max.ru': 'MAX',
            'max.me': 'MAX', 'ok.ru': 'OK', 'instagram.com': 'Instagram'}

def enrich(record, refresh=False):
    website = record['website']
    soup = fetch(website, refresh)
    urls = [website]
    for a in soup.find_all('a', href=True):
        href = urljoin(website, a['href'])
        if urlparse(href).netloc == urlparse(website).netloc and any(
            word in (a.get_text(' ', strip=True) + href).lower()
            for word in ['контакты', '/contacts', '/contact']
        ) and href not in urls:
            urls.append(href)
        if len(urls) >= 2:
            break
    social = {(x['network'],x['url']):x for x in record['contacts']['social']}
    for url in urls:
        page = soup if url == website else fetch(url, refresh)
        for a in page.find_all('a', href=True):
            href = urljoin(url,a['href'].strip())
            host = (urlparse(href).hostname or '').removeprefix('www.')
            if href.startswith('mailto:'):
                email = href[7:].split('?')[0]
                if '@' in email:
                    record['contacts']['emails'].append(email)
            elif href.startswith('tel:'):
                record['contacts']['phones'].append(href[4:])
            elif host in NETWORKS and urlparse(href).scheme in ('https','http'):
                if host in ('t.me','telegram.me') and urlparse(href).path.startswith(('/share','/iv')):
                    continue
                social[(NETWORKS[host],href)] = {'network':NETWORKS[host], 'url':href, 'source_url':url}
    record['contacts']['social'] = list(social.values())
    for field in ('emails','phones'):
        record['contacts'][field] = sorted(set(record['contacts'][field]))
    record['enrichment_status'] = 'collected'
    return record
