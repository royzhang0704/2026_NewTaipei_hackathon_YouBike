import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

const SLOT_MS = 30 * 60 * 1000 // 批次預測一格 = 30 分

/* 虛擬時鐘跨過 30 分邊界（= 有新一輪 origin）的當下，就讓 /alerts 和 /day
   重抓 —— 資料緊跟虛擬時間，不用靠密集輪詢等相位對上。 */
export function useSlotSync(time: Date | null) {
  const qc = useQueryClient()
  const slot = useRef<number | null>(null)

  useEffect(() => {
    if (!time) return
    const s = Math.floor(time.getTime() / SLOT_MS)
    if (slot.current === null) {
      slot.current = s
      return
    }
    if (s !== slot.current) {
      slot.current = s
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['station-day'] })
    }
  }, [time, qc])
}
