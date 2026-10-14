"""HTTP с ограниченным временем, повторными попытками и дисковым кэшем."""
import hashlib
import time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]

def fetch(url, refresh=False):
    cache = ROOT / 'data/raw' / (hashlib.sha256(url.encode()).hexdigest() + '.html')
    if cache.exists() and not refresh:
        return BeautifulSoup(cache.read_bytes(), 'html.parser')
    cache.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=(8, 20), headers={
                'User-Agent': 'MITEX-Research/0.1 (public exhibition directory)',
                'Accept-Encoding': 'gzip',
            })
            response.raise_for_status()
            if 'html' not in response.headers.get('Content-Type', ''):
                raise ValueError('Источник не является HTML: ' + url)
            cache.write_bytes(response.content)
            time.sleep(.2)
            return BeautifulSoup(response.content, 'html.parser')
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
