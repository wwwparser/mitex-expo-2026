# -*- coding: utf-8 -*-
"""
Сбор контактов с сайтов компаний → Excel + кликабельная HTML-страница.

Обходит главную страницу каждого сайта и страницы «контакты / о нас»,
извлекает: email, телефоны, Telegram, мессенджер MAX, WhatsApp, VK,
Instagram, Facebook, Одноклассники.

Логика обхода переиспользована из проекта e-mail-scraper-2025-v1
(httpx + фолбэк на Playwright), но в самодостаточном виде: без ключей/прокси.

ВХОД (один из):
  --excel file.xlsx [--site-col Сайт] [--name-col Компания]   # колонка с сайтами
  --urls https://a.ru https://b.ru ...
  --txt sites.txt                                              # по одному URL в строке

ВЫХОД (в --outdir, по умолчанию ./contacts_out):
  contacts.xlsx   — таблица (одна строка на компанию)
  contacts.html   — интерактивная страница с кликабельными ссылками
  contacts.jsonl  — промежуточные результаты (resume-режим)

ЗАПУСК:
  python scrape_contacts.py --excel exhibitors_ecom_expo_2026.xlsx --site-col Сайт --name-col Компания
  python scrape_contacts.py --urls https://akfa.ru --workers 8
"""
from __future__ import annotations

import argparse
import concurrent.futures
import html as html_lib
import json
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

try:
    import idna  # кириллические домены -> punycode
except ImportError:
    idna = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("contact-scraper")

# ---- DeepSeek (описание деятельности компании) ----
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
_SECRETS = Path.home() / ".claude" / "secrets" / "api-keys.env"


def get_deepseek_key() -> str | None:
    key = os.getenv("DEEPSEEK_API_KEY")
    if key:
        return key.strip()
    if _SECRETS.exists():
        for line in _SECRETS.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def deepseek_describe(key: str, name: str, short_desc: str, site_text: str) -> str:
    """2–3 предложения о деятельности компании по тексту главной страницы."""
    site_text = (site_text or "")[:3500]
    context = f"Название: {name or '—'}\n"
    if short_desc:
        context += f"Короткое описание из каталога: {short_desc}\n"
    context += f"Текст главной страницы сайта:\n{site_text}"
    prompt = (
        "Опиши на русском в 2-3 предложениях, чем занимается компания "
        "(тип деятельности: разработчик ПО, логистика/фулфилмент, маркетплейс, банк, "
        "маркетинг, производство и т.п., ключевые продукты/услуги). "
        "Только суть деятельности, без рекламных штампов и воды. "
        "Если данных недостаточно — ответь пустой строкой.\n\n" + context
    )
    try:
        r = httpx.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": DEEPSEEK_MODEL,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 220},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.info(f"DeepSeek не дал описание для {name or site_text[:20]}: {e}")
        return ""

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# Страницы, которые стоит посетить дополнительно (контакты / о компании)
TARGET_KEYWORDS = [
    "контакт", "о нас", "о компании", "о фирме", "связ", "обратная связь",
    "реквизит", "адрес", "напишите", "contact", "about", "reach", "get-in-touch",
    "info", "office",
]
# Разделы, которые обычно НЕ содержат контакты (исключаем по URL)
EXCLUDE_KEYWORDS = [
    "privacy", "policy", "terms", "agreement", "legal", "disclaimer", "cookie",
    "offer", "license", "licence", "gdpr", "vacanc", "career", "job", "blog",
    "news", "press", "faq", "оферт", "политик", "соглашен", "конфиденц",
    "персональн", "лицензи", "ваканс", "новост",
]
EXCLUDED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".pdf",
    ".tiff", ".avif", ".heic", ".mp4", ".avi", ".mov", ".zip", ".rar", ".7z",
    ".tar", ".gz", ".doc", ".docx", ".xls", ".xlsx",
)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Международный номер в любом формате: +49 30 1234567, +86 574 8888 6666, 0086...
INTL_PHONE_RE = re.compile(r"(?:\+|00)\d[\d\s().\-/]{7,20}\d")

PHONE_RES = [
    re.compile(r"\+7[-\s]?\(?\d{3}\)?[-\s]?\d{2}[-\s]?\d{2}[-\s]?\d{3}"),
    re.compile(r"\+7[-\s]?\(?\d{3}\)?[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{2}"),
    re.compile(r"8[-\s]?\(?\d{3}\)?[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{2}"),
    re.compile(r"8[-\s]?\(?\d{3}\)?[-\s]?\d{2}[-\s]?\d{2}[-\s]?\d{3}"),
]

