import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const build=path.dirname(fileURLToPath(import.meta.url));
const base=path.dirname(build);
const skill='/Users/jobum/.codex/plugins/cache/openai-primary-runtime/presentations/26.1013.11914/skills/presentations';
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')).href);
const spec=JSON.parse(await fs.readFile(path.join(base,'work/slides.json'),'utf8'));
const tableOwners=spec.slides.filter(s=>s.kind==='table').map(s=>s.id);
try {
  const result=await finalizePresentation({
    workspaceDir:base,
    candidatePath:path.join(build,'candidate.pptx'),
    finalPath:path.join(build,'final','sql_retriever_워크플로우설계서_과제완성본.pptx'),
    pythonExecutable:'/Users/jobum/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3',
    integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
    layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
    layoutArgs:['--expected-slide-size-emu','14630400,8229600','--validate-bullet-geometry',
      '--validate-heading-fit'],
    fontPolicy:{basis:'user_request',families:['Pretendard']},
    verifyArtifactToolImport:true,
    receiptPath:path.join(build,'final_validation.json'),
  });
  await fs.copyFile(path.join(build,'final','sql_retriever_워크플로우설계서_과제완성본.pptx'),
    path.join(base,'sql_retriever_워크플로우설계서_과제완성본.pptx'));
  console.log(JSON.stringify({finalized:true,result}));
} catch (error) {
  console.error(error.message);
  if(error.report)console.error(JSON.stringify(error.report));
  process.exit(1);
}
