#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._figure: dict[str, str] | None = None

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
        elif tag == "img" and self._figure is not None:
            self._figure["img_src"] = attr.get("src", "")
            self._figure["img_alt"] = attr.get("alt", "")

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


def verify_home(lang: str) -> None:
    parser = parse(ROOT / lang / "index.html")
    expected = EXPECTED[lang]
    if not parser.h1 or parser.h1[0] != expected["title"]:
        fail(f"{lang}/index.html hero title is not approved Sheet text: {parser.h1[:1]}")
    if not parser.leads or parser.leads[0] != expected["lead"]:
        fail(f"{lang}/index.html hero lead is not approved Sheet text: {parser.leads[:1]}")


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
    verify_home("en")
    verify_home("zh")
    verify_services("en")
    verify_services("zh")
    print("Static site verification passed.")


if __name__ == "__main__":
    main()
