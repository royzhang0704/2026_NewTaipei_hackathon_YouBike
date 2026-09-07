/* 過渡期的「假腦」——後端 RAG 上線前，用已快取的即時資料做規則式問答。
   刻意只涵蓋 4~5 個高頻意圖；答不出來就說明能問什麼，不硬掰。
   ⚠ 這裡只處理「即時狀態」類問題；名詞 / SOP 類要等 RAG 知識庫。 */

import type { AlertItem, Health, Station, Town } from '@/api/types'
import type { AssistantAction, ChatHandlers, ChatResult, WireMessage } from './types'

export interface MockSnapshot {
  alerts: AlertItem[]
  stations: Station[]
  towns: Town[]
  health?: Health
}

const LV: Record<string, string> = { high: '高', mid: '中', low: '低', none: '無' }

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** 逐段吐字模擬串流，讓 mock 與 live 的觀感一致 */
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
  // 取問句裡最長的中文片段當關鍵字，比對站名
  const frag = (q.match(/[一-龥A-Za-z0-9]{2,}/g) ?? []).sort((a, b) => b.length - a.length)
  for (const f of frag) {
    const hit = stations.find((s) => s.name.includes(f))
    if (hit) return hit
  }
  return undefined
}

function respond(q: string, snap: MockSnapshot): ChatResult {
  const { alerts, stations, towns } = snap
  const town = towns.find((t) => q.includes(t.town))?.town
  const hi = alerts.filter((a) => a.level === 'high')
  const mid = alerts.filter((a) => a.level === 'mid')

  // ── 單站展望 ───────────────────────────────
  const st = findStation(q, stations)
  if (st && /(缺車|滿站|風險|狀況|怎樣|如何|預測|要補|要取)/.test(q)) {
    const a = alerts.find((x) => x.station_uid === st.uid)
    const actions: AssistantAction[] = [{ label: `在單站檢視開啟「${st.name}」`, type: 'select_station', value: st.uid }]
    if (!a || a.level === 'none') {
      return { content: `「${st.name}」（${st.town}）目前供需健康，未進警示清單。`, actions }
    }
    const d = a.dispatch
    const act = d?.action === 'refill' ? `建議補 ${d.bikes} 台` : d?.action === 'remove' ? `建議取 ${d.bikes} 台` : '暫可觀察'
    return {
      content:
        `「${st.name}」（${st.town}）目前 ${LV[a.level]}風險・${a.side === 'shortage' ? '缺車' : '滿站'}。\n` +
        `可借 ${a.now.avail ?? '—'}／容量 ${a.capacity ?? '?'}，${act}` +
        (a.onset ? `，預計 ${a.onset.slice(11, 16)} 前後越線。` : '。'),
      actions,
    }
  }

  // ── 待補 / 待取清單 ────────────────────────
  if (/(補車|要補|缺車|優先|派車|人力|調度車)/.test(q)) {
    const list = townRefill(alerts, town).slice(0, 6)
    if (!list.length) return { content: `${town ?? '目前全區'}沒有待補車的站。` }
    const lines = list.map((a, i) => `${i + 1}. ${a.name}（${a.town}）補 ${a.dispatch?.bikes} 台・可借 ${a.now.avail ?? '—'}`)
    return {
      content: `${town ?? '全區'}最該優先補車的站（依缺口排序）：\n${lines.join('\n')}`,
      actions: list.slice(0, 3).map((a) => ({ label: a.name, type: 'select_station', value: a.station_uid })),
    }
  }

  // ── 滿站 / 待取 ────────────────────────────
  if (/(滿站|還不了|滿了|要取|取車)/.test(q)) {
    const list = alerts
      .filter((a) => a.dispatch?.action === 'remove' && (!town || a.town === town))
      .sort((x, y) => (y.dispatch?.bikes ?? 0) - (x.dispatch?.bikes ?? 0))
      .slice(0, 6)
    if (!list.length) return { content: `${town ?? '目前全區'}沒有需要取車的滿站。` }
    const lines = list.map((a, i) => `${i + 1}. ${a.name}（${a.town}）取 ${a.dispatch?.bikes} 台・可還 ${a.now.free ?? '—'}`)
    return { content: `${town ?? '全區'}最該優先取車的站：\n${lines.join('\n')}` }
  }

  // ── 區域摘要 ───────────────────────────────
  if (town) {
    const th = hi.filter((a) => a.town === town)
    const tm = mid.filter((a) => a.town === town)
    const refill = townRefill(alerts, town).reduce((s, a) => s + (a.dispatch?.bikes ?? 0), 0)
    return {
      content:
        `${town}：高風險 ${th.length} 站、中風險 ${tm.length} 站，合計待補約 ${refill} 台。` +
        (th[0] ? `\n最急：${th[0].name}（${th[0].side === 'shortage' ? '缺車' : '滿站'}）。` : ''),
      actions: [{ label: `地圖只看 ${town}`, type: 'filter_town', value: towns.find((t) => t.town === town)?.town_code ?? '' }],
    }
  }

  // ── 全市概況 ───────────────────────────────
  if (/(概況|狀況|現在|多少|幾個|高風險|整體|總覽)/.test(q)) {
    const refill = alerts.filter((a) => a.dispatch?.action === 'refill').reduce((s, a) => s + (a.dispatch?.bikes ?? 0), 0)
    const top = hi.slice(0, 3).map((a) => `${a.name}（${a.town}・${a.side === 'shortage' ? '缺車' : '滿站'}）`)
    return {
      content:
        `目前全市高風險 ${hi.length} 站、中風險 ${mid.length} 站，估計待補約 ${refill} 台。` +
        (top.length ? `\n最急的幾站：\n・${top.join('\n・')}` : ''),
      actions: hi.slice(0, 3).map((a) => ({ label: a.name, type: 'select_station', value: a.station_uid })),
    }
  }

  // ── 兜底 ──────────────────────────────────
  return {
    content:
      '我可以回答目前的調度狀態，例如：\n' +
      '・現在有幾個高風險站？\n' +
      '・哪些站一小時內要補車？\n' +
      '・「（站名）」接下來會缺車嗎？\n' +
      '・板橋區狀況如何？\n' +
      '（名詞解釋與調度 SOP 待知識庫上線後支援）',
  }
}

export async function mockChat(
  messages: WireMessage[],
  snap: MockSnapshot,
  handlers: ChatHandlers = {},
): Promise<ChatResult> {
  const q = [...messages].reverse().find((m) => m.role === 'user')?.content ?? ''
  await sleep(220)
  const res = respond(q, snap)
  await stream(res.content, handlers)
  if (res.actions) handlers.onActions?.(res.actions)
  return res
}
