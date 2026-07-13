export const meta = {
  name: 'batch-tcms-automate',
  description: '吃一串 TCMS ID，並行把每個 case 全平台實作到閉環達標（per-platform 交付 gate + 忠實度 review + 回修）。harness 未來只丟一串 TCMS ID 進來。',
  phases: [
    { title: 'Implement', detail: '每個 case 一個 qa-case-automator（worktree 隔離、並行模式）全平台實作' },
    { title: 'Gate+Review', detail: 'per-platform 交付 gate + 忠實度 review，未達標回修（最多 3 輪）' },
  ],
}

// ── 常數（harness 接入時可改成從 args 物件帶入）────────────────────────
const REPO = '/Users/eden.lai/Downloads/qa_test/test/kkday-QA-automation'
const SKILLS = '/Users/eden.lai/Downloads/ai/kkday-qa-skills'
const MAX_FIX_ROUNDS = 3

// ── 入口：args = 一串 TCMS ID（陣列，或空白/逗號分隔字串）──────────────
const caseIds = Array.isArray(args)
  ? args
  : String(args || '').split(/[\s,]+/).filter(Boolean)
if (!caseIds.length) {
  throw new Error('需要 args = 一串 TCMS ID，例 ["KQT-T37931","KQT-T37932"] 或 "KQT-T37931 KQT-T37932"')
}
log(`批次自動化 ${caseIds.length} 個 case：${caseIds.join(', ')}`)

const IMPL_SCHEMA = {
  type: 'object',
  additionalProperties: true,
  properties: {
    caseid: { type: 'string' },
    tags_platforms: { type: 'array', items: { type: 'string' } },
    per_platform: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: true,
        properties: {
          platform: { type: 'string' },
          status: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
        },
        required: ['platform', 'status'],
      },
    },
    traceability: { type: 'string' },
  },
  required: ['caseid', 'tags_platforms', 'per_platform'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: true,
  properties: {
    caseid: { type: 'string' },
    delivered: { type: 'boolean' },
    gate_missing: { type: 'array', items: { type: 'string' } },
    fidelity_issues: { type: 'array', items: { type: 'string' } },
    fix_instructions: { type: 'string' },
  },
  required: ['caseid', 'delivered'],
}

function implPrompt(caseId, fixNote) {
  return `你是 qa-case-automator，**並行模式**。case=${caseId}。
${fixNote ? `這是回修，請針對以下未達標點補實作：${fixNote}\n` : ''}
並行模式規則（照 qa-case-automator.md §3.5）：
- 驗元素用**各自 launch 的 Python playwright**，不用共享 playwright MCP browser（會搶）。
- 在你所在的 git worktree 內寫檔，不自己做 git 操作。
- **禁打 prod**：開頁 host 依環境組出 www{suffix}.kkday.com，用 stage / sit，絕不碰 www.kkday.com。

全平台鐵則（照 §2）：撈 case 的 tag，**tag 標的所有平台都要涵蓋，且平台間「共用」同一份 case + test_step**（不是各寫一份，只有些許步驟不同）：
- web ↔ mweb 共用一份（web_playwright/）：做 web 就**一併**補 mweb 的 limit_test_platform:test_platform:mweb entry + 些許 [M] 差異步驟，不是拿 web case 硬套 --platform mweb、也不是另開一份。
- android ↔ ios 共用一份（mobile/），靠 [iOS]/[Android] 標記分差異；需實體機，沒設備該平台標 blocked+原因，共用的其餘平台照做。
每個平台逐一跑過（qa-test-runner，HEADLESS=1）。

回傳結構化：caseid、tags_platforms（該 case tag 標的平台）、per_platform（每平台 platform+status(pass/fail/blocked)+files）、traceability（step→assertion 可追溯表）。`
}

