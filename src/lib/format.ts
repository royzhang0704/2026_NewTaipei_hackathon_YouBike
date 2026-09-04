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
