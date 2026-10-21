"""PPTX 개체 틀 정리와 구조 검증. 시각 검증은 LibreOffice 미리보기로 별도 수행."""
from pathlib import Path
import argparse
import json
import re
import zipfile
from lxml import etree as E

NS={'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
    'p':'http://schemas.openxmlformats.org/presentationml/2006/main',
    'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def normalize(deck):
    temporary=deck.with_suffix('.fixed.pptx')
    with zipfile.ZipFile(deck) as source,zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            if item.is_dir():
                continue
            data=source.read(item.filename)
            if item.filename == '[Content_Types].xml':
                root=E.fromstring(data)
                for entry in list(root):
                    target=entry.get('PartName','').lstrip('/')
                    # PptxGenJS의 없는 추가 마스터 선언만 제거함. 실제 마스터는 유지함.
                    if target.startswith('ppt/slideMasters/') and target not in source.namelist():
                        root.remove(entry)
                data=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
            if item.filename.startswith('ppt/') and item.filename.endswith('.xml'):
                root=E.fromstring(data)
                for ph in root.xpath('//p:ph',namespaces=NS):
                    if ph.get('type')=='title': ph.attrib.pop('idx',None)
                    if ph.get('type')=='sldNum': ph.set('idx','4')
                for font in root.xpath('//a:latin|//a:ea|//a:cs|//a:font',namespaces=NS):
                    font.set('typeface','Pretendard')
                data=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
            out.writestr(item,data)
    temporary.replace(deck)


def validate(deck):
    errors=[];sizes=[];tables=[];shapes=[];texts={}
    with zipfile.ZipFile(deck) as z:
        if any(i.is_dir() for i in z.infolist()): errors.append('빈 폴더 항목 존재')
        root=E.fromstring(z.read('ppt/presentation.xml'))
        size=root.find('p:sldSz',NS)
        if (size.get('cx'),size.get('cy'))!=('14630400','8229600'): errors.append('슬라이드 크기 불일치')
        slidefiles=sorted([n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)],key=lambda n:int(re.search(r'slide(\d+)',n)[1]))
        for i,name in enumerate(slidefiles,1):
            root=E.fromstring(z.read(name))
            text='\n'.join(root.xpath('//a:t/text()',namespaces=NS));texts[i]=text
            if '·' in text: errors.append(f'{i}쪽 가운뎃점 존재')
            title=root.xpath('//p:sp[p:nvSpPr/p:nvPr/p:ph[@type="title"]]',namespaces=NS)
            if not title or not title[0].xpath('.//a:t/text()',namespaces=NS): errors.append(f'{i}쪽 제목 틀 없음')
            if root.xpath('//a:schemeClr',namespaces=NS): errors.append(f'{i}쪽 테마색 사용')
            for run in root.xpath('//*[@sz]',namespaces=NS):
                try:
                    size=float(run.get('sz'))/100;sizes.append(size)
                    if size<14: errors.append(f'{i}쪽 14pt 미만 글자 {size}')
                except ValueError: pass
            tables.append(len(root.xpath('//a:tbl',namespaces=NS)))
            shapes.append(len(root.xpath('//p:sp',namespaces=NS)))
            rels=E.fromstring(z.read(f'ppt/slides/_rels/slide{i}.xml.rels'))
            for rel in rels:
                if rel.get('Type','').endswith('/slideLayout'):
                    target=rel.get('Target').replace('../','ppt/',1)
                    layout=E.fromstring(z.read(target))
                    common=layout.find('p:cSld',NS)
                    if common.get('name')!='내용_단쪽': errors.append(f'{i}쪽 레이아웃 이름 불일치')
        for name in z.namelist():
            if name.startswith('ppt/') and name.endswith('.xml'):
                root=E.fromstring(z.read(name))
                fonts=root.xpath('//a:latin/@typeface|//a:ea/@typeface|//a:cs/@typeface',namespaces=NS)
                if any(f!='Pretendard' for f in fonts): errors.append(f'글꼴 불일치 {name}')
                if name.startswith('ppt/notesSlides/') and '·' in ''.join(root.xpath('//a:t/text()',namespaces=NS)):
                    errors.append(f'발표자 노트의 가운뎃점 {name}')
    spec=json.loads((Path(__file__).resolve().parents[1]/'work/slides_v2.json').read_text())
    expected_tables=[slide['id'] for slide in spec['slides'] if slide['kind'] in ('table','code')]
    for number in expected_tables:
        if tables[number-1] != 1: errors.append(f'{number}쪽 편집 가능 표 수 불일치')
    report={'required_table_slides':expected_tables,'slide_count':len(slidefiles),'minimum_explicit_font_pt':min(sizes),'native_tables':tables,
            'native_shapes':shapes,'errors':errors,'result':'passed' if not errors else 'failed',
            'visual_review_required':True}
    ((deck.parent if deck.parent.name=='build' else deck.parent/'build')/'package_validation_v2.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    ((deck.parent if deck.parent.name=='build' else deck.parent/'build')/'extracted_text_v2.json').write_text(json.dumps(texts,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
    return not errors


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('deck',type=Path);parser.add_argument('--normalize',action='store_true')
    args=parser.parse_args()
    if args.normalize: normalize(args.deck)
    raise SystemExit(0 if validate(args.deck) else 1)
