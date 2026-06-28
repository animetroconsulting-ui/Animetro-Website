#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = ROOT / "content"
CONTENT_EXPORT = CONTENT_DIR / "website_conetent_2.json"

FORBIDDEN_REFERENCES = [
    "animetrowebherobanner0617",
    "hero-consulting-2",
    "hero-consulting-3",
    "animetro-philosophy",
]

PAGES = [
    ROOT / "index.html",
    ROOT / "insights" / "index.html",
    ROOT / "en" / "index.html",
    ROOT / "zh" / "index.html",
    ROOT / "en" / "services" / "index.html",
    ROOT / "zh" / "services" / "index.html",
    ROOT / "en" / "insights" / "index.html",
    ROOT / "zh" / "insights" / "index.html",
]

EXPECTED_SERVICE_KEYS = [
    "service_1_title",
    "service_2_title",
    "service_3_title",
    "service_4_title",
    "service_5_title",
]


class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.h3: list[str] = []
        self.leads: list[str] = []
        self.images: list[dict[str, str]] = []
        self.service_source = ""
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag in {"h1", "h2", "h3"}:
            self._capture = tag
            self._buffer = []
        elif tag == "p" and "lead" in attr.get("class", "").split():
            self._capture = "lead"
            self._buffer = []
        elif tag == "img":
            self.images.append(attr)
        elif tag == "div" and "service-list" in attr.get("class", "").split():
            self.service_source = attr.get("data-content-source", "")

    def handle_endtag(self, tag: str) -> None:
        if self._capture == tag:
            getattr(self, tag).append(normalize("".join(self._buffer)))
            self._capture = None
        elif tag == "p" and self._capture == "lead":
            self.leads.append(normalize("".join(self._buffer)))
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def clean_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def first(row: dict[str, str], names: list[str], default: str = "") -> str:
    for name in names:
        value = row.get(clean_key(name), "").strip()
        if value:
            return value
    return default


def rows() -> list[dict[str, str]]:
    if not CONTENT_EXPORT.exists():
        fail("Missing Website-conetent-2 export: content/website_conetent_2.json")
    return json.loads(CONTENT_EXPORT.read_text(encoding="utf-8"))


def approved_rows() -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows():
        key = first(row, ["Key"])
        if not key:
            continue
        status = clean_key(first(row, ["Status"]))
        if status and status not in {"approved", "confirmed", "approve"}:
            continue
        output.append(row)
    return output


def key_index() -> dict[str, dict[str, str]]:
    return {clean_key(first(row, ["Key"])): row for row in approved_rows()}


def sheet_text(index: dict[str, dict[str, str]], key: str, lang: str) -> str:
    row = index.get(clean_key(key), {})
    if lang == "zh":
        return first(row, ["Traditional Chinese Text", "Chinese Text", "zh"])
    return first(row, ["English Text", "English", "en"])


def sheet_link(index: dict[str, dict[str, str]], key: str) -> str:
    return first(index.get(clean_key(key), {}), ["Web Link", "Link", "Image File"])


def parse(path: Path) -> Parser:
    parser = Parser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def expected_asset_path(file_name: str) -> str:
    aliases = {
        "animetro-header-logo-light-2026.png": "/assets/images/brand/animetro-header-logo-light-2026.png",
        "animetro_transparentlogo_final.png": "/assets/images/brand/animetro_transparentlogo_final.png",
        "wechat-qr.jpg": "/assets/images/contact/wechat-qr.jpeg",
        "whatsapp-qr.jpg": "/assets/images/contact/whatsapp-qr.jpeg",
    }
    return aliases.get(file_name, f"/assets/images/{file_name}")


