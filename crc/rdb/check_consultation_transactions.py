"""실제 추가 거래 검증, 상담 원문의 집계·README 갱신."""
import json
import re
import collections
from add_consultation_transactions import ROOT,DOCS,PLAN,query,literal

def main():
    plan=json.loads(PLAN.read_text(encoding='utf-8'))
    ids=','.join(literal(x) for x in plan['member_ids'])
    before=json.loads((ROOT/'data/consultation_transactions_before.json').read_text(encoding='utf-8'))
    original=query("SELECT json_build_object('count',count(*),'digest',md5(string_agg(row_to_json(t)::text,'' ORDER BY txn_id))) FROM card_txn t WHERE txn_id NOT LIKE 'D3ADD-%'")
    assert original==before,'Original transaction rows changed'
    actual=query(f"""SELECT json_agg(x ORDER BY member_id,month) FROM (SELECT c.member_id,
        to_char(t.txn_date,'YYYY-MM') AS month,sum(t.amount) AS amount,count(*) AS cnt
        FROM card c JOIN card_txn t USING(card_id) WHERE c.member_id IN ({ids}) AND t.approval_code='APPROVED'
        GROUP BY c.member_id,to_char(t.txn_date,'YYYY-MM')) x""")
    monthly={(x['member_id'],x['month']):x for x in actual}
    for target in plan['targets']:
        assert monthly[target['member_id'],target['month']]['amount']==target['after_target']>=1000000
    integrity=query(f"""SELECT json_build_object('added',count(*),'invalid',count(*) FILTER
      (WHERE c.member_id NOT IN ({ids}) OR t.txn_date<m.join_date OR t.txn_date<c.issue_date
       OR t.txn_date<'2025-08-01' OR t.txn_date>'2026-08-31' OR t.approval_code<>'APPROVED'))
      FROM card_txn t JOIN card c USING(card_id) JOIN member m USING(member_id) WHERE t.txn_id LIKE 'D3ADD-%'""")
    assert integrity['added']==len(plan['transactions']) and integrity['invalid']==0
    updates=0
    for p in DOCS.glob('D3_S*.txt'):
        lines=p.read_text(encoding='utf-8').splitlines();mid=None
        for i,line in enumerate(lines):
            h=re.search(r'^\[상담ID\].*회원번호: (M-\d+)',line)
            if h:mid=h[1]
            fact=re.search(r'(\d{4}-\d{2}) 승인 거래는 \d+건, 합계 [\d,]+원입니다\. 취소 건은 제외했습니다\.',line)
            if fact:
                row=monthly.get((mid,fact[1]),{'cnt':0,'amount':0})
                new=f"{fact[1]} 승인 거래는 {row['cnt']}건, 합계 {row['amount']:,}원입니다. 취소 건은 제외했습니다."
                lines[i]=line[:fact.start()]+new+line[fact.end():];updates+=1
        p.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    support=DOCS/'_instructor'/'D3'
    facts=json.loads((support/'이용사실_대조표.json').read_text(encoding='utf-8'))
    for f in facts:
        row=monthly.get((f['member_id'],f['month']),{'cnt':0,'amount':0})
        f['approved_count']=row['cnt'];f['approved_amount']=row['amount']
    (support/'이용사실_대조표.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
    generation_path=support/'생성검증.json'
    generation=json.loads(generation_path.read_text(encoding='utf-8'))
    utterances=[line for p in DOCS.glob('D3_S*.txt') for line in p.read_text(encoding='utf-8').splitlines()
                if line.startswith(('고객:','상담사:'))]
    counts=collections.Counter(utterances)
    generation.update(utterances=len(utterances),unique_utterances=len(counts),largest_exact_utterance_repetition=max(counts.values()))
    generation['transaction_augmentation']='12명 가입일 이후 전 기간 월 승인액 100만원 이상. 원문 금액은 추가 거래 반영 후 재집계함.'
    generation_path.write_text(json.dumps(generation,ensure_ascii=False,indent=2),encoding='utf-8')
    totals=query(f"""SELECT json_agg(x ORDER BY segment_id,member_id) FROM (SELECT m.member_id,m.segment_id,
        (SELECT count(*) FROM card c WHERE c.member_id=m.member_id) AS cards,
        count(t.txn_id) AS txns,count(t.txn_id) FILTER(WHERE t.approval_code='APPROVED') AS approved
        FROM member m JOIN card c USING(member_id) LEFT JOIN card_txn t USING(card_id)
        WHERE m.member_id IN ({ids}) GROUP BY m.member_id,m.segment_id) x""")
    path=ROOT/'README.md';text=path.read_text(encoding='utf-8')
    for r in totals:
        pattern=r'^\| ([^|]+) \| `'+re.escape(r['member_id'])+r'` \|.*$'
        text=re.sub(pattern,lambda m:f"| {m[1].strip()} | `{r['member_id']}` | {r['cards']:,} | {r['txns']:,} | {r['approved']:,} |",text,flags=re.M)
    text=re.sub(r'^\| 합계 \| 12명.*$',f"| 합계 | 12명 | {sum(r['cards'] for r in totals)} | {sum(r['txns'] for r in totals):,} | {sum(r['approved'] for r in totals):,} |",text,flags=re.M)
    text=re.sub(r'^- \*\*`M-1001`은.*$', '- 12명 모두 가입일 이후 대상 월의 승인 이용금액이 100만원 이상임. 취소 거래는 월 사용액에서 제외함.',text,flags=re.M)
    text=re.sub(r'^- `M-4001`, `M-4049`는.*$', '- `M-1001`의 미이용 및 `M-4001`·`M-4049`의 최근 승인 0건 사례는 추가 거래 반영 후 더 이상 해당하지 않음.',text,flags=re.M)
    text=text.replace('- 데이터 점검만 수행했으며 회원·카드·거래 데이터는 변경하지 않음.', '- 2026-09-09 사용자 요청에 따라 승인 거래 1,152건을 추가함. 기존 거래·회원·카드는 유지함.')
    text=text.replace('| `card_txn` | 77,650 |','| `card_txn` | 78,802 |')
    m1042=[monthly['M-1042',f'2026-{m:02d}']['amount'] for m in range(3,9)]
    text=text.replace('사용액 880,000 → 510,000원(6개월)',f"사용액 {m1042[0]:,} → {m1042[-1]:,}원(2026년 3~8월, 추가 거래 반영)")
    text=text.replace('두 데이터는 회원·거래 생성 규칙이 같고 상품만 다름(SQLite 8종 고정 / 여기 D2 64종).','기본 회원·거래 생성 규칙은 같지만 PostgreSQL에는 D2 상품 64종과 상담 고객 12명의 추가 거래가 반영됨.\nSQLite의 거래금액과 PostgreSQL의 상담 고객 거래금액은 같지 않을 수 있음.')
    section='''## 상담 고객 월 100만원 이상 추가 거래

2025년 8월~2026년 8월 중 가입일 이후 142개 고객·월에 적용함.
월 사용액은 승인 거래 합계이며 혜택 실적·청구금액과는 다른 값임.
기존 거래는 유지하고 `D3ADD-` 접두어 거래 1,152건만 추가함.
재실행 시 같은 ID의 거래는 중복 삽입하지 않으며, 같은 ID의 내용이 다르면 적용을 중단함.

상담의 소비 감소·앱 불편·생활비 절약을 참고한 교육용 합성 설계임.
업종 분류만으로 온라인 결제·가전 품목·마트/외식을 구분하거나 해지·인증 성공을 확정하지 않음.
기존 미이용·휴면 반례는 나머지 회원에서 찾을 수 있으며, 이번 12명은 월 사용액 하한 적용 대상임.

```bash
python rdb/add_consultation_transactions.py          # 확정 추가 거래 JSON으로 SQL 생성
python rdb/add_consultation_transactions.py --apply  # 실행 중인 DB에 중복 없이 적용
python rdb/check_consultation_transactions.py        # 검증·상담 집계·README 갱신
```

`data/consultation_transactions.json`은 확정 추가 거래와 월별 전후 금액임.
`init/03_consultation_transactions.sql`은 신규 DB 생성 시 `02_seed.sql` 다음에 자동 실행됨.
따라서 전체 DB를 재생성해도 추가 거래가 유지됨. 기존 볼륨에는 `--apply`로 반영함.
검증 결과는 `data/consultation_transactions_audit.json`에 저장함.

'''
    if '## 상담 고객 월 100만원 이상 추가 거래' not in text:text=text.replace('## 기동과 종료',section+'## 기동과 종료')
    path.write_text(text,encoding='utf-8')
    report={'added_rows':integrity['added'],'member_months':len(plan['targets']),
      'minimum_approved_month':min(monthly[t['member_id'],t['month']]['amount'] for t in plan['targets']),
      'original_rows_unchanged':True,'original_rows':original['count'],'invalid_added_rows':0,
      'refreshed_consultation_facts':updates,'member_totals':totals,'monthly_totals':actual}
    (ROOT/'data/consultation_transactions_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'member_totals','monthly_totals'}},ensure_ascii=False))

if __name__=='__main__':main()
