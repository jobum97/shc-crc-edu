"""공통 정형 DB의 합성 데이터를 만들어 init/02_seed.sql로 저장함.

고정 난수(SEED)로 생성하므로 몇 번을 돌려도 같은 결과가 나옴.
Postgres 드라이버 없이 psql만으로 적재할 수 있도록 COPY 형식으로 씀.

w2-3의 SQLite 시드(src/common/seed.py)가 만든 교육 서사를 그대로 보존함.
  - 대표 고객 M-1042: 장기 보유, 최근 6개월 사용액 하락, 연체 없음
  - 세그먼트별 100명 x 6종 = 600명, 거래 2025-08 ~ 2026-08, 기준일 2026-08-31
  - '확인 필요' 실습용 결측: 연체 지표 전체 없음 1명, 최신월 없음 1명
  - 정지 카드 1명, 해지 카드 1명, 미이용 고객, 휴면 직전 고객

SQLite 시드와 다른 점은 상품뿐임. 8종 고정 상품 대신 D2 문서의 64종을 씀.
"""
from __future__ import annotations

import calendar
import io
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent
PRODUCTS_JSON = ROOT / "data" / "d2_products.json"
OUT = ROOT / "init" / "02_seed.sql"

SEED = 20260831
BASE_DATE = "2026-08-31"
MONTHS = [f"2025-{m:02d}" for m in range(8, 13)] + [f"2026-{m:02d}" for m in range(1, 9)]
SEGMENTS = {1: "신규 가입", 2: "장기 보유", 3: "VIP", 4: "휴면 직전", 5: "다중 카드", 6: "연회비 부담"}
CARDS_PER_SEGMENT = {1: 1, 2: 2, 3: 2, 4: 1, 5: 4, 6: 2}
MONTHLY_BASE = {1: 250000, 2: 900000, 3: 6000000, 4: 500000, 5: 1300000, 6: 100000}

# 세그먼트별로 어느 연회비 구간의 카드를 발급할지 정함.
# 64종을 대표 연회비(국내전용 우선) 오름차순으로 4구간 16종씩 나눈 뒤 배정함.
# VIP와 연회비 부담 세그먼트가 고액 카드를 갖도록 하여 기존 교재의 서사를 유지함.
SEGMENT_TIERS = {1: [0], 2: [1, 1], 3: [3, 3], 4: [0], 5: [0, 1, 2, 3], 6: [2, 3]}

# 대표 고객의 원 회원번호는 교재(S2.3 슬라이드 8)의 상담 예시와 맞춰 고정함.
FIXED_SOURCE_NO = {"M-1042": "10023981"}


def _member_ids(segment: int) -> list[str]:
    if segment == 1:
        return [f"M-{i}" for i in range(1001, 1102) if i != 1042]
    if segment == 2:
        return ["M-1042"] + [f"M-{i}" for i in range(2001, 2100)]
    return [f"M-{segment * 1000 + i}" for i in range(1, 101)]


def _representative_fee(product: dict) -> int:
    """카드의 대표 연회비. 국내전용이 있으면 그 값, 없으면 첫 브랜드 값."""
    for fee in product["fees"]:
        if fee["brand"] == "국내전용":
            return fee["total_fee"]
    return product["fees"][0]["total_fee"]


def _tiers(products: list[dict]) -> list[list[dict]]:
    ordered = sorted(products, key=lambda p: (_representative_fee(p), p["product_id"]))
    size = len(ordered) // 4
    return [ordered[i * size:(i + 1) * size] for i in range(4)]


class Copy:
    """COPY ... FROM stdin 한 블록을 모아 두는 그릇."""

    def __init__(self, table: str, columns: tuple[str, ...]) -> None:
        self.table, self.columns, self.rows = table, columns, []

    def add(self, *values) -> None:
        self.rows.append("\t".join("\\N" if v is None else str(v) for v in values))

    def write(self, out: io.TextIOBase) -> None:
        out.write(f"COPY {self.table} ({', '.join(self.columns)}) FROM stdin;\n")
        out.write("\n".join(self.rows))
        out.write("\n\\.\n\n")


