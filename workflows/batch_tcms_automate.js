export const meta = {
  name: 'batch-tcms-automate',
  description: '吃一串 TCMS ID，並行把每個 case 全平台實作到閉環達標（per-platform 交付 gate + 忠實度 review + 回修）。harness 未來只丟一串 TCMS ID 進來。',
  phases: [
    { title: 'Plan', detail: '每個 case 一個 qa-case-planner：抓 spec + 查 flow-registry + 出計畫（前置/斷言/假設），供人確認意圖' },
    { title: 'Implement', detail: '每個 case 一個 qa-case-automator（worktree 隔離、並行模式）照確認的計畫全平台實作' },
    { title: 'Gate+Review', detail: 'per-platform 交付 gate + 忠實度 review，未達標回修（最多 3 輪）' },
  ],
}

// ── 常數（harness 接入時可改成從 args 物件帶入）────────────────────────
const REPO = '/Users/eden.lai/Downloads/qa_test/test/kkday-QA-automation'
const SKILLS = '/Users/eden.lai/Downloads/ai/kkday-qa-skills'
const MAX_FIX_ROUNDS = 3

// ── 入口：args = 一串 TCMS ID（陣列/字串），或 {cases:[...], platforms:[...]}──────
// platforms 用於限制本批平台（如無 App 實體機時限 web,mweb）；不給＝用各 case tag 全平台。
// robust 解析：args 可能是 陣列 / 空白逗號字串 / {cases,platforms} 物件 / 被序列化成 JSON 的字串。
function _parseInput(a) {
  if (a == null) return { cases: [], platforms: null }
  if (typeof a === 'string') {
    const s = a.trim()
    if (s.startsWith('{') || s.startsWith('[')) {
      try {
        return _parseInput(JSON.parse(s))
      } catch (e) {
        /* 非合法 JSON，退為分隔字串 */
      }
    }
    return { cases: s.split(/[\s,]+/).filter(Boolean), platforms: null }
  }
  if (Array.isArray(a)) return { cases: a, platforms: null }
  if (typeof a === 'object')
    return {
      cases: a.cases ?? [],
      platforms: a.platforms ?? null,
      mode: a.mode ?? null, // plan | execute | auto
      plans: a.plans ?? null, // execute 模式：{caseId: 已確認計畫}
    }
  return { cases: [], platforms: null }
}
const _p = _parseInput(args)
const _rawCases = Array.isArray(_p.cases) ? _p.cases.map((c) => String(c).trim()) : []
// 防呆白名單：只認合法 TCMS id（KQT-T#####），垃圾一律丟棄——即使 args 傳壞也不會 spawn 一堆垃圾 automator。
const caseIds = _rawCases.filter((c) => /^KQT-T\d+$/.test(c))
const _dropped = _rawCases.filter((c) => !/^KQT-T\d+$/.test(c))
if (_dropped.length) {
  log(`⚠️ 丟棄 ${_dropped.length} 個非法 caseId（args 可能傳壞）：${_dropped.slice(0, 5).join(' | ')}`)
}
const LIMIT_PLATFORMS =
  Array.isArray(_p.platforms) && _p.platforms.length ? _p.platforms : null
if (!caseIds.length) {
  throw new Error('沒有合法 TCMS caseId（KQT-T#####）；args 可能傳壞：' + JSON.stringify(args).slice(0, 200))
}
log(
  `批次自動化 ${caseIds.length} 個 case：${caseIds.join(', ')}` +
    (LIMIT_PLATFORMS ? `（本批限平台 ${LIMIT_PLATFORMS.join('/')}）` : '')
)
// 使用量遙測：workflow 沙箱不能跑 shell，故 emit_tool_usage.py 由「啟動本 workflow 的主對話」
// 在叫用當下先發一筆 outcome=invoked（見 prompts/automate-tcms-cases.md 步驟 0）；此處不自行 emit。