# Категории соцсетей/мессенджеров: ключ -> функция-детектор по href
# MAX — мессенджер от VK (домены max.ru / max.me), добавлен по запросу.
SOCIAL_MATCHERS = {
    "telegram": lambda h, host: ("t.me" in h or "telegram.me" in h or "telegram.dog" in h)
                                 and "/share" not in h,
    "max": lambda h, host: host == "max.ru" or host.endswith(".max.ru") or host == "max.me",
    "whatsapp": lambda h, host: "wa.me" in h or "whatsapp.com" in h or "api.whatsapp" in h,
    "vk": lambda h, host: ("vk.com" in h or "vkontakte.ru" in h) and "/share" not in h,
    "instagram": lambda h, host: "instagram.com" in h,
    "facebook": lambda h, host: "facebook.com" in h and "sharer" not in h,
    "ok": lambda h, host: host == "ok.ru" or host.endswith(".ok.ru"),
    # международный набор: нужен для выставочных каталогов и азиатских поставщиков
    "linkedin": lambda h, host: "linkedin.com/company" in h or "linkedin.com/in/" in h,
    "youtube": lambda h, host: "youtube.com/" in h and "/share" not in h,
    "x": lambda h, host: (host in ("twitter.com", "x.com")
                          or host.endswith(".twitter.com")) and "intent" not in h,
    "wechat": lambda h, host: "weixin.qq.com" in h or host.endswith("wechat.com"),
    "line": lambda h, host: host in ("line.me", "lin.ee"),
    "xing": lambda h, host: "xing.com/companies" in h or "xing.com/profile" in h,
}
CONTACT_KEYS = ["emails", "phones", "telegram", "max", "whatsapp", "vk",
                "instagram", "facebook", "ok", "linkedin", "youtube", "x",
                "wechat", "line", "xing"]

# Playwright подключается лениво (необязательная зависимость)
_PLAYWRIGHT_OK = None


def normalize_url(url: str) -> str:
    """Кириллический домен -> punycode; гарантирует схему."""
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if idna is None:
        return url
    try:
        r = urlparse(url)
        host = r.netloc.lower()
        if any(ord(c) > 127 for c in host):
            enc = idna.encode(host).decode("ascii")
            url = url.replace(r.netloc, enc, 1)
    except Exception:
        pass
    return url


def base_url(url: str) -> str:
    r = urlparse(normalize_url(url))
    return f"{r.scheme}://{r.netloc}"


# Включается флагом --intl: телефоны приводятся к E.164 (+<цифры>), а не к
# российскому формату, и в текст добавляется поиск международных номеров.
INTL_MODE = False


def fmt_phone(raw: str) -> str:
    clean = re.sub(r"[^0-9+]", "", raw)
    if INTL_MODE:
        if clean.startswith("00"):
            clean = "+" + clean[2:]
        digits = re.sub(r"\D", "", clean)
        if not clean.startswith("+") or not 8 <= len(digits) <= 15:
            return ""
        return "+" + digits
    if clean.startswith("8") and len(clean) == 11:
        clean = "+7" + clean[1:]
    elif clean.startswith("7") and len(clean) == 11:
        clean = "+" + clean
    if clean.startswith("+7") and len(clean) == 12:
        c = clean
        return f"+7 ({c[2:5]}) {c[5:8]}-{c[8:10]}-{c[10:12]}"
    return clean


def extract_contacts(page_url: str, soup: BeautifulSoup, acc: dict) -> None:
    """Извлекает контакты из страницы в аккумулятор acc (set по каждой категории)."""
    # 1) href-ссылки
    tel_nodes = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        hl = href.lower()
        if hl.startswith("tel:"):
            acc["phones"].add(fmt_phone(href[4:]))
            tel_nodes.append(a)
            continue
        if hl.startswith("mailto:"):
            em = href[7:].split("?")[0].strip()
            if em:
                acc["emails"].add(em)
            continue
        host = urlparse(href).netloc.lower()
        for key, match in SOCIAL_MATCHERS.items():
            try:
                if match(hl, host):
                    acc[key].add(href.split("?")[0] if key in ("telegram", "max") else href)
                    break
            except Exception:
                continue
    # удаляем tel:-ссылки, чтобы их текст не попал в regex телефонов
    for n in tel_nodes:
        n.decompose()

    text = soup.get_text(" ")
    for em in EMAIL_RE.findall(text):
        # отсекаем мусорные «email» из имён файлов
        if not em.lower().endswith(EXCLUDED_EXTENSIONS):
            acc["emails"].add(em)
    for rx in (PHONE_RES + [INTL_PHONE_RE]) if INTL_MODE else PHONE_RES:
        for ph in rx.findall(text):
            formatted = fmt_phone(ph)
            if formatted:
                acc["phones"].add(formatted)