def is_remote(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def verify_referenced_images(path: Path) -> None:
    parser = parse(path)
    for image in parser.images:
        src = image.get("src", "")
        if not src:
            fail(f"{path.relative_to(ROOT)} has an image without src")
        if src.startswith("data:") or is_remote(src):
            continue
        if not (ROOT / src.lstrip("/")).exists():
            fail(f"{path.relative_to(ROOT)} references missing image file: {src}")
        if not image.get("alt"):
            fail(f"{path.relative_to(ROOT)} image is missing alt text: {src}")


def verify_no_empty_content(path: Path) -> None:
    page = text(path)
    for pattern, label in [
        (r"<h[1-6][^>]*>\s*</h[1-6]>", "empty heading"),
        (r"<p[^>]*>\s*</p>", "empty paragraph"),
        (r"<section[^>]*>\s*</section>", "blank section"),
    ]:
        if re.search(pattern, page):
            fail(f"{path.relative_to(ROOT)} contains {label}")


def verify_no_forbidden_references(path: Path) -> None:
    page = text(path)
    for forbidden in FORBIDDEN_REFERENCES:
        if forbidden in page:
            fail(f"{path.relative_to(ROOT)} references forbidden generated asset: {forbidden}")


def verify_logo(path: Path, index: dict[str, dict[str, str]]) -> None:
    page = text(path)
    header_logo = expected_asset_path(sheet_link(index, "header_logo"))
    footer_logo = expected_asset_path(sheet_link(index, "footer_logo"))
    if not header_logo or header_logo not in page:
        fail(f"{path.relative_to(ROOT)} does not render Website-conetent-2 header logo: {header_logo}")
    if not (ROOT / header_logo.lstrip("/")).exists():
        fail(f"{path.relative_to(ROOT)} header logo file is missing: {header_logo}")
    footer_logo_path = ROOT / footer_logo.lstrip("/")
    if footer_logo_path.exists():
        if footer_logo not in page:
            fail(f"{path.relative_to(ROOT)} does not render Website-conetent-2 footer logo: {footer_logo}")
    elif footer_logo in page:
        fail(f"{path.relative_to(ROOT)} renders missing Website-conetent-2 footer logo: {footer_logo}")


def verify_footer_qr_mapping(path: Path, index: dict[str, dict[str, str]]) -> None:
    page = text(path)
    wechat_src = expected_asset_path(sheet_link(index, "footer_wechat_qr"))
    whatsapp_src = expected_asset_path(sheet_link(index, "footer_whatsapp_qr"))
    if wechat_src == whatsapp_src:
        fail("Website-conetent-2 maps WeChat and WhatsApp QR codes to the same file")
    for label, src in [("WeChat", wechat_src), ("WhatsApp", whatsapp_src)]:
        if src not in page:
            fail(f"{path.relative_to(ROOT)} missing {label} QR image from Website-conetent-2: {src}")
        if not (ROOT / src.lstrip("/")).exists():
            fail(f"{path.relative_to(ROOT)} {label} QR file is missing: {src}")


def verify_home(lang: str, index: dict[str, dict[str, str]]) -> None:
    path = ROOT / lang / "index.html"
    parser = parse(path)
    page = text(path)
    expected_title = sheet_text(index, "hero_title", lang)
    expected_subtitle = sheet_text(index, "hero_subtitle", lang)
    expected_description = sheet_text(index, "hero_description", lang)
    if not parser.h1 or parser.h1[0] != expected_title:
        fail(f"{lang}/index.html hero title does not match Website-conetent-2")
    if expected_subtitle not in parser.leads:
        fail(f"{lang}/index.html hero subtitle does not match Website-conetent-2")
    if expected_description not in normalize(page):
        fail(f"{lang}/index.html hero description does not match Website-conetent-2")
    for key in ["hero_primary_cta", "hero_secondary_cta"]:
        value = sheet_text(index, key, lang)
        if value and value not in normalize(page):
            fail(f"{lang}/index.html missing Website-conetent-2 {key}: {value}")
    service_titles = [sheet_text(index, key, lang) for key in EXPECTED_SERVICE_KEYS]
    h3_text = set(parser.h3)
    for title in service_titles:
        if title not in h3_text:
            fail(f"{lang}/index.html missing core service from Website-conetent-2: {title}")
    if len([title for title in service_titles if title in h3_text]) != 5:
        fail(f"{lang}/index.html does not render exactly five Website-conetent-2 core services")


def verify_services(lang: str, index: dict[str, dict[str, str]]) -> None:
    path = ROOT / lang / "services" / "index.html"
    parser = parse(path)
    if parser.service_source != "Google Sheet: Website-conetent-2":
        fail(f"{lang}/services/index.html is not generated from Website-conetent-2")
    service_titles = [sheet_text(index, key, lang) for key in EXPECTED_SERVICE_KEYS]
    h2_text = set(parser.h2)
    for title in service_titles:
        if title not in h2_text:
            fail(f"{lang}/services/index.html missing service from Website-conetent-2: {title}")


def verify_nav_insights(lang: str, index: dict[str, dict[str, str]]) -> None:
    expected_label = sheet_text(index, "nav_insights", lang)
    expected_href = f'/{lang}/insights/'
    for slug in ["", "services", "about", "resources", "insights", "events", "contact"]:
        path = ROOT / lang / slug / "index.html" if slug else ROOT / lang / "index.html"
        if not path.exists():
            continue
        page = text(path)
        if expected_label and expected_label not in normalize(page):
            fail(f"{path.relative_to(ROOT)} missing Website-conetent-2 nav_insights label: {expected_label}")
        if f'href="{expected_href}"' not in page:
            fail(f"{path.relative_to(ROOT)} nav_insights does not point to {expected_href}")


def verify_insights(lang: str, index: dict[str, dict[str, str]]) -> None:
    path = ROOT / lang / "insights" / "index.html"
    parser = parse(path)
    page = normalize(text(path))
    expected_title = sheet_text(index, "insights_title", lang)
    expected_subtitle = sheet_text(index, "insights_subtitle", lang)
    expected_description = sheet_text(index, "insights_description", lang)
    if not parser.h1 or parser.h1[0] != expected_title:
        fail(f"{lang}/insights/index.html title does not match Website-conetent-2")
    if expected_subtitle not in parser.leads:
        fail(f"{lang}/insights/index.html subtitle does not match Website-conetent-2")
    if expected_description not in page:
        fail(f"{lang}/insights/index.html description does not match Website-conetent-2")
    for key in [
        "insights_school_pathways",
        "insights_university",
        "insights_student_growth",
        "insights_neurodiversity",
        "insights_student_athlete",
        "insights_parent_strategy",
        "insights_cta_title",
        "insights_cta_description",
        "insights_cta_button",
    ]:
        value = sheet_text(index, key, lang)
        if value and value not in page:
            fail(f"{lang}/insights/index.html missing Website-conetent-2 {key}: {value}")
    for number in range(1, 7):
        for suffix in ["title", "summary"]:
            key = f"insight_{number}_{suffix}"
            value = sheet_text(index, key, lang)
            if value and value not in page:
                fail(f"{lang}/insights/index.html missing Website-conetent-2 {key}: {value}")


def verify_sheet_image_rows(index: dict[str, dict[str, str]]) -> None:
    for key in EXPECTED_SERVICE_KEYS:
        raw = sheet_link(index, key)
        if not raw:
            continue
        candidate = expected_asset_path(raw)
        if not (ROOT / candidate.lstrip("/")).exists():
            # Missing image rows are allowed to be reported, but must not render.
            for path in PAGES:
                if raw in text(path) or candidate in text(path):
                    fail(f"{path.relative_to(ROOT)} renders missing Website-conetent-2 image: {raw}")


def main() -> None:
    index = key_index()
    for path in PAGES:
        if not path.exists():
            fail(f"Missing generated file: {path.relative_to(ROOT)}")
        verify_no_forbidden_references(path)
        verify_no_empty_content(path)
        verify_referenced_images(path)
        if path.name == "index.html" and path.parent.name in {"en", "zh"} or path.parent.name == "services":
            verify_logo(path, index)
            verify_footer_qr_mapping(path, index)
    verify_home("en", index)
    verify_home("zh", index)
    verify_services("en", index)
    verify_services("zh", index)
    verify_insights("en", index)
    verify_insights("zh", index)
    verify_nav_insights("en", index)
    verify_nav_insights("zh", index)
    verify_sheet_image_rows(index)
    print("Static site verification passed.")


if __name__ == "__main__":
    main()
