import { useEffect, useRef } from 'react'
import { useEventListener } from 'usehooks-ts'
import { X } from 'lucide-react'
import { useAppStore } from '@/stores/useAppStore'
import { useStationDay } from '@/api/queries'
import { useUrlSync } from '@/hooks/useUrlSync'
import { cn } from '@/lib/utils'
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

  // 選了站 → 把「單站檢視」捲進視野
  const detailRef = useRef<HTMLDivElement>(null)
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

      {/* 桌機：地圖 + 右 rail 吃 flex 剩餘高度（不再 calc 魔術數字），這一區不整頁捲；<xl 流式捲動 */}
      <div className="mt-4 grid grid-cols-1 border border-edge bg-panel xl:min-h-0 xl:flex-1 xl:grid-cols-[1fr_396px] xl:overflow-hidden">
        {/* 地圖欄 */}
        <div className="flex min-w-0 flex-col">
          <div className="phead flex-none">
            <h3 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">空滿熱度圖</h3>
          </div>

          <DistrictPicker trailing={<StationSearch />} />

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
                className="ml-auto flex items-center gap-1 rounded-xs border border-hair px-2 py-[3px] text-[0.7rem] tracking-[0.08em] text-ink3 hover:border-edge hover:text-ink"
              >
                <X className="size-3" />
                清除
              </button>
            ) : (
              <span className="ml-auto text-[0.7rem] tracking-[0.1em] text-ink3">含未來三小時預測</span>
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
              <StationDetail key={day.station.uid} day={day} />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
