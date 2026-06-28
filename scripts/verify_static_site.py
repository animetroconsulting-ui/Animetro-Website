#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = ROOT / "content"
FORBIDDEN_REFERENCES = [
    "animetrowebherobanner0617",
    "hero-consulting-2",
    "hero-consulting-3",
    "animetro-philosophy",
]
BRAND_REFERENCE_HINTS = (
    "logo",
    "favicon",
    "app-icon",
    "brand",
    "business-card",
    "mockup",
)

EXPECTED = {
    "en": {
        "title": "Growth Beyond Admissions",
        "lead": "Personalized education pathway planning for students and families — from school admissions and university applications to academic strategy, STEAM development, student-athlete planning, and support for gifted, high-potential, and neurodiverse learners.",
    },
    "zh": {
        "title": "成長超越升學",
        "lead": "為學生與家庭提供個性化教育路徑規劃，涵蓋學校申請、大學申請、學術策略、STEAM 發展、學生運動員規劃，以及高智商、高潛能與多元神經譜系學生支持。",
    },
}


class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1: list[str] = []
        self.leads: list[str] = []
        self.service_source = False
        self.service_figures: list[dict[str, str]] = []
        self.hero_media: list[dict[str, str]] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._figure: dict[str, str] | None = None
        self._hero_media: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag == "h1":
            self._capture = "h1"
            self._buffer = []
        elif tag == "p" and "lead" in attr.get("class", "").split():
            self._capture = "lead"
            self._buffer = []
        elif tag == "div" and "service-list" in attr.get("class", "").split():
            self.service_source = "Google Sheet" in attr.get("data-content-source", "")
        elif tag == "figure" and "service-image" in attr.get("class", "").split():
            self._figure = attr
        elif tag == "aside" and "hero-media" in attr.get("class", "").split():
            self._hero_media = attr
        elif tag == "img" and self._figure is not None:
            self._figure["img_src"] = attr.get("src", "")
            self._figure["img_alt"] = attr.get("alt", "")
        elif tag == "img" and self._hero_media is not None:
            self._hero_media["img_src"] = attr.get("src", "")
            self._hero_media["img_alt"] = attr.get("alt", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._capture == "h1":
            self.h1.append(normalize("".join(self._buffer)))
            self._capture = None
        elif tag == "p" and self._capture == "lead":
            self.leads.append(normalize("".join(self._buffer)))
            self._capture = None
        elif tag == "figure" and self._figure is not None:
            self.service_figures.append(self._figure)
            self._figure = None
        elif tag == "aside" and self._hero_media is not None:
            self.hero_media.append(self._hero_media)
            self._hero_media = None

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse(path: Path) -> Parser:
    parser = Parser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def clean_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def first(row: dict[str, str], names: list[str], default: str = "") -> str:
    for name in names:
        value = row.get(clean_key(name), "").strip()
        if value:
            return value
    return default


def load_rows(name: str) -> list[dict[str, str]]:
    path = CONTENT_DIR / name
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def approved_logo_filenames() -> set[str]:
    approved: set[str] = set()
    for row in load_rows("websitecontentmaster.json"):
        status = clean_key(first(row, ["Status"]))
        content_type = clean_key(first(row, ["Content Type"]))
        key = clean_key(first(row, ["Key"]))
        if status == "approved" and content_type == "logo" and key in {"header_logo", "footer_logo"}:
            for field in ["Web Link", "Link", "Image File"]:
                value = first(row, [field])
                if value and "." in Path(value).name:
                    approved.add(Path(value).name)
    for row in load_rows("logo_package.json"):
        status = clean_key(first(row, ["Status"]))
        if status == "approved":
            for field in ["File Name", "Link"]:
                value = first(row, [field])
                if value and "." in Path(value).name:
                    approved.add(Path(value).name)
    for row in load_rows("brand_identity.json"):
        status = clean_key(first(row, ["Status"]))
        category = clean_key(first(row, ["Category", "Logo ID"]))
        if status == "approved" and ("logo" in category or first(row, ["links"])):
            for field in ["links", "filename"]:
                value = first(row, [field])
                if value and "." in Path(value).name:
                    approved.add(Path(value).name)
    for row in load_rows("website_images.json"):
        category = clean_key(first(row, ["Image Category", "Website Section", "Purpose"]))
        if "logo" in category or "favicon" in category or "brand" in category:
            for field in ["Image File Name", "Google Drive Link"]:
                value = first(row, [field])
                if value and "." in Path(value).name:
                    approved.add(Path(value).name)
    return approved


def website_content_logo_filenames() -> set[str]:
    approved: set[str] = set()
    for row in load_rows("websitecontentmaster.json"):
        status = clean_key(first(row, ["Status"]))
        content_type = clean_key(first(row, ["Content Type"]))
        key = clean_key(first(row, ["Key"]))
        if status == "approved" and content_type == "logo" and key in {"header_logo", "footer_logo"}:
            for field in ["Web Link", "Link", "Image File"]:
                value = first(row, [field])
                if value and "." in Path(value).name:
                    approved.add(Path(value).name)
    return approved


def is_usable_remote_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def approved_home_hero_sources() -> set[str]:
    approved: set[str] = set()
    local_aliases = {
        "animetro-education-strategy-consulting.png": "/assets/images/services/educationstratgyconsulting.jpeg",
    }
    for row in load_rows("website_images.json"):
        status = clean_key(first(row, ["Status"]))
        section = first(row, ["Website Section", "website_section", "section"])
        category = first(row, ["Image Category", "image_category"])
        recommended = first(row, ["Recommended Use", "recommended_use"])
        if status != "approved":
            continue
        home_candidate = (
            "home hero" in section.lower()
            or "hero" in category.lower()
            or section.lower().startswith("home /")
            or recommended.lower().startswith("home page")
        )
        if not home_candidate:
            continue
        raw_url = first(row, ["Google Drive Link", "google_drive_link", "Image URL", "image_url"])
        file_name = first(row, ["Image File Name", "image_file_name", "file_name"])
        if raw_url and is_usable_remote_url(raw_url):
            approved.add(raw_url)
        for value in [raw_url, file_name]:
            if value and not is_usable_remote_url(value):
                alias = local_aliases.get(value.strip().lower(), "")
                if alias and (ROOT / alias.lstrip("/")).exists():
                    approved.add(alias)
                candidate = f"/assets/images/{value.strip().lstrip('/')}"
                if (ROOT / candidate.lstrip("/")).exists():
                    approved.add(candidate)
    return approved


def referenced_asset_filenames(text: str) -> set[str]:
    values = set(re.findall(r'''(?:src|href|data-image-url)=["']([^"']+)["']''', text))
    return {Path(value).name for value in values if not value.startswith("data:")}


def verify_no_fake_logo_references(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_REFERENCES:
        if forbidden in text:
            fail(f"{path.relative_to(ROOT)} still references unauthorized generated logo asset: {forbidden}")
    approved = approved_logo_filenames()
    website_content_logos = website_content_logo_filenames()
    for filename in referenced_asset_filenames(text):
        lower = filename.lower()
        if "header-logo" in lower and website_content_logos and filename not in website_content_logos:
            fail(
                f"{path.relative_to(ROOT)} references header/footer logo asset {filename}, "
                "but Website-conetent-2 names a different approved logo file"
            )
        if any(hint in lower for hint in BRAND_REFERENCE_HINTS) and filename not in approved:
            fail(
                f"{path.relative_to(ROOT)} references brand asset {filename}, "
                "but it is not approved in Website-conetent-2 or approved fallback image exports"
            )


def verify_home(lang: str) -> None:
    parser = parse(ROOT / lang / "index.html")
    expected = EXPECTED[lang]
    if not parser.h1 or parser.h1[0] != expected["title"]:
        fail(f"{lang}/index.html hero title is not approved Sheet text: {parser.h1[:1]}")
    if not parser.leads or parser.leads[0] != expected["lead"]:
        fail(f"{lang}/index.html hero lead is not approved Sheet text: {parser.leads[:1]}")
    approved_hero_sources = approved_home_hero_sources()
    if not approved_hero_sources and parser.hero_media:
        fail(f"{lang}/index.html renders hero media without an approved Website Images Home Hero source")
    for media in parser.hero_media:
        src = media.get("img_src", "")
        if src not in approved_hero_sources:
            fail(f"{lang}/index.html hero media source is not approved in Website Images: {src}")
        if not media.get("img_alt"):
            fail(f"{lang}/index.html hero media is missing alt text")


def verify_services(lang: str) -> None:
    parser = parse(ROOT / lang / "services" / "index.html")
    if not parser.service_source:
        fail(f"{lang}/services/index.html is not marked as Google Sheet generated")
    if len(parser.service_figures) < 5:
        fail(f"{lang}/services/index.html has too few rendered service images")
    for figure in parser.service_figures:
        for field in ["data-image-file-name", "data-image-url", "data-image-alt", "data-image-purpose", "data-image-status"]:
            if not figure.get(field):
                fail(f"{lang}/services/index.html service image missing {field}")
        if figure.get("data-image-status") == "Implemented":
            fail(f"{lang}/services/index.html must not mark Service Images as Implemented")
        if figure.get("img_src") != figure.get("data-image-url"):
            fail(f"{lang}/services/index.html image src does not match data-image-url")
        if not figure.get("img_alt"):
            fail(f"{lang}/services/index.html rendered service image is missing alt text")


def main() -> None:
    for path in [
        ROOT / "index.html",
        ROOT / "en" / "index.html",
        ROOT / "zh" / "index.html",
        ROOT / "en" / "services" / "index.html",
        ROOT / "zh" / "services" / "index.html",
    ]:
        if not path.exists():
            fail(f"Missing generated file: {path.relative_to(ROOT)}")
        verify_no_fake_logo_references(path)
    verify_home("en")
    verify_home("zh")
    verify_services("en")
    verify_services("zh")
    print("Static site verification passed.")


if __name__ == "__main__":
    main()
