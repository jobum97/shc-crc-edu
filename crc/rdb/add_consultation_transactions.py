"""상담 고객 12명의 합성 승인 거래 추가. 원본 보존, 재실행 중복 방지."""
from __future__ import annotations
import argparse
import calendar
import collections
import datetime as dt
import json
import math
from pathlib import Path
import random
import re
import subprocess

ROOT=Path(__file__).resolve().parent
DOCS=ROOT.parent/'docs'
PLAN=ROOT/'data'/'consultation_transactions.json'
SQL_FILE=ROOT/'init'/'03_consultation_transactions.sql'
MONTHS=[f'2025-{m:02d}' for m in range(8,13)]+[f'2026-{m:02d}' for m in range(1,9)]

def psql(sql):
    r=subprocess.run(['docker','exec','-i','-e','PGPASSWORD=cardlab','hybrid-ai-lab-rdb',
        'psql','-h','localhost','-U','cardlab','-d','cardlab','-X','-v','ON_ERROR_STOP=1','-t','-A'],
        input=sql,capture_output=True,encoding='utf-8',check=True)
    return r.stdout.strip()

def query(sql):return json.loads(psql(sql))

def literal(x):return "'"+str(x).replace("'","''")+"'"

def prepare():
    ids=sorted({mid for p in DOCS.glob('D3_S*.txt') for mid in re.findall(r'회원번호: (M-\d+)',p.read_text(encoding='utf-8'))})
    assert len(ids)==12
    members=query('SELECT json_agg(m) FROM member m')
    members={m['member_id']:m for m in members if m['member_id'] in ids}
    assert len(members)==12
    cards=query('SELECT json_agg(c) FROM card c')
    cards={mid:[c for c in cards if c['member_id']==mid and c['status']=='ACTIVE'] for mid in ids}
    merchants=query('SELECT json_agg(m) FROM merchant m')
    by_category=collections.defaultdict(list)
    for m in merchants:by_category[m['category']].append(m['merchant_id'])
    sums=query("""SELECT json_agg(x) FROM (SELECT c.member_id,to_char(t.txn_date,'YYYY-MM') AS month,
        sum(t.amount) AS amount FROM card_txn t JOIN card c USING(card_id)
        WHERE approval_code='APPROVED' GROUP BY c.member_id,to_char(t.txn_date,'YYYY-MM')) x""")
    sums={(r['member_id'],r['month']):int(r['amount']) for r in sums}
    category_rows=query("""SELECT json_agg(x) FROM (SELECT c.member_id,m.category,sum(t.amount) AS amount
        FROM card_txn t JOIN card c USING(card_id) JOIN merchant m USING(merchant_id)
        WHERE approval_code='APPROVED' GROUP BY c.member_id,m.category) x""")
    distribution={mid:{r['category']:int(r['amount']) for r in category_rows if r['member_id']==mid} for mid in ids}
    rng=random.Random(2026090912); rows=[]; targets=[]
    for mid in ids:
        months=[month for month in MONTHS if month>=members[mid]['join_date'][:7]]
        floor=max(1_050_000,math.ceil(max([sums.get((mid,m),0) for m in months]+[0])/10000)*10000+50000)
        for pos,month in enumerate(months):
            progress=pos/max(1,len(months)-1)
            if mid in {'M-1042','M-2048','M-3001','M-4001'}:
                ratio=1+(0.8 if mid in {'M-1042','M-4001'} else 0.35)*(1-progress)
            elif mid=='M-1001':ratio=1+0.25*progress
            elif mid=='M-3049':ratio=1.65 if month in {'2026-01','2026-02'} else (1.12 if month<'2026-03' else 1.02)
            elif mid=='M-6001':ratio=1+0.18*(1-progress)
            elif mid=='M-6049':ratio=1+0.5*(1-progress)+(0.12 if month=='2026-05' else 0)
            else:ratio=1+rng.choice([0.04,0.07,0.10,0.13,0.16])
            target=math.ceil(floor*ratio/1000)*1000
            existing=sums.get((mid,month),0); deficit=max(0,target-existing)
            weights=distribution[mid] or {'생활':5,'식비':3,'교통':2}
            if mid=='M-3049':weights={'쇼핑':8,'생활':2} if month in {'2026-01','2026-02'} else {'생활':6,'식비':2,'교통':2}
            if mid=='M-6001':weights={'식비':7,'쇼핑':2,'생활':1}
            if mid=='M-6049':weights={'식비':5,'생활':4,'쇼핑':2 if month=='2026-05' else 1}
            if mid=='M-1001':weights={'생활':5,'식비':3,'교통':2}
            count=max(6,math.ceil(deficit/(450000 if mid.startswith('M-3') else 130000))) if deficit else 0
            shares=[rng.uniform(.6,1.4) for _ in range(count)]
            amounts=[int(deficit*s/sum(shares)//100)*100 for s in shares] if count else []
            if amounts:amounts[-1]+=deficit-sum(amounts)
            for i,amount in enumerate(amounts):
                year,mon=map(int,month.split('-')); day=rng.randint(1,calendar.monthrange(year,mon)[1])
                date=f'{month}-{day:02d}'
                eligible=[c for c in cards[mid] if c['issue_date']<=date]
                assert eligible and date>=members[mid]['join_date']
                chosen=eligible[i%len(eligible)]
                category=rng.choices(list(weights),weights=list(weights.values()),k=1)[0]
                merchant=rng.choice(by_category[category])
                rows.append({'txn_id':f'D3ADD-{mid}-{month.replace("-","")}-{i+1:03d}',
                    'card_id':chosen['card_id'],'merchant_id':merchant,'txn_date':date,'amount':amount,'approval_code':'APPROVED'})
            targets.append({'member_id':mid,'month':month,'before_approved':existing,'added_amount':deficit,
                            'after_target':target,'added_rows':count})
    plan={'period':'2025-08~2026-08, 가입일 이후','monthly_minimum':1000000,'member_ids':ids,
          'targets':targets,'transactions':rows,
          'note':'상담을 참고한 합성 설계. 업종은 광범위 분류이며 온라인 여부·가전 품목·마트/외식 구분은 현 스키마로 증명하지 않음. 거래를 해지·민원·인증 결과 라벨로 사용하지 않음.'}
    PLAN.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    return plan

def make_sql(plan):
    columns=['txn_id','card_id','merchant_id','txn_date','amount','approval_code']
    values=[]
    for row in plan['transactions']:
        values.append('('+','.join(str(row[k]) if k=='amount' else literal(row[k]) for k in columns)+')')
    ids=','.join(literal(mid) for mid in plan['member_ids'])
    sql="-- 상담12명 승인거래 추가. data/consultation_transactions.json에서 생성함.\nBEGIN;\n"
    sql+='CREATE TEMP TABLE d3_incoming (LIKE card_txn) ON COMMIT DROP;\n'
    sql+='INSERT INTO d3_incoming ('+','.join(columns)+') VALUES\n'+',\n'.join(values)+';\n'
    sql+="""DO $$ BEGIN
IF EXISTS (SELECT 1 FROM d3_incoming i JOIN card_txn t USING(txn_id)
 WHERE ROW(i.card_id,i.merchant_id,i.txn_date,i.amount,i.approval_code)
 IS DISTINCT FROM ROW(t.card_id,t.merchant_id,t.txn_date,t.amount,t.approval_code))
THEN RAISE EXCEPTION 'D3 transaction ID exists with different values'; END IF;
END $$;
INSERT INTO card_txn SELECT * FROM d3_incoming ON CONFLICT(txn_id) DO NOTHING;
"""
    sql+=f"""DO $$ BEGIN
IF EXISTS (
 SELECT m.member_id,d.month FROM member m
 CROSS JOIN generate_series(date '2025-08-01',date '2026-08-01',interval '1 month') d(month)
 LEFT JOIN card c ON c.member_id=m.member_id
 LEFT JOIN card_txn t ON t.card_id=c.card_id AND t.approval_code='APPROVED'
 AND t.txn_date>=d.month AND t.txn_date<d.month+interval '1 month'
 WHERE m.member_id IN ({ids}) AND d.month>=date_trunc('month',m.join_date)
 GROUP BY m.member_id,d.month HAVING COALESCE(sum(t.amount),0)<1000000
) THEN RAISE EXCEPTION 'Monthly approved amount below 1000000'; END IF;
END $$;
COMMIT;
"""
    SQL_FILE.write_text(sql,encoding='utf-8')
    return sql

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    plan=json.loads(PLAN.read_text(encoding='utf-8')) if PLAN.exists() else prepare()
    sql=make_sql(plan)
    print(json.dumps({'planned_rows':len(plan['transactions']),'member_months':len(plan['targets']),
        'minimum_target':min(t['after_target'] for t in plan['targets'])},ensure_ascii=False))
    if args.apply:print(psql(sql))

if __name__=='__main__':main()
