// SQL 검색기 설계서 빌드. 입력은 검증된 work/slides_v2.json임.
// 실행: NODE_PATH=<pptxgenjs 설치 폴더> node build/build_deck.js
const fs = require('fs');
const path = require('path');
const pptxgen = require('pptxgenjs');
const BASE = path.resolve(__dirname, '..');
const SPEC = JSON.parse(fs.readFileSync(path.join(BASE, 'work/slides_v2.json'), 'utf8'));
const FONT = 'Pretendard';
const MIN_FONT = 14;
const LAYOUT = '내용_단쪽';
const X0 = 0.557, CW = 14.9, BODY_TOP = 1.90, BODY_BOTTOM = 7.95;
const C = {
  title:'2C2926', lead:'000000', navy:'1E2A5C', blue:'2E74C6', ink:'2B3242',
  slate:'4A5364', sub:'7C8598', tint:'EEF3FA', altRow:'F5F8FC', tableHead:'E2EEF9',
  dark:'404155', border:'D9E0EC', line:'EDF0F6', titleLine:'E2E8F0',
  footLine:'E9ECF3', pageNum:'6B6B7B', white:'FFFFFF', copyright:'A6A6A6',
};
let pptx;
const layoutLog = [];
function fsMin(size) {
  if (size < MIN_FONT) throw new Error(`fontSize ${size} < ${MIN_FONT}`);
  return size;
}
function text(slide, value, options) {
  slide.addText(String(value), {fontFace:FONT, fontSize:fsMin(18), color:C.ink,
    margin:0, breakLine:false, valign:'middle', ...options});
}
function headerBar(slide, {x,y,w,text:label,accent=false}) {
  slide.addShape(pptx.shapes.ROUNDED_RECTANGLE,{x,y,w,h:0.5,rectRadius:0.06,
    fill:{color:accent?C.blue:C.navy},line:{type:'none'}});
  text(slide,label,{x,y,w,h:0.5,align:'center',color:C.white,bold:true,fontSize:fsMin(24)});
}
function numBadge(slide,{x,y,n,color=C.blue,size=0.4}) {
  slide.addShape(pptx.shapes.ROUNDED_RECTANGLE,{x,y,w:size,h:size,rectRadius:0.05,
    fill:{color},line:{type:'none'}});
  text(slide,n,{x,y,w:size,h:size,align:'center',color:C.white,bold:true,fontSize:fsMin(18)});
}
function defineContentLayout() {
  pptx.defineSlideMaster({title:LAYOUT,background:{color:C.white},objects:[
    {line:{x:0.556,y:0.95,w:14.888,h:0,line:{color:C.titleLine,width:1.5}}},
    {text:{text:'무단전재 및 배포 금지',options:{x:0.4875,y:8.517,w:2.1,h:0.27,
      fontFace:FONT,fontSize:fsMin(14),color:C.copyright,margin:0}}},
    {placeholder:{options:{name:'title',type:'title',x:X0,y:0.109,w:15.025,h:0.906,
      fontFace:FONT,fontSize:fsMin(32),bold:true,color:C.title,margin:0,align:'left',valign:'middle'},text:''}},
  ],slideNumber:{x:11.913,y:8.517,w:3.6,h:0.25,fontFace:FONT,fontSize:fsMin(16),color:C.pageNum,align:'right'}});
}
function pageHeader(slide,{title,lead}) {
  slide.addText(title,{placeholder:'title',fontFace:FONT,fontSize:fsMin(32),bold:true,color:C.title});
  if(lead) text(slide,lead,{x:X0,y:1.259,w:CW,h:0.42,fontSize:fsMin(18),bold:true,color:C.lead});
}
function sourceLine(slide,label) {
  slide.addShape(pptx.shapes.LINE,{x:X0,y:8.08,w:CW,h:0,line:{color:C.footLine,width:1}});
  text(slide,label,{x:X0,y:8.14,w:CW,h:0.32,fontSize:fsMin(14),color:C.sub});
}
function widthUnits(value) {
  return [...String(value)].reduce((sum,ch)=>sum+(ch.charCodeAt(0)>255?1:(/[MW@]/.test(ch)?0.8:(/[il.,'| :]/.test(ch)?0.28:0.53))),0);
}
function estimatedLines(value,width,font) {
  return String(value).split('\n').reduce((sum,line)=>sum+Math.max(1,Math.ceil(widthUnits(line)*font/(width*72-20))),0);
}
function inferWidths(spec) {
  if(spec.col_widths) return spec.col_widths;
  const n=spec.columns.length;
  if(n===2) return [2.8,12.1];
  if(n===3) return [3.8,3.5,7.6];
  if(n===4) return [2.2,3.4,3.0,6.3];
  return Array(n).fill(CW/n);
}
function tableSlide(slide,spec) {
  const colW=inferWidths(spec);
  let size=18, rowHeights=[];
  const allRows=[spec.columns,...spec.rows];
  for(;size>=MIN_FONT;size--) {
    rowHeights=allRows.map((row,i)=>Math.max(i===0?0.45:0.36,
      ...row.map((v,j)=>(estimatedLines(v,colW[j],size)*size*1.18+12)/72)));
    if(rowHeights.reduce((a,b)=>a+b,0)<=BODY_BOTTOM-BODY_TOP) break;
  }
  if(size<MIN_FONT) throw new Error(`Slide ${spec.id}: table too tall. Split content.`);
  // 짧은 표는 행 높이를 늘려 하단의 과도한 빈 공간을 줄임.
  const natural=rowHeights.reduce((a,b)=>a+b,0);
  const desired=Math.max(natural,5.85);
  rowHeights=rowHeights.map(h=>h*desired/natural);
  const rows=allRows.map((row,i)=>row.map((cell,j)=>({text:String(cell),options:{
    fill:i===0?C.tableHead:(i%2===0?C.altRow:C.white),
    color:i===0?C.navy:C.ink,bold:i===0||(j===0&&spec.columns.length===2),
    fontFace:FONT,fontSize:fsMin(size),valign:'middle',
    margin:[6,10,6,10],
  }})));
  slide.addTable(rows,{x:X0,y:BODY_TOP,w:CW,colW,rowH:rowHeights,
    fontFace:FONT,fontSize:fsMin(size),margin:[6,10,6,10],
    border:{type:'solid',color:C.line,pt:0.75},autoPage:false,verbose:false,
    paraSpaceAfterPt:0,breakLine:false});
  layoutLog.push({id:spec.id,kind:spec.kind,font_size:size,estimated_height:rowHeights.reduce((a,b)=>a+b,0)});
}
function textSlide(slide,spec) {
  const lines=spec.text_lines||[];
  const header=spec.section_label||'설계 기준';
  headerBar(slide,{x:X0,y:BODY_TOP,w:CW,text:header});
  const top=2.65, room=5.10;
  const font=lines.length<=5?22:18;
  const weights=lines.map(v=>Math.max(1,estimatedLines(v,CW-0.9,font)));
  const total=weights.reduce((a,b)=>a+b,0);
  const unit=Math.min(1.05,room/Math.max(total,1));
  let y=top;
  for(let i=0;i<lines.length;i++) {
    const h=unit*weights[i];
    numBadge(slide,{x:X0,y:y+0.06,n:i+1,size:0.42});
    text(slide,lines[i],{x:X0+0.7,y,w:CW-0.8,h:h-0.08,fontSize:fsMin(font),valign:'top',breakLine:false});
    if(i<lines.length-1) slide.addShape(pptx.shapes.LINE,{x:X0+0.7,y:y+h-0.03,w:CW-0.8,h:0,line:{color:C.line,width:1}});
    y+=h;
  }
  if(y>BODY_BOTTOM+0.01) throw new Error(`Slide ${spec.id}: text overflow`);
}
function coverSlide(slide,spec) {
  slide.addShape(pptx.shapes.RECTANGLE,{x:X0,y:2.25,w:0.1,h:5.5,fill:{color:C.blue},line:{type:'none'}});
  text(slide,'질문에서 검증된 조회까지',{x:X0+0.45,y:2.55,w:13.9,h:0.7,fontSize:fsMin(34),bold:true,color:C.navy});
  const lines=spec.text_lines||[];
  let y=3.75;
  lines.forEach((line,i)=>{text(slide,line,{x:X0+0.45,y,w:13.9,h:0.62,fontSize:fsMin(i===0?25:20),color:i===0?C.ink:C.slate});y+=1.12;});
}
function codeSlide(slide,spec) {
  const value=spec.code||'';
  const lineCount=value.split('\n').length;
  const size=18;
  if(lineCount*size*1.2>245) throw new Error(`Slide ${spec.id}: code requires splitting`);
  slide.addShape(pptx.shapes.ROUNDED_RECTANGLE,{x:X0,y:BODY_TOP,w:CW,h:3.80,rectRadius:0.08,
    fill:{color:C.altRow},line:{color:C.border,width:1}});
  text(slide,value,{x:X0+0.24,y:BODY_TOP+0.22,w:CW-0.48,h:3.36,fontSize:fsMin(size),
    valign:'top',breakLine:false,lineSpacingMultiple:1.1});
  const rows=spec.rows;
  slide.addTable(rows,{x:X0,y:6.02,w:CW,colW:[2.8,12.1],rowH:[0.58,0.58,0.58],
    fontFace:FONT,fontSize:fsMin(18),margin:[6,10,6,10],color:C.ink,
    fill:C.white,border:{type:'solid',color:C.line,pt:0.75},autoPage:false});
}
function arrow(slide,x1,y1,x2,y2,{color=C.blue,end=true,dash=false}={}) {
  const opts={x:Math.min(x1,x2),y:Math.min(y1,y2),w:Math.abs(x2-x1),h:Math.abs(y2-y1),
    flipH:x2<x1,flipV:y2<y1,line:{color,width:1.8,...(end?{endArrowType:'triangle'}:{}),...(dash?{dashType:'dash'}:{})}};
  slide.addShape(pptx.shapes.LINE,opts);
}
function flowSlide(slide,spec) {
  const y=3.65,h=1.12,w=1.70;
  const pos={R1:0.557,R2:2.66,F:4.76,L:4.76,R3:7.03,R4:9.13,R5:11.23,R6:13.33};
  function box(id,label,x,yy,shape,color,ww=w,hh=h){
    slide.addShape(shape,{x,y:yy,w:ww,h:hh,...(shape===pptx.shapes.ROUNDED_RECTANGLE?{rectRadius:0.06}:{}),fill:{color},line:{color,width:1}});
    text(slide,label,{x:x+0.07,y:yy+0.10,w:ww-0.14,h:hh-0.20,align:'center',fontSize:fsMin(16),color:C.white,bold:true});
  }
  box('R1','S-R1\n요청 접수와 검증',pos.R1,y,pptx.shapes.ROUNDED_RECTANGLE,C.navy);
  box('R2','S-R2\n조회 경로 선택',pos.R2,y,pptx.shapes.DIAMOND,C.navy);
  box('F','고정 Q-ID와\nSQL 선택',pos.F,2.60,pptx.shapes.ROUNDED_RECTANGLE,C.navy,1.90,1.0);
  box('L','Groq SQL 생성\n최대 1회',pos.L,4.82,pptx.shapes.ROUNDED_RECTANGLE,C.blue,1.90,1.0);
  box('R3','S-R3\n계획 적합성',pos.R3,y,pptx.shapes.ROUNDED_RECTANGLE,C.navy);
  box('R4','S-R4\nSQL 검증',pos.R4,y,pptx.shapes.ROUNDED_RECTANGLE,C.navy);
  box('R5','S-R5\n내부 DB 조회',pos.R5,y,pptx.shapes.CAN,C.slate);
  box('R6','S-R6\n응답 구성과 반환',pos.R6,y,pptx.shapes.ROUNDED_RECTANGLE,C.navy);
  arrow(slide,pos.R1+w,y+h/2,pos.R2,y+h/2);
  arrow(slide,pos.R2+w/2,y,pos.R2+w/2,3.10,{end:false});
  arrow(slide,pos.R2+w/2,3.10,pos.F,3.10);
  arrow(slide,pos.R2+w/2,y+h,pos.R2+w/2,5.32,{end:false});
  arrow(slide,pos.R2+w/2,5.32,pos.L,5.32);
  text(slide,'일치',{x:3.69,y:2.64,w:0.75,h:0.3,fontSize:fsMin(14),color:C.navy,align:'center'});
  text(slide,'미일치',{x:3.69,y:5.42,w:0.88,h:0.3,fontSize:fsMin(14),color:C.navy,align:'center'});
  arrow(slide,pos.F+1.9,3.1,6.86,3.1,{end:false});
  arrow(slide,6.86,3.1,6.86,y+h/2,{end:false});
  arrow(slide,pos.L+1.9,5.32,6.86,5.32,{end:false});
  arrow(slide,6.86,5.32,6.86,y+h/2,{end:false});
  arrow(slide,6.86,y+h/2,pos.R3,y+h/2);
  ['R3','R4','R5'].forEach((id,i)=>arrow(slide,pos[id]+w,y+h/2,pos[['R4','R5','R6'][i]],y+h/2));
  text(slide,'동기 요청',{x:pos.R1,y:2.48,w,h:0.38,fontSize:fsMin(18),bold:true,align:'center',color:C.navy});
  arrow(slide,pos.R1+w/2,2.96,pos.R1+w/2,y);
  text(slide,'종료',{x:pos.R6,y:2.48,w,h:0.38,fontSize:fsMin(18),bold:true,align:'center',color:C.navy});
  arrow(slide,pos.R6+w/2,y,pos.R6+w/2,2.96);
  const bus=6.48;
  ['R1','R2','R3','R4','R5'].forEach(id=>{
    const cx=pos[id]+(id==='R2'?w/4:w/2);
    const startY=y+(id==='R2'?h*0.75:h);
    arrow(slide,cx,startY,cx,bus,{color:C.dark,end:false,dash:true});
  });
  arrow(slide,pos.L+0.95,5.82,pos.L+0.95,bus,{color:C.dark,end:false,dash:true});
  arrow(slide,pos.R1+w/2,bus,pos.R6+w/2,bus,{color:C.dark,end:false,dash:true});
  arrow(slide,pos.R6+w/2,bus,pos.R6+w/2,y+h,{color:C.dark,dash:true});
  text(slide,'오류 또는 시간 부족 → S-R6에서 오류 반환. SQL 재생성 없음',
    {x:X0,y:6.87,w:CW,h:0.38,fontSize:fsMin(18),color:C.dark});
  const legends=[['규칙 코드',C.navy],['LLM 호출',C.blue],['내부 DB',C.slate],['오류 경로: 점선',C.dark]];
  legends.forEach(([label,color],i)=>{const x=X0+i*3.60;slide.addShape(pptx.shapes.RECTANGLE,{x,y:7.55,w:0.25,h:0.25,fill:{color},line:{color}});text(slide,label,{x:x+0.38,y:7.48,w:3.0,h:0.36,fontSize:fsMin(16),color:C.ink});});
}
function makeSlide(p,spec) {
  const slide=p.addSlide({masterName:LAYOUT});
  slide.background={color:C.white};
  pageHeader(slide,spec);
  if(spec.kind==='table') tableSlide(slide,spec);
  else if(spec.kind==='cover') coverSlide(slide,spec);
  else if(spec.kind==='flow') flowSlide(slide,spec);
  else if(spec.kind==='code') codeSlide(slide,spec);
  else textSlide(slide,spec);
  sourceLine(slide,spec.source||'출처: 승인된 설계 결정');
  slide.addNotes([spec.notes||'',spec.design||''].filter(Boolean).join('\n'));
  return slide;
}

async function createSlide01(p) { return makeSlide(p,SPEC.slides[0]); }
async function createSlide02(p) { return makeSlide(p,SPEC.slides[1]); }
async function createSlide03(p) { return makeSlide(p,SPEC.slides[2]); }
async function createSlide04(p) { return makeSlide(p,SPEC.slides[3]); }
async function createSlide05(p) { return makeSlide(p,SPEC.slides[4]); }
async function createSlide06(p) { return makeSlide(p,SPEC.slides[5]); }
async function createSlide07(p) { return makeSlide(p,SPEC.slides[6]); }
async function createSlide08(p) { return makeSlide(p,SPEC.slides[7]); }
async function createSlide09(p) { return makeSlide(p,SPEC.slides[8]); }
async function createSlide10(p) { return makeSlide(p,SPEC.slides[9]); }
async function createSlide11(p) { return makeSlide(p,SPEC.slides[10]); }
async function createSlide12(p) { return makeSlide(p,SPEC.slides[11]); }
async function createSlide13(p) { return makeSlide(p,SPEC.slides[12]); }
async function createSlide14(p) { return makeSlide(p,SPEC.slides[13]); }
async function createSlide15(p) { return makeSlide(p,SPEC.slides[14]); }
async function createSlide16(p) { return makeSlide(p,SPEC.slides[15]); }
async function createSlide17(p) { return makeSlide(p,SPEC.slides[16]); }
async function createSlide18(p) { return makeSlide(p,SPEC.slides[17]); }
async function createSlide19(p) { return makeSlide(p,SPEC.slides[18]); }
async function createSlide20(p) { return makeSlide(p,SPEC.slides[19]); }
async function createSlide21(p) { return makeSlide(p,SPEC.slides[20]); }
async function createSlide22(p) { return makeSlide(p,SPEC.slides[21]); }
async function createSlide23(p) { return makeSlide(p,SPEC.slides[22]); }
async function createSlide24(p) { return makeSlide(p,SPEC.slides[23]); }
async function createSlide25(p) { return makeSlide(p,SPEC.slides[24]); }
async function createSlide26(p) { return makeSlide(p,SPEC.slides[25]); }
async function createSlide27(p) { return makeSlide(p,SPEC.slides[26]); }
async function createSlide28(p) { return makeSlide(p,SPEC.slides[27]); }
async function createSlide29(p) { return makeSlide(p,SPEC.slides[28]); }
async function createSlide30(p) { return makeSlide(p,SPEC.slides[29]); }
async function createSlide31(p) { return makeSlide(p,SPEC.slides[30]); }
async function createSlide32(p) { return makeSlide(p,SPEC.slides[31]); }
async function createSlide33(p) { return makeSlide(p,SPEC.slides[32]); }
async function createSlide34(p) { return makeSlide(p,SPEC.slides[33]); }
async function createSlide35(p) { return makeSlide(p,SPEC.slides[34]); }
async function createSlide36(p) { return makeSlide(p,SPEC.slides[35]); }
async function createSlide37(p) { return makeSlide(p,SPEC.slides[36]); }
async function createSlide38(p) { return makeSlide(p,SPEC.slides[37]); }
async function createSlide39(p) { return makeSlide(p,SPEC.slides[38]); }
async function createSlide40(p) { return makeSlide(p,SPEC.slides[39]); }
async function createSlide41(p) { return makeSlide(p,SPEC.slides[40]); }
async function createSlide42(p) { return makeSlide(p,SPEC.slides[41]); }
async function createSlide43(p) { return makeSlide(p,SPEC.slides[42]); }
async function createSlide44(p) { return makeSlide(p,SPEC.slides[43]); }
async function createSlide45(p) { return makeSlide(p,SPEC.slides[44]); }
async function createSlide46(p) { return makeSlide(p,SPEC.slides[45]); }
async function createSlide47(p) { return makeSlide(p,SPEC.slides[46]); }
async function createSlide48(p) { return makeSlide(p,SPEC.slides[47]); }
async function createSlide49(p) { return makeSlide(p,SPEC.slides[48]); }
async function createSlide50(p) { return makeSlide(p,SPEC.slides[49]); }
async function createSlide51(p) { return makeSlide(p,SPEC.slides[50]); }
async function createSlide52(p) { return makeSlide(p,SPEC.slides[51]); }
async function createSlide53(p) { return makeSlide(p,SPEC.slides[52]); }
async function createSlide54(p) { return makeSlide(p,SPEC.slides[53]); }
async function createSlide55(p) { return makeSlide(p,SPEC.slides[54]); }
async function createSlide56(p) { return makeSlide(p,SPEC.slides[55]); }
async function createSlide57(p) { return makeSlide(p,SPEC.slides[56]); }
async function createSlide58(p) { return makeSlide(p,SPEC.slides[57]); }
async function createSlide59(p) { return makeSlide(p,SPEC.slides[58]); }
async function createSlide60(p) { return makeSlide(p,SPEC.slides[59]); }
async function createSlide61(p) { return makeSlide(p,SPEC.slides[60]); }
async function createSlide62(p) { return makeSlide(p,SPEC.slides[61]); }
const CREATE_SLIDES=[createSlide01,createSlide02,createSlide03,createSlide04,createSlide05,createSlide06,createSlide07,createSlide08,createSlide09,createSlide10,createSlide11,createSlide12,createSlide13,createSlide14,createSlide15,createSlide16,createSlide17,createSlide18,createSlide19,createSlide20,createSlide21,createSlide22,createSlide23,createSlide24,createSlide25,createSlide26,createSlide27,createSlide28,createSlide29,createSlide30,createSlide31,createSlide32,createSlide33,createSlide34,createSlide35,createSlide36,createSlide37,createSlide38,createSlide39,createSlide40,createSlide41,createSlide42,createSlide43,createSlide44,createSlide45,createSlide46,createSlide47,createSlide48,createSlide49,createSlide50,createSlide51,createSlide52,createSlide53,createSlide54,createSlide55,createSlide56,createSlide57,createSlide58,createSlide59,createSlide60,createSlide61,createSlide62];

async function main() {
  pptx=new pptxgen();
  pptx.defineLayout({name:'CUSTOM',width:16,height:9});pptx.layout='CUSTOM';
  pptx.author='law-consult';pptx.subject='SQL 검색기 워크플로우 설계';
  pptx.title='SQL 검색기 워크플로우 설계서';pptx.company='law-consult';
  pptx.lang='ko-KR';pptx.theme={headFontFace:FONT,bodyFontFace:FONT,lang:'ko-KR'};
  defineContentLayout();
  for(const create of CREATE_SLIDES) await create(pptx);
  await pptx.writeFile({fileName:path.join(__dirname,'candidate_v2.pptx')});
  fs.writeFileSync(path.join(__dirname,'layout_log_v2.json'),JSON.stringify(layoutLog,null,2));
  console.log(`PPT 생성 완료: ${SPEC.slides.length}장`);
}
main().catch(e=>{console.error('PPT 생성 실패:',e.message);process.exit(1);});