def is_excluded_link(url: str) -> bool:
    u = url.lower()
    if u.endswith(EXCLUDED_EXTENSIONS):
        return True
    return any(k in u for k in EXCLUDE_KEYWORDS)


def find_target_links(home_url: str, soup: BeautifulSoup) -> list:
    home_host = urlparse(home_url).netloc
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True).lower()
        hl = href.lower()
        if any(k in hl for k in TARGET_KEYWORDS) or any(k in text for k in TARGET_KEYWORDS):
            full = urljoin(home_url, href)
            if urlparse(full).netloc != home_host:
                continue
            if is_excluded_link(full) or full in seen:
                continue
            seen.add(full)
            found.append(full)
    return found


def _ensure_playwright() -> bool:
    global _PLAYWRIGHT_OK
    if _PLAYWRIGHT_OK is None:
        try:
            import playwright.sync_api  # noqa: F401
            _PLAYWRIGHT_OK = True
        except Exception:
            _PLAYWRIGHT_OK = False
            log.warning("Playwright не установлен — фолбэк рендеринга недоступен.")
    return _PLAYWRIGHT_OK


def fetch_playwright(url: str, timeout: int) -> str | None:
    if not _ensure_playwright():
        return None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=HTTP_HEADERS["User-Agent"])
                resp = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                if resp and resp.status == 404:
                    return None
                page.wait_for_timeout(2500)
                return page.content()
            finally:
                browser.close()
    except Exception as e:
        log.warning(f"Playwright не смог открыть {url}: {e}")
        return None


