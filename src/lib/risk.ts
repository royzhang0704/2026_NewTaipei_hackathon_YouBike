/* 風險判定 / 調度建議的「翻成人話」邏輯 —— 從 單站檢視.html 逐條移植。
   純函式，回傳結構化物件給 template 用（不再拼 innerHTML）。

   8/31 定案要點：
   - 等級講「多快會發生」不是「多確定」：高=現在越線且 1h 回不來、中=1h 內越線、
     低=1~3h 內越線、無=整段不越線；一律看 q50。
   - 只出一張卡：q50 判定下缺車與滿站不可能同時成立，取較嚴重的一側。
   - 不講機率百分比（三分位插不出可信機率）。 */

import type { DayRisk, Dispatch, ForecastPoint, RiskLevel } from '@/api/types'
import { hhmm } from './format'

const LV_WORD: Record<RiskLevel, string> = {
  none: '無風險',
  low: '低風險',
  mid: '中風險',
  high: '高風險',
}
const LV_N: Record<RiskLevel, number> = { none: 0, low: 1, mid: 2, high: 3 }

export interface RiskCard {
  tone: RiskLevel
  title: string
  level: string
  desc: string
}

export function riskCard(k: DayRisk): RiskCard {
  const ls = LV_N[k.shortage.level]
  const lf = LV_N[k.full.level]
  const T = k.threshold

  if (!ls && !lf) {
    return {
      tone: 'none',
      title: '風險判定',
      level: '無風險',
      desc: `未來 3 小時可借都在 ${T} 台以上、可還都在 ${T} 席以上`,
    }
  }

  const lend = ls >= lf
  const s = lend ? k.shortage : k.full
  const unit = lend ? '台' : '席'
  const verb = lend ? '低於' : '高於'
  const act = lend ? '借' : '還'

  let desc: string
  if (s.level === 'high') {
    desc = `現在可${act}已${verb} ${T} ${unit}，未來 1 小時都回不來（越線 ${s.slots} 格）`
  } else if (s.level === 'mid') {
    desc = `${hhmm(s.onset)} 起可${act}${verb} ${T} ${unit} —— 1 小時內，共 ${s.slots} 格`
  } else {
    desc = `${hhmm(s.onset)} 起可${act}${verb} ${T} ${unit}，共 ${s.slots} 格`
  }

  return {
    tone: s.level,
    title: lend ? '缺車風險' : '滿站風險',
    level: LV_WORD[s.level],
    desc,
  }
}

/** 由 by_slot 算出越線區間的實話。回傳空字串代表沒得講。 */
function crossSpan(k: DayRisk, d: Dispatch, f: ForecastPoint[]): string {
  const side = d.action === 'refill' ? k.shortage : k.full
  const bs = side?.by_slot
  if (!bs || !f?.length) return ''
  const last = bs.lastIndexOf(1)
  if (last < 0) return '預測範圍內未越線'
  if (last === bs.length - 1) return `至 ${hhmm(f[last].at)} 仍低於門檻，但已接近同時段常態`
  return `${hhmm(f[last].at)} 後回到門檻之上`
}

const ACT_WORD: Record<'refill' | 'remove', string> = { refill: '補車', remove: '取車' }

export interface DispatchBar {
  kind: 'refill' | 'remove' | 'hold'
  urgent: boolean
  label: string
  desc: string
}

export function dispatchBar(k: DayRisk, f: ForecastPoint[]): DispatchBar | null {
  const d = k?.dispatch
  if (!d) return null

  if (d.action === 'hold') {
    const span = crossSpan(k, d, f)
    const hint = d.hint ?? '會自行退燒，不派車'
    return {
      kind: 'hold',
      urgent: false,
      label: '暫不派車',
      desc: span ? `${hint}　·　${span}` : hint,
    }
  }

  const parts: string[] = []
  if (d.urgency === 'high') parts.push('現在已越線')
  else if (d.by) parts.push(`${hhmm(d.by)} 前到位`)
  if (d.hint) parts.push(d.hint)
  if (k.baseline != null) parts.push(`同時段常態 ${k.baseline} 台`)

  return {
    kind: d.action,
    urgent: d.urgency === 'high',
    label: `${ACT_WORD[d.action]} ${d.bikes} 台`,
    desc: parts.join('　·　'),
  }
}
