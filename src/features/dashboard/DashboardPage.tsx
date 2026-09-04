import { useEffect, useMemo, useRef } from 'react'
import { useEventListener } from 'usehooks-ts'
import { X } from 'lucide-react'
import { useAppStore } from '@/stores/useAppStore'
import { useStationDay, useTowns } from '@/api/queries'
import { cn } from '@/lib/utils'
import { KpiStrip } from './KpiStrip'
import { AlertList } from './AlertList'
import { CityMap } from './CityMap/CityMap'
import { StationDetail } from '@/features/station/StationDetail'
import { StationSearch } from '@/features/station/StationSearch'

export default function DashboardPage() {
  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectTown = useAppStore((s) => s.selectTown)
  const selectStation = useAppStore((s) => s.selectStation)

  const { data: towns } = useTowns()
  const chips = useMemo(
    () => [{ code: '', label: '全部行政區' }, ...(towns ?? []).map((t) => ({ code: t.town_code, label: t.town }))],
    [towns],
  )

  const { data: day, isPending: dayPending, error: dayError } = useStationDay(selectedUid)

  // Esc → 清除選取
  useEventListener('keydown', (e) => {
    if (e.key === 'Escape' && selectedUid) selectStation(null)
  })

  // 選了站 → 把「單站檢視」捲進視野
  const detailRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (selectedUid) {
      detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [selectedUid])

  return (
    <div className="mx-auto max-w-[1780px] px-4 pb-16 md:px-6">
      <div className="flex items-baseline gap-4 py-4">
        <h2 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">即時概況</h2>
        <p className="ml-auto hidden max-w-[42ch] text-right text-[0.8rem] leading-[1.6] text-ink3 md:block">
          數字取自最新一輪批次預測（每 30 分）。只讀資料庫，查詢不觸發推論。
        </p>
      </div>

      <KpiStrip />

      {/* 桌機：地圖 + 右 rail 鎖進視窗高度，這一區不整頁捲；<xl 回到流式捲動 */}
      <div className="mt-4 grid grid-cols-1 border border-edge bg-panel xl:h-[calc(100dvh-236px)] xl:min-h-[520px] xl:grid-cols-[1fr_396px] xl:overflow-hidden">
        {/* 地圖欄 */}
        <div className="flex min-w-0 flex-col">
          <div className="phead flex-none gap-3">
            <h3 className="m-0 shrink-0 text-[0.82rem] font-semibold tracking-[0.13em]">空滿熱度圖</h3>
            <div className="ml-auto">
              <StationSearch />
            </div>
          </div>

          <div className="flex flex-none items-center gap-3 border-b border-hair px-4 py-[9px]">
            <span className="kicker flex-none">地區</span>
            <div className="flex gap-[6px] overflow-x-auto [scrollbar-width:none]">
              {chips.map((c) => (
                <button
                  key={c.code}
                  onClick={() => selectTown(c.code)}
                  className={cn(
                    'flex-none whitespace-nowrap rounded-xs border px-[10px] py-[5px] text-[0.76rem] tracking-[0.04em]',
                    townCode === c.code
                      ? 'border-ink bg-ink font-semibold text-bg'
                      : 'border-hair text-ink2 hover:border-edge hover:text-ink',
                  )}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>

          <div className="h-[clamp(460px,60vh,760px)] xl:h-auto xl:min-h-0 xl:flex-1">
            <CityMap />
          </div>
        </div>

        {/* 右 rail：主動警示（上，內捲）＋ 單站檢視（下，選站時展開內捲） */}
        <div className="flex min-w-0 flex-col border-t border-edge xl:h-full xl:overflow-hidden xl:border-l xl:border-t-0">
          <div className="phead flex-none">
            <h3 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">主動警示</h3>
          </div>
          <div
            className={cn(
              'overflow-y-auto',
              selectedUid
                ? 'max-h-[32vh] xl:max-h-none xl:h-[34%] xl:shrink-0'
                : 'max-h-[48vh] xl:max-h-none xl:min-h-0 xl:flex-1',
            )}
          >
            <AlertList />
          </div>

          <div ref={detailRef} className="phead flex-none scroll-mt-16 border-t border-edge">
            <h3 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">單站檢視</h3>
            {selectedUid ? (
              <button
                onClick={() => selectStation(null)}
                className="ml-auto flex items-center gap-1 rounded-xs border border-hair px-2 py-[3px] text-[0.66rem] tracking-[0.1em] text-ink3 hover:border-edge hover:text-ink"
              >
                <X className="size-3" />
                清除
              </button>
            ) : (
              <span className="ml-auto text-[0.66rem] tracking-[0.14em] text-ink3">含未來三小時預測</span>
            )}
          </div>

          <div className={cn(selectedUid && 'xl:min-h-0 xl:flex-1 xl:overflow-y-auto')}>
            {!selectedUid ? (
              <div className="p-4 text-[0.82rem] text-ink3">點地圖站點或上方警示展開。</div>
            ) : dayError ? (
              <div className="m-4 border-l-2 border-hot bg-hot-wash px-3 py-2 text-[0.82rem]">
                {dayError.message || '查詢失敗'}
              </div>
            ) : dayPending ? (
              <div className="p-4 text-[0.82rem] text-ink3">查詢中…</div>
            ) : day ? (
              <StationDetail day={day} />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
