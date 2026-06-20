#!/usr/bin/env python3
"""Sync Animetro static website files from Google Sheets.

The Google Sheet is the source of truth. This script reads:
- Website-content-2 / Website-conetent-2
- Brand Identity
- Website Images

It writes raw tab exports to content/ and regenerates static files.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = ROOT / "content"
EN_DIR = ROOT / "en"
ZH_DIR = ROOT / "zh"

CONTENT_TAB = "websitecontentmaster"
REQUIRED_TABS = {
    CONTENT_TAB: [
        "Website-conetent-2",
        "0Website-content-2",
        "Website-content-2",
        "Website Content 2",
        "website-content 2",
        "websitecontent2",
        "website-content-1",
        "websitecontentmaster",
        "websitecontentmaster01",
        "Website Content Master",
    ],
    "Brand Identity": ["Brand Identity", "brand identity", "    Brand Identity"],
    "Website Images": ["Website Images", "website images"],
}
SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
NAV_KEYS = ["home", "services", "about", "events", "resources", "contact"]


@dataclass
class SheetTable:
    name: str
    headers: list[str]
    rows: list[dict[str, str]]


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def credentials():
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    trimmed_json = raw_json.strip()
    diagnostic = f"GOOGLE_SERVICE_ACCOUNT_JSON length: {len(raw_json)}"
    if (
        not trimmed_json
        or not trimmed_json.startswith("{")
        or not trimmed_json.endswith("}")
    ):
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is empty or invalid. "
            "Paste the full downloaded Google service account JSON file content into "
            "the GitHub repository secret. It must start with { and end with }. "
            + diagnostic
        )

    try:
        info = json.loads(trimmed_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is empty or invalid. "
            "Paste the full downloaded Google service account JSON file content into "
            "the GitHub repository secret. It must start with { and end with }. "
            + diagnostic
        ) from exc

    try:
        return service_account.Credentials.from_service_account_info(info, scopes=SHEETS_SCOPE)
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError(
            "Invalid GOOGLE_SERVICE_ACCOUNT_JSON: the JSON is missing required service account fields. "
            "Confirm it includes type, project_id, private_key, client_email, token_uri, and related fields."
        ) from exc


def sheets_service():
    return build("sheets", "v4", credentials=credentials(), cache_discovery=False)


def clean_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def normalize_headers(headers: list[str]) -> list[str]:
    normalized: list[str] = []
    used: dict[str, int] = {}
    for index, header in enumerate(headers):
        key = clean_key(header) or f"column_{index + 1}"
        if key in used:
            used[key] += 1
            key = f"{key}_{used[key]}"
        else:
            used[key] = 1
        normalized.append(key)
    return normalized


def sheet_title_key(value: str) -> str:
    return clean_key(value.strip())


def resolve_required_tabs(service: Any, spreadsheet_id: str) -> dict[str, str]:
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    available = {
        sheet_title_key(sheet["properties"]["title"]): sheet["properties"]["title"]
        for sheet in metadata.get("sheets", [])
    }
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for canonical, aliases in REQUIRED_TABS.items():
        match = ""
        for alias in aliases:
            match = available.get(sheet_title_key(alias), "")
            if match:
                break
        if match:
            resolved[canonical] = match
        else:
            missing.append(f"{canonical} (accepted names: {', '.join(aliases)})")
    if missing:
        raise RuntimeError("Missing required Google Sheet tabs: " + "; ".join(missing))
    return resolved


def fetch_table(service: Any, spreadsheet_id: str, tab_name: str, canonical_name: str | None = None) -> SheetTable:
    response = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A:ZZ", valueRenderOption="FORMATTED_VALUE")
        .execute()
    )
    values = response.get("values", [])
    if not values:
        return SheetTable(canonical_name or tab_name, [], [])

    headers = normalize_headers([str(cell).strip() for cell in values[0]])
    rows: list[dict[str, str]] = []
    for raw_row in values[1:]:
        row = {header: "" for header in headers}
        for index, cell in enumerate(raw_row[: len(headers)]):
            row[headers[index]] = str(cell).strip()
        if any(row.values()):
            rows.append(row)
    return SheetTable(canonical_name or tab_name, headers, rows)


def write_table(table: SheetTable) -> None:
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    slug = clean_key(table.name)
    json_path = CONTENT_DIR / f"{slug}.json"
    csv_path = CONTENT_DIR / f"{slug}.csv"
    json_path.write_text(json.dumps(table.rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=table.headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(table.rows)


def first(row: dict[str, str], names: list[str], default: str = "") -> str:
    for name in names:
        value = row.get(clean_key(name), "").strip()
        if value:
            return value
    return default


def row_key(row: dict[str, str]) -> str:
    return first(row, ["key", "content key", "content_key", "id", "slug", "field"])


def status_value(row: dict[str, str]) -> str:
    return clean_key(first(row, ["status"]))


def approved_content_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    approved: list[dict[str, str]] = []
    for row in rows:
        key = row_key(row)
        if not key or clean_key(key) in {"key", "content_key", "service_page", "section_1_services_hero"}:
            continue
        status = status_value(row)
        if status and status not in {"approved", "approve", "confirmed"}:
            continue
        if not any(first(row, names) for names in (["English Text", "English", "en"], ["Traditional Chinese Text", "Chinese Text", "zh"], ["Link"], ["Image File"])):
            continue
        approved.append(row)
    return approved


def build_key_index(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in approved_content_rows(rows):
        key = clean_key(row_key(row))
        if key and key not in index:
            index[key] = row
    return index


def lang_value(row: dict[str, str] | None, lang: str, default: str = "") -> str:
    if not row:
        return default
    if lang == "en":
        return first(row, ["English Text", "English", "en", "english_content", "content_en", "value_en"], default)
    return first(row, ["Traditional Chinese Text", "Chinese Text", "zh", "Chinese", "traditional_chinese", "zh_hant", "content_zh", "value_zh"], default)


def text(index: dict[str, dict[str, str]], key: str, lang: str, default: str = "") -> str:
    return lang_value(index.get(clean_key(key)), lang, default)


def link_value(index: dict[str, dict[str, str]], key: str, default: str = "") -> str:
    return first(index.get(clean_key(key), {}), ["Link", "url", "href"], default)


def image_value(index: dict[str, dict[str, str]], key: str, default: str = "") -> str:
    return first(index.get(clean_key(key), {}), ["Image File", "Image File ", "image", "image_url", "src", "asset"], default)


def section_id(index: dict[str, dict[str, str]], key: str, default: str = "") -> str:
    return first(index.get(clean_key(key), {}), ["section ID", "section_id", "section id"], default)


def escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def page_title(lang: str, suffix: str = "") -> str:
    name = "艾美加教育顧問" if lang == "zh" else "Animetro Consulting"
    return f"{suffix} | {name}" if suffix else name


def rel_css(depth: int) -> str:
    return "../" * depth + "assets/styles.css"


def nav_html(index: dict[str, dict[str, str]], lang: str, active: str, depth: int) -> str:
    home = "/zh/" if lang == "zh" else "/en/"
    other = "/en/" if lang == "zh" else "/zh/"
    other_label = "English" if lang == "zh" else "中文"
    logo_alt = text(index, "header_logo", lang, "艾美加教育顧問" if lang == "zh" else "Animetro Consulting")
    links = []
    for nav_key in NAV_KEYS:
        label = text(index, f"nav_{nav_key}", lang, nav_key.title())
        href = home if nav_key == "home" else f"{home}{nav_key}/"
        cls = ' class="active"' if active == nav_key else ""
        links.append(f'<a{cls} href="{href}">{escape(label)}</a>')
    links.append(f'<a class="lang-link" href="{other}" lang="zh-Hant">{escape(other_label)}</a>')
    nav_links_html = "\n          ".join(links)
    return f'''    <header class="site-header">
      <nav class="nav" aria-label="Main navigation">
        <a class="brand" href="{home}" aria-label="{escape(logo_alt)} home"><img class="brand-logo" src="/assets/brand/exports/website-header-transparent.png" alt="{escape(logo_alt)}"></a>
        <div class="nav-links">
          {nav_links_html}
        </div>
      </nav>
    </header>'''


def sheet_asset_path(value: str, fallback: str) -> str:
    asset = value.strip()
    if not asset:
        return fallback
    if asset.startswith("/") or asset.startswith("http://") or asset.startswith("https://"):
        return asset
    aliases = {
        "finallogo0617.png": "/assets/brand/exports/animetro-primary-transparent.png",
        "animetrowebsite_header0617.png": "/assets/brand/exports/website-header-transparent.png",
        "wechat-qr.jpg": "/assets/images/contact/wechat-qr.jpeg",
        "wechat-qr.jpeg": "/assets/images/contact/wechat-qr.jpeg",
        "whatsapp-qr.jpg": "/assets/images/contact/whatsapp-qr.jpeg",
        "whatsapp-qr.jpeg": "/assets/images/contact/whatsapp-qr.jpeg",
    }
    return aliases.get(asset.lower(), f"/assets/images/{asset}")


def hero_slideshow_html(lang: str) -> str:
    if lang == "zh":
        return '''<aside class="hero-media hero-slideshow" aria-label="私人教育諮詢">
          <img src="/assets/images/hero-consulting-1.jpg" alt="家庭與教育顧問諮詢">
          <img src="/assets/images/hero-consulting-2.png" alt="學生與家庭教育規劃諮詢">
          <img src="/assets/images/animetrowebherobanner0617.png" alt="Animetro Consulting 成長超越升學品牌圖">
        </aside>'''
    return '''<aside class="hero-media hero-slideshow" aria-label="Private education consultation">
          <img src="/assets/images/hero-consulting-1.jpg" alt="Family meeting with an education consultant">
          <img src="/assets/images/hero-consulting-2.png" alt="Student and family consultation with an advisor">
          <img src="/assets/images/animetrowebherobanner0617.png" alt="Animetro Consulting growth beyond admission banner">
        </aside>'''


def footer_html(index: dict[str, dict[str, str]], lang: str) -> str:
    is_zh = lang == "zh"
    logo_alt = text(index, "footer_logo", lang, "艾美加教育顧問" if is_zh else "Animetro Consulting")
    tagline = text(index, "footer_tagline", lang, "成長超越升學" if is_zh else "Growth Beyond Admission")
    phone = text(index, "footer_phone", lang, "905-955-7068")
    email = text(index, "footer_email", lang, "consulting@animetro.ca")
    website = text(index, "footer_website", lang, "www.animetro.ca")
    website_href = link_value(index, "footer_website", "https://www.animetro.ca/")
    copyright_text = text(index, "footer_copyright", lang, "© 2026 艾美加教育顧問．版權所有" if is_zh else "© 2026 Animetro Consulting. All Rights Reserved.")
    contact_label = "聯絡方式" if is_zh else "Contact"
    services_label = text(index, "nav_services", lang, "服務" if is_zh else "Services")
    qr_label = "二维码" if is_zh else "QR Codes"
    get_started = "開始諮詢" if is_zh else "Get Started"
    qr_wechat = text(index, "footer_wechat_label", lang, "掃碼添加微信" if is_zh else "Scan to connect on WeChat")
    qr_whatsapp = text(index, "footer_whatsapp_label", lang, "掃碼聯絡 WhatsApp" if is_zh else "Scan to connect on WhatsApp")
    footer_logo_src = sheet_asset_path(
        link_value(index, "footer_logo") or image_value(index, "footer_logo"),
        "/assets/brand/exports/animetro-primary-transparent.png",
    )
    whatsapp_src = sheet_asset_path(
        link_value(index, "footer_whatsapp_qr") or image_value(index, "footer_whatsapp_qr"),
        "/assets/images/contact/whatsapp-qr.jpeg",
    )
    wechat_src = sheet_asset_path(
        link_value(index, "footer_wechat_qr") or image_value(index, "footer_wechat_qr"),
        "/assets/images/contact/wechat-qr.jpeg",
    )
    base = "/zh" if is_zh else "/en"
    service_links = service_link_items(index, lang, footer=True)
    return f'''    <footer class="site-footer">
      <div class="footer-inner">
        <div class="footer-brand"><img class="footer-logo" src="{escape(footer_logo_src)}" alt="{escape(logo_alt)}"><span>{escape(logo_alt)}<small>{escape(tagline)}</small></span></div>
        <div class="footer-contact" aria-label="{escape(contact_label)}">
          <p class="footer-column-title">{escape(contact_label)}</p>
          <a href="tel:+19059557068">{escape(phone)}</a>
          <a href="mailto:{escape(email)}">{escape(email)}</a>
          <a href="{escape(website_href)}">{escape(website)}</a>
          <a class="footer-cta" href="{base}/contact/">{escape(get_started)}</a>
        </div>
        <nav class="footer-services" aria-label="{escape(services_label)}">
          <p class="footer-column-title">{escape(services_label)}</p>
          {service_links}
        </nav>
        <div class="footer-actions">
          <p class="footer-column-title">{escape(qr_label)}</p>
          <div class="footer-qr-list" aria-label="{escape(qr_label)}">
            <figure class="footer-qr"><img src="{escape(whatsapp_src)}" alt="WhatsApp QR code"><figcaption>{escape(qr_whatsapp)}</figcaption></figure>
            <figure class="footer-qr"><img src="{escape(wechat_src)}" alt="WeChat QR code"><figcaption>{escape(qr_wechat)}</figcaption></figure>
          </div>
        </div>
      </div>
      <p class="footer-copyright">{escape(copyright_text)}</p>
    </footer>'''


def page_shell(index: dict[str, dict[str, str]], lang: str, active: str, title_suffix: str, depth: int, body: str, description: str = "") -> str:
    html_lang = "zh-Hant" if lang == "zh" else "en"
    desc = f'\n    <meta name="description" content="{escape(description)}">' if description else ""
    return f'''<!doctype html>
<html lang="{html_lang}">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="icon" href="/assets/brand/uploads/05_Animetro_Social_Icon.png" type="image/png">
    <link rel="apple-touch-icon" href="/assets/brand/uploads/05_Animetro_Social_Icon.png">
    <title>{escape(page_title(lang, title_suffix))}</title>{desc}
    <link rel="stylesheet" href="{rel_css(depth)}">
  </head>
  <body>
{nav_html(index, lang, active, depth)}

{body}

{footer_html(index, lang)}
  </body>
</html>
'''


def section_card_grid(items: list[tuple[str, str, str]], extra_class: str = "") -> str:
    cards = []
    for title, desc, link in items:
        link_start = f'<a href="{escape(link)}">' if link else ""
        link_end = "</a>" if link else ""
        cards.append(f'''            <article class="card{extra_class}">
              <h3>{link_start}{escape(title)}{link_end}</h3>
              {f'<p>{escape(desc)}</p>' if desc else ''}
            </article>''')
    return "\n".join(cards)


def numbered_pairs(index: dict[str, dict[str, str]], lang: str, prefix: str, title_suffix: str = "title", desc_suffix: str = "desc", limit: int = 12, links: bool = False) -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    for number in range(1, limit + 1):
        title_key = f"{prefix}_{number}_{title_suffix}"
        desc_key = f"{prefix}_{number}_{desc_suffix}"
        title = text(index, title_key, lang)
        if not title:
            continue
        desc = text(index, desc_key, lang)
        link = ""
        if links:
            sid = section_id(index, title_key)
            link = f"/{lang}/services/#{sid}" if sid else f"/{lang}/services/"
        items.append((title, desc, link))
    return items


def service_link_items(index: dict[str, dict[str, str]], lang: str, footer: bool = False) -> str:
    labels = []
    for number in range(1, 16):
        key = f"service_{number}_title"
        title = text(index, key, lang)
        if not title:
            continue
        sid = section_id(index, key, f"service-{number}")
        labels.append((title, f"/{lang}/services/#{sid}"))
    if not labels:
        fallback = [
            ("Education Strategy", "strategic-planning"),
            ("Prep School Admissions", "elite-private-school"),
            ("University Admissions", "university-application"),
            ("GPA Management", "gpa-management"),
        ]
        labels = [(title, f"/{lang}/services/#{sid}") for title, sid in fallback]
    limit = 10 if footer else len(labels)
    return "\n          ".join(f'<a href="{escape(link)}">{escape(title)}</a>' for title, link in labels[:limit])


def homepage_html(index: dict[str, dict[str, str]], lang: str) -> str:
    is_zh = lang == "zh"
    hero_title = text(index, "hero_title", lang, "")
    hero_subtitle = text(index, "hero_subtitle", lang, "")
    hero_description = text(index, "hero_description", lang, "")
    primary_cta = text(index, "hero_primary_cta", lang, "預約免費諮詢" if is_zh else "Book a Free Consultation")
    secondary_cta = text(index, "hero_secondary_cta", lang, "了解我們的服務" if is_zh else "Explore Our Services")
    why_title = text(index, "why_title", lang)
    services_title = text(index, "services_title", lang)
    success_title = text(index, "success_stories_title", lang, text(index, "success_title", lang, "成功案例" if is_zh else "Student Success Stories"))
    founder_title = text(index, "founder_title", lang)
    founder_subtitle = text(index, "founder_subtitle", lang)
    founder_bio = text(index, "founder_bio", lang)
    founder_cta = text(index, "founder_cta", lang)
    testimonials_title = text(index, "testimonials_title", lang)
    cta_title = text(index, "cta_title", lang)
    cta_subtitle = text(index, "cta_subtitle", lang)
    cta_description = text(index, "cta_description", lang)
    cta_button = text(index, "cta_button", lang, primary_cta)
    cta_phone = text(index, "cta_phone", lang)
    cta_email = text(index, "cta_email", lang)
    cta_website = text(index, "cta_website", lang)
    cta_closing = text(index, "cta_closing", lang)

    why_items = numbered_pairs(index, lang, "why", limit=8)
    service_items = numbered_pairs(index, lang, "service", limit=16, links=True)
    success_items = numbered_pairs(index, lang, "success", limit=8)
    founder_highlights = [text(index, f"founder_highlight_{i}", lang) for i in range(1, 9)]
    founder_highlights = [item for item in founder_highlights if item]
    testimonials = []
    for i in range(1, 8):
        quote = text(index, f"testimonial_{i}", lang)
        name = text(index, f"testimonial_{i}_name", lang)
        if quote:
            testimonials.append((quote, name, ""))
    benefits = [text(index, f"cta_benefit_{i}", lang) for i in range(1, 8)]
    benefits = [item for item in benefits if item]

    body = f'''    <main>
      <section class="hero hero-with-media">
        <div>
          {f'<p class="eyebrow">{escape(hero_subtitle)}</p>' if hero_subtitle else ''}
          <h1>{escape(hero_title)}</h1>
          {f'<p class="lead">{escape(hero_description)}</p>' if hero_description else ''}
          <div class="actions">
            <a class="button" href="/{lang}/contact/">{escape(primary_cta)}</a>
            <a class="button secondary" href="/{lang}/services/">{escape(secondary_cta)}</a>
          </div>
        </div>
        {hero_slideshow_html(lang)}
      </section>

      <section class="band" id="why-animetro">
        <div class="section">
          <div class="section-header"><h2>{escape(why_title)}</h2></div>
          <div class="grid">
{section_card_grid(why_items)}
          </div>
        </div>
      </section>

      <section class="section" id="core-services">
        <div class="section-header"><h2>{escape(services_title)}</h2></div>
        <div class="grid">
{section_card_grid(service_items)}
        </div>
      </section>

      <section class="band" id="success-stories">
        <div class="section">
          <div class="section-header"><h2>{escape(success_title)}</h2></div>
          <div class="grid">
{section_card_grid(success_items)}
          </div>
        </div>
      </section>

      <section class="section panel" id="meet-emily">
        <p class="eyebrow">{escape(founder_subtitle)}</p>
        <h2>{escape(founder_title)}</h2>
        <p class="lead">{escape(founder_bio)}</p>
        {f'<ul class="check-list">{"".join(f"<li>{escape(item)}</li>" for item in founder_highlights)}</ul>' if founder_highlights else ''}
        {f'<div class="actions"><a class="button secondary" href="/{lang}/about/">{escape(founder_cta)}</a></div>' if founder_cta else ''}
      </section>

      <section class="band" id="testimonials">
        <div class="section">
          <div class="section-header"><h2>{escape(testimonials_title)}</h2></div>
          <div class="grid testimonials-grid">
{section_card_grid(testimonials, ' testimonial-card')}
          </div>
        </div>
      </section>

      <section class="section panel" id="consultation">
        <h2>{escape(cta_title)}</h2>
        {f'<p class="lead">{escape(cta_subtitle)}</p>' if cta_subtitle else ''}
        {f'<p>{escape(cta_description)}</p>' if cta_description else ''}
        {f'<ul class="check-list">{"".join(f"<li>{escape(item)}</li>" for item in benefits)}</ul>' if benefits else ''}
        {f'<p class="cta-closing">{escape(cta_closing)}</p>' if cta_closing else ''}
        <div class="cta-contact-links">
          {f'<a href="tel:+19059557068">{escape(cta_phone)}</a>' if cta_phone else ''}
          {f'<a href="mailto:{escape(cta_email)}">{escape(cta_email)}</a>' if cta_email else ''}
          {f'<a href="https://{escape(cta_website).removeprefix("https://").removeprefix("http://")}">{escape(cta_website)}</a>' if cta_website else ''}
        </div>
        <div class="actions"><a class="button" href="/{lang}/contact/">{escape(cta_button)}</a></div>
      </section>
    </main>'''
    return page_shell(index, lang, "home", "", 1, body, hero_description)


def services_for_sheet(index: dict[str, dict[str, str]], lang: str) -> list[tuple[str, str, str]]:
    specs = [
        ("ps", "elite-private-school"),
        ("uni", "university-application"),
        ("gpa", "gpa-management"),
        ("stem", "steam-pathway"),
        ("athlete", "student-athlete"),
        ("neuro", "gifted-diverse-learning"),
        ("guardian", "short-term-guardianship"),
    ]
    items = []
    for prefix, sid in specs:
        title = text(index, f"{prefix}_title", lang)
        desc = text(index, f"{prefix}_desc", lang)
        if title:
            items.append((sid, title, desc))
    if not items:
        for number in range(1, 16):
            title = text(index, f"service_{number}_title", lang)
            desc = text(index, f"service_{number}_desc", lang)
            sid = section_id(index, f"service_{number}_title", f"service-{number}")
            if title:
                items.append((sid, title, desc))
    return items


def services_page_html(index: dict[str, dict[str, str]], lang: str) -> str:
    is_zh = lang == "zh"
    hero_title = text(index, "services_hero_title", lang, "服務" if is_zh else "Services")
    hero_subtitle = text(index, "services_hero_subtitle", lang, "")
    hero_cta = text(index, "services_hero_cta", lang, "預約諮詢" if is_zh else "Book a Consultation")
    cta_title = text(index, "services_cta_title", lang, text(index, "cta_title", lang))
    cta_subtitle = text(index, "services_cta_subtitle", lang, text(index, "cta_subtitle", lang))
    cta_button = text(index, "services_cta_button", lang, text(index, "cta_button", lang, hero_cta))
    service_articles = []
    for sid, title, desc in services_for_sheet(index, lang):
        service_articles.append(f'''          <article class="service-detail" id="{escape(sid)}">
            <h2>{escape(title)}</h2>
            {f'<p>{escape(desc)}</p>' if desc else ''}
          </article>''')
    body = f'''    <main>
      <section class="page-hero">
        <p class="eyebrow">{escape(text(index, 'nav_services', lang, '服務' if is_zh else 'Services'))}</p>
        <h1>{escape(hero_title)}</h1>
        {f'<p class="lead">{escape(hero_subtitle)}</p>' if hero_subtitle else ''}
        <div class="actions"><a class="button" href="/{lang}/contact/">{escape(hero_cta)}</a></div>
      </section>

      <section class="section">
        <div class="service-list">
{chr(10).join(service_articles)}
        </div>
      </section>

      <section class="section panel" id="consultation">
        <h2>{escape(cta_title)}</h2>
        {f'<p class="lead">{escape(cta_subtitle)}</p>' if cta_subtitle else ''}
        <div class="actions"><a class="button" href="/{lang}/contact/">{escape(cta_button)}</a></div>
      </section>
    </main>'''
    return page_shell(index, lang, "services", text(index, "nav_services", lang, "服務" if is_zh else "Services"), 2, body, hero_subtitle)


def write_site(tables: dict[str, SheetTable]) -> None:
    content_rows = tables[CONTENT_TAB].rows
    index = build_key_index(content_rows)
    EN_DIR.mkdir(parents=True, exist_ok=True)
    ZH_DIR.mkdir(parents=True, exist_ok=True)
    (EN_DIR / "services").mkdir(parents=True, exist_ok=True)
    (ZH_DIR / "services").mkdir(parents=True, exist_ok=True)
    (EN_DIR / "index.html").write_text(homepage_html(index, "en"), encoding="utf-8")
    (ZH_DIR / "index.html").write_text(homepage_html(index, "zh"), encoding="utf-8")
    (EN_DIR / "services" / "index.html").write_text(services_page_html(index, "en"), encoding="utf-8")
    (ZH_DIR / "services" / "index.html").write_text(services_page_html(index, "zh"), encoding="utf-8")
    (ROOT / "index.html").write_text(
        '''<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="0; url=/en/">
    <link rel="canonical" href="/en/">
    <title>Animetro Consulting</title>
  </head>
  <body>
    <p><a href="/en/">Continue to Animetro Consulting</a></p>
  </body>
</html>
''',
        encoding="utf-8",
    )


def main() -> None:
    spreadsheet_id = env_required("GOOGLE_SHEET_ID")
    service = sheets_service()
    tables: dict[str, SheetTable] = {}
    resolved_tabs = resolve_required_tabs(service, spreadsheet_id)
    for canonical, tab in resolved_tabs.items():
        table = fetch_table(service, spreadsheet_id, tab, canonical)
        write_table(table)
        tables[canonical] = table
    write_site(tables)
    print("Synced Google Sheet content into static site files.")


if __name__ == "__main__":
    main()
