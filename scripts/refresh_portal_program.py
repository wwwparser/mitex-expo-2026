"""Refresh the public MITEX 2026 program without touching the legacy site.

Run: python scripts/refresh_portal_program.py [--refresh | --offline] [--photos]
No paid APIs. Sources and request metadata are retained under data/portal.
Editorial copy is authored separately; this collector never generates it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/portal"
SOURCES = OUT / "program_sources"
BASE = "https://mitexpo.ru/"
DATES = [f"2026-11-{day}" for day in range(10, 14)]
TIME = re.compile(r"(\d{1,2}[:.]\d{2})\s*[–—−-]\s*(\d{1,2}[:.]\d{2})")
NETWORKS = {"t.me": "Telegram", "vk.com": "VK", "vk.ru": "VK",
            "youtube.com": "YouTube", "youtu.be": "YouTube", "rutube.ru": "Rutube",
            "instagram.com": "Instagram", "max.ru": "MAX", "max.me": "MAX"}


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def txt(node):
    if node is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(s.strip() for s in node.find_all(string=True)
        if s.parent.name not in ("script", "style", "noscript") and s.strip())).strip()


def lines(node):
    return [re.sub(r"\s+", " ", x).strip() for x in node.get_text("\n", strip=True).splitlines() if x.strip()] if node else []


def norm(value):
    return re.sub(r"[^a-zа-я0-9]", "", value.lower().replace("ё", "е"))


def absolute(url, base=BASE):
    return urljoin(base, url)


def canonical(url):
    return urldefrag(url)[0].rstrip("/")


def image_url(block):
    node = block.select_one('[data-original],meta[itemprop="image"],img') if block else None
    return (node.get("data-original") or node.get("content") or node.get("src") or "") if node else ""


def content(soup):
    clone = BeautifulSoup(str(soup), "html.parser")
    for node in clone.select("header,footer,script,style,noscript"):
        node.decompose()
    return clone


class Fetcher:
    """Per-URL resume, conservative retries, source dates distinct from run date."""
    def __init__(self, refresh=False, offline=False):
        self.refresh, self.offline = refresh, offline
        self.records, self.errors, self.memory = {}, [], {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MITEX-Research/1.0 (public exhibition program)"})

    def get(self, url):
        url = urldefrag(url)[0]
        if url in self.memory:
            return self.memory[url]
        key = hashlib.sha256(url.encode()).hexdigest()
        html, meta = SOURCES / (key + ".html"), SOURCES / (key + ".json")
        previous = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None
        usable = previous and html.exists() and previous.get("status_code") == 200
        fresh = usable and previous.get("checked_at", "")[:10] == now()[:10]
        if usable and (self.offline or (fresh and not self.refresh)):
            self.records[url] = {**previous, "cache_used": True}
            result = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
            self.memory[url] = result
            return result
        if self.offline:
            self.records[url] = previous or {"url": url, "status": "not_cached", "checked_at": None}
            self.errors.append({**previous, "cache_used": True} if previous else {"url": url, "error": "no_successful_cached_source", "checked_at": None})
            self.memory[url] = None
            return None
        response = None
        error = ""
        for attempt in range(3):
            try:
                time.sleep(.18 if attempt == 0 else 1 + attempt)
                response = self.session.get(url, timeout=(8, 25))
                if response.status_code in (403, 429, 500, 502, 503, 504):
                    error = f"HTTP {response.status_code}"
                    continue
                response.raise_for_status()
                error = ""
                break
            except requests.RequestException as exc:
                error = f"{type(exc).__name__}: {exc}"
        record = {"url": url, "final_url": response.url if response is not None else None,
                  "status_code": response.status_code if response is not None else None,
                  "checked_at": now(), "error": error or None,
                  "content_type": response.headers.get("Content-Type", "") if response is not None else None}
        self.records[url] = record
        if error:
            self.errors.append(record)
            # Never disguise an older successful page as today's successful check.
            self.memory[url] = None
            if not usable:
                save(meta, record)
            return None
        if "html" not in record["content_type"]:
            record["status"] = "non_html_link_checked"
            save(meta, record)
            self.memory[url] = None
            return None
        response.encoding = "utf-8"
        SOURCES.mkdir(parents=True, exist_ok=True)
        html.write_text(response.text, encoding="utf-8")
        record["sha256"] = hashlib.sha256(response.content).hexdigest()
        record["snapshot"] = html.relative_to(ROOT).as_posix()
        save(meta, record)
        result = BeautifulSoup(response.text, "html.parser")
        self.memory[url] = result
        print("SOURCE", response.status_code, url, flush=True)
        return result


def section_type(url, label):
    text = (url + " " + label).lower()
    for needles, kind in [(("7054",), "catalog"), (("scheme",), "floor_plan"),
                          (("speakers",), "speakers"), (("special",), "professional_skills"),
                          (("programme", "/program", "деловая"), "program"),
                          (("news", "novosti", "новости"), "news"),
                          (("photo", "фото"), "photos"), ((".pdf", "documents", "релизы"), "materials"),
                          (("контакты", "contacts"), "contacts")]:
        if any(x in text for x in needles):
            return kind
    return "navigation"


def discover(fetch, home):
    result = []
    top = {a.get("data-menu-item-number"): txt(a) for a in home.select("header a.t-menu__link-item")}
    for area, selector in [("main", "header a[href]"), ("footer", "footer a[href]"), ("home", "body a[href]")]:
        seen = set()
        for a in home.select(selector):
            if area == "home" and a.find_parent(["header", "footer"]):
                continue
            href = a.get("href", "").strip()
            label = txt(a) or a.get("aria-label") or (a.select_one("img").get("alt", "") if a.select_one("img") else "")
            if not label:
                # Social and sponsor links use SVG or CSS background images.
                label = (a.get("title") or urlparse(href).hostname or "").removeprefix("www.")
            if not label or (label, href) in seen:
                continue
            seen.add((label, href))
            url = absolute(href) if href else None
            row = {"label": label, "found_on": BASE, "location": area, "href": href, "url": url,
                   "parent_label": top.get(a.get("data-menu-item-number")) if "t-menusub__link-item" in a.get("class", []) else None,
                   "dropdown": "t-menusub__link-item" in a.get("class", []),
                   "section_type": section_type(url or "", label), "edition": None,
                   "edition_status": "not_established", "checked_at": fetch.records[BASE]["checked_at"]}
            for parent in a.parents:
                context_text = txt(parent)
                if len(context_text) > 1400:
                    break
                if "2027" in context_text:
                    row.update(context_edition=2027, context_evidence=context_text)
                    break
            if not href:
                row.update(status="menu_trigger", final_url=None)
            elif href.startswith(("#", "mailto:", "tel:")):
                row.update(status="local_or_contact_link", final_url=url)
            else:
                page = fetch.get(url)
                meta = fetch.records[urldefrag(url)[0]]
                row.update(final_url=meta.get("final_url"), status_code=meta.get("status_code"),
                           checked_at=meta.get("checked_at"), status="available" if meta.get("status_code") == 200 else "unavailable")
                if page:
                    row["observed_title"] = txt(page.title)
                    body = txt(content(page))
                    # Dates inside copyright or shared navigation do not establish an edition.
                    evidence = txt(page.title) + " " + body[:9000]
                    if re.search(r"2027", evidence) and "участи" in label.lower():
                        row.update(edition=2027, edition_status="explicit_next_edition")
                    elif "2026" in evidence or "7054" in (url or ""):
                        row.update(edition=2026, edition_status="explicit_on_page" if "2026" in evidence else "catalog_id")
                    if "2023" in evidence and "2026" not in evidence:
                        row.update(edition=2023, edition_status="archive")
                    if "Страница находится в разработке" in body:
                        row["content_status"] = "under_construction"
                elif meta.get("status_code") == 200 and "2026" in (url or ""):
                    row.update(edition=2026, edition_status="asset_url_only_not_document_contents")
                if row.get("context_edition") and row.get("edition") != row["context_edition"]:
                    row["edition_note"] = "Год в подписи блока главной и выпуск целевой страницы различаются либо выпуск целевой страницы не указан."
                if meta.get("final_url") and canonical(meta["final_url"]) != canonical(url):
                    row["redirected"] = True
            result.append(row)
    return result


def day_blocks(soup, selector):
    blocks = soup.select(selector)
    if len(blocks) != 4:
        raise ValueError(f"Expected four dated program blocks, found {len(blocks)}: {selector}")
    # Tabs explicitly declare all four dates; Tilda maps each tab to one record.
    tab = soup.select_one(".t1281")
    if tab:
        tabtext = txt(tab)
        for date in DATES:
            dd = date[-2:]
            if f"{dd}.11.2026" not in tabtext and f"{int(dd)} ноября" not in tabtext:
                raise ValueError("Missing tab date " + date)
    if tab:
        mapping = {}
        for item in tab.select("[data-tab-number][data-tab-rec-ids]"):
            match = re.search(r"(\d{2})\.11\.2026", txt(item))
            if match:
                for record in item["data-tab-rec-ids"].split(","):
                    mapping["rec" + record.strip()] = "2026-11-" + match[1]
        if mapping:
            for block in blocks:
                if block["id"] not in mapping:
                    raise ValueError("Program record absent from tab mapping: " + block["id"])
            return [(mapping[b["id"]], b) for b in blocks]
    return list(zip(DATES, blocks))


def event_base(identity, title, date, clock, venue, kind, url, source_block, fetch):
    match = TIME.search(clock)
    return {"id": identity, "title": title, "date": date,
            "start": match[1].replace(".", ":").zfill(5) if match else None, "end": match[2].replace(".", ":").zfill(5) if match else None,
            "venue": venue, "kind": kind, "source_url": url,
            "description": "", "people": [], "timezone": "Europe/Moscow", "edition": 2026,
            "catalog_id": "7054", "status": "announcement", "description_kind": "official_announcement",
            "checked_at": fetch.records[urldefrag(url)[0]]["checked_at"], "source_block": source_block,
            "people_evidence": [], "issues": []}


def company_from(role):
    # Only literal organization names in official role text, never footer links.
    organizations = ["Ассоциации развития аддитивных технологий", "Ассоциация развития аддитивных технологий",
        "Ассоциации РАТПЭ", "РАТПЭ", "Ассоциация торговых компаний и производителей электроинструмента и средств малой механизации",
        "Единая платформа продавцов инструмента и оборудования", "Министерства промышленности и торговли Российской Федерации",
        "Минпромторга России", "ВсеИнструменты.ру", "ELITECH", "Greenworks", "Интерскол", "Wildberries", "Ozon",
        "BRANDEXPERT Остров Свободы", "EM Studio", "РАНХиГС", "ГК ТСС", "Hugong", "ТД Комплект", "Управляющей Компании Е1",
        "AYA GROUP", "Елизар", "РОСТЕСТ", "Центр развития перспективных технологий", "НПК Симба", "АВРОРА",
        "GardenTools", "DADZ RACING Team", "Foxweld", "АО «ЦАТ»", "РэнджВижн", "ООО МС", "СНИР", "Heartwood",
        "Sales Engineering", "АЙ 3Д ГРУППА", "CAIMAN", "Спрут Сервис", "ДТИ Москвы", "Федеральная сервисная компания",
        "ТПП РФ", "КЛС Трейд", "СМАРТТУЛЗ", "DCK", "DCA", "Dong Cheng", "Стереотек", "Plantic", "КАМАЗ-мастер", "KNX24", "А100",
        "Greenhouse", "Karikomi", "Международная Академия Топиарного Искусства"]
    if role.startswith("автор канала"):
        return ""
    found = []
    for org in organizations:
        hit = re.search(re.escape(org), role, flags=re.I)
        if hit and not any(hit.group().lower() in old.lower() for old in found):
            found.append(hit.group())
    return "; ".join(found)


class Collector:
    def __init__(self, fetch, old_people):
        self.fetch, self.people, self.by_url, self.warnings = fetch, {}, {}, []
        self.old_people = {norm(p["name"]): p for p in old_people}
        previous_path = OUT / "people.json"
        self.previous_people = {norm(p["name"]): p for p in json.loads(previous_path.read_text(encoding="utf-8"))} if previous_path.exists() else {}

    def person(self, name, role, url, photo="", found_on=None, participation="speaker"):
        key = norm(name)
        if not key:
            raise ValueError("Empty person name")
        if key not in self.people:
            old = self.old_people.get(key, {})
            identity = old.get("id") or (urlparse(url).path.rstrip("/").split("/")[-1] if "/speakers/" in url else "person-" + hashlib.sha256(key.encode()).hexdigest()[:12])
            self.people[key] = {"id": identity, "name": name, "role": role, "company": company_from(role),
                "source_url": url, "photo": photo, "events": [], "social": [], "checked_at": self.fetch.records.get(urldefrag(found_on or url)[0], {}).get("checked_at"),
                "profile_status": "inline_announcement", "participation_roles": [], "sources": [], "profile_links": [], "issues": [], "aliases": []}
            photo_record = self.previous_people.get(key, {}).get("photo_download", {})
            if photo_record.get("url") == photo and photo_record.get("local_path") and (ROOT / photo_record["local_path"]).exists():
                self.people[key]["photo_download"] = photo_record
            for social in old.get("social", []):
                if not (social.get("verification_note") or social.get("affiliation_source") or social.get("telegram_title")):
                    continue
                self.people[key]["social"].append({**social, "checked_at": social.get("checked_at") or None,
                    "verification_period": "2026-10", "verification_status": "historical_not_rechecked",
                    "inherited_from": "data/site/people.json", "note": "Сохранено из октябрьского среза; точный день проверки в исходной записи отсутствует, сегодня ссылка не перепроверялась."})
        p = self.people[key]
        if name != p["name"] and name not in p["aliases"]:
            p["aliases"].append(name)
        if role and not p["role"]:
            p.update(role=role, company=company_from(role))
        if photo and not p["photo"]:
            p["photo"] = photo
        if participation not in p["participation_roles"]:
            p["participation_roles"].append(participation)
        source = {"url": found_on or url, "name": name, "role": role, "photo": photo}
        if source not in p["sources"]:
            p["sources"].append(source)
        return p

    def attach(self, e, p, source, participation="speaker"):
        evidence = {"person_id": p["id"], "source_url": source, "participation_role": participation}
        if evidence not in e["people_evidence"]:
            e["people_evidence"].append(evidence)

    def directory(self, url):
        soup = self.fetch.get(url)
        if soup is None:
            raise ValueError("Speaker directory unavailable")
        participation = "moderator"
        count = 0
        for rec in content(soup).select("[id^=rec]"):
            if txt(rec).upper() == "СПИКЕРЫ":
                participation = "speaker"
            for card in rec.select("a.t404__link"):
                name, role = txt(card.select_one(".t404__title")), txt(card.select_one(".t404__descr"))
                profile = absolute(card["href"], url)
                p = self.person(name, role, profile, image_url(card), url, participation)
                self.by_url[canonical(profile)] = p
                if profile not in p["profile_links"]:
                    p["profile_links"].append(profile)
                count += 1
        return count

    def main_program(self, url):
        soup = self.fetch.get(url)
        events = []
        for date, rec in day_blocks(content(soup), '.t-rec[data-record-type="513"]'):
            for row in rec.select(".t513__row"):
                title = txt(row.select_one(".t513__title"))
                clock = txt(row.select_one(".t513__time"))
                link = row.select_one('.t513__text a[href]')
                detail = absolute(link["href"], url) if link else url
                field = row.select_one(".t513__title").get("field")
                identity = "mitex-" + urlparse(detail).path.rstrip("/").split("/")[-1] if link else f"mitex-{date}-{field}"
                kind = title.split(":", 1)[0] if "сесси" in title.lower() or "дискуссия:" in title.lower() else ("Розыгрыш" if "Золотой час" in title else "Конференция" if title.startswith("Конференция") else "Торжественная церемония")
                e = event_base(identity, title, date, clock, "Зал деловой программы MITEX", kind, url, rec["id"] + "/" + field, self.fetch)
                e["schedule_source_url"] = url + "#" + rec["id"]
                e["source_url"] = detail
                e["venue_note"] = "Зал назван на странице программы; прежнее название «MITEX Арена» не переносилось как сегодняшнее."
                textblock = row.select_one(".t513__text")
                e["description"] = "\n".join(x for x in lines(textblock) if "Подробнее" not in x)
                e["organizers"] = []
                moderator = txt(row.select_one(".t513__persname"))
                if moderator:
                    for p in self.people.values():
                        if norm(p["name"]) in norm(moderator):
                            self.attach(e, p, e["schedule_source_url"], "moderator")
                if link:
                    self.event_detail(e)
                events.append(e)
        return events

    def event_detail(self, event):
        url = event["source_url"]
        page = self.fetch.get(url)
        if page is None:
            event["detail_status"] = "unavailable"
            return
        block = content(page)
        event["detail_status"] = "collected"
        event["detail_text"] = "\n".join(lines(block.select_one("#allrecords") or block.body or block))
        event["detail_checked_at"] = self.fetch.records[url]["checked_at"]
        event["detail_heading"] = txt(block.select_one(".t1123__descr"))
        header_time = TIME.search(txt(block.select_one(".t1123__title")))
        if header_time:
            detail_clock = [x.replace(".", ":").zfill(5) for x in header_time.groups()]
            event["detail_header_time"] = {"start": detail_clock[0], "end": detail_clock[1]}
            if detail_clock != [event["start"], event["end"]]:
                event["issues"].append({"type": "schedule_detail_time_conflict", "schedule": {"start": event["start"], "end": event["end"]}, "detail": event["detail_header_time"], "resolution": "В основных полях оставлено время общего расписания; расхождение требует уточнения у организатора."})
        if event["detail_heading"] and SequenceMatcher(None, norm(event["title"]), norm(event["detail_heading"])).ratio() < .6:
            event["issues"].append({"type": "schedule_detail_heading_conflict", "schedule_title": event["title"], "detail_heading": event["detail_heading"], "resolution": "Сохранено название из общего расписания и title страницы; ошибочный видимый заголовок не заменяет тему сессии."})
        event["detail_descriptions"] = [txt(x) for x in block.select(".t106__text") if txt(x)]
        if event["detail_descriptions"]:
            event["description"] = "\n\n".join(event["detail_descriptions"])
        event["detail_time_values"] = list(dict.fromkeys(TIME.findall(txt(block))))
        for card in block.select(".t526__col"):
            name_node = card.select_one(".t526__persname")
            name = txt(name_node)
            role_node = card.select_one(".t526__persdescr")
            raw_role = txt(role_node)
            role = raw_role.split("Тема:", 1)[0].replace("МОДЕРАТОР", "").strip()
            topic = raw_role.split("Тема:", 1)[1].strip() if "Тема:" in raw_role else None
            link = name_node.select_one("a[href]") if name_node else None
            profile = absolute(link["href"], url) if link else url
            p = self.by_url.get(canonical(profile)) if link else None
            if p:
                if norm(name) != norm(p["name"]) or (role and role != p["role"]):
                    event["issues"].append({"type": "speaker_card_differs_from_directory", "person_id": p["id"], "card_name": name, "card_role": role, "directory_name": p["name"], "directory_role": p["role"]})
                self.person(p["name"], role, p["source_url"], image_url(card), url)
            else:
                p = self.person(name, role, profile, image_url(card), url)
                if link:
                    p["profile_links"].append(profile)
                    self.by_url[canonical(profile)] = p
            self.attach(event, p, url, "moderator" if any(x["person_id"] == p["id"] and x["participation_role"] == "moderator" for x in event["people_evidence"]) else "speaker")
            if "МОДЕРАТОР" in raw_role:
                self.attach(event, p, url, "moderator")
            if topic:
                event.setdefault("announced_talks", []).append({"person_id": p["id"], "title": topic, "source_url": url, "status": "announcement"})
        # The RATPE conference publishes a nested timetable, not a t526 grid.
        # A row without a title continues the preceding talk with a second speaker.
        nested = []
        for row in block.select(".t513__row"):
            title = txt(row.select_one(".t513__title"))
            if title:
                field = row.select_one(".t513__title").get("field", str(len(nested)))
                item = event_base(event["id"] + "-" + field, title, event["date"], txt(row.select_one(".t513__time")), event["venue"], "Пункт программы конференции", url, field, self.fetch)
                item["parent_event_id"] = event["id"]
                nested.append(item)
            elif not nested:
                continue
            item = nested[-1]
            for person_block in row.select(".t513__persname"):
                link = person_block.select_one("a[href]")
                if not link:
                    continue
                profile = absolute(link["href"], url)
                p = self.by_url.get(canonical(profile))
                if p is None:
                    # Retain evidence rather than guessing an identity from surname order.
                    self.warnings.append({"type": "nested_speaker_not_in_directory", "name": txt(link), "url": profile, "event_id": event["id"]})
                    continue
                role = txt(person_block).removeprefix(txt(link)).strip()
                if txt(link) not in p["aliases"] and txt(link) != p["name"]:
                    p["aliases"].append(txt(link))
                self.person(p["name"], role, p["source_url"], image_url(row), url)
                self.attach(event, p, url)
                if not any(x["id"] == p["id"] for x in item["people"]):
                    item["people"].append({k: p[k] for k in ["id", "name", "role", "source_url", "photo"]})
                item["description"] = txt(person_block)
        if nested:
            event["subevents"] = nested
        # Organizers are read from the main content, not sponsor/footer banners.
        full_lines = lines(block)
        for i, value in enumerate(full_lines[:-1]):
            if value in ("Организатор:", "Организатор"):
                organizer = full_lines[i + 1]
                if organizer not in event["organizers"]:
                    event["organizers"].append(organizer)

    def park(self, url):
        page = self.fetch.get(url)
        events = []
        for date, rec in day_blocks(content(page), '.t-rec[data-record-type="516"]'):
            # Visible heading preceding each record independently verifies its date.
            heading = rec.find_previous(class_="t-rec")
            if f"{int(date[-2:])} ноября 2026" not in txt(heading):
                raise ValueError("Park record date mismatch: " + date)
            for row in rec.select(".t516__row"):
                title = txt(row.select_one(".t516__title"))
                field = row.select_one(".t516__title").get("field")
                clock = txt(row.select_one(".t516__time"))
                e = event_base("park-" + field, title, date, clock, "MITEX ПАРК", "Мастер-класс" if "МАСТЕР-КЛАСС" in clock else "Лекция", url, rec["id"] + "/" + field, self.fetch)
                e["source_url"] = url + "#" + rec["id"]
                text = row.select_one(".t516__text")
                e["description"] = "\n".join(lines(text))
                strong = next((t for t in text.select("strong") if "Спикер:" in txt(t)), None) if text else None
                if strong:
                    name = txt(strong).split("Спикер:", 1)[1].strip()
                    bio = e["description"].split(txt(strong), 1)[-1].strip()
                    # Roles in current inline bios only; a historical profile is not current evidence.
                    role = next((x for x in lines(text) if x != txt(strong)), "") if bio else ""
                    if len(role) > 180:
                        role = re.split(r"(?<=\.)\s", role, maxsplit=1)[0]
                        if len(role) > 180:
                            role = ""
                    if role.startswith("Талантливый"):
                        role = ""
                    named_role = re.search(re.escape(name) + r"\s*[—–]\s*([^.!?]+)", bio)
                    director_role = re.search(r"Директор АНО ДПО «[^»]+»", bio)
                    if named_role:
                        role = named_role[1].strip()
                    elif director_role:
                        role = director_role[0]
                    p = self.person(name, role, e["source_url"], image_url(row), url)
                    if bio:
                        p.setdefault("biography_sources", []).append({"text": bio, "source_url": e["source_url"]})
                        if not p["company"]:
                            p["company"] = company_from(bio)
                    self.attach(e, p, e["source_url"])
                events.append(e)
        return events

    def special(self, url):
        page = self.fetch.get(url)
        events = []
        named = [("Алексея Колчанова", "Алексей Колчанов", ""),
                 ("Даниила Винокурова", "Даниил Винокуров", "Строительный блогер (Dltboy)"),
                 ("Александра Махничева", "Александр Махничев", ""),
                 ("Ярослава Варламова", "Ярослав Варламов", "")]
        for date, rec in day_blocks(content(page), '.t-rec[data-record-type="650"]'):
            for card in rec.select(".t-card__col"):
                title_node = card.select_one(".t-card__title")
                community, field = txt(title_node), title_node.get("field")
                block = card.select_one(".t-card__descr")
                e = event_base("skills-" + date + "-" + field, community, date, "", "Зона профмастерства — " + community, "Анонс программы сообщества", url, rec["id"] + "/" + field, self.fetch)
                e.update(description="\n".join(lines(block)), activities=lines(block), community=community,
                         time_status="not_published", source_url=url + "#" + rec["id"], entry_granularity="community_day_announcement")
                e["note"] = "Один официальный блок сообщества за день; перечисленные занятия не превращены в вымышленные отдельные доклады или временные слоты."
                for label, name, role in named:
                    if label in e["description"]:
                        p = self.person(name, role, e["source_url"], found_on=url)
                        p.setdefault("source_name_forms", [])
                        if label not in p["source_name_forms"]:
                            p["source_name_forms"].append(label)
                        self.attach(e, p, e["source_url"], "master_class_host")
                events.append(e)
        return events

    def profiles(self, events):
        checked = set()
        while True:
            queue = [(u, p) for p in list(self.people.values()) for u in p["profile_links"] if canonical(u) not in checked]
            if not queue:
                break
            for url, p in queue:
                if canonical(url) in checked:
                    continue
                checked.add(canonical(url))
                page = self.fetch.get(url)
                if page is None:
                    p.setdefault("unavailable_profile_links", []).append(url)
                    if p["profile_status"] != "collected":
                        p["profile_status"] = "unavailable"
                    continue
                block = content(page)
                profile = block.select_one(".t545")
                if profile is None:
                    if p["profile_status"] != "collected":
                        p["profile_status"] = "unrecognized_markup"
                    self.warnings.append({"type": "unrecognized_profile", "url": url})
                    continue
                name, role = txt(profile.select_one(".t545__title")), txt(profile.select_one(".t545__descr"))
                if norm(name) != norm(p["name"]):
                    p["issues"].append({"type": "profile_name_differs_from_directory", "directory_name": p["name"], "profile_name": name, "source_url": url})
                    if name not in p["aliases"]:
                        p["aliases"].append(name)
                if role != p["role"]:
                    p["issues"].append({"type": "profile_role_differs_from_directory", "directory_role": p["role"], "profile_role": role, "source_url": url})
                # Keep both source claims; directory's unambiguous public name is the display name.
                p.update(profile_status="collected", profile_name=name, profile_role=role,
                         profile_text="\n".join(lines(profile)), checked_at=self.fetch.records[url]["checked_at"])
                p["photo"] = image_url(profile) or p["photo"]
                p.setdefault("profile_sessions", [])
                p.setdefault("public_links", [])
                for a in profile.select("a[href]"):
                    href = absolute(a["href"], url)
                    if "/programme/" in href:
                        session = {"title": txt(a), "source_url": href}
                        p["profile_sessions"].append(session)
                        e = next((e for e in events if canonical(e["source_url"]) == canonical(href)), None)
                        if e:
                            self.attach(e, p, url, "moderator" if "moderator" in p["participation_roles"] else "speaker")
                        else:
                            self.warnings.append({"type": "profile_session_not_in_current_schedule", "person_id": p["id"], **session})
                    else:
                        p["public_links"].append({"label": txt(a), "url": href, "source_url": url})
                        host = (urlparse(href).hostname or "").removeprefix("www.")
                        if host in NETWORKS:
                            p["social"].append({"network": NETWORKS[host], "url": href, "source_url": url,
                                "checked_at": p["checked_at"], "verification_status": "linked_in_current_official_profile",
                                "destination_checked_at": None, "kind": "profile_link", "note": "Проверено наличие ссылки в официальном профиле; содержимое внешнего профиля не проверялось."})

    def finish(self, events):
        by_id = {p["id"]: p for p in self.people.values()}
        for e in events:
            for pid in dict.fromkeys(x["person_id"] for x in e["people_evidence"]):
                p = by_id[pid]
                participation = list(dict.fromkeys(x["participation_role"] for x in e["people_evidence"] if x["person_id"] == pid))
                if "moderator" in participation:
                    participation = ["moderator"]
                e["people"].append({k: p[k] for k in ["id", "name", "role", "source_url", "photo"]} | {"participation_roles": participation})
                p["events"].append({k: e[k] for k in ["id", "title", "date", "start", "end", "source_url"]} | {"participation_roles": participation})
            if not e["people"]:
                e["people_status"] = "not_named_in_source"
        for p in by_id.values():
            p["social_status"] = "historical_links_preserved" if p["social"] else "not_present_in_current_official_profile" if p["profile_status"] == "collected" else "not_present_in_inline_announcement"
            p["company_source_url"] = p["source_url"] if p["company"] else None
            p["company_note"] = "Организация выделена только из опубликованной должности/биографии; пустое значение означает, что надежно выделить организацию нельзя."
            previous_photo = self.previous_people.get(norm(p["name"]), {}).get("photo_download", {})
            if previous_photo.get("url") == p["photo"] and previous_photo.get("local_path") and (ROOT / previous_photo["local_path"]).exists():
                p["photo_download"] = previous_photo
        return sorted(by_id.values(), key=lambda p: p["name"])


def compare(old_program, old_people, events, people):
    current, changes, missing = set(), [], []
    for old in old_program["events"]:
        candidates = [e for e in events if e["date"] == old["date"] and ("ПАРК" in e["venue"]) == ("ПАРК" in old["venue"])]
        best = max(candidates, key=lambda e: SequenceMatcher(None, norm(old["title"]), norm(e["title"])).ratio(), default=None)
        ratio = SequenceMatcher(None, norm(old["title"]), norm(best["title"])).ratio() if best else 0
        if ratio < .58:
            old_names = {norm(p["name"]) for p in old["people"]}
            supported = [e for e in candidates if e["kind"] == old.get("kind") and len(old_names & {norm(p["name"]) for p in e["people"]}) >= 2]
            if len(supported) == 1:
                best, ratio = supported[0], 1
        if ratio < .58:
            missing.append({"id": old["id"], "title": old["title"], "source_url": old["source_url"], "status": "not_found_in_current_program_not_assumed_cancelled"})
            continue
        current.add(best["id"])
        fields = {}
        for key in ["title", "start", "end", "venue", "source_url"]:
            if old.get(key) != best.get(key):
                fields[key] = {"previous": old.get(key), "current": best.get(key)}
        before, after = {p["name"] for p in old["people"]}, {p["name"] for p in best["people"]}
        if before != after:
            fields["people"] = {"added": sorted(after - before), "no_longer_listed": sorted(before - after)}
        best["legacy_id"] = old["id"]
        if fields:
            changes.append({"legacy_id": old["id"], "current_id": best["id"], "changes": fields})
    old_names = {norm(p["name"]): p for p in old_people}
    new_names = {norm(p["name"]): p for p in people}
    return {"previous_checked_at": old_program.get("checked_at"), "previous_event_count": len(old_program["events"]),
        "previous_people_count": len(old_people), "changed_events": changes, "not_found_events": missing,
        "added_event_ids": [e["id"] for e in events if e["id"] not in current],
        "added_people": [{"id": new_names[n]["id"], "name": new_names[n]["name"]} for n in sorted(new_names.keys() - old_names.keys())],
        "not_found_people": [{"id": old_names[n]["id"], "name": old_names[n]["name"]} for n in sorted(old_names.keys() - new_names.keys())],
        "changed_people": [{"id": new_names[n]["id"], "name": new_names[n]["name"], "changes": {k: {"previous": old_names[n].get(k), "current": new_names[n].get(k)} for k in ["role", "source_url", "photo"] if old_names[n].get(k) != new_names[n].get(k)}} for n in sorted(old_names.keys() & new_names.keys())],
        "note": "Сопоставление событий по дате, площадке и сходству темы; отсутствие в новой программе не означает отмену. Старые редакционные описания не переносились."}


def download_photos(fetch, people):
    for p in people:
        url = p.get("photo")
        if p.get("photo_download", {}).get("url") == url and p["photo_download"].get("local_path") and (ROOT / p["photo_download"]["local_path"]).exists():
            continue
        if not url:
            p["photo_download"] = {"status": "not_published"}
            continue
        try:
            time.sleep(.12)
            r = fetch.session.get(url, timeout=(8, 25))
            r.raise_for_status()
            mime = r.headers.get("Content-Type", "").split(";")[0]
            if not mime.startswith("image/"):
                raise ValueError("Unexpected image content type: " + mime)
            digest = hashlib.sha256(r.content).hexdigest()
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/svg+xml": ".svg"}.get(mime, ".img")
            path = ROOT / "docs/media/people" / (digest + ext)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(r.content)
            p["photo_download"] = {"status": "downloaded", "url": url, "source_url": p["source_url"], "sha256": digest, "local_path": path.relative_to(ROOT).as_posix(), "checked_at": now()}
        except (requests.RequestException, ValueError) as exc:
            p["photo_download"] = {"status": "unavailable", "url": url, "error": str(exc), "checked_at": now()}
        save(OUT / "people.json", people)


def validate(program, people):
    events = program["events"]
    assert len({e["id"] for e in events}) == len(events), "Duplicate event IDs"
    assert len({p["id"] for p in people}) == len(people), "Duplicate person IDs"
    assert set(e["date"] for e in events) == set(DATES)
    for e in events:
        assert all(k in e for k in ["id", "title", "date", "start", "end", "venue", "kind", "source_url", "description", "people"])
        assert e["title"] and e["source_url"].startswith("https://")
        assert (e["start"] is None and e["end"] is None) or (e["start"] < e["end"])
        for p in e["people"]:
            full = next(x for x in people if x["id"] == p["id"])
            assert any(x["id"] == e["id"] for x in full["events"])
    for p in people:
        assert all(k in p for k in ["id", "name", "role", "company", "source_url", "photo", "events", "social"])
        for e in p["events"]:
            assert any(x["id"] == e["id"] and any(y["id"] == p["id"] for y in x["people"]) for x in events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true", help="Re-fetch successful sources, too")
    mode.add_argument("--offline", action="store_true", help="Rebuild only from cached, dated sources")
    parser.add_argument("--photos", action="store_true")
    args = parser.parse_args()
    if args.offline and args.photos:
        parser.error("--offline cannot download photos")
    started = now()
    old_program = json.loads((ROOT / "data/site/program.json").read_text(encoding="utf-8"))
    old_people = json.loads((ROOT / "data/site/people.json").read_text(encoding="utf-8"))
    fetch = Fetcher(args.refresh, args.offline)
    home = fetch.get(BASE)
    if home is None:
        raise RuntimeError("Official homepage unavailable; existing output retained")
    sections = discover(fetch, home)
    def found(kind, host=None):
        return next(r["url"] for r in sections if r["location"] == "main" and r["section_type"] == kind and r["url"] and (host is None or urlparse(r["url"]).hostname == host))
    main_url, people_url = found("program", "mitexpo.ru"), found("speakers")
    skills_url = found("professional_skills")
    park_url = next(r["url"] for r in sections if r["location"] == "main" and r["section_type"] == "program" and urlparse(r["url"]).hostname == "mitex-park.ru")
    collector = Collector(fetch, old_people)
    directory_count = collector.directory(people_url)
    main_events = collector.main_program(main_url)
    park_events = collector.park(park_url)
    skills_events = collector.special(skills_url)
    events = main_events + park_events + skills_events
    collector.profiles(events)
    people = collector.finish(events)
    events.sort(key=lambda e: (e["date"], e["start"] or "99:99", e["venue"], e["id"]))
    changes = compare(old_program, old_people, events, people)
    legacy_url = old_program["source_url"]
    fetch.get(legacy_url)
    program = {"source_url": main_url, "checked_at": fetch.records[main_url]["checked_at"],
        "edition": 2026, "catalog_id": "7054", "dates": DATES, "source_urls": [main_url, park_url, skills_url],
        "note": "Опубликованные анонсы MITEX 2026, MITEX ПАРК и зоны профмастерства. Состоявшиеся доклады не подтверждались. Время московское. В зоне профмастерства один блок — программа сообщества за день; время отдельных занятий не опубликовано. Отсутствующие часы и неподтвержденные организации не додумывались.",
        "events": events}
    validate(program, people)
    save(OUT / "program.json", program)
    save(OUT / "people.json", people)
    if args.photos:
        download_photos(fetch, people)
    profile_people = [p for p in people if p["profile_links"]]
    coverage = {"source_url": main_url, "checked_at": started, "finished_at": now(), "edition": 2026, "catalog_id": "7054",
        "counts": {"events": len(events), "main_program_entries": len(main_events), "park_entries": len(park_events),
            "skills_community_day_announcements": len(skills_events), "people": len(people),
            "speaker_directory_cards": directory_count, "detailed_profiles_discovered": len(profile_people),
            "detailed_profiles_collected": sum(p["profile_status"] == "collected" for p in profile_people),
            "event_detail_pages_discovered": sum("detail_status" in e for e in events),
            "event_detail_pages_collected": sum(e.get("detail_status") == "collected" for e in events),
            "event_people_links": sum(len(e["people"]) for e in events), "without_published_time": sum(e["start"] is None for e in events),
            "nested_conference_program_items": sum(len(e.get("subevents", [])) for e in events),
            "individually_titled_speaker_announcements": sum(len(e.get("announced_talks", [])) for e in events),
            "without_named_people": sum(not e["people"] for e in events),
            "historical_social_links": sum(s.get("verification_status") == "historical_not_rechecked" for p in people for s in p["social"]),
            "photo_downloads": sum(p.get("photo_download", {}).get("status") == "downloaded" for p in people)},
        "days": {d: {"total": sum(e["date"] == d for e in events), "main": sum(e["date"] == d for e in main_events), "park": sum(e["date"] == d for e in park_events), "skills": sum(e["date"] == d for e in skills_events)} for d in DATES},
        "catalog_evidence": {"url": found("catalog"), "edition": 2026, "note": "Каталог 7054 связан из текущего меню. Архивные подписи 2023 в его общем подвале не использованы для программы 2026."},
        "legacy_program_url_check": fetch.records[legacy_url], "changes_from_legacy": changes,
        "source_conflicts": [{"event_id": e["id"], **issue} for e in events for issue in e["issues"]] + [{"person_id": p["id"], **issue} for p in people for issue in p["issues"]],
        "warnings": collector.warnings, "request_errors": fetch.errors,
        "sources": list(fetch.records.values()),
        "limitations": ["Полнота относится к опубликованным карточкам трёх разделов и всем четырём вкладкам дней, не к неопубликованным выступлениям.",
            "В зоне профмастерства сохранены цельные дневные анонсы со всеми строками занятий; точного расписания внутри блоков нет.",
            "Ссылки на соцсети из старого среза сохранены как исторические с периодом 2026-10; точный день старой проверки неизвестен.",
            "Контакты и соцсети из общего подвала не приписываются людям. Неоднозначные должности и имена в официальных источниках сохранены как расхождения.",
            "Профили без связанного события остаются в people.json; связь по одному лишь сходству темы не создавалась.",
            "Наличие нового анонса не подтверждает, что мероприятие состоялось. Отсутствие старой сессии не доказывает отмену."],
        "validation": "passed: unique IDs, four dates, required fields, clock order, bidirectional event/person links"}
    coverage["untimed_announcements"] = {"count": len(skills_events), "event_ids": [e["id"] for e in skills_events],
        "label": "Дневной анонс без времени", "note": "Все 21 записи — опубликованные программы сообществ на конкретный день. start/end=null. Время не назначалось, отдельные доклады не выдумывались."}
    section_map = {"source_url": BASE, "checked_at": fetch.records[BASE]["checked_at"], "edition": 2026, "catalog_id": "7054",
        "note": "Фактические href главной, включая выпадающие пункты и пустые триггеры. Выпуск определяется по содержимому целевой страницы, не по общему copyright. Текущая главная содержит предупреждение о временном переносе хостинга.",
        "links": sections}
    browser_evidence = SOURCES / "browser_menu.json"
    if browser_evidence.exists():
        section_map["browser_verification"] = json.loads(browser_evidence.read_text(encoding="utf-8"))
    save(OUT / "section_map.json", section_map)
    save(OUT / "program_coverage.json", coverage)
    print(json.dumps(coverage["counts"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
