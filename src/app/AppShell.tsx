import { Suspense } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useDebounceValue } from 'usehooks-ts'
import { useIsFetching } from '@tanstack/react-query'
import { Loader2, Moon, Sun } from 'lucide-react'
import { useHealth } from '@/api/queries'
import { fmtClock, isReplayMode, mdhm, parseServerTs } from '@/lib/format'
import { useServerClock } from '@/hooks/useServerClock'
import { useSlotSync } from '@/hooks/useSlotSync'
import { useAppStore, type FontScale } from '@/stores/useAppStore'
import { ShortcutsHelp } from '@/components/ShortcutsHelp'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { PredictingOverlay } from '@/components/PredictingOverlay'
import { AssistantWidget } from '@/features/assistant/AssistantWidget'
import { UserMenu } from '@/features/auth/UserMenu'
import { cn } from '@/lib/utils'

// 批次預測一格 = 30 分（同 useSlotSync）。遮罩的「跨格」判定用它。
const SLOT_MS = 30 * 60 * 1000

const FONT_STEPS: { key: FontScale; label: string }[] = [
  { key: 'sm', label: '小' },
  { key: 'md', label: '中' },
  { key: 'lg', label: '大' },
]

/** 有 query 在飛、且持續 > 450ms 才回 true（快取命中的瞬間刷新不閃指示；結束時立即收）。 */
function useSlowFetching() {
  const count = useIsFetching()
  const [debounced] = useDebounceValue(count, 450)
  return count > 0 && debounced > 0
}

