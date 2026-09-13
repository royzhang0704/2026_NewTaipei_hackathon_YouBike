import { useEffect, useRef } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'

const SLOT_MS = 30 * 60 * 1000 // 批次預測一格 = 30 分

/** 一輪換格後要跟著換的所有查詢。★ 新增查詢時記得補進來。 */
function refresh(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: ['alerts'] })
  qc.invalidateQueries({ queryKey: ['station-day'] })
  // 調度單：新一輪 risk_snapshot 進來後 dispatch_sweep 會收掉已完成／已失效的，
  // 也會把「建議台數下修」的單就地改少（規則③）—— 行動卡的「已調度 N 台」靠它
  qc.invalidateQueries({ queryKey: ['dispatch-orders'] })
  // 候選：餘裕（supply）扣的是即時的 active 單，換輪必變
  qc.invalidateQueries({ queryKey: ['dispatch-candidates'] })
}

/* 換到新一輪時讓畫面上的資料跟著換。兩個觸發點，各自管一種情境。

   ★★ 2026-09-12 三修：新增「解鎖時重抓」，原本只有「跨格時重抓」。
     舊的單一觸發點是**錯的時機** —— 虛擬時鐘跨格的當下，後端才正要開始
     推進這一格（搬資料 → 預測 → 風險 → 收單），抓回來的必定是上一格：
     風險還沒重判、dispatch_sweep 還沒收單。於是行動卡的「已調度 N 台」
     停在舊值，要等 25 秒的備援輪詢才會對上。使用者看到的就是這個。 */
export function useSlotSync(time: Date | null, locked = false) {
  const qc = useQueryClient()
  const slot = useRef<number | null>(null)
  const wasLocked = useRef(false)

  /* ① 解鎖的瞬間 —— 這才是後端四階段真的跑完的時刻。
     ★ 判定用「上一拍是鎖著的、這一拍不是」，不是「locked 為 false」：
       後者每次 render 都成立，會變成無限重抓。 */
  useEffect(() => {
    if (locked) {
      wasLocked.current = true
      return
    }
    if (!wasLocked.current) return
    wasLocked.current = false
    refresh(qc)
  }, [locked, qc])

  /* ② 跨格 —— 真實模式的唯一來源（沒有回放迴圈，不會上鎖，①永遠不觸發）。
     ★ 上鎖中不重抓：那時後端正在改資料，抓回來的是半套，而且①馬上會再抓
       一次。書籤照樣要更新，否則解鎖後這裡會補放一次過期的觸發。 */
  useEffect(() => {
    if (!time) return
    const s = Math.floor(time.getTime() / SLOT_MS)
    if (slot.current === null) {
      slot.current = s
      return
    }
    if (s !== slot.current) {
      slot.current = s
      if (!locked) refresh(qc)
    }
  }, [time, locked, qc])
}