// ── 模式：workflow headless、不能中途等人，故意圖確認做成兩段 ──────────────
//   mode=plan（預設，互動）：只跑 planner 回計畫 → 主對話攤給使用者確認 → 再用 execute 跑。
//   mode=execute：帶主對話確認過的 plans（{caseId:計畫}）→ automator 照計畫實作 → gate+review。
//   mode=auto（自主/harness，無人確認）：現場 spawn planner → 直接接 automator，不停等。
const MODE = _p.mode || 'plan'
const CONFIRMED_PLANS = _p.plans || {}

const PLAN_SCHEMA = {
  type: 'object',
  additionalProperties: true,
  properties: {
    caseid: { type: 'string' },
    platform: { type: 'string' },
    plan: { type: 'string' },
  },
  required: ['caseid', 'plan'],
}
function planPrompt(caseId) {
  return `你是 qa-case-planner。case=${caseId}。框架 repo=${REPO}。
依你的職責產出實作前計畫（唯讀、不寫 code）：
1. 抓 TCMS spec（tcms-fetch-cases）→ 讀懂要測的真正邏輯。
2. 起手先查 flow-registry：\`python3 ${SKILLS}/scripts/get_verified_flow.py --q "<前置語意>" --platform <平台> --repo-path ${REPO} --registry ${SKILLS}/flow_registry/registry.json\`（用前先驗；只信 verified）。沒命中才 grep repo，挖到新可重用 flow **當下**用 \`${SKILLS}/scripts/send_flow_registry.py\` 寫回。
3. 出計畫：解讀 / 平台 / 前置用哪個既有 flow 建真實資源（禁捏假 id）/ 關鍵 specific 斷言（綁 expected，禁鬆 proxy）/ 沿用哪些現成 / priority（TCMS→框架：Critical→RAT/High→FAST/Medium→TOFT/Low→FET）/ 假設 / 待確認點。
回傳結構化：caseid、platform、plan（完整計畫文字，供人確認）。`
}

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

function implPrompt(caseId, fixNote, plan) {
  const planStr = plan ? (typeof plan === 'string' ? plan : JSON.stringify(plan, null, 2)) : ''
  return `你是 qa-case-automator，**並行模式**。case=${caseId}。
${planStr ? `\n**照這份已確認的實作計畫做**（前置怎麼用既有 flow 建真實資源、關鍵 specific 斷言、沿用哪些現成、priority 都在裡面；別自己另生一套）：\n${planStr}\n` : ''}${fixNote ? `這是回修，請針對以下未達標點補實作：${fixNote}\n` : ''}${LIMIT_PLATFORMS ? `\n**本批只做這些平台：${LIMIT_PLATFORMS.join(', ')}**——其餘 tag 平台本批直接標 blocked、不嘗試（如無 App 實體機）。\n` : ''}
並行模式規則（照 qa-case-automator.md §3.5）：
- 驗元素用**各自 launch 的 headless Python playwright**（scripts/verify_locator.py），不用 MCP（避免彈窗/搶共用瀏覽器）。
- 在你所在的 git worktree 內寫檔，不自己做 git 操作。
- **禁打 prod**：開頁 host 依環境組出 www{suffix}.kkday.com，用 stage / sit，絕不碰 www.kkday.com。

全平台鐵則（照 §2）：撈 case 的 tag，**tag 標的所有平台都要涵蓋，且平台間「共用」同一份 case + test_step**（不是各寫一份，只有些許步驟不同）：
- web ↔ mweb 共用一份（web_playwright/）：一份 case + test_step 用 \`if pages.platform == Platform.MWEB:\` 分支處理差異，**絕不加 limit_test_platform**（那會 Skip、把 case 限死只跑單一平台）；用 --platform web 和 --platform mweb 各跑過。
- android ↔ ios 共用一份（mobile/）：用 \`if platform==Android / iOS\` 分支處理差異，一樣不加 limit；需實體機，沒設備該平台標 blocked+原因，共用的其餘平台照做。
每個平台逐一跑過（qa-test-runner，HEADLESS=1）。

回傳結構化：caseid、tags_platforms（該 case tag 標的平台）、per_platform（每平台 platform+status(pass/fail/blocked)+files）、traceability（step→assertion 可追溯表）。`
}

