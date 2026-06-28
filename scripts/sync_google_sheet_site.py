#!/usr/bin/env python3
"""Sync Animetro static website files from Google Sheets.

The Google Sheet is the source of truth. This script reads Website-conetent-2
as the primary source of truth for generated website content and assets.

It writes raw tab exports to content/ and regenerates static files.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = ROOT / "content"
EN_DIR = ROOT / "en"
ZH_DIR = ROOT / "zh"

REQUIRED_TABS = {
    "Website-conetent-2": ["Website-conetent-2"],
}
SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
NAV_KEYS = ["home", "services", "about", "resources", "insights", "events", "contact"]
HEADER_LOGO_SRC = ""
FOOTER_LOGO_SRC = ""
FAVICON_SRC = ""
APP_ICON_SRC = ""
SERVICE_PLACEHOLDER_IMAGE = (
    "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%201200%20720'%3E"
    "%3Crect%20width='1200'%20height='720'%20fill='%23f6f0e6'/%3E"
    "%3Crect%20x='60'%20y='60'%20width='1080'%20height='600'%20rx='24'%20fill='%23fffdf8'%20stroke='%23e7ddcf'%20stroke-width='4'/%3E"
    "%3Ctext%20x='600'%20y='360'%20text-anchor='middle'%20font-family='Arial,sans-serif'%20font-size='42'%20fill='%235d6674'%3EService%20image%20pending%3C/text%3E"
    "%3C/svg%3E"
)
LOCAL_ASSET_DIRS = [
    ROOT / "assets" / "images",
    ROOT / "assets" / "images" / "brand",
    ROOT / "assets" / "images" / "contact",
    ROOT / "assets" / "images" / "services",
]

SERVICE_IMAGE_SECTION_IDS = {
    "education strategy & admissions": "education-admissions",
    "education strategy": "education-admissions",
    "prep school admissions": "education-admissions",
    "university admissions": "education-admissions",
    "gpa management / academic skills": "education-admissions",
    "gpa management": "education-admissions",
    "steam pathway / enrichment": "student-development",
    "student development": "student-development",
    "student athlete planning": "student-athlete",
    "student-athlete development": "student-athlete",
    "neurodiversity support": "gifted-neurodiverse",
    "gifted support": "gifted-neurodiverse",
    "gifted & neurodiverse learner support": "gifted-neurodiverse",
    "family & student support": "family-support",
    "mental health support": "family-support",
    "guardianship / student care": "family-support",
}

CORE_SERVICE_IDS = [
    "education-admissions",
    "student-development",
    "student-athlete",
    "gifted-neurodiverse",
    "family-support",
]


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
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"):
        creds, _ = google.auth.default(scopes=SHEETS_SCOPE)
        return creds

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

    header_index = 0
    for index, candidate in enumerate(values):
        normalized_candidate = {clean_key(str(cell)) for cell in candidate}
        if normalized_candidate & {"key", "english_text", "traditional_chinese_text", "service_id", "image_file_name"}:
            header_index = index
            break

    headers = normalize_headers([str(cell).strip() for cell in values[header_index]])
    rows: list[dict[str, str]] = []
    for raw_row in values[header_index + 1:]:
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


def normalize_brand_text(value: str) -> str:
    return value


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
        return normalize_brand_text(default)
    if lang == "en":
        return normalize_brand_text(first(row, ["English Text", "English", "en", "english_content", "content_en", "value_en"], default))
    return normalize_brand_text(first(row, ["Traditional Chinese Text", "Chinese Text", "zh", "Chinese", "traditional_chinese", "zh_hant", "content_zh", "value_zh"], default))


def text(index: dict[str, dict[str, str]], key: str, lang: str, default: str = "") -> str:
    return lang_value(index.get(clean_key(key)), lang, default)


def link_value(index: dict[str, dict[str, str]], key: str, default: str = "") -> str:
    return first(index.get(clean_key(key), {}), ["Web Link", "Link", "url", "href"], default)


def image_value(index: dict[str, dict[str, str]], key: str, default: str = "") -> str:
    return first(index.get(clean_key(key), {}), ["Image File", "Image File ", "image", "image_url", "src", "asset"], default)


def section_id(index: dict[str, dict[str, str]], key: str, default: str = "") -> str:
    return first(index.get(clean_key(key), {}), ["section ID", "section_id", "section id"], default)


def local_service_image(file_name: str) -> tuple[str, bool]:
    if not file_name:
        return SERVICE_PLACEHOLDER_IMAGE, True
    image_url = f"/assets/images/services/{file_name}"
    return image_url, not (ROOT / image_url.lstrip("/")).exists()


def local_asset_from_sheet_filename(file_name: str) -> tuple[str, bool]:
    asset = file_name.strip()
    if not asset:
        return "", False
    if asset.startswith("http://") or asset.startswith("https://"):
        return asset, False
    if asset.startswith("/"):
        return (asset, False) if (ROOT / asset.lstrip("/")).exists() else ("", True)
    for directory in LOCAL_ASSET_DIRS:
        candidate = directory / asset
        if candidate.exists():
            return "/" + str(candidate.relative_to(ROOT)), False
    for candidate in (ROOT / "assets" / "images").rglob(asset):
        if candidate.exists():
            return "/" + str(candidate.relative_to(ROOT)), False
    return "", True


def service_image_row_id(row: dict[str, str]) -> str:
    raw_id = first(row, ["Service ID", "service_id"])
    if raw_id:
        aliases = {
            "service_education_strategy": "education-admissions",
            "service_student_development": "student-development",
            "service_student_athlete": "student-athlete",
            "service_neurodiverse": "gifted-neurodiverse",
            "service_family_support": "family-support",
        }
        return aliases.get(clean_key(raw_id), clean_key(raw_id))
    service_name = first(row, ["Service Name", "service_name", "Website Section", "website_section", "section"])
    return SERVICE_IMAGE_SECTION_IDS.get(service_name.strip().lower(), clean_key(service_name))


def service_id_for_number(number: int) -> str:
    if 1 <= number <= len(CORE_SERVICE_IDS):
        return CORE_SERVICE_IDS[number - 1]
    return f"service-{number}"


def service_image_index(service_rows: list[dict[str, str]], website_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    images: dict[str, dict[str, str]] = {}
    for row in website_rows:
        page = first(row, ["No", "Page", "page", "no"])
        if clean_key(page) != "services":
            continue
        section = first(row, ["Website Section", "website_section", "section"])
        service_id = SERVICE_IMAGE_SECTION_IDS.get(section.strip().lower())
        if not service_id:
            continue
        file_name = first(row, ["image_file_name", "Image File Name", "file_name"])
        image_url, placeholder = local_service_image(file_name)
        if placeholder:
            image_url = SERVICE_PLACEHOLDER_IMAGE
        existing = images.get(service_id)
        if existing and existing.get("is_placeholder") != "true":
            continue
        images[service_id] = {
            "image_url": image_url,
            "image_alt": first(row, ["alt_text", "Alt Text"], section),
            "image_file_name": file_name or "service-image-pending.svg",
            "image_purpose": first(row, ["purpose"], f"Service page visual for {section}"),
            "image_status": first(row, ["status"], "In Progress") or "In Progress",
            "drive_link": first(row, ["google_drive_link", "Google Drive Link"]),
            "recommended_use": first(row, ["recommended_use", "Recommended Use"]),
            "source_section": section,
            "is_placeholder": "true" if placeholder else "false",
        }
    for row in service_rows:
        service_id = service_image_row_id(row)
        if not service_id:
            continue
        file_name = first(row, ["image_file_name", "Image File Name", "file_name"])
        image_url, placeholder = local_service_image(file_name)
        if placeholder:
            existing = images.get(service_id, {})
            image_url = existing.get("image_url", SERVICE_PLACEHOLDER_IMAGE)
            placeholder = image_url == SERVICE_PLACEHOLDER_IMAGE
        service_name = first(row, ["Service Name", "service_name"], service_id)
        images[service_id] = {
            "image_url": image_url,
            "image_alt": first(row, ["alt_text", "Alt Text"], service_name),
            "image_file_name": file_name or images.get(service_id, {}).get("image_file_name", "service-image-pending.svg"),
            "image_purpose": first(row, ["image_purpose", "Image Type", "Recommended Image Concept"], f"Service page visual for {service_name}"),
            "image_status": first(row, ["status"], "In Progress") or "In Progress",
            "drive_link": first(row, ["image_url_google_drive_link", "Image URL / Google Drive Link", "google_drive_link", "Google Drive Link"]),
            "recommended_use": first(row, ["website_placement", "Website Placement", "recommended_use", "Recommended Use"]),
            "source_section": service_name,
            "is_placeholder": "true" if placeholder else "false",
        }
    return images


def website_content_service_image_index(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    index = build_key_index(rows)
    images: dict[str, dict[str, str]] = {}
    for number, service_id in enumerate(CORE_SERVICE_IDS, start=1):
        key = f"service_{number}_title"
        title = text(index, key, "en", service_id)
        raw_file = link_value(index, key) or image_value(index, key)
        image_url, missing = local_asset_from_sheet_filename(raw_file)
        if not raw_file or missing or not image_url:
            continue
        images[service_id] = {
            "image_url": image_url,
            "image_alt": title,
            "image_file_name": raw_file,
            "image_purpose": f"Website-conetent-2 image for {title}",
            "image_status": status_value(index.get(clean_key(key), {})) or "In Progress",
            "drive_link": "",
            "recommended_use": "Homepage/service card",
            "source_section": title,
            "is_placeholder": "false",
        }
    return images


def service_image_figure(service_id: str, title: str, image: dict[str, str] | None) -> str:
    if not image or image.get("is_placeholder") == "true":
        return ""
    return f'''            <figure class="service-image" data-image-file-name="{escape(image.get("image_file_name", ""))}" data-image-url="{escape(image.get("image_url", ""))}" data-image-alt="{escape(image.get("image_alt", title))}" data-image-purpose="{escape(image.get("image_purpose", ""))}" data-image-status="{escape(image.get("image_status", "In Progress"))}" data-drive-link="{escape(image.get("drive_link", ""))}" data-recommended-use="{escape(image.get("recommended_use", ""))}" data-placeholder="{escape(image.get("is_placeholder", "false"))}">
              <img src="{escape(image.get("image_url", SERVICE_PLACEHOLDER_IMAGE))}" alt="{escape(image.get("image_alt", title))}">
            </figure>'''


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
    brand_label = "艾美加教育顧問" if lang == "zh" else "Animetro Consulting"
    def href_for(nav_key: str) -> str:
        default = home if nav_key == "home" else f"{home}{nav_key}/"
        raw = link_value(index, f"nav_{nav_key}")
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        if raw.startswith("/") and not Path(raw).suffix:
            if raw in {"/", "/en", "/en/", "/zh", "/zh/"}:
                return home if nav_key == "home" else raw
            return f"/{lang}{raw.rstrip('/')}/"
        return default

    links = []
    for nav_key in NAV_KEYS:
        label = text(index, f"nav_{nav_key}", lang, nav_key.title())
        href = href_for(nav_key)
        cls = ' class="active"' if active == nav_key else ""
        links.append(f'<a{cls} href="{href}">{escape(label)}</a>')
    links.append(f'<a class="lang-link" href="{other}" lang="zh-Hant">{escape(other_label)}</a>')
    start_label = text(index, "nav_start_here", lang, "從這裡開始" if lang == "zh" else "Start Here")
    links.append(f'<a class="nav-cta" href="/{lang}/contact/">{escape(start_label)}</a>')
    nav_links_html = "\n          ".join(links)
    brand_html = brand_mark_html(index, lang, "header")
    return f'''    <header class="site-header">
      <nav class="nav" aria-label="Main navigation">
        <a class="brand brand-text" href="{home}" aria-label="{escape(logo_alt)} home">{brand_html or escape(brand_label)}</a>
        <button class="nav-toggle" type="button" aria-expanded="false" aria-controls="primary-navigation">
          <span class="nav-toggle-bar"></span>
          <span class="nav-toggle-bar"></span>
          <span class="nav-toggle-bar"></span>
          <span class="sr-only">Menu</span>
        </button>
        <div class="nav-links" id="primary-navigation">
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
        "finallogo0617.png": "",
        "animetrowebsite_header0617.png": "",
        "animetro-header-logo-light-2026.png": "/assets/images/brand/animetro-header-logo-light-2026.png",
        "animetro_transparentlogo_final.png": "/assets/images/brand/animetro_transparentlogo_final.png",
        "animetro-favicon-logo-2026.png": "",
        "animetro-app-icon-logo-2026.png": "",
        "wechat-qr.jpg": "/assets/images/contact/wechat-qr.jpeg",
        "wechat-qr.jpeg": "/assets/images/contact/wechat-qr.jpeg",
        "whatsapp-qr.jpg": "/assets/images/contact/whatsapp-qr.jpeg",
        "whatsapp-qr.jpeg": "/assets/images/contact/whatsapp-qr.jpeg",
    }
    return aliases.get(asset.lower(), f"/assets/images/{asset}")


def asset_exists_or_remote(src: str) -> bool:
    if src.startswith("https://") or src.startswith("http://"):
        return True
    return bool(src.startswith("/") and (ROOT / src.lstrip("/")).exists())


def first_existing_asset(candidates: list[str]) -> str:
    for candidate in candidates:
        src = sheet_asset_path(candidate, "")
        if src and asset_exists_or_remote(src):
            return src
    return ""


def configure_approved_brand_assets(logo_rows: list[dict[str, str]]) -> None:
    global HEADER_LOGO_SRC, FOOTER_LOGO_SRC, FAVICON_SRC, APP_ICON_SRC
    header_candidates: list[str] = []
    footer_candidates: list[str] = []
    favicon_candidates: list[str] = []
    app_candidates: list[str] = []
    fallback_candidates: list[str] = []

    for row in logo_rows:
        if not is_approved_status(first(row, ["status", "Status"])):
            continue
        purpose = first(row, ["purpose", "Purpose"]).lower()
        asset_name = first(row, ["asset_name", "Asset Name"]).lower()
        values = [first(row, ["link", "Link"]), first(row, ["file_name", "File Name"])]
        if "header" in purpose or "header" in asset_name:
            header_candidates.extend(values)
        if "footer" in purpose or "dark" in asset_name:
            footer_candidates.extend(values)
        if "favicon" in purpose or "favicon" in asset_name:
            favicon_candidates.extend(values)
        if "app" in purpose or "app" in asset_name:
            app_candidates.extend(values)
        if "official company logo" in purpose or "master logo" in asset_name:
            fallback_candidates.extend(values)

    HEADER_LOGO_SRC = first_existing_asset(header_candidates + fallback_candidates)
    FOOTER_LOGO_SRC = first_existing_asset(footer_candidates + fallback_candidates)
    FAVICON_SRC = first_existing_asset(favicon_candidates)
    APP_ICON_SRC = first_existing_asset(app_candidates)


def approved_logo_asset(index: dict[str, dict[str, str]], placement: str) -> str:
    if placement == "header":
        candidates = [link_value(index, "header_logo"), image_value(index, "header_logo"), HEADER_LOGO_SRC]
    elif placement == "footer":
        sheet_candidates = [link_value(index, "footer_logo"), image_value(index, "footer_logo")]
        if any(sheet_candidates):
            candidates = sheet_candidates
        else:
            candidates = [FOOTER_LOGO_SRC]
    else:
        candidates = []
    for candidate in candidates:
        src = sheet_asset_path(candidate, "")
        if src and asset_exists_or_remote(src):
            return src
    return ""


def brand_mark_html(index: dict[str, dict[str, str]], lang: str, placement: str) -> str:
    src = approved_logo_asset(index, placement)
    if not src:
        return ""
    alt = text(index, f"{placement}_logo", lang, "艾美加教育顧問" if lang == "zh" else "Animetro Consulting")
    return f'<img class="brand-logo" src="{escape(src)}" alt="{escape(alt)}">'


def is_approved_status(value: str) -> bool:
    return clean_key(value) == "approved"


def is_usable_image_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def local_home_image_url(file_name: str) -> str:
    asset = file_name.strip()
    if not asset:
        return ""
    aliases = {
        "animetro-education-strategy-consulting.png": "/assets/images/services/educationstratgyconsulting.jpeg",
    }
    if asset.lower() in aliases and (ROOT / aliases[asset.lower()].lstrip("/")).exists():
        return aliases[asset.lower()]
    if asset.startswith("/"):
        return asset if (ROOT / asset.lstrip("/")).exists() else ""
    image_url = f"/assets/images/{asset}"
    return image_url if (ROOT / image_url.lstrip("/")).exists() else ""


def approved_home_hero_image(website_rows: list[dict[str, str]], lang: str) -> dict[str, str] | None:
    for row in website_rows:
        section = first(row, ["website_section", "Website Section", "section"])
        category = first(row, ["image_category", "Image Category"])
        recommended = first(row, ["recommended_use", "Recommended Use"])
        home_candidate = (
            "home hero" in section.lower()
            or "hero" in category.lower()
            or section.lower().startswith("home /")
            or recommended.lower().startswith("home page")
        )
        if not home_candidate:
            continue
        if not is_approved_status(first(row, ["status", "Status"])):
            continue
        raw_url = first(row, ["google_drive_link", "Google Drive Link", "image_url", "Image URL"])
        file_name = first(row, ["image_file_name", "Image File Name", "file_name"])
        image_url = raw_url if is_usable_image_url(raw_url) else local_home_image_url(raw_url or file_name)
        if not image_url:
            continue
        alt = first(row, ["alt_text", "Alt Text"], "校園教育環境" if lang == "zh" else "Campus education environment")
        return {"src": image_url, "alt": alt, "file_name": file_name or raw_url}
    return None


def hero_media_html(website_rows: list[dict[str, str]], lang: str) -> str:
    image = approved_home_hero_image(website_rows, lang)
    if not image:
        return ""
    label = "首頁主視覺圖片" if lang == "zh" else "Homepage hero image"
    return f'''        <aside class="hero-media" aria-label="{escape(label)}" data-image-file-name="{escape(image["file_name"])}">
          <img src="{escape(image["src"])}" alt="{escape(image["alt"])}">
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
    nav_label = "網站導覽" if is_zh else "Explore"
    qr_label = "二维码" if is_zh else "QR Codes"
    get_started = "Start Here"
    qr_wechat = text(index, "footer_wechat_label", lang, "掃碼添加微信" if is_zh else "Scan to connect on WeChat")
    qr_whatsapp = text(index, "footer_whatsapp_label", lang, "掃碼聯絡 WhatsApp" if is_zh else "Scan to connect on WhatsApp")
    whatsapp_src = sheet_asset_path(
        link_value(index, "footer_whatsapp_qr") or image_value(index, "footer_whatsapp_qr"),
        "/assets/images/contact/whatsapp-qr.jpeg",
    )
    wechat_src = sheet_asset_path(
        link_value(index, "footer_wechat_qr") or image_value(index, "footer_wechat_qr"),
        "/assets/images/contact/wechat-qr.jpeg",
    )
    base = "/zh" if is_zh else "/en"
    nav_links = []
    for nav_key in NAV_KEYS:
        label = text(index, f"nav_{nav_key}", lang, nav_key.title())
        href = base + "/" if nav_key == "home" else f"{base}/{nav_key}/"
        nav_links.append(f'<a href="{href}">{escape(label)}</a>')
    nav_links_html = "\n          ".join(nav_links)
    footer_brand = brand_mark_html(index, lang, "footer")
    footer_brand_html = footer_brand or f'<span>{escape(logo_alt)}<small>{escape(tagline)}</small></span>'
    return f'''    <footer class="site-footer">
      <div class="footer-inner">
        <div class="footer-brand">{footer_brand_html}</div>
        <div class="footer-contact" aria-label="{escape(contact_label)}">
          <p class="footer-column-title">{escape(contact_label)}</p>
          <a href="tel:+19059557068">{escape(phone)}</a>
          <a href="mailto:{escape(email)}">{escape(email)}</a>
          <a href="{escape(website_href)}">{escape(website)}</a>
        </div>
        <nav class="footer-services" aria-label="{escape(nav_label)}">
          <p class="footer-column-title">{escape(nav_label)}</p>
          {nav_links_html}
        </nav>
        <div class="footer-actions">
          <p class="footer-column-title">{escape(qr_label)}</p>
          <a class="footer-cta" href="/start-here">{escape(get_started)}</a>
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
    favicon = f'\n    <link rel="icon" href="{escape(FAVICON_SRC)}">' if FAVICON_SRC else ""
    app_icon = f'\n    <link rel="apple-touch-icon" href="{escape(APP_ICON_SRC)}">' if APP_ICON_SRC else ""
    head_assets = f"{favicon}{app_icon}"
    return f'''<!doctype html>
<html lang="{html_lang}">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(page_title(lang, title_suffix))}</title>{desc}{head_assets}
    <link rel="stylesheet" href="{rel_css(depth)}">
  </head>
  <body>
{nav_html(index, lang, active, depth)}

{body}

{footer_html(index, lang)}
    <script src="/assets/navigation.js"></script>
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
            sid = service_id_for_number(number) if prefix == "service" else section_id(index, title_key, "")
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


def homepage_html(index: dict[str, dict[str, str]], website_image_rows: list[dict[str, str]], lang: str) -> str:
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
    hero_media = ""
    hero_class = "hero hero-with-media" if hero_media else "hero hero-simple"
    founder_section = ""
    if founder_title or founder_subtitle or founder_bio or founder_highlights or founder_cta:
        founder_section = f'''
      <section class="section panel" id="meet-emily">
        {f'<p class="eyebrow">{escape(founder_subtitle)}</p>' if founder_subtitle else ''}
        {f'<h2>{escape(founder_title)}</h2>' if founder_title else ''}
        {f'<p class="lead">{escape(founder_bio)}</p>' if founder_bio else ''}
        {f'<ul class="check-list">{"".join(f"<li>{escape(item)}</li>" for item in founder_highlights)}</ul>' if founder_highlights else ''}
        {f'<div class="actions"><a class="button secondary" href="/{lang}/about/">{escape(founder_cta)}</a></div>' if founder_cta else ''}
      </section>'''

    body = f'''    <main>
      <section class="{hero_class}">
        <div>
          <h1>{escape(hero_title)}</h1>
          {f'<p class="lead">{escape(hero_subtitle)}</p>' if hero_subtitle else ''}
          {f'<p>{escape(hero_description)}</p>' if hero_description else ''}
          <div class="actions">
            <a class="button" href="/{lang}/contact/">{escape(primary_cta)}</a>
            <a class="button secondary" href="/{lang}/services/">{escape(secondary_cta)}</a>
          </div>
        </div>
{hero_media}
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

{founder_section}

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


def services_for_sheet(
    rows: list[dict[str, str]],
    lang: str,
    images: dict[str, dict[str, str]],
) -> list[dict[str, str | list[str] | dict[str, str]]]:
    index = build_key_index(rows)
    core_services: list[dict[str, str | list[str] | dict[str, str]]] = []
    for number, sid in enumerate(CORE_SERVICE_IDS, start=1):
        title = text(index, f"service_{number}_title", lang)
        desc = text(index, f"service_{number}_desc", lang)
        if title:
            core_services.append({"id": sid, "title": title, "desc": desc, "items": [], "image": images.get(sid, {})})
    if core_services:
        return core_services

    categories: dict[str, dict[str, str | list[str] | dict[str, str]]] = {}
    for row in approved_content_rows(rows):
        content_type = clean_key(first(row, ["Content Type", "content_type"]))
        key = clean_key(row_key(row))
        sid = first(row, ["section ID", "section_id", "section id"], clean_key(first(row, ["Section"], key)))
        if not sid:
            continue
        category = categories.setdefault(
            sid,
            {"id": sid, "title": "", "desc": "", "items": [], "image": images.get(sid, {})},
        )
        if content_type == "category_title":
            category["title"] = lang_value(row, lang)
        elif content_type == "category_description":
            category["desc"] = lang_value(row, lang)
        elif content_type == "subservice" or "_item_" in key:
            items = category["items"]
            if isinstance(items, list):
                items.append(lang_value(row, lang))
    items = [
        category
        for category in categories.values()
        if str(category.get("title", "")).strip()
    ]
    for service_id, image in images.items():
        if any(item.get("id") == service_id for item in items):
            continue
        if image.get("source_section"):
            items.append({"id": service_id, "title": image["source_section"], "desc": "", "items": [], "image": image})
    return items


def services_page_html(
    index: dict[str, dict[str, str]],
    services_rows: list[dict[str, str]],
    service_image_rows: list[dict[str, str]],
    website_image_rows: list[dict[str, str]],
    lang: str,
) -> str:
    is_zh = lang == "zh"
    images = website_content_service_image_index(services_rows)
    hero_title = text(index, "services_hero_title", lang, text(index, "services_title", lang, "服務" if is_zh else "Services"))
    hero_subtitle = text(index, "services_hero_subtitle", lang, text(index, "services_intro", lang, ""))
    hero_cta = text(index, "services_hero_cta", lang, "預約諮詢" if is_zh else "Book a Consultation")
    cta_title = text(index, "services_cta_title", lang, text(index, "cta_title", lang))
    cta_subtitle = text(index, "services_cta_subtitle", lang, text(index, "cta_subtitle", lang))
    cta_button = text(index, "services_cta_button", lang, text(index, "cta_button", lang, hero_cta))
    service_articles = []
    for service in services_for_sheet(services_rows, lang, images):
        sid = str(service["id"])
        title = str(service["title"])
        desc = str(service["desc"])
        image = service["image"] if isinstance(service["image"], dict) else {}
        subitems = service["items"] if isinstance(service["items"], list) else []
        subitems_html = "".join(f"<li>{escape(str(item))}</li>" for item in subitems if item)
        service_articles.append(f'''          <article class="service-detail" id="{escape(sid)}">
            <h2>{escape(title)}</h2>
{service_image_figure(sid, title, image)}
            {f'<p>{escape(desc)}</p>' if desc else ''}
            {f'<ul class="check-list">{subitems_html}</ul>' if subitems_html else ''}
          </article>''')
    consultation_section = ""
    if cta_title or cta_subtitle or cta_button:
        consultation_section = f'''
      <section class="section panel" id="consultation">
        {f'<h2>{escape(cta_title)}</h2>' if cta_title else ''}
        {f'<p class="lead">{escape(cta_subtitle)}</p>' if cta_subtitle else ''}
        <div class="actions"><a class="button" href="/{lang}/contact/">{escape(cta_button)}</a></div>
      </section>'''
    body = f'''    <main>
      <section class="page-hero">
        <p class="eyebrow">{escape(text(index, 'nav_services', lang, '服務' if is_zh else 'Services'))}</p>
        <h1>{escape(hero_title)}</h1>
        {f'<p class="lead">{escape(hero_subtitle)}</p>' if hero_subtitle else ''}
        <div class="actions"><a class="button" href="/{lang}/contact/">{escape(hero_cta)}</a></div>
      </section>

      <section class="section">
        <div class="service-list" data-content-source="Google Sheet: Website-conetent-2">
{chr(10).join(service_articles)}
        </div>
      </section>

{consultation_section}
    </main>'''
    return page_shell(index, lang, "services", text(index, "nav_services", lang, "服務" if is_zh else "Services"), 2, body, hero_subtitle)


def write_site(tables: dict[str, SheetTable]) -> None:
    configure_approved_brand_assets(tables.get("Logo Package", SheetTable("Logo Package", [], [])).rows)
    content_rows = tables["Website-conetent-2"].rows
    home_rows = content_rows
    services_rows = content_rows
    service_page_rows = content_rows
    website_image_rows: list[dict[str, str]] = []
    service_image_rows: list[dict[str, str]] = []
    home_index = build_key_index(home_rows)
    services_index = build_key_index(service_page_rows)
    EN_DIR.mkdir(parents=True, exist_ok=True)
    ZH_DIR.mkdir(parents=True, exist_ok=True)
    (EN_DIR / "services").mkdir(parents=True, exist_ok=True)
    (ZH_DIR / "services").mkdir(parents=True, exist_ok=True)
    (EN_DIR / "index.html").write_text(homepage_html(home_index, website_image_rows, "en"), encoding="utf-8")
    (ZH_DIR / "index.html").write_text(homepage_html(home_index, website_image_rows, "zh"), encoding="utf-8")
    (EN_DIR / "services" / "index.html").write_text(
        services_page_html(services_index, services_rows, service_image_rows, website_image_rows, "en"),
        encoding="utf-8",
    )
    (ZH_DIR / "services" / "index.html").write_text(
        services_page_html(services_index, services_rows, service_image_rows, website_image_rows, "zh"),
        encoding="utf-8",
    )
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
