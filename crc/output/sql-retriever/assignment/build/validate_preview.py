"""LibreOffice 미리보기 PDF의 텍스트, 글꼴과 페이지 경계를 확인한다."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pdfplumber


BASE = Path(__file__).resolve().parents[1]
PDF = BASE / "build" / "rendered" / "candidate.pdf"
SLIDES = json.loads((BASE / "work" / "slides.json").read_text(encoding="utf-8"))["slides"]


def norm(value: object) -> str:
    return re.sub(r"\s+", "", str(value)).replace("’", "'").replace("‘", "'")


def widths(slide: dict) -> list[float]:
    size = len(slide["columns"])
    return {2: [2.8, 12.1], 3: [3.8, 3.5, 7.6],
            4: [2.2, 3.4, 3.0, 6.3]}.get(size, [14.9 / size] * size)


pages = []
with pdfplumber.open(PDF) as pdf:
    assert len(pdf.pages) == len(SLIDES)
    for slide, page in zip(SLIDES, pdf.pages):
        regions = [norm(page.extract_text() or "")]
        expected = [slide["title"], slide["lead"]]
        if slide["kind"] == "table":
            expected += slide["columns"]
            expected += [value for row in slide["rows"] for value in row]
            left = 0.557 * 72
            for width in widths(slide):
                regions.append(norm(page.crop((left, 1.9 * 72,
                                               left + width * 72,
                                               7.95 * 72)).extract_text() or ""))
                left += width * 72
        elif slide["kind"] == "code":
            expected += [line for line in slide["code"].splitlines() if line]
        elif slide["kind"] == "text":
            expected += slide["text_lines"]
        missing = [value for value in expected
                   if not any(norm(value) in region for region in regions)]
        outside = [c["text"] for c in page.chars
                   if c["x0"] < -0.5 or c["x1"] > page.width + 0.5
                   or c["top"] < -0.5 or c["bottom"] > page.height + 0.5]
        fonts = sorted({c["fontname"].split("+")[-1] for c in page.chars})
        minimum = min(c["size"] for c in page.chars)
        pages.append({
            "page": slide["id"],
            "missing_strings": missing,
            "out_of_page_count": len(outside),
            "fonts": fonts,
            "minimum_font_pt": round(minimum, 3),
            "passed": not missing and not outside and minimum >= 13.95
                      and all("Pretendard" in font for font in fonts),
        })

report = {
    "pdf_sha256": hashlib.sha256(PDF.read_bytes()).hexdigest(),
    "page_count": len(pages),
    "pages": pages,
    "result": "passed" if all(p["passed"] for p in pages) else "failed",
    "scope": "LibreOffice PDF의 텍스트와 글꼴 검증. PowerPoint 앱 자체 확인은 아님.",
}
(BASE / "build" / "preview_validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps({"result": report["result"], "pages": len(pages),
                  "failed_pages": [p["page"] for p in pages if not p["passed"]]},
                 ensure_ascii=False))
raise SystemExit(0 if report["result"] == "passed" else 1)
