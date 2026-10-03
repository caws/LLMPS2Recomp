export const meta = {
  name: 're-fanout',
  description: 'Read-only multi-agent static-RE fan-out: N agents chase sub-questions in parallel, one synthesizes a buildable override',
  whenToUse: 'When a PS2Recomp frontier is an architecture QUESTION (what sequences X? what invokes Y? where do these vtables come from?) rather than a single CSV/override bug. Sub-agents are READ-ONLY (disasm/grep/generated-code only); only the main agent builds.',
  phases: [ { title: 'Investigate' }, { title: 'Synthesize' } ],
}

// args = {
//   context: string,                          // shared CTX: paths, funcs.py cmd, gp value,
//                                              //   the READ-ONLY constraint, state-of-play, GOAL
//   questions: [ { key: string, q: string } ],// one focused, non-overlapping sub-question per agent
//   synthesis?: string,                        // optional extra instruction for the synthesis agent
// }
const A = args || {}
const CTX = A.context || ''
const QUESTIONS = Array.isArray(A.questions) ? A.questions : []
if (!CTX || !QUESTIONS.length) {
  log('re-fanout: needs args.context (string) and args.questions ([{key,q}]). Nothing to do.')
  return { error: 'missing args.context or args.questions' }
}

const READONLY = `\n\nHARD CONSTRAINT: READ-ONLY. Use ONLY Read, Grep, and Bash for static RE` +
  ` (funcs.py disasm/bounds/find/scan, grep over tmp/generated, readelf, python ELF scans).` +
  ` DO NOT run scripts/03_build_game.sh, DO NOT run ./tmp/ps2EntryRunner, DO NOT run gdb — the` +
  ` main agent owns builds/runs; concurrent ones collide. Cite guest addresses; verify against` +
  ` disasm + generated C++ (ground truth), not notes.`

const SCHEMA = { type:'object', additionalProperties:false,
  required:['summary','key_addresses','trigger_or_precondition','proposed_override','confidence'],
  properties:{
    summary:{type:'string'},
    key_addresses:{type:'array',items:{type:'object',additionalProperties:false,
      required:['addr','role'],properties:{addr:{type:'string'},role:{type:'string'}}}},
    trigger_or_precondition:{type:'string'},
    proposed_override:{type:'string'},
    confidence:{type:'string',enum:['high','medium','low']},
  } }

phase('Investigate')
const findings = (await parallel(QUESTIONS.map(it => () =>
  agent(`${CTX}${READONLY}\n\n=== YOUR FOCUSED QUESTION (${it.key}) ===\n${it.q}`,
    { label:`re:${it.key}`, phase:'Investigate', schema:SCHEMA }).then(r => ({ key:it.key, ...r }))
))).filter(Boolean)

log(`Investigation: ${findings.length}/${QUESTIONS.length} returned`)

phase('Synthesize')
const SYN = { type:'object', additionalProperties:false,
  required:['root_cause','recommended_override','why_it_works','alternatives','open_questions'],
  properties:{ root_cause:{type:'string'}, recommended_override:{type:'string'},
    why_it_works:{type:'string'}, alternatives:{type:'string'}, open_questions:{type:'string'} } }
const synthesis = await agent(
  `${CTX}${READONLY}\n\n=== SYNTHESIS ===\n${findings.length} read-only RE agents investigated the question.` +
  ` Findings JSON:\n\n${JSON.stringify(findings,null,2)}\n\n` +
  `Reconcile them into the SINGLE best concrete override(s) the main agent can build — expressible as` +
  ` registerFunction hook(s) (read/write rdram, set return reg, set ctx->pc=ra, optionally call a` +
  ` recompiled FUN_ once). Give exact guest addresses, the gate/order, and ranked alternatives.` +
  (A.synthesis ? `\n\nADDITIONAL: ${A.synthesis}` : ''),
  { label:'synthesize', phase:'Synthesize', schema:SYN })

return { findings, synthesis }
