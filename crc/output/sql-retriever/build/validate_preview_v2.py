"""최종 LibreOffice PDF의 텍스트 보존, 글꼴, 크기와 페이지 경계 검사."""
from pathlib import Path
import json
import re
import hashlib
import pdfplumber

BASE = Path(__file__).resolve().parents[1]
PDF = BASE/'build/out_v2/sql_retriever_설계서_v2.pdf'
spec = json.loads((BASE/'work/slides_v2.json').read_text())
normalize = lambda value: re.sub(r'\s+', '', str(value))
pages=[]
with pdfplumber.open(PDF) as pdf:
    for slide, page in zip(spec['slides'], pdf.pages):
        text = normalize(page.extract_text() or '')
        expected = [slide['title'], slide['lead']]
        if slide['kind'] == 'table':
            expected += slide['columns']
            expected += [v for row in slide['rows'] for v in row]
        elif slide['kind'] == 'code':
            expected += [slide['code']] + [v for row in slide['rows'] for v in row]
        elif slide['kind'] in ('cover','text'):
            expected += slide['text_lines']
        # 여러 줄짜리 왼쪽 셀과 한 줄짜리 오른쪽 셀이 PDF 읽기 순서에서 섞일 수 있음.
        text_regions = [text]
        if slide['kind'] == 'table':
            widths = slide.get('col_widths') or ({2:[2.8,12.1],3:[3.8,3.5,7.6],4:[2.2,3.4,3.0,6.3]}[len(slide['columns'])])
            left = 0.557*72
            for width in widths:
                text_regions.append(normalize(page.crop((left,1.90*72,left+width*72,7.95*72)).extract_text() or ''))
                left += width*72
        missing = [value for value in expected if not any(normalize(value) in region for region in text_regions)]
        outside = [c['text'] for c in page.chars if c['x0'] < -0.5 or c['x1'] > page.width+0.5
                   or c['top'] < -0.5 or c['bottom'] > page.height+0.5]
        fonts=sorted({c['fontname'].split('+')[-1] for c in page.chars})
        smallest=min(c['size'] for c in page.chars)
        pages.append({'page':slide['id'],'missing_strings':missing,'out_of_page_count':len(outside),
                      'fonts':fonts,'minimum_font_pt':round(smallest,3),
                      'passed':not missing and not outside and smallest>=13.95 and
                      all('Pretendard' in font for font in fonts)})
    report={'pdf_sha256':hashlib.sha256(PDF.read_bytes()).hexdigest(),'page_count':len(pdf.pages),
            'expected_page_count':len(spec['slides']),'pages':pages,
            'result':'passed' if len(pdf.pages)==len(spec['slides']) and all(p['passed'] for p in pages) else 'failed',
            'claim_boundary':'PDF 텍스트와 글꼴 검사이며 개별 PNG 시각 검수를 대체하지 않음'}
(BASE/'build/preview_validation_v2.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'result':report['result'],'page_count':report['page_count'],
                  'failed_pages':[p for p in pages if not p['passed']]},ensure_ascii=False))
raise SystemExit(0 if report['result']=='passed' else 1)
