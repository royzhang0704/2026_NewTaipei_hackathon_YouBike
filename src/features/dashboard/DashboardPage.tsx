import { useEffect, useRef } from 'react'
import { useEventListener } from 'usehooks-ts'
import { X } from 'lucide-react'
import { useAppStore } from '@/stores/useAppStore'
import { useStationDay } from '@/api/queries'
import { useUrlSync } from '@/hooks/useUrlSync'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { KpiStrip } from './KpiStrip'
import { OverviewCaption } from './OverviewCaption'
import { AlertList } from './AlertList'
import { CityMap } from './CityMap/CityMap'
import { DistrictPicker } from './DistrictPicker'
import { StationDetail } from '@/features/station/StationDetail'
import { StationSearch } from '@/features/station/StationSearch'

export default function DashboardPage() {
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectStation = useAppStore((s) => s.selectStation)

  useUrlSync() // ?town=&station= ↔ store：重新整理 / 分享連結保留視野

  const { data: day, isPending: dayPending, error: dayError } = useStationDay(selectedUid)

  // Esc → 清除選取
  useEventListener('keydown', (e) => {
    if (e.key === 'Escape' && selectedUid) selectStation(null)
  })

  // 選了站 → 把單站檢視面板捲進視野（<xl 流式版；xl 是固定側欄，不需要）
  const detailRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (selectedUid) {
      detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [selectedUid])

  return (
    // xl：撐滿 AppShell 的 <main>（flex-1 of h-dvh），成直欄；地圖區用 flex-1 吃剩餘高度。<xl 流式捲動
    // 2xl（壁掛 / 大監視器）放寬上限，多塞地圖和警示列、不浪費兩側留白
    <div className="mx-auto max-w-[1780px] px-4 pb-8 md:px-6 xl:flex xl:h-full xl:min-h-0 xl:flex-col 2xl:max-w-[2160px]">
      <div className="flex items-baseline gap-3 py-4">
        <h2
          aria-describedby="overview-caption"
          className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]"
        >
          供需概況
        </h2>
        <OverviewCaption />
      </div>

      <KpiStrip />

      {/* B（非 modal 側滑抽屜）：grid 永遠 2 欄（地圖 | 主動警示），尺寸不隨選站變。
          單站檢視 xl 時 absolute 貼在地圖欄右緣、蓋住地圖最右 400px；<xl 正常堆疊在地圖下方。 */}
      <div className="mt-4 grid grid-cols-1 border border-edge bg-panel xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(0,1fr)_396px] xl:overflow-hidden">
        {/* 地圖欄（relative：單站抽屜以它的右緣為錨） */}
        <div className="relative flex min-w-0 flex-col">
          <div className="phead flex-none">
            <h3 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">空滿熱度圖</h3>
          </div>

          <DistrictPicker trailing={<StationSearch />} />

          <div className="h-[clamp(460px,60vh,760px)] xl:h-auto xl:min-h-0 xl:flex-1">
            <CityMap />
          </div>

          {/* 單站檢視抽屜：選站才在 DOM。<xl 正常區塊；xl absolute 貼右緣不動 grid */}
          {selectedUid && (
            <aside
              ref={detailRef}
              aria-label="單站檢視"
              className="anim-panel drawer-float flex scroll-mt-16 flex-col border-t border-edge bg-float xl:absolute xl:inset-y-0 xl:right-0 xl:z-20 xl:w-[clamp(360px,25rem,460px)] xl:border-l xl:border-l-control xl:border-t-0"
            >
              <div className="phead flex-none">
                <h3 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">單站檢視</h3>
                <button
                  onClick={() => selectStation(null)}
                  aria-label="關閉單站檢視"
                  className="ml-auto -mr-1 flex items-center gap-1 rounded-xs px-1.5 py-1 text-[0.7rem] tracking-[0.08em] text-ink3 hover:bg-hair hover:text-ink"
                >
                  <X className="size-3.5" />
                  關閉
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                {dayError ? (
                  <div className="m-4 border-l-2 border-hot bg-hot-wash px-3 py-2 text-[0.82rem]">
                    {dayError.message || '查詢失敗'}
                  </div>
                ) : dayPending ? (
                  <div className="p-4 text-[0.82rem] text-ink3">查詢中…</div>
                ) : day ? (
                  // key＝uid：換站時連同 ErrorBoundary 一起重掛 → 某站 render 炸掉後，
                  // 改選別站會復原（不會卡在錯誤畫面）。壞掉時只壞這個抽屜，不牽連整頁。
                  <ErrorBoundary
                    key={day.station.uid}
                    fallback={
                      <div className="m-4 border-l-2 border-hot bg-hot-wash px-3 py-2 text-[0.82rem] leading-[1.5] text-ink2">
                        單站檢視載入失敗，請改選其他站或重新整理。
                      </div>
                    }
                  >
                    <StationDetail day={day} />
                  </ErrorBoundary>
                ) : null}
              </div>
            </aside>
          )}
        </div>

        {/* 主動警示：常駐右欄，尺寸不變、滿版可捲 */}
        <div className="flex min-w-0 flex-col border-t border-edge xl:h-full xl:overflow-hidden xl:border-l xl:border-t-0">
          <div className="phead flex-none">
            <h3 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">主動警示</h3>
          </div>
          <div className="max-h-[52vh] min-h-0 flex-1 overflow-y-auto xl:max-h-none">
            <AlertList />
          </div>
        </div>
      </div>
    </div>
  )
}
