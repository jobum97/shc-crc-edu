"""발표 스크립트를 슬라이드 빌드 입력으로 변환한다."""

from __future__ import annotations

import json
import re
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
SCRIPT = (BASE / "design_script.md").read_text(encoding="utf-8")
BLOCK = re.compile(r"(?m)^## (\d{2})\. (.+)$")
matches = list(BLOCK.finditer(SCRIPT))
assert len(matches) == 21, len(matches)
slides = []

for idx, match in enumerate(matches):
    end = matches[idx + 1].start() if idx + 1 < len(matches) else len(SCRIPT)
    body = SCRIPT[match.end():end]
    sid = int(match.group(1))
    assert sid == idx + 1
    title = match.group(2).strip()
    lead_match = re.search(r"(?m)^리드문: (.+?)\s*$", body)
    assert lead_match, title
    lead = lead_match.group(1).strip()
    pattern_match = re.search(r"(?m)^패턴: ([A-E])", body)
    design_match = re.search(r"(?m)^도형 의도: (.+?)\s*$", body)
    source_match = re.search(r"(?m)^근거: (.+?)\s*$", body)
    notes = [line.removeprefix("발표자 노트: ").strip()
             for line in body.splitlines() if line.startswith("발표자 노트: ")]
    notes.extend(line.split(":", 1)[1].strip() for line in body.splitlines()
                 if line.startswith("본문 메모: ") or line.startswith("본문 식: "))
    if source_match:
        notes.append("근거: " + source_match.group(1).strip())
    slide = {
        "id": sid,
        "title": title,
        "lead": lead,
        "pattern": pattern_match.group(1) if pattern_match else "D",
        "source": "근거: 과제 설계서, DB SQL과 검증 결과. 상세 출처는 발표자 노트",
        "notes": "\n".join(notes),
        "design": design_match.group(1).strip() if design_match else "",
    }
    if sid == 4:
        slide["kind"] = "flow"
    elif sid == 19:
        slide["kind"] = "code"
        code_match = re.search(r"```sql\n(.*?)\n```", body, re.S)
        assert code_match
        slide["code"] = code_match.group(1)
    elif "본문 표:" in body:
        table_part = body.split("본문 표:", 1)[1]
        lines = []
        started = False
        for line in table_part.splitlines():
            if line.startswith("|"):
                lines.append(line.rstrip().strip("|").split("|"))
                started = True
            elif started:
                break
        assert len(lines) >= 3, title
        rows = [[cell.strip() for cell in row] for row in lines]
        assert all(len(row) == len(rows[0]) for row in rows), title
        slide["kind"] = "table"
        slide["columns"] = rows[0]
        slide["rows"] = rows[2:]
    else:
        slide["kind"] = "text"
        slide["section_label"] = "설계 기준" if sid != 1 else "고객 질문"
        slide["text_lines"] = [line[2:].strip() for line in body.splitlines()
                               if line.startswith("- ")]
        assert slide["text_lines"], title
    slides.append(slide)

out = BASE / "work" / "slides.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"slides": slides}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"슬라이드 명세 생성: {len(slides)}장")
