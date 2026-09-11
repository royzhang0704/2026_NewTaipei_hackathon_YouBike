import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useAlerts, useHealth } from '@/api/queries'
import { isReplayMode, mdhm, parseServerTs } from '@/lib/format'
import { cn } from '@/lib/utils'

/* 「供需概況」副標＝讀這頁數字的前提，兩種時間軸分開處理：

   正式時間軸（/api/v1/healthz 的 now ≈ 真實現在）：重點是資料新鮮度。data_age_min（後端算好）
   > 60 分 = 跳過一個批次週期以上 → ⚠ 過期。門檻取 2×批次間隔（30 分）：落後一個週期屬正常波動（當前格尚未關閉），落後兩個週期才視為異常，以免誤報。

   示範回放（/api/v1/healthz 的 now 本身就是歷史時刻 → isHistoricalClock）：副標刻意跟「即時正常」
   長一樣（每 30 分批次預測 · 資料截至 · 預測至），只差不算「下次更新」倒數，也不出現 ⚠
   「資料延遲」——回放時 data_age_min 沒意義。是否為回放，由頭欄時鐘（回放時間 ×N）標示，
   副標不再放置模式標記，避免展示感。
   （不分「回放中／已結束」——判「已結束」要靠 forecast_end，但 jobs.demo --start 會把它
   清成 null，方案 A 無 tick loop 又不會重寫，這訊號結構性失效。時鐘停表時頭欄的 ×N
   會消失，那已是「沒在前進」的隱性提示。）

   「資料截至」一律取 /alerts 回應的 origin —— 那才是 KPI / 警示實際依據的那輪預測時刻，
   會隨 effective_now() 前進；不 fallback 到 health.current_slot（那是 --start 寫死的
   書籤 t0v−30min，方案 A 下永遠是它，會誤導）。 */
const STALE_MIN = 60

// 後端時間字串是台北時間、無時區；<time dateTime> 需要 ISO，補 +08:00（台灣不換日光節約時間）
const toISO = (ts: string) => `${ts.replace(' ', 'T').slice(0, 16)}+08:00`

/** 下次批次 ≈ current_slot + 30 分。每 15 秒重算（分鐘級顯示夠用、churn 極低）。
    aria-hidden：一分鐘變一次的倒數對報讀者是雜訊，且父層 aria-atomic 會整段重播。 */
function NextUpdate({ slot }: { slot: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(id)
  }, [])
  const t0 = parseServerTs(slot)
  if (!t0) return null
  const leftMs = t0.getTime() + 30 * 60_000 - now
  return (
    <span aria-hidden>
      {leftMs <= 30_000 ? '即將更新' : `下次更新 約 ${Math.round(leftMs / 60_000)} 分`}
    </span>
  )
}

export function OverviewCaption() {
  const { data: health } = useHealth()
  // 只為讀頂層 origin，limit 拉到 1；1000 列那份交給 CityMap（且選區時 cache key 不同、不共用）
  const { data: alerts } = useAlerts({ limit: 1, town_code: null })

  const replay = isReplayMode(health)
  const originTime = alerts?.origin ? (
    <time dateTime={toISO(alerts.origin)}>{mdhm(alerts.origin)}</time>
  ) : null
  const fcEnd = health?.forecast_end ?? null

  // 一律渲染（含手機）：「資料延遲」是判讀前提，不應因視窗過窄而隱藏。
  // 只有冗長的時刻段落（資料截至 / 預測至）在 <sm 收起。
  const base =
    'm-0 inline-flex flex-wrap items-center gap-x-2 gap-y-1 border-l border-hair pl-3 text-[0.76rem] tracking-[0.02em]'
  const sep = (
    <span className="text-ink3" aria-hidden>
      ·
    </span>
  )
  // 分隔點 + 時刻段落，<sm 整組收起
  const timeSeg = (label: string, node: typeof originTime) => (
    <span className="hidden items-center gap-x-2 sm:inline-flex">
      {sep}
      <span>
        {label} {node}
      </span>
    </span>
  )

  // 示範回放：中性語氣、不警示、不放模式 chip —— 跟「即時正常」同一個長相，只少「下次更新」倒數
  if (replay) {
    return (
      <p
        key="replay"
        id="overview-caption"
        aria-live="polite"
        aria-atomic="true"
        className={cn(base, 'anim-soft text-ink2')}
      >
        <span>每 30 分批次預測</span>
        {originTime && timeSeg('資料截至', originTime)}
        {fcEnd && timeSeg('預測至', <time dateTime={toISO(fcEnd)}>{mdhm(fcEnd)}</time>)}
      </p>
    )
  }

  // 正式時間軸
  const age = health?.data_age_min ?? null
  const behind = age != null && age > STALE_MIN
  const shownLag = age != null ? Math.round(age / 5) * 5 : 0

  return (
    <p
      key={behind ? 'behind' : 'normal'}
      id="overview-caption"
      aria-live="polite"
      aria-atomic="true"
      className={cn(base, 'anim-soft', behind ? 'text-hot' : 'text-ink2')}
    >
      {behind ? (
        <>
          <AlertTriangle className="size-[13px] flex-none" aria-hidden />
          <span>資料延遲 約 {shownLag} 分</span>
          {sep}
          <span>每 30 分批次預測</span>
        </>
      ) : (
        <>
          <span>每 30 分批次預測</span>
          {health?.current_slot && (
            <>
              {sep}
              <NextUpdate slot={health.current_slot} />
            </>
          )}
          {originTime && timeSeg('資料截至', originTime)}
          {fcEnd && timeSeg('預測至', <time dateTime={toISO(fcEnd)}>{mdhm(fcEnd)}</time>)}
        </>
      )}
    </p>
  )
}