// 獨立驗證：確定性 gate（矇混不過）+ per-platform 忠實度 review。不信 automator 自評。
async function verify(caseId, impl) {
  const tags = (impl.tags_platforms || []).join(',')
  const resultsLines = (impl.per_platform || [])
    .map((p) => JSON.stringify({ caseid: caseId, platform: p.platform, status: p.status }))
    .join('\n')
  return await agent(
    `你是 per-platform 交付驗證員（獨立、對抗式，不信 automator 自評）。case=${caseId}，tag 要求平台=${tags}。

步驟 1 — 確定性 gate（Bash，矇混不過）：
把下列每行寫進 /tmp/results_${caseId}.jsonl：
${resultsLines || '（automator 未回報任何平台結果）'}
再執行：
  python3 ${SKILLS}/scripts/check_platform_delivery.py --caseid ${caseId} --tags ${tags} --repo ${REPO} --results /tmp/results_${caseId}.jsonl
讀 exit code 與 JSON。missing_registration = yaml 沒有正確平台註冊（例：mweb 缺 limit_test_platform:mweb entry）；missing_pass = 該平台沒真的跑 pass。

步驟 2 — 對**每個已交付平台**做忠實度 review：比對 TCMS case 規格 vs 實作斷言，抓「沒真的驗到的 expected」「過弱/恆真斷言」「keyword/參數收了沒用」等自我美化。

回傳：delivered（gate 全過 **且** 每個平台 fidelity 都達標才 true）、gate_missing（gate 缺的平台）、fidelity_issues（各平台忠實度問題）、fix_instructions（要 automator 具體補什麼）。`,
    { label: `verify:${caseId}`, phase: 'Gate+Review', schema: VERDICT_SCHEMA }
  )
}

// ── pipeline：每個 case 獨立流過 實作 → gate+review+回修，彼此不等 ──────
const results = await pipeline(
  caseIds,
  (caseId) =>
    agent(implPrompt(caseId), {
      label: `impl:${caseId}`,
      phase: 'Implement',
      isolation: 'worktree',
      agentType: 'qa-case-automator',
      schema: IMPL_SCHEMA,
    }),
  async (impl, caseId) => {
    if (!impl) return { caseId, delivered: false, error: 'automator 無回傳（可能中途失敗）' }
    let cur = impl
    let round = 0
    let verdict = await verify(caseId, cur)
    while (verdict && !verdict.delivered && round < MAX_FIX_ROUNDS) {
      round++
      const gaps = (verdict.gate_missing || []).concat(verdict.fidelity_issues || []).join('；')
      log(`${caseId} 未達標（round ${round}/${MAX_FIX_ROUNDS}）：${gaps}`)
      const fixNote = `gate 缺平台=${JSON.stringify(verdict.gate_missing || [])}；忠實度問題=${JSON.stringify(verdict.fidelity_issues || [])}。${verdict.fix_instructions || ''}`
      cur = await agent(implPrompt(caseId, fixNote), {
        label: `fix:${caseId}#${round}`,
        phase: 'Implement',
        isolation: 'worktree',
        agentType: 'qa-case-automator',
        schema: IMPL_SCHEMA,
      })
      if (!cur) break
      verdict = await verify(caseId, cur)
    }
    return { caseId, rounds: round, ...(verdict || { delivered: false }), impl: cur }
  }
)

// ── 收斂：達標 vs 未達標；統一開 PR 由主對話收 worktree 後處理（需先問使用者）──
const delivered = results.filter((r) => r && r.delivered)
const failed = results.filter((r) => r && !r.delivered)
log(`完成：${delivered.length}/${caseIds.length} 全平台交付達標；${failed.length} 未達標`)

return {
  total: caseIds.length,
  delivered: delivered.map((r) => r.caseId),
  failed: failed.map((r) => ({
    caseId: r.caseId,
    rounds: r.rounds,
    gate_missing: r.gate_missing,
    fidelity_issues: r.fidelity_issues,
  })),
  note:
    '達標 case 已在各自 git worktree 完成全平台實作。主對話收攏各 worktree 改動、統一開「一個」PR（依規範須先問使用者是否開 PR）。未達標的排入待人工。',
}
