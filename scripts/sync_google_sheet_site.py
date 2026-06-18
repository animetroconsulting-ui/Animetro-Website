#!/usr/bin/env python3
"""Sync Animetro static website files from Google Sheets.

The Google Sheet is the source of truth. This script reads:
- websitecontentmaster
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
from google.auth import default as google_auth_default
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = ROOT / "content"
ASSETS_DIR = ROOT / "assets"
EN_DIR = ROOT / "en"
ZH_DIR = ROOT / "zh"

REQUIRED_TABS = ["websitecontentmaster", "Brand Identity", "Website Images"]
SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


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
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw_json:
        info = json.loads(raw_json)
        return service_account.Credentials.from_service_account_info(info, scopes=SHEETS_SCOPE)

    creds, _ = google_auth_default(scopes=SHEETS_SCOPE)
    return creds


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


def fetch_table(service: Any, spreadsheet_id: str, tab_name: str) -> SheetTable:
    response = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A:ZZ", valueRenderOption="FORMATTED_VALUE")
        .execute()
    )
    values = response.get("values", [])
    if not values:
        return SheetTable(tab_name, [], [])

    headers = normalize_headers([str(cell).strip() for cell in values[0]])
    rows: list[dict[str, str]] = []
    for raw_row in values[1:]:
        row = {header: "" for header in headers}
        for index, cell in enumerate(raw_row[: len(headers)]):
            row[headers[index]] = str(cell).strip()
        if any(row.values()):
            rows.append(row)
    return SheetTable(tab_name, headers, rows)


def write_table(table: SheetTable) -> None:
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    slug = clean_key(table.name)
    json_path = CONTENT_DIR / f"{slug}.json"
    csv_path = CONTENT_DIR / f"{slug}.csv"
    json_path.write_text(json.dumps(table.rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=table.headers)
        writer.writeheader()
        writer.writerows(table.rows)


def first(row: dict[str, str], names: list[str], default: str = "") -> str:
    for name in names:
        value = row.get(clean_key(name), "").strip()
        if value:
            return value
    return default


def content_id(row: dict[str, str]) -> str:
    return first(row, ["section_id", "section id", "key", "id", "slug", "field", "content_key"])


def lang_value(row: dict[str, str], lang: str) -> str:
    if lang == "en":
        return first(row, ["en", "english", "english_content", "content_en", "value_en", "text_en", "copy_en", "title_en", "english_text"])
    return first(
        row,
        [
            "zh",
            "chinese",
            "chinese_text",
            "traditional_chinese",
            "zh_hant",
            "content_zh",
            "value_zh",
            "text_zh",
            "copy_zh",
            "title_zh",
        ],
    )


def pick_content(rows: list[dict[str, str]], key_patterns: list[str], lang: str, fallback: str) -> str:
    patterns = [clean_key(pattern) for pattern in key_patterns]
    for row in rows:
        haystack = " ".join(
            [
                content_id(row),
                first(row, ["page", "page_updated"]),
                first(row, ["section", "section_updated"]),
                first(row, ["label", "title", "name"]),
            ]
        )
        normalized = clean_key(haystack)
        if all(pattern in normalized for pattern in patterns):
            value = lang_value(row, lang)
            if value:
                return value
    return fallback


def section_rows(rows: list[dict[str, str]], section_name: str) -> list[dict[str, str]]:
    section_key = clean_key(section_name)
    matches = []
    for row in rows:
        text = " ".join([first(row, ["section", "section_updated", "category"]), content_id(row)])
        if section_key in clean_key(text):
            matches.append(row)
    return matches


def brand_value(rows: list[dict[str, str]], names: list[str], fallback: str) -> str:
    patterns = [clean_key(name) for name in names]
    for row in rows:
        text = " ".join(row.values())
        normalized = clean_key(text)
        if any(pattern in normalized for pattern in patterns):
            for key in ["value", "content", "url", "asset_url", "file", "image_url", "english", "en"]:
                value = row.get(key, "").strip()
                if value and clean_key(value) not in patterns:
                    return value
    return fallback


def image_for(images: list[dict[str, str]], names: list[str], fallback: str = "") -> str:
    patterns = [clean_key(name) for name in names]
    for row in images:
        text = " ".join([first(row, ["section", "page", "name", "image_name", "asset", "alt"]), content_id(row)])
        if any(pattern in clean_key(text) for pattern in patterns):
            value = first(row, ["url", "image_url", "src", "path", "file_url", "asset_url"])
            if value:
                return value
    return fallback


def escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def card_grid(rows: list[dict[str, str]], lang: str, images: list[dict[str, str]]) -> str:
    cards: list[str] = []
    for row in rows[:12]:
        title = lang_value(row, lang) or first(row, ["title", "name", "label"])
        if not title:
            continue
        description = first(row, [f"description_{lang}", "description", "summary", "body", "details"])
        link = first(row, ["link", "url", "href"], "#")
        image = image_for(images, [title], "")
        image_html = f'<img src="{escape(image)}" alt="{escape(title)}">' if image else ""
        cards.append(
            f"""<article class="card">
              {image_html}
              <div class="card-body">
                <h3><a href="{escape(link)}">{escape(title)}</a></h3>
                {f'<p>{escape(description)}</p>' if description else ''}
              </div>
            </article>"""
        )
    return "\n".join(cards) or '<p class="lead">Content will appear here after the Google Sheet is populated.</p>'


def page_html(lang: str, content_rows: list[dict[str, str]], brand_rows: list[dict[str, str]], image_rows: list[dict[str, str]]) -> str:
    is_zh = lang == "zh"
    title = pick_content(content_rows, ["home", "title"], lang, "艾美加教育顾问" if is_zh else "Animetro Consulting")
    eyebrow = pick_content(content_rows, ["home", "eyebrow"], lang, "艾美加教育顧問" if is_zh else "Animetro Consulting")
    headline = pick_content(content_rows, ["home", "headline"], lang, "面向未來學生的戰略教育諮詢" if is_zh else "Strategic Education Consulting")
    lead = pick_content(
        content_rows,
        ["home", "lead"],
        lang,
        "由 Google Sheet 內容主表自動生成。" if is_zh else "Automatically generated from the Google Sheet content master.",
    )
    primary_cta = pick_content(content_rows, ["primary", "cta"], lang, "預約免費私人諮詢" if is_zh else "Book a Free Private Consultation")
    services_heading = pick_content(content_rows, ["services", "heading"], lang, "核心服務" if is_zh else "Core Services")
    contact_heading = pick_content(content_rows, ["contact", "heading"], lang, "聯絡我們" if is_zh else "Contact Us")
    footer = pick_content(content_rows, ["footer"], lang, "Education Beyond Admission")
    logo = brand_value(brand_rows, ["header logo", "logo"], "/assets/brand/animetro-horizontal.svg")
    hero_image = image_for(image_rows, ["home hero", "hero"], "")
    service_rows = section_rows(content_rows, "service") or content_rows[:6]
    contact_text = pick_content(content_rows, ["contact", "intro"], lang, "consulting@animetro.ca")

    html_lang = "zh-Hant" if is_zh else "en"
    home_href = "/zh/" if is_zh else "/en/"
    other_href = "/en/" if is_zh else "/zh/"
    other_label = "English" if is_zh else "中文"
    nav_services = "服務" if is_zh else "Services"
    nav_contact = "聯絡" if is_zh else "Contact"

    hero_media = f'<img src="{escape(hero_image)}" alt="{escape(headline)}">' if hero_image else ""
    return f"""<!doctype html>
