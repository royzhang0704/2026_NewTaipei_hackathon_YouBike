import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { mdhmOf } from '@/lib/format'

/* 全頁遮罩：後端正在推進這一格時蓋住整個畫面。
   出處：meet/20260912/計劃-demo回放邏輯重整.md §3-4（定案③：全頁遮罩）。

   ★ 為什麼要遮整頁而不是只換頂欄狀態：回放走到沒有預測過的格時，後端會
     先把時鐘降回 1x 再打 endpoint，那幾十秒裡畫面上的預測、風險、調度全是
     **上一格**的。不遮的話評審會對著一組已經不成立的警示做判斷。

   ★ 2026-09-12 二修：後端把旗標從「只蓋現算」擴大到**整格**（搬資料＋預測
     ＋風險＋收單，見 jobs/demo.py step()）。理由是風險那階段會先刪掉整個
     origin 的快照再重判，那幾秒 /alerts 讀到的是空的 —— 已經有預測的格
     也會閃一下空白警示。所以文案不能再寫死「正在產生預測」。

   ★ 2026-09-12 三修：**鎖的起點改由前端的虛擬時鐘決定**（跨過 30 分格就鎖），
     判定在 AppShell。原本只看 predicting_origin，但那是後端「開始推進之後」
     才寫的 —— 迴圈最多睡 2 秒才醒、前端再輪詢才看到，中間那幾秒畫面正是
     「時鐘已走進新的一格、資料還停在上一格」的錯位狀態。

   ★ **開**的條件仍然只看後端（current_slot 追上 ＋ 沒有在推進），前端不自己
     計時關閉 —— 後端那支有 TTL 自癒，兩邊各設一套逾時只會讓「遮罩為什麼
     還在／為什麼提早關」變成兩個地方要查。下面那個 30 秒的 timer 只加一行
     提示文字，**不會關掉遮罩**。

   ★ 不吃 Esc、不給關閉鈕：這不是 dialog，是「資料還沒到」的事實。
     能關掉就等於允許看到半套資料。 */
export function PredictingOverlay({
  at,
  speed,
}: {
  /** 要鎖到哪一格；null = 不鎖。可能來自後端 predicting_origin，也可能是前端剛跨過的格。 */
  at?: Date | null
  /** 目前回放流速；<= 1 才顯示「已切回 1x」那行（快轉中通過已預測的格不適用）。 */
  speed?: number | null
}) {
  const key = at ? at.getTime() : null
  // ★ 記「哪一格等太久」而不是一個 boolean —— 換格時 stalled 自然就是 false，
  //   不必在 effect 裡同步 setState 歸零（那會多觸發一次 render）。
  const [slowKey, setSlowKey] = useState<number | null>(null)
  const stalled = key !== null && slowKey === key

  // ★ 只負責多印一行提示，不關遮罩。沒有這道，回放迴圈沒在跑時畫面會鎖死
  //   而且不給任何線索 —— 鎖住是對的（資料真的不一致），但要說得出為什麼。
  useEffect(() => {
    if (key === null) return
    const t = setTimeout(() => setSlowKey(key), 30_000)
    return () => clearTimeout(t)
  }, [key])

  if (!at) return null
  const slowed = speed != null && speed <= 1
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
          正在產生 <span className="tabular-nums">{mdhmOf(at)}</span> 這一輪的
          <br />
          預測與風險判定，完成後會自動接續。
        </p>
        {/* ★ 只在真的降速時才說 —— 快轉中通過「已經有預測」的格也會開遮罩
            （那是在重判風險），那時講「切回 1x」是假的。 */}
        {slowed && (
          <p className="m-0 text-[0.7rem] leading-[1.6] text-ink3">
            回放時鐘已切回 1x —— 這一格沒有預先算好的結果。
          </p>
        )}
        {stalled && (
          <p className="m-0 text-[0.7rem] leading-[1.6] text-ink3">
            已等待逾 30 秒 —— 請確認回放迴圈（jobs.demo --run）仍在執行。
          </p>
        )}
      </div>
    </div>
  )
}
