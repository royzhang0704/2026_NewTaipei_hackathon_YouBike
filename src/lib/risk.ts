/* 風險判定與調度建議的可讀化處理。

   ★ 文字在這裡（前端）組。後端只給結構化事實：
     k.threshold / k.shortage.level / .slots / .onset / k.baseline
     k.dispatch.action / .bikes / .urgency / .by / .hint
   此模組將這些欄位轉為畫面所需的 levelWord / sideWord / action / why / support。
   句子模板刻意採「各等級平行、每句完整」，避免殘句或語意矛盾。

   8/31 定案：等級講「多快」不是「多確定」；只出一張卡（取較嚴重一側）；不講機率百分比。 */

import type { DayRisk, RiskLevel } from '@/api/types'
import { hhmm } from './format'

const LV_WORD: Record<RiskLevel, string> = {
  none: '無風險',
  low: '低風險',
  mid: '中風險',
  high: '高風險',
}
const LV_N: Record<RiskLevel, number> = { none: 0, low: 1, mid: 2, high: 3 }

export interface ActionBlock {
  tone: RiskLevel
  urgent: boolean
  sideWord: string | null
  levelWord: string
  action: string | null
  actionKind: 'refill' | 'remove' | 'hold' | 'none'
  why: string
  support: string | null
}

const ACT_WORD: Record<'refill' | 'remove', string> = { refill: '建議補車', remove: '建議取車' }

export function actionBlock(k: DayRisk): ActionBlock {
  const ls = LV_N[k.shortage.level]
  const lf = LV_N[k.full.level]
  const T = k.threshold
  const d = k.dispatch
  const base = k.baseline != null ? Math.round(k.baseline) : null

  // ── 無風險 ──────────────────────────────────────────────
  if (!ls && !lf) {
    return {
      tone: 'none',
      urgent: false,
      sideWord: null,
      levelWord: '無風險',
      action: null,
      actionKind: 'none',
      why: `未來 3 小時：可借 ≥ ${T} 台、可還 ≥ ${T} 席`,
      support: null,
    }
  }

  const lend = ls >= lf
  const s = lend ? k.shortage : k.full

  // ── why：各級平行、每句完整 ────────────────────────────
  let why: string
  if (s.level === 'high') {
    why = `現在已越線，1 小時內${lend ? '補不回' : '清不掉'}`
  } else if (s.level === 'mid') {
    why = `預計 ${hhmm(s.onset)} 越線（1 小時內）`
  } else {
    why = `預計 ${hhmm(s.onset)} 越線（1～3 小時內）`
  }

  // ── action + support ─────────────────────────────────
  let action: string | null = null
  let actionKind: ActionBlock['actionKind'] = 'none'
  let urgent = false
  const support: string[] = []

  if (d?.action === 'hold') {
    action = '暫不派車'
    actionKind = 'hold'
    support.push(d.hint ?? '會自行退燒')
  } else if (d && (d.action === 'refill' || d.action === 'remove')) {
    action = `${ACT_WORD[d.action]} ${d.bikes} 台`
    actionKind = d.action
    urgent = d.urgency === 'high'
    support.push(`越線 ${s.slots} 格`)
    if (!urgent && d.by) support.push(`${hhmm(d.by)} 前到位`)
  }
  if (base != null) support.push(`常態約 ${base} 台`)

  return {
    tone: s.level,
    urgent,
    sideWord: lend ? '空站' : '滿站',
    levelWord: LV_WORD[s.level],
    action,
    actionKind,
    why,
    support: support.length ? support.join('・') : null,
  }
}
