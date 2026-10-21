import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const build=path.dirname(fileURLToPath(import.meta.url));
const base=path.dirname(build);
const skill='/Users/jobum/.codex/plugins/cache/openai-primary-runtime/presentations/26.1013.11914/skills/presentations';
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')).href);
const spec=JSON.parse(await fs.readFile(path.join(base,'work/slides_v2.json'),'utf8'));
const tableOwners=spec.slides.filter(s=>s.kind==='table'||s.kind==='code').map(s=>s.id);
try {
  const results=[];
  // Finalizer policy supports at most 96 arguments; verify all table owners in two batches.
  for (let offset=0; offset<tableOwners.length; offset+=40) {
    const owners=tableOwners.slice(offset,offset+40);
    const result=await finalizePresentation({
    workspaceDir:base,
    candidatePath:path.join(build,'candidate_v2.pptx'),
    finalPath:path.join(build,'final',`sql_retriever_설계서_v2_batch${offset/40+1}.pptx`),
    pythonExecutable:'/Users/jobum/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3',
    integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
    layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
    layoutArgs:['--expected-slide-size-emu','14630400,8229600','--validate-bullet-geometry',
      '--validate-heading-fit','--cover-role','cover', ...owners.flatMap(id=>['--require-native-table-slide',String(id)])],
    fontPolicy:{basis:'user_request',families:['Pretendard']},
    verifyArtifactToolImport:true,
    requiredNativeTableOwnerSlides:owners,
    receiptPath:path.join(build,`final_validation_v2_${offset/40+1}.json`),
  });
    results.push(result);
  }
  await fs.copyFile(path.join(build,'final',`sql_retriever_설계서_v2_batch${Math.ceil(tableOwners.length/40)}.pptx`),path.join(base,'sql_retriever_설계서_v2.pptx'));
  console.log(JSON.stringify({finalized:true,results}));
} catch (error) {
  console.error(error.message);
  if(error.report)console.error(JSON.stringify(error.report));
  process.exit(1);
}
