/* 後端服務接上前的內建回應邏輯：以已快取的即時資料，就數個高頻問題型態做規則式回覆。
   僅涵蓋常見意圖；無法對應時列出可查詢的項目。
   僅處理「即時狀態」類問題；名詞定義與作業規範類待後端知識庫支援。 */

import type { AlertItem, Health, Station, Town } from '@/api/types'
import { riskPhrase } from '@/lib/risk'
import type { AssistantAction, AssistantContext, ChatHandlers, ChatResult, WireMessage } from './types'

export interface MockSnapshot {
  alerts: AlertItem[]
  stations: Station[]
  towns: Town[]
  health?: Health
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** 逐段輸出以模擬串流，使 mock 與 live 觀感一致 */
async function stream(text: string, h: ChatHandlers): Promise<void> {
  const step = 14
  for (let i = 0; i < text.length; i += step) {
    if (h.signal?.aborted) return
    h.onDelta?.(text.slice(i, i + step))
    await sleep(28)
  }
}

function townRefill(alerts: AlertItem[], town?: string) {
  return alerts
    .filter((a) => a.dispatch?.action === 'refill' && (!town || a.town === town))
    .sort((x, y) => (y.dispatch?.bikes ?? 0) - (x.dispatch?.bikes ?? 0))
}

function findStation(q: string, stations: Station[]): Station | undefined {
  // 取問句中最長的中文片段作為關鍵字比對站名（站表偶有 name 缺值，需防呆）
  const frag = (q.match(/[一-龥A-Za-z0-9]{2,}/g) ?? []).sort((a, b) => b.length - a.length)
  for (const f of frag) {
    const hit = stations.find((s) => typeof s.name === 'string' && s.name.includes(f))
    if (hit) return hit
  }
  return undefined
}

function respond(q: string, snap: MockSnapshot, ctx?: AssistantContext): ChatResult {
  const { alerts, stations, towns } = snap
  // 問句明確指定「全市 / 全區」時不限行政區；否則優先採問句指定的行政區，其次為畫面當前選取（ctx.town_code）
  const wantsCity = /(全市|全區|各區|所有)/.test(q)
  const namedTown = towns.find((t) => q.includes(t.town))?.town
  const ctxTown = ctx?.town_code ? towns.find((t) => t.town_code === ctx.town_code)?.town : undefined
  const town = wantsCity ? undefined : (namedTown ?? ctxTown)
  const hi = alerts.filter((a) => a.level === 'high')
  const mid = alerts.filter((a) => a.level === 'mid')

  // ── 單站展望 ───────────────────────────────
  const st = findStation(q, stations)
  if (st && /(缺車|滿站|風險|狀況|怎樣|如何|預測|要補|要取)/.test(q)) {
    const a = alerts.find((x) => x.station_uid === st.uid)
    const actions: AssistantAction[] = [{ label: `在單站檢視開啟「${st.name}」`, type: 'select_station', value: st.uid }]
    if (!a || a.level === 'none') {
      return {
        content: `「${st.name}」（${st.town}）目前供需健康，未進警示清單。`,
        actions,
        suggestions: [`${st.town}整體狀況如何？`, '全市概況如何？'],
      }
    }
    const d = a.dispatch
    const act = d?.action === 'refill' ? `建議補 ${d.bikes} 台` : d?.action === 'remove' ? `建議取 ${d.bikes} 台` : '暫可觀察'
    return {
      content:
        `「${st.name}」（${st.town}）目前${riskPhrase(a.level, a.side)}。\n` +
        `可借 ${a.now?.avail ?? '—'}／容量 ${a.capacity ?? '?'}，${act}` +
        (a.onset
          ? `，預計 ${a.onset.slice(11, 16)} 前後${a.side === 'shortage' ? '缺車' : '滿站'}。`
          : '。'),
      actions,
      suggestions: [`${st.town}最急的是哪幾站？`, '哪些站要優先補車？'],
    }
  }

  // ── 待補 / 待取清單 ────────────────────────
  if (/(補車|要補|缺車|優先|派車|人力|調度車)/.test(q)) {
    const list = townRefill(alerts, town).slice(0, 6)
    if (!list.length)
      return { content: `${town ?? '目前全區'}沒有待補車的站。`, suggestions: ['全市概況如何？', '哪些站要取車？'] }
    const lines = list.map((a, i) => `${i + 1}. ${a.name}（${a.town}）補 ${a.dispatch?.bikes} 台・可借 ${a.now?.avail ?? '—'}`)
    return {
      content: `${town ?? '全區'}最該優先補車的站（依缺口排序）：\n${lines.join('\n')}`,
      actions: list.slice(0, 3).map((a) => ({ label: a.name, type: 'select_station', value: a.station_uid })),
      suggestions: ['哪些站要取車？', `${town ?? '全市'}供需概況？`],
    }
  }

  // ── 滿站 / 待取 ────────────────────────────
  if (/(滿站|還不了|滿了|要取|取車)/.test(q)) {
    const list = alerts
      .filter((a) => a.dispatch?.action === 'remove' && (!town || a.town === town))
      .sort((x, y) => (y.dispatch?.bikes ?? 0) - (x.dispatch?.bikes ?? 0))
      .slice(0, 6)
    if (!list.length)
      return { content: `${town ?? '目前全區'}沒有需要取車的滿站。`, suggestions: ['全市概況如何？', '哪些站要補車？'] }
    const lines = list.map((a, i) => `${i + 1}. ${a.name}（${a.town}）取 ${a.dispatch?.bikes} 台・可還 ${a.now?.free ?? '—'}`)
    return {
      content: `${town ?? '全區'}最該優先取車的站：\n${lines.join('\n')}`,
      suggestions: ['哪些站要補車？', '全市概況如何？'],
    }
  }

  // ── 區域摘要 ───────────────────────────────
  if (town) {
    const th = hi.filter((a) => a.town === town)
    const tm = mid.filter((a) => a.town === town)
    const refill = townRefill(alerts, town).reduce((s, a) => s + (a.dispatch?.bikes ?? 0), 0)
    return {
      content:
        `${town}：已缺車或滿站 ${th.length} 站、預測再 ${tm.length} 站，合計待補約 ${refill} 台。` +
        (th[0] ? `\n最急：${th[0].name}（${th[0].side === 'shortage' ? '缺車' : '滿站'}）。` : ''),
      actions: [{ label: `地圖只看 ${town}`, type: 'filter_town', value: towns.find((t) => t.town === town)?.town_code ?? '' }],
      suggestions: [`${town}哪些站要優先補車？`, '其他行政區狀況呢？', '全市整體概況？'],
    }
  }

  // ── 全市概況 ───────────────────────────────
  if (/(概況|狀況|現在|多少|幾個|高風險|整體|總覽)/.test(q)) {
    const refill = alerts.filter((a) => a.dispatch?.action === 'refill').reduce((s, a) => s + (a.dispatch?.bikes ?? 0), 0)
    const top = hi.slice(0, 3).map((a) => `${a.name}（${a.town}・${a.side === 'shortage' ? '缺車' : '滿站'}）`)
    return {
      content:
        `目前全市已缺車或滿站 ${hi.length} 站、預測再 ${mid.length} 站，估計待補約 ${refill} 台。` +
        (top.length ? `\n最急的幾站：\n・${top.join('\n・')}` : ''),
      actions: hi.slice(0, 3).map((a) => ({ label: a.name, type: 'select_station', value: a.station_uid })),
      suggestions: ['哪些站要優先補車？', '哪些站要取車？', '板橋區狀況如何？'],
    }
  }

  // ── 無對應意圖 ─────────────────────────────
  return {
    content:
      '目前可查詢即時調度狀態，例如：\n' +
      '・現在有幾個站已缺車或滿站\n' +
      '・哪些站一小時內需補車\n' +
      '・指定站點接下來的供需\n' +
      '・指定行政區的整體狀況\n' +
      '名詞定義與調度 SOP 待知識庫上線後支援。',
  }
}

export async function mockChat(
  messages: WireMessage[],
  snap: MockSnapshot,
  handlers: ChatHandlers = {},
  context?: AssistantContext,
): Promise<ChatResult> {
  const q = [...messages].reverse().find((m) => m.role === 'user')?.content ?? ''
  await sleep(220)
  let res: ChatResult
  try {
    res = respond(q, snap, context)
  } catch (e) {
    // mock 不應拋出例外；若發生則記錄原因並回一則安全訊息，避免呈現為錯誤狀態
    console.error('[assistant mock] respond() threw:', e, { q, snap })
    res = { content: '無法處理這個查詢，請重試或換個問法。' }
  }
  await stream(res.content ?? '', handlers)
  if (res.actions?.length) handlers.onActions?.(res.actions)
  if (res.suggestions?.length) handlers.onSuggestions?.(res.suggestions)
  return { ...res, content: res.content ?? '' }
}
