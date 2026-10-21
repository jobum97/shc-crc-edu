"""D2 카드 혜택 안내 PDF에서 카드 64종과 브랜드별 연회비를 추출함.

산출물: data/d2_products.json (seed.py가 읽는 유일한 상품 원천)
PDF를 다시 읽지 않아도 시드를 재현할 수 있도록 중간 산출물을 파일로 남김.

추출 대상은 각 카드 상세 페이지의 연회비 표뿐임. 혜택 블록(B01~)은 비정형 문서
검색 실습의 재료이므로 RDB에 넣지 않음.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
PDF = ROOT.parent / "docs" / "D2_카드혜택안내_합성.pdf"
OUT = ROOT / "data" / "d2_products.json"

CARD_ID = re.compile(r"^D2-C\d{3}$")
NUMBER = re.compile(r"^[\d,]+$")
RUNNING_HEAD = "한빛카드 |"
RUNNING_SUB = "교육용 합성 자료"


def _page_lines(pdf_path: Path) -> list[str]:
    """쪽마다 반복되는 머리말 두 줄과 그 뒤의 쪽번호만 제거함.

    쪽번호를 "1~3자리 숫자"로 일괄 제거하면 연회비 0원·3,000원 같은 표 값까지
    지워지므로(D2-C021·D2-C055에서 실제로 발생), 머리말 바로 뒤에 오는 한 줄만 버림.
    """
    import fitz

    lines: list[str] = []
    with fitz.open(pdf_path) as document:
        for page in document:
            page_lines = [line.strip() for line in page.get_text().split("\n")]
            page_lines = [line for line in page_lines if line]
            if (len(page_lines) >= 3 and page_lines[0].startswith(RUNNING_HEAD)
                    and page_lines[1].startswith(RUNNING_SUB) and page_lines[2].isdigit()):
                page_lines = page_lines[3:]
            lines.extend(page_lines)
    return lines


def _is_noise(line: str) -> bool:
    return (not line) or line.startswith((RUNNING_HEAD, RUNNING_SUB))


def _card_starts(lines: list[str]) -> list[int]:
    """카드 상세 페이지의 시작 줄 번호. 목차의 카드 ID와 구분하려고 세 번째 줄을 확인함."""
    return [i for i, line in enumerate(lines)
            if CARD_ID.match(line) and i + 2 < len(lines) and lines[i + 2].startswith("가상 시행일")]


def _fees(block: list[str]) -> list[dict]:
    """연회비 표를 (브랜드, 기본, 서비스, 총연회비) 네 줄 묶음으로 읽음.

    브랜드 라벨은 '국내전용'·'해외 A'뿐 아니라 '해외 A 모바일'처럼 변형이 있어
    고정 목록으로 걸러내지 않고 "숫자가 아닌 줄 + 숫자 세 줄" 형태로 인식함.
    """
    start = block.index("총연회비") + 1
    stop = next((i for i, line in enumerate(block) if line.startswith("가족카드:")), len(block))
    segment = block[start:stop]
    rows, index = [], 0
    while index <= len(segment) - 4:
        head, values = segment[index], segment[index + 1:index + 4]
        if not NUMBER.match(head) and all(NUMBER.match(value) for value in values):
            base, service, total = (int(value.replace(",", "")) for value in values)
            rows.append({"brand": head, "base_fee": base, "service_fee": service, "total_fee": total})
            index += 4
        else:
            index += 1
    return rows


def extract(pdf_path: Path = PDF) -> list[dict]:
    lines = _page_lines(pdf_path)
    starts = _card_starts(lines)
    cards = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = [line for line in lines[start:end] if not _is_noise(line)]
        effective = re.search(r"가상 시행일 (\d{4}-\d{2}-\d{2})", block[2])
        cards.append({
            "product_id": block[0],
            "product_name": block[1],
            "effective_date": effective.group(1) if effective else None,
            "family_card_rule": next((l for l in block if l.startswith("가족카드:")), None),
            "fees": _fees(block),
        })
    return cards


def _verify(cards: list[dict]) -> None:
    """추출 실패를 조용히 넘기지 않음. 하나라도 어긋나면 시드를 만들지 않음."""
    problems = []
    if len(cards) != 64:
        problems.append(f"카드 수 {len(cards)} (기대 64)")
    ids = [c["product_id"] for c in cards]
    if ids != [f"D2-C{n:03d}" for n in range(1, 65)]:
        problems.append("카드 ID가 D2-C001~D2-C064 연속이 아님")
    for card in cards:
        if not card["fees"]:
            problems.append(f"{card['product_id']} 연회비 표 없음")
        if not card["family_card_rule"]:
            problems.append(f"{card['product_id']} 가족카드 조건 없음")
        if not card["effective_date"]:
            problems.append(f"{card['product_id']} 시행일 없음")
        for fee in card["fees"]:
            if fee["base_fee"] + fee["service_fee"] != fee["total_fee"]:
                problems.append(f"{card['product_id']} {fee['brand']} 기본+서비스 != 총연회비")
        brands = [fee["brand"] for fee in card["fees"]]
        if len(brands) != len(set(brands)):
            problems.append(f"{card['product_id']} 브랜드 중복")
    if problems:
        raise ValueError("D2 추출 검증 실패:\n- " + "\n- ".join(problems))


def main() -> int:
    if not PDF.exists():
        print(f"원문 PDF를 찾을 수 없음: {PDF}")
        return 1
    cards = extract()
    _verify(cards)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = sum(len(card["fees"]) for card in cards)
    print(f"카드 {len(cards)}종 · 연회비 행 {rows}개 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