def build() -> dict[str, Copy]:
    products = json.loads(PRODUCTS_JSON.read_text(encoding="utf-8"))
    tiers = _tiers(products)
    rng = random.Random(SEED)

    t_member = Copy("member", ("member_id", "segment_id", "join_date", "age_band"))
    t_product = Copy("product", ("product_id", "product_name", "effective_date",
                                 "family_card_available", "family_card_rule"))
    t_fee = Copy("product_annual_fee", ("product_id", "brand", "base_fee", "service_fee", "total_fee"))
    t_merchant = Copy("merchant", ("merchant_id", "merchant_name", "category"))
    t_card = Copy("card", ("card_id", "member_id", "product_id", "brand", "issue_date", "status"))
    t_txn = Copy("card_txn", ("txn_id", "card_id", "merchant_id", "txn_date", "amount", "approval_code"))
    t_delq = Copy("delinquency", ("member_id", "base_month", "overdue_days",
                                  "overdue_count_12m", "overdue_amount"))
    t_map = Copy("private.member_id_map", ("source_member_no", "member_id"))
    t_meta = Copy("lab_metadata", ("key", "value"))

    for product in products:
        available = not product["family_card_rule"].startswith("가족카드: 가족카드 미운영")
        t_product.add(product["product_id"], product["product_name"], product["effective_date"],
                      "true" if available else "false", product["family_card_rule"])
        for fee in product["fees"]:
            t_fee.add(product["product_id"], fee["brand"],
                      fee["base_fee"], fee["service_fee"], fee["total_fee"])

    categories = ["식비", "쇼핑", "교통", "주유", "여행", "해외", "생활", "교육"]
    merchants = [(f"SHOP-{i:03d}", f"가상 {categories[(i - 1) % 8]} 가맹점 {i:03d}", categories[(i - 1) % 8])
                 for i in range(1, 81)]
    for merchant in merchants:
        t_merchant.add(*merchant)

    all_members: list[str] = []
    txn_count = 0
    for segment in SEGMENTS:
        for pos, member_id in enumerate(_member_ids(segment)):
            all_members.append(member_id)
            join_date = f"2026-{3 + pos % 6:02d}-01" if segment == 1 else f"{2018 + pos % 5}-03-15"
            if member_id == "M-1042":
                join_date = "2021-03-15"
            t_member.add(member_id, segment, join_date,
                         rng.choice(["20대", "30대", "40대", "50대", "60대 이상"]))

            cards: list[str] = []
            for index, tier in enumerate(SEGMENT_TIERS[segment]):
                pool = tiers[tier]
                # 같은 구간에서 두 장을 뽑는 세그먼트가 있으므로 슬롯마다 자리를 어긋나게 함.
                product = pool[(pos + index * 5) % len(pool)]
                brand = product["fees"][(pos + index) % len(product["fees"])]["brand"]
                card_id = f"C-{member_id[2:]}-{index + 1}"
                issued = "2024-07-02" if member_id == "M-1042" and index == 1 else join_date
                status = ("SUSPENDED" if pos == 97 and index == 0 else
                          "CLOSED" if pos == 96 and index == 0 else "ACTIVE")
                t_card.add(card_id, member_id, product["product_id"], brand, issued, status)
                cards.append(card_id)

            for mi, month in enumerate(MONTHS):
                if month < join_date[:7]:
                    continue
                # 신규 대표는 미이용 고객, 휴면 대표는 최근 두 달 승인 거래 없음.
                if (segment == 1 and pos % 10 == 0) or (segment == 4 and mi >= 11 and pos % 3 == 0):
                    total = 0
                else:
                    factor = (max(.05, 1 - mi * .075) if segment == 4 else
                              1 - mi * .035 if segment == 2 else 1)
                    total = int(MONTHLY_BASE[segment] * factor * rng.uniform(.65, 1.4))
                if member_id == "M-1042" and mi >= 7:
                    total = [880000, 850000, 820000, 700000, 600000, 510000][mi - 7]
                n = rng.randint(8, 18) if total else 0
                # VIP는 고액 거래 필터 실습이 가능하도록 건수를 줄여 건당 금액을 키움.
                if segment == 3:
                    n = 4
                pieces = [total // n] * n if n else []
                if n:
                    pieces[-1] += total - sum(pieces)
                year, mon = map(int, month.split("-"))
                last_day = calendar.monthrange(year, mon)[1]
                for ti, amount in enumerate(pieces):
                    day = 1 if ti == 0 else last_day if ti == n - 1 else rng.randint(2, last_day - 1)
                    txn_count += 1
                    t_txn.add(f"T-{txn_count:08d}", cards[ti % len(cards)],
                              rng.choice(merchants)[0], f"{month}-{day:02d}", amount, "APPROVED")
                if pos % 4 == 0:
                    txn_count += 1
                    t_txn.add(f"T-{txn_count:08d}", cards[0], merchants[pos % 80][0],
                              f"{month}-15", 99900, "CANCELLED")

            # 100번째 고객은 연체 지표 전체 누락, 99번째는 최신월 누락 → '확인 필요' 실습.
            if pos == 99:
                continue
            events = [int(pos % 7 == 0 and i % 5 == pos % 5) for i in range(24)]
            if segment == 1:
                first_index = 11 + MONTHS.index(join_date[:7])
                events[:first_index] = [0] * first_index
            if member_id == "M-1042":
                events = [0] * 24
            for mi, month in enumerate(MONTHS):
                if month < join_date[:7] or (pos == 98 and mi == 12):
                    continue
                current = events[mi + 11]
                t_delq.add(member_id, month, (3 + pos % 20) if current else 0,
                           sum(events[mi:mi + 12]), 50000 + pos * 1000 if current else 0)

    # 원 회원번호 8자리. 대표 고객만 교재 예시 값으로 고정하고 나머지는 고정 난수로 뽑음.
    reserved = set(FIXED_SOURCE_NO.values())
    pool = random.Random(SEED).sample(range(10_000_000, 20_000_000), len(all_members) + len(reserved))
    supply = (f"{n:08d}" for n in pool if f"{n:08d}" not in reserved)
    for member_id in all_members:
        t_map.add(FIXED_SOURCE_NO.get(member_id) or next(supply), member_id)

    for key, value in (("schema_version", "1"),
                       ("base_date", BASE_DATE),
                       ("txn_period", f"{MONTHS[0]} ~ {MONTHS[-1]}"),
                       ("member_count", str(len(all_members))),
                       ("product_source", "docs/D2_카드혜택안내_합성.pdf (D2, 가상 시행일 2027-01-15)"),
                       ("terms_source", "docs/D1_개인회원표준약관_합성.pdf (D1)"),
                       ("synthetic", "교육용 합성 데이터. 실제 고객·상품·금액이 아님.")):
        t_meta.add(key, value)

    return {"member": t_member, "product": t_product, "fee": t_fee, "merchant": t_merchant,
            "card": t_card, "txn": t_txn, "delq": t_delq, "map": t_map, "meta": t_meta}


def main() -> int:
    if not PRODUCTS_JSON.exists():
        print(f"먼저 extract_d2.py를 실행해야 함: {PRODUCTS_JSON}")
        return 1
    tables = build()
    # 외래 키 순서대로 적재함.
    order = ("member", "product", "fee", "merchant", "card", "txn", "delq", "map", "meta")
    with OUT.open("w", encoding="utf-8", newline="\n") as out:
        out.write("-- build_seed.py가 생성함. 직접 고치지 말 것.\n")
        out.write(f"-- 고정 난수 SEED={SEED} · 기준일 {BASE_DATE}\n\n")
        for name in order:
            tables[name].write(out)
        out.write("ANALYZE;\n")
    for name in order:
        print(f"{tables[name].table:>26} {len(tables[name].rows):>7}행")
    print(f"→ {OUT} ({OUT.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