<html lang="{html_lang}">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)}</title>
    <link rel="stylesheet" href="/assets/styles.css">
    <script src="/assets/site.js" defer></script>
  </head>
  <body>
    <header class="site-header">
      <nav class="nav" aria-label="Main navigation">
        <a href="{home_href}"><img class="brand-logo" src="{escape(logo)}" alt="{escape(title)}"></a>
        <div class="nav-links">
          <a class="active" href="{home_href}">{'首頁' if is_zh else 'Home'}</a>
          <a href="#services">{nav_services}</a>
          <a href="#contact">{nav_contact}</a>
          <a href="{other_href}">{other_label}</a>
        </div>
      </nav>
    </header>
    <main class="page">
      <section class="hero">
        <div>
          <p class="eyebrow">{escape(eyebrow)}</p>
          <h1>{escape(headline)}</h1>
          <p class="lead">{escape(lead)}</p>
          <div class="actions">
            <a class="button" href="#contact">{escape(primary_cta)}</a>
            <a class="button secondary" href="#services">{escape(nav_services)}</a>
          </div>
        </div>
        {f'<aside class="panel">{hero_media}</aside>' if hero_media else ''}
      </section>
      <section class="section" id="services">
        <h2>{escape(services_heading)}</h2>
        <div class="grid">
          {card_grid(service_rows, lang, image_rows)}
        </div>
      </section>
      <section class="section panel" id="contact">
        <h2>{escape(contact_heading)}</h2>
        <p class="lead">{escape(contact_text)}</p>
      </section>
    </main>
    <footer class="site-footer">{escape(footer)}</footer>
  </body>
</html>
"""


def write_site(tables: dict[str, SheetTable]) -> None:
    content_rows = tables["websitecontentmaster"].rows
    brand_rows = tables["Brand Identity"].rows
    image_rows = tables["Website Images"].rows

    EN_DIR.mkdir(parents=True, exist_ok=True)
    ZH_DIR.mkdir(parents=True, exist_ok=True)
    (EN_DIR / "index.html").write_text(page_html("en", content_rows, brand_rows, image_rows), encoding="utf-8")
    (ZH_DIR / "index.html").write_text(page_html("zh", content_rows, brand_rows, image_rows), encoding="utf-8")
    (ROOT / "index.html").write_text(
        """<!doctype html>
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
""",
        encoding="utf-8",
    )


def main() -> None:
    spreadsheet_id = env_required("GOOGLE_SHEET_ID")
    service = sheets_service()
    tables: dict[str, SheetTable] = {}
    for tab in REQUIRED_TABS:
        table = fetch_table(service, spreadsheet_id, tab)
        write_table(table)
        tables[tab] = table
    write_site(tables)
    print("Synced Google Sheet content into static site files.")


if __name__ == "__main__":
    main()