def fetch(url: str, client: httpx.Client, timeout: int, use_playwright: bool) -> str | None:
    try:
        r = client.get(url, headers=HTTP_HEADERS, follow_redirects=True, timeout=timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        ct = r.headers.get("content-type", "").lower()
        if "windows-1251" in ct or "cp1251" in ct:
            r.encoding = "windows-1251"
        return r.text
    except Exception as e:
        log.info(f"httpx не смог {url}: {e}")
        if use_playwright:
            return fetch_playwright(url, timeout)
        return None


def _home_text(soup: BeautifulSoup) -> str:
    """Видимый текст главной для LLM: убираем скрипты/стили, сжимаем пробелы."""
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def crawl_site(name: str, site: str, timeout: int, max_pages: int,
               use_playwright: bool) -> dict:
    home = base_url(site)
    acc = {k: set() for k in CONTACT_KEYS}
    title = ""
    meta_desc = ""
    home_text = ""
    ok = False
    with httpx.Client(timeout=timeout) as client:
        content = fetch(home, client, timeout, use_playwright)
        if content:
            ok = True
            soup = BeautifulSoup(content, "lxml")
            t = soup.find("title")
            title = t.get_text(strip=True) if t else ""
            md = soup.find("meta", attrs={"name": "description"}) or \
                soup.find("meta", attrs={"property": "og:description"})
            if md and md.get("content"):
                meta_desc = md["content"].strip()
            home_text = _home_text(BeautifulSoup(content, "lxml"))
            extract_contacts(home, soup, acc)
            targets = find_target_links(home, soup)
            if not targets and use_playwright:
                pw = fetch_playwright(home, timeout)
                if pw:
                    soup = BeautifulSoup(pw, "lxml")
                    extract_contacts(home, soup, acc)
                    targets = find_target_links(home, soup)
            for link in targets[:max_pages]:
                c = fetch(link, client, timeout, use_playwright)
                if c:
                    extract_contacts(link, BeautifulSoup(c, "lxml"), acc)
    return {
        "name": name,
        "site": site,
        "home": home,
        "title": title,
        "reachable": ok,
        "meta_desc": meta_desc,
        "home_text": home_text[:4000],
        **{k: sorted(acc[k]) for k in CONTACT_KEYS},
    }


# ---------- ввод ----------

def load_inputs(args) -> list:
    items = []
    if args.urls:
        items = [{"name": "", "site": u} for u in args.urls]
    elif args.txt:
        for line in Path(args.txt).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                items.append({"name": "", "site": line})
    elif args.excel:
        from openpyxl import load_workbook
        wb = load_workbook(args.excel, read_only=True, data_only=True)
        for ws in wb.worksheets:
            if ws.title == "Сводка":
                continue
            headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
            try:
                si = headers.index(args.site_col)
            except ValueError:
                continue
            ni = headers.index(args.name_col) if args.name_col in headers else None
            di = headers.index(args.desc_col) if args.desc_col in headers else None
            for row in ws.iter_rows(min_row=2, values_only=True):
                site = row[si] if si < len(row) else None
                if not site or not str(site).strip():
                    continue
                name = (row[ni] if ni is not None and ni < len(row) else "") or ""
                sdesc = (row[di] if di is not None and di < len(row) else "") or ""
                items.append({"name": str(name).strip(), "site": str(site).strip(),
                              "short_desc": str(sdesc).strip()})
    # дедуп по домену
    seen, uniq = set(), []
    for it in items:
        it.setdefault("short_desc", "")
        d = urlparse(base_url(it["site"])).netloc.lower()
        if d and d not in seen:
            seen.add(d)
            uniq.append(it)
    return uniq


# ---------- вывод ----------

def write_jsonl(path: Path, rows: list) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_excel(path: Path, rows: list) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "Контакты"
    cols = [("name", "Компания"), ("description", "Чем занимается"), ("site", "Сайт"),
            ("emails", "Email"), ("phones", "Телефоны"), ("telegram", "Telegram"),
            ("max", "MAX"), ("whatsapp", "WhatsApp"), ("vk", "VK"),
            ("instagram", "Instagram"), ("facebook", "Facebook"), ("ok", "OK"),
            ("reachable", "Сайт открылся")]
    for i, (_, h) in enumerate(cols, 1):
        c = ws.cell(1, i, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F7A3D")
    for r, row in enumerate(rows, 2):
        for i, (k, _) in enumerate(cols, 1):
            v = row.get(k)
            if isinstance(v, list):
                v = "\n".join(v)
            elif isinstance(v, bool):
                v = "да" if v else "нет"
            ws.cell(r, i, v)
    widths = [26, 55, 28, 28, 20, 32, 26, 22, 26, 26, 26, 16, 13]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(rows)+1}"
    for r in range(2, len(rows) + 2):
        for i in range(1, len(cols) + 1):
            ws.cell(r, i).alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def _chips(label, urls, cls):
    if not urls:
        return ""
    chips = "".join(
        f'<a class="chip {cls}" href="{html_lib.escape(u)}" target="_blank" '
        f'rel="noopener">{html_lib.escape(u.replace("https://","").replace("http://",""))[:48]}</a>'
        for u in urls
    )
    return f'<div class="row"><span class="lbl">{label}</span><div class="chips">{chips}</div></div>'


def write_html(path: Path, rows: list, source: str) -> None:
    total = len(rows)
    with_tg = sum(1 for r in rows if r.get("telegram"))
    with_max = sum(1 for r in rows if r.get("max"))
    with_mail = sum(1 for r in rows if r.get("emails"))
    cards = []
    for r in rows:
        name = html_lib.escape(r.get("name") or urlparse(r["home"]).netloc)
        site = html_lib.escape(r["home"])
        emails = "".join(
            f'<a class="chip mail" href="mailto:{html_lib.escape(e)}">{html_lib.escape(e)}</a>'
            for e in r.get("emails", []))
        phones = "".join(
            f'<a class="chip tel" href="tel:{html_lib.escape(re.sub(chr(92)+"D","",p))}">{html_lib.escape(p)}</a>'
            for p in r.get("phones", []))
        body = ""
        if emails:
            body += f'<div class="row"><span class="lbl">Email</span><div class="chips">{emails}</div></div>'
        if phones:
            body += f'<div class="row"><span class="lbl">Телефон</span><div class="chips">{phones}</div></div>'
        body += _chips("Telegram", r.get("telegram", []), "tg")
        body += _chips("MAX", r.get("max", []), "max")
        body += _chips("WhatsApp", r.get("whatsapp", []), "wa")
        body += _chips("VK", r.get("vk", []), "vk")
        body += _chips("Instagram", r.get("instagram", []), "ig")
        body += _chips("Facebook", r.get("facebook", []), "fb")
        body += _chips("OK", r.get("ok", []), "ok")
        if not body:
            body = '<div class="empty">контакты не найдены</div>'
        flags = ""
        if r.get("telegram"):
            flags += '<span class="badge tg">TG</span>'
        if r.get("max"):
            flags += '<span class="badge max">MAX</span>'
        desc = r.get("description") or ""
        desc_html = f'<div class="desc">{html_lib.escape(desc)}</div>' if desc else ""
        search_blob = html_lib.escape((r.get("name") or "") + " " + r["home"] + " " + desc).lower()
        cards.append(f'''<div class="card" data-search="{search_blob}">
      <div class="head"><a class="cname" href="{site}" target="_blank" rel="noopener">{name}</a>{flags}</div>
      <div class="site">{site}</div>
      {desc_html}
      {body}
    </div>''')

    doc = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Контакты участников — {total}</title>
<style>
 :root{{--bg:#0f1216;--card:#1a1f27;--mut:#8b97a7;--txt:#e6ebf1;--acc:#27ae60}}
 *{{box-sizing:border-box}}
 body{{margin:0;font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--txt)}}
 header{{position:sticky;top:0;background:#11151b;padding:16px 20px;border-bottom:1px solid #232a34;z-index:5}}
 h1{{margin:0 0 6px;font-size:18px}}
 .stat{{color:var(--mut);font-size:13px}}
 .stat b{{color:var(--txt)}}
 #q{{margin-top:10px;width:100%;max-width:420px;padding:9px 12px;border-radius:8px;border:1px solid #2c3543;background:#0c0f13;color:var(--txt)}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px;padding:20px}}
 .card{{background:var(--card);border:1px solid #232a34;border-radius:12px;padding:14px 16px}}
 .head{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
 .cname{{font-weight:700;font-size:16px;color:var(--txt);text-decoration:none}}
 .cname:hover{{color:var(--acc)}}
 .site{{color:var(--mut);font-size:12px;margin:2px 0 8px;word-break:break-all}}
 .desc{{font-size:13px;color:#c2ccd8;margin:0 0 10px;padding:8px 10px;background:#141921;border-radius:8px;border-left:3px solid var(--acc)}}
 .row{{display:flex;gap:8px;margin:6px 0;align-items:flex-start}}
 .lbl{{flex:0 0 70px;color:var(--mut);font-size:12px;padding-top:5px}}
 .chips{{display:flex;flex-wrap:wrap;gap:6px}}
 .chip{{display:inline-block;padding:4px 9px;border-radius:7px;font-size:12px;text-decoration:none;background:#222b36;color:#dbe4ee;border:1px solid #2f3a48}}
 .chip:hover{{filter:brightness(1.25)}}
 .chip.tg{{background:#1c4a63;border-color:#2b7aa0;color:#bfe6ff}}
 .chip.max{{background:#4a2b63;border-color:#7d4fb0;color:#e7d4ff}}
 .chip.mail{{background:#2a3b2a;border-color:#3e6b3e;color:#cdeccd}}
 .chip.wa{{background:#1d4a37;border-color:#2f8a63}}
 .badge{{font-size:10px;font-weight:700;padding:2px 6px;border-radius:5px}}
 .badge.tg{{background:#2b7aa0;color:#fff}} .badge.max{{background:#7d4fb0;color:#fff}}
 .empty{{color:#5f6b7a;font-size:12px;font-style:italic}}
 footer{{padding:14px 20px;color:var(--mut);font-size:12px}}
</style></head><body>
<header>
 <h1>Контакты участников выставки</h1>
 <div class="stat">Всего компаний: <b>{total}</b> · с Telegram: <b>{with_tg}</b> · с MAX: <b>{with_max}</b> · с Email: <b>{with_mail}</b> · источник: {html_lib.escape(source)}</div>
 <input id="q" placeholder="Фильтр по названию или сайту…" oninput="flt()">
</header>
<div class="grid" id="grid">
{''.join(cards)}
</div>
<footer>Сгенерировано навыком contact-scraper. Клик по Telegram/MAX открывает ссылку в новой вкладке.</footer>
<script>
 function flt(){{var v=document.getElementById('q').value.toLowerCase();
 document.querySelectorAll('.card').forEach(function(c){{
   c.style.display = c.dataset.search.indexOf(v)>=0 ? '' : 'none';}});}}
</script>
</body></html>'''
    path.write_text(doc, encoding="utf-8")


# ---------- main ----------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сбор контактов с сайтов → Excel + HTML")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--excel", help="Excel-файл со списком сайтов")
    src.add_argument("--urls", nargs="+", help="Список URL")
    src.add_argument("--txt", help="Текстовый файл, по URL в строке")
    ap.add_argument("--site-col", default="Сайт", help="Имя колонки с сайтом (для --excel)")
    ap.add_argument("--name-col", default="Компания", help="Имя колонки с названием (для --excel)")
    ap.add_argument("--desc-col", default="Описание", help="Колонка с кратким описанием (для --excel)")
    ap.add_argument("--intl", action="store_true",
                    help="международный режим: телефоны E.164, LinkedIn/WeChat/Line/X")
    ap.add_argument("--describe", choices=["none", "deepseek"], default="none",
                    help="Источник описания деятельности: deepseek (по тексту главной) или none")
    ap.add_argument("--outdir", default="contacts_out", help="Папка результатов")
    ap.add_argument("--workers", type=int, default=8, help="Параллельных потоков")
    ap.add_argument("--timeout", type=int, default=15, help="Таймаут запроса, сек")
    ap.add_argument("--max-pages", type=int, default=6, help="Доп. страниц на сайт")
    ap.add_argument("--playwright", action="store_true", help="Включить фолбэк Playwright")
    ap.add_argument("--resume", action="store_true", help="Продолжить (пропустить готовые домены)")
    args = ap.parse_args(argv)

    global INTL_MODE
    INTL_MODE = bool(args.intl)

    items = load_inputs(args)
    if not items:
        print("Не найдено ни одного сайта во входных данных.", file=sys.stderr)
        return 1

    ds_key = get_deepseek_key() if args.describe == "deepseek" else None
    if args.describe == "deepseek" and not ds_key:
        log.warning("DeepSeek-ключ не найден — описание будет из meta/каталога.")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    jsonl = outdir / "contacts.jsonl"

    done = {}
    if args.resume and jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done[urlparse(r["home"]).netloc.lower()] = r
            except Exception:
                pass
        log.info(f"Resume: уже собрано {len(done)} сайтов")

    todo = [it for it in items if urlparse(base_url(it["site"])).netloc.lower() not in done]
    log.info(f"Сайтов всего: {len(items)} | к обработке: {len(todo)} | потоков: {args.workers}")

    rows = list(done.values())

    def work(it):
        try:
            r = crawl_site(it["name"], it["site"], args.timeout,
                           args.max_pages, args.playwright)
        except Exception as e:
            log.error(f"Ошибка на {it['site']}: {e}")
            r = {"name": it["name"], "site": it["site"], "home": base_url(it["site"]),
                 "title": "", "reachable": False, "meta_desc": "", "home_text": "",
                 **{k: [] for k in CONTACT_KEYS}}
        # описание деятельности
        short = it.get("short_desc", "")
        desc = ""
        if args.describe == "deepseek" and ds_key:
            desc = deepseek_describe(ds_key, r.get("name") or it["name"], short,
                                     r.get("home_text") or r.get("meta_desc"))
        if not desc:  # fallback: meta-описание сайта, затем короткое из каталога
            desc = (r.get("meta_desc") or "").strip() or short
        r["description"] = desc
        r.pop("home_text", None)  # не тащим объёмный текст в результат
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, it): it for it in todo}
        n = 0
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            rows.append(r)
            n += 1
            write_jsonl(jsonl, rows)  # промежуточное сохранение после каждого
            tg = len(r.get("telegram", []))
            mx = len(r.get("max", []))
            em = len(r.get("emails", []))
            log.info(f"[{n}/{len(todo)}] {r['home']} → email:{em} tg:{tg} max:{mx}")

    # упорядочим как во входе
    order = {urlparse(base_url(it["site"])).netloc.lower(): i for i, it in enumerate(items)}
    rows.sort(key=lambda r: order.get(urlparse(r["home"]).netloc.lower(), 1e9))

    write_excel(outdir / "contacts.xlsx", rows)
    write_html(outdir / "contacts.html", rows, source=args.excel or args.txt or "urls")

    tg = sum(1 for r in rows if r.get("telegram"))
    mx = sum(1 for r in rows if r.get("max"))
    em = sum(1 for r in rows if r.get("emails"))
    print(f"\nГотово. Компаний: {len(rows)} | с email: {em} | с Telegram: {tg} | с MAX: {mx}")
    print(f"  {outdir/'contacts.xlsx'}")
    print(f"  {outdir/'contacts.html'}")
    print(f"  {outdir/'contacts.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
