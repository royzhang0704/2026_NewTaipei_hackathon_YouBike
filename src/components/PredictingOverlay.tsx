import { Loader2 } from 'lucide-react'
import { mdhm } from '@/lib/format'

/* 全頁遮罩：後端正在「現算」這一格的預測時蓋住整個畫面。
   出處：meet/20260912/計劃-demo回放邏輯重整.md §3-4（定案③：全頁遮罩）。

   ★ 為什麼要遮整頁而不是只換頂欄狀態：回放走到沒有預測過的格時，後端會
     先把時鐘降回 1x 再打 endpoint，那幾十秒裡畫面上的預測、風險、調度全是
     **上一格**的。不遮的話評審會對著一組已經不成立的警示做判斷。

   ★ 開關完全由後端決定（health.predicting_origin），前端不自己計時 ——
     後端那支有 TTL 自癒（迴圈被 kill 之後會回 null），兩邊各設一套逾時
     只會讓「遮罩為什麼還在／為什麼提早關」變成兩個地方要查。

   ★ 不吃 Esc、不給關閉鈕：這不是 dialog，是「資料還沒到」的事實。
     能關掉就等於允許看到半套資料。 */
export function PredictingOverlay({ origin }: { origin?: string | null }) {
  if (!origin) return null
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/85 px-4 backdrop-blur-[2px]"
    >
      <div className="flex w-full max-w-[360px] flex-col items-center gap-3 border border-edge bg-panel px-6 py-7 text-center">
        <Loader2 className="size-6 animate-spin text-ink3" aria-hidden />
        <p className="m-0 text-[0.92rem] font-semibold tracking-[0.02em] text-ink">
          最新資料載入中
        </p>
        <p className="m-0 text-[0.76rem] leading-[1.7] text-ink2">
          正在為 <span className="tabular-nums">{mdhm(origin)}</span> 這一輪產生預測，
          <br />
          完成後會自動接續。
        </p>
        <p className="m-0 text-[0.7rem] leading-[1.6] text-ink3">
          回放時鐘已切回 1x —— 這一格沒有預先算好的結果。
        </p>
      </div>
    </div>
  )
}