// 獨立驗證：確定性 gate（矇混不過）+ per-platform 忠實度 review。不信 automator 自評。
async function verify(caseId, impl) {
  const tags = (LIMIT_PLATFORMS || impl.tags_platforms || []).join(',')
  const resultsLines = (impl.per_platform || [])
    .map((p) => JSON.stringify({ caseid: caseId, platform: p.platform, status: p.status }))
    .join('\n')
  return await agent(
    `你是 per-platform 交付驗證員（獨立、對抗式，不信 automator 自評）。case=${caseId}，tag 要求平台=${tags}。

步驟 1 — 確定性交付 gate（Bash，客觀 parse qatest.log、不靠 automator 自報）：
執行：
  python3 ${SKILLS}/scripts/check_platform_delivery.py --caseid ${caseId} --tags ${tags} --repo ${REPO} --qatest-log ~/Documents/QATest_Output/qatest.log
它靠 \`pid+case+platform\` parse qatest.log，判每個 tag 平台是否真跑出 \`0 failed\`。讀 JSON 的 \`delivered\` / \`missing_pass\`（沒真跑出 pass 的平台）。**「有沒有真跑過 pass」gate 已客觀判定，你不用靠 automator 貼 stdout、也不碰全域 log 對應問題（parser 用 pid 分並行）。**

步驟 2 — 忠實度（gate 判不出、靠你）：對每個 gate 判 pass 的平台，比對 case 規格 vs 實作斷言，抓「沒真驗到的 expected」「過弱/恆真斷言」「參數收了沒用」。判準是**該平台每個 expected 有沒有被真斷言驗到**——步驟與 web 相同就共用斷言、有差異才要對應斷言；**不是**看有沒有 \`if platform\` 分支（步驟一樣本來就沒分支，不代表沒交付）。

回傳：delivered（gate \`missing_pass\` 空 **且** 每平台 fidelity 達標才 true）、gate_missing（gate 的 missing_pass）、fidelity_issues、fix_instructions。`,
    { label: `verify:${caseId}`, phase: 'Gate+Review', schema: VERDICT_SCHEMA }
  )
}

// ── mode=plan：只跑 planner 出計畫給人確認，不實作（互動預設）──────────
if (MODE === 'plan') {
  phase('Plan')
  const plans = await parallel(
    caseIds.map((caseId) => () =>
      agent(planPrompt(caseId), {
        label: `plan:${caseId}`,
        phase: 'Plan',
        agentType: 'qa-case-planner',
        schema: PLAN_SCHEMA,
      })
    )
  )
  const out = caseIds
    .map((caseId, i) => ({ caseId, plan: plans[i] }))
    .filter((x) => x.plan != null)
  log(`已產出 ${out.length}/${caseIds.length} 份計畫，待主對話攤給使用者確認`)
  return {
    mode: 'plan',
    plans: out,
    note:
      '計畫已產出。主對話請把每份計畫攤給使用者確認/修正；確認後用 {mode:"execute", cases:[...], plans:{caseId:計畫}} 再跑本 workflow 執行。無人可確認的自主/harness 情境改用 mode:"auto"（現場 spawn planner 直接接實作，不停等）。',
  }
}

// ── mode=execute / auto：計畫 → 實作 → gate+review+回修，每案獨立不等 ──
const results = await pipeline(
  caseIds,
  // stage 0：取得計畫（auto=現場 spawn planner；execute=用主對話確認過的）
  async (caseId) => {
    if (MODE === 'auto') {
      return await agent(planPrompt(caseId), {
        label: `plan:${caseId}`,
        phase: 'Plan',
        agentType: 'qa-case-planner',
        schema: PLAN_SCHEMA,
      })
    }
    return CONFIRMED_PLANS[caseId] || null
  },
  // stage 1：照計畫實作
  (plan, caseId) =>
    agent(implPrompt(caseId, null, plan), {
      label: `impl:${caseId}`,
      phase: 'Implement',
      isolation: 'worktree',
      agentType: 'qa-case-automator',
      schema: IMPL_SCHEMA,
    }),
  // stage 2：gate + 忠實度 review + 回修
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
