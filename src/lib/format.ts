/** "2026-05-02 00:30:00" -> "00:30"。非字串或格式不符時回傳原值/空字串。 */
export function hhmm(ts: string | null | undefined): string {
  if (!ts || ts.length < 16) return ts ?? ''
  return ts.slice(11, 16)
}

/** "2026-05-02 00:30:00" -> "05/02 00:30" */
export function mdhm(ts: string | null | undefined): string {
  if (!ts || ts.length < 16) return ts ?? ''
  return `${ts.slice(5, 10).replace('-', '/')} ${ts.slice(11, 16)}`
}

export function pct(n: number, digits = 0): string {
  return `${(n * 100).toFixed(digits)}％`
}

const p2 = (n: number) => String(n).padStart(2, '0')

/** 後端時間字串 "2026-05-02 00:30:00"（本地時間、無時區）→ Date */
export function parseServerTs(ts: string | null | undefined): Date | null {
  if (!ts || ts.length < 19) return null
  const d = new Date(ts.replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? null : d
}

/** Date -> "05/02 00:30:45" */
export function fmtClock(d: Date): string {
  return `${p2(d.getMonth() + 1)}/${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`
}

/** /api/v1/healthz 的 now 本身就是歷史時刻（離瀏覽器時鐘 > 30 分）= 走在回放 / 示範時間軸上。
 *  關鍵：真實環境即使排程掛掉，healthz 的 now 仍是當下（只有 current_slot 落後）；
 *  只有 demo 回放會讓 now 本身跑到過去。這個比較能乾淨區分「示範」與「真故障」。
 *  （/api/v1/healthz 沒吐 is_demo 旗標，只能這樣推。30 分：遠大於時鐘偏差、遠小於數月的 demo。） */
export function isHistoricalClock(nowStr: string | null | undefined): boolean {
  const d = parseServerTs(nowStr)
  return !!d && Math.abs(d.getTime() - Date.now()) > 30 * 60_000
}

/** 是否走在回放／示範時間軸上（不是「現在」）。now 本身是歷史時刻，或後端有靜態凍結旗標
 *  virtual_now，兩者任一即算。AppShell 與 OverviewCaption 共用同一判定，避免
 *  「header 說回放、caption 說故障」那種兩處分岔。 */
export function isReplayMode(
  health: { now?: string | null; virtual_now?: string | null } | null | undefined,
): boolean {
  return isHistoricalClock(health?.now) || !!health?.virtual_now
}