export function AppShell() {
  const { data: health, isError, refetch: refetchHealth, dataUpdatedAt } = useHealth()
  const fetching = useSlowFetching()
  const theme = useAppStore((s) => s.theme)
  const toggleTheme = useAppStore((s) => s.toggleTheme)
  const fontScale = useAppStore((s) => s.fontScale)
  const setFontScale = useAppStore((s) => s.setFontScale)

  const { time, speed } = useServerClock(health?.now, isError, refetchHealth, dataUpdatedAt)
  useSlotSync(time)

  // 回放 / 示範時間軸判定：與 OverviewCaption 共用 isReplayMode（見 lib/format）
  const replaying = isReplayMode(health)
  const fast = replaying && speed >= 1.3
  const clock = isError ? '離線' : time ? fmtClock(time) : '—'
  const dot = isError ? 'bg-ink3' : replaying ? 'bg-cold' : 'bg-hot'

  /* ── 全頁遮罩的判定（計劃 §3-4，2026-09-12 三修）─────────────────
     規則：**跨過 30 分格就先鎖，拿到新資料才開。**

     鎖的起點由前端的虛擬時鐘決定，開的條件只看後端：
       鎖 ← 虛擬時鐘已走進一個「資料還沒搬過來」的格（floor(now) > current_slot）
            或 後端明說正在推進（predicting_origin）
       開 ← 兩者皆否：書籤追上了，而且迴圈不在推進中

     ★ 為什麼不能只看 predicting_origin：那是後端**開始推進之後**才寫的。
       回放迴圈最多睡 2 秒才醒、前端再輪詢幾秒才看到，中間那段畫面正是
       「時鐘已經走進新的一格、資料還停在上一格」的錯位狀態 —— 使用者看到
       的閃動就是它。前端自己知道時鐘跨格了，不必等後端通知。

     ⚠ 三道護欄，少一道就會鎖死：
       ① replaying：非 demo 不鎖（真實時間沒有回放迴圈會推進書籤，
          tick.py 已刪、crontab 已清空 —— 不擋的話真實模式開畫面就是黑的）
       ② scheduler_on：總開關關著時書籤本來就不會動，不該鎖
       ③ 停表（demo_until 到點）時鐘不走 → 不會跨格 → 自然不鎖，不必特判 */
  const curSlotMs = parseServerTs(health?.current_slot)?.getTime() ?? null
  const crossedMs =
    replaying && (health?.scheduler_on ?? false) && time && curSlotMs !== null
      ? Math.floor(time.getTime() / SLOT_MS) * SLOT_MS
      : null
  const lockAt =
    parseServerTs(health?.predicting_origin) ??
    (crossedMs !== null && curSlotMs !== null && crossedMs > curSlotMs
      ? new Date(crossedMs)
      : null)

  const dataTo = health?.current_slot ? mdhm(health.current_slot) : null
  const fcTo = health?.forecast_end ? mdhm(health.forecast_end) : null

  return (
    // <xl：min-h-dvh，內容可超出、整頁捲，footer 靠 flex-1 的 main 沉底。
    // xl：h-dvh 固定高（有天花板，關鍵差異）→ main 拿 flex-1 剩餘高、overflow-hidden 當保險，
    //     DashboardPage 的地圖區再吃 main 剩餘高，不再用 calc 魔術數字。
    <div className="flex min-h-dvh flex-col xl:h-dvh xl:min-h-0">
      <header className="sticky top-0 z-30 flex h-14 flex-none items-center gap-4 border-b border-edge bg-bg px-4 md:px-6">
        <NavLink
          to="/"
          aria-label="新北市 YouBike 調度中心，回首頁"
          className="flex items-center gap-3 no-underline"
        >
          {/* 自製識別：沿用地圖「站點圓點」的視覺語言，不套 YouBike 品牌字標 */}
          <span className="flex size-[15px] flex-none items-center justify-center text-hot" aria-hidden>
            <svg viewBox="0 0 16 16" fill="none" className="size-full">
              <circle cx="8" cy="8" r="6.25" stroke="currentColor" strokeWidth="1.8" />
              <circle cx="8" cy="8" r="2.4" fill="currentColor" />
            </svg>
          </span>
          <span className="text-[0.92rem] font-semibold tracking-[0.01em] text-ink">新北市 YouBike</span>
          <span className="hidden border-l border-hair pl-3 text-[0.7rem] tracking-[0.16em] text-ink2 sm:inline">
            調度中心
          </span>
        </NavLink>

        <div className="ml-auto flex items-center gap-3">
          {/* 資料時間：狀態＋時鐘收成一顆 chip；資料 / 預測涵蓋範圍放 title 提示 */}
          <div
            className="inline-flex h-8 items-center gap-2 rounded-xs border border-control bg-panel px-3"
            title={dataTo || fcTo ? `資料到 ${dataTo ?? '—'}　·　預測到 ${fcTo ?? '—'}` : undefined}
          >
            <span
              className={cn('beat-dot size-[6px] flex-none rounded-full', dot)}
              style={{ animation: 'beat 2.1s infinite' }}
            />
            <span
              aria-live="polite"
              className="text-[0.66rem] font-semibold tracking-[0.14em] text-ink2"
            >
              {isError ? '離線' : replaying ? '回放時間' : '現在時間'}
            </span>
            {!isError && (
              <span className="tabular-nums text-[0.82rem] tracking-[0.01em] text-ink">{clock}</span>
            )}
            {fast && (
              <span className="rounded-xs border border-hair px-1 text-[0.64rem] font-semibold tracking-[0.04em] text-ink2">
                ×{Math.round(speed)}
              </span>
            )}
            {fetching && (
              <Loader2 className="spin-ind size-3 flex-none animate-spin text-ink3" aria-label="更新中" />
            )}
          </div>

          {/* 文字大小（無障礙）：調 root font-size，rem 型的字級與間距一起等比縮放 */}
          <div
            role="group"
            aria-label="文字大小"
            className="hidden h-8 items-center overflow-hidden rounded-xs border border-control bg-panel sm:flex"
          >
            {FONT_STEPS.map(({ key, label }, i) => (
              <button
                key={key}
                type="button"
                onClick={() => setFontScale(key)}
                aria-pressed={fontScale === key}
                aria-label={`文字大小：${label}`}
                className={cn(
                  'flex h-full w-7 items-center justify-center text-[0.72rem] leading-none',
                  i > 0 && 'border-l border-hair',
                  // 選中＝ink 實心反白，跟地區 chip / 警示篩選（segChip）同一套語言
                  fontScale === key
                    ? 'bg-ink font-semibold text-bg'
                    : 'text-ink3 hover:bg-hair hover:text-ink',
                )}
              >
                {label}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? '切換為淺色主題' : '切換為深色主題'}
            className="flex size-8 items-center justify-center rounded-xs border border-control bg-panel text-ink3 hover:text-ink active:bg-hair"
          >
            {theme === 'dark' ? <Sun className="size-[15px]" /> : <Moon className="size-[15px]" />}
          </button>

          <UserMenu />
        </div>
      </header>

      <main className="flex flex-1 flex-col xl:min-h-0 xl:overflow-hidden">
        <ErrorBoundary>
          <Suspense fallback={<div className="p-8 text-sm text-ink3">載入中…</div>}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </main>

      <footer className="flex-none border-t border-hair px-4 py-3 text-center text-[0.72rem] leading-[1.7] text-ink3 md:px-6">
        資料來源：新北市政府 · YouBike 公開資料。本工具為 2026 新北市 AI 智慧城市黑客松作品，非 YouBike 官方產品。
        <span className="ml-2 whitespace-nowrap">
          按{' '}
          <kbd className="rounded-[3px] border border-hair px-[4px] py-px text-[0.7rem] text-ink2">?</kbd>{' '}
          看快捷鍵
        </span>
      </footer>

      <ShortcutsHelp />
      <AssistantWidget />
      {/* ★ 放在最後、z-50：要蓋住頂欄、抽屜與助理浮層。 */}
      <PredictingOverlay at={lockAt} speed={health?.demo_speed ?? null} />

      <style>{`
        @keyframes beat { 50% { opacity: 0.25 } }
        @media (prefers-reduced-motion: reduce) { .beat-dot { animation: none !important } }
      `}</style>
    </div>
  )
}
