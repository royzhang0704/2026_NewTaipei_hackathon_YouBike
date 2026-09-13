import { useEffect, useRef, useState } from 'react'
import { parseServerTs } from '@/lib/format'

/* 頭欄時鐘：每次 /api/v1/healthz 回應時強制對時至後端的 now，其間以 setInterval 內插前推。
   流速 = 連續兩次 now 差 / 真實時間差（後端會調 speed，故每輪重估）：
   - 突變（改速度 / 剛從停表恢復）→ 直接採用，不 EMA
   - 兩次 now 未變化 → 視為停表，speed = 0，畫面靜止
   ★ effect 依 tick（react-query 的 dataUpdatedAt）觸發，不只依 nowStr ——
     demo 走到 demo_until 後 now 不再變，若只看 nowStr，effect 不再跑、
     速度無法歸零，時鐘會持續前移。 */

interface Sample {
  serverMs: number
  clientMs: number
}

const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x))

export function useServerClock(
  nowStr: string | null | undefined,
  offline: boolean,
  refetch?: () => void,
  tick?: number,
) {
  const [display, setDisplay] = useState<Date | null>(null)
  const anchor = useRef<Sample | null>(null)
  const speed = useRef(0) // 0 = 尚未定速（不內插）
  const inited = useRef(false)

  useEffect(() => {
    const d = parseServerTs(nowStr)
    if (!d) return
    const s: Sample = { serverMs: d.getTime(), clientMs: Date.now() }
    const prev = anchor.current

    if (prev) {
      const dS = s.serverMs - prev.serverMs
      const dC = s.clientMs - prev.clientMs
      if (dC > 300) {
        if (dS <= 50) {
          speed.current = 0 // 後端時鐘沒動 → 停表
        } else {
          const obs = clamp(dS / dC, 0.05, 500)
          const ratio = inited.current && speed.current > 0 ? obs / speed.current : Infinity
          speed.current =
            !inited.current || ratio > 1.6 || ratio < 0.62 ? obs : speed.current * 0.6 + obs * 0.4
          inited.current = true
        }
      }
    } else {
      // 第一筆：1.2s 後補打一次，讓流速快點定出來
      const timer = setTimeout(() => refetch?.(), 1200)
      anchor.current = s
      setDisplay(d)
      return () => clearTimeout(timer)
    }

    anchor.current = s
    setDisplay(d) // 強制對時
  }, [nowStr, tick, refetch])

  useEffect(() => {
    if (offline) return
    const id = setInterval(() => {
      const a = anchor.current
      if (!a || !inited.current) return // 未定速前不內插
      setDisplay(new Date(a.serverMs + (Date.now() - a.clientMs) * speed.current))
    }, 200)
    return () => clearInterval(id)
  }, [offline])

  return { time: display, speed: speed.current }
}
