import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useAppStore } from '@/stores/useAppStore'
import { useStationDay } from '@/api/queries'
import { ApiFail } from '@/api/client'
import { StationPicker } from './StationPicker'
import { StationDetail } from './StationDetail'

export default function StationPage() {
  const { uid: routeUid } = useParams()
  const navigate = useNavigate()
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectStation = useAppStore((s) => s.selectStation)

  // 網址 → store（進頁 / 重整 / 貼連結）
  useEffect(() => {
    if (routeUid && routeUid !== selectedUid) selectStation(routeUid)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeUid])

  // store → 網址（下拉選站後可分享）
  useEffect(() => {
    if (selectedUid && selectedUid !== routeUid) {
      navigate(`/station/${selectedUid}`, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedUid])

  const { data: day, isPending, isFetching, error } = useStationDay(selectedUid)

  return (
    <div className="mx-auto max-w-[920px] px-4 pb-16 md:px-6">
      <section className="panel mt-[18px]">
        <div className="phead">
          <h3 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">選擇站點</h3>
        </div>
        <div className="p-4">
          <StationPicker />
        </div>
      </section>

      <section className="panel mt-[18px]">
        <div className="phead">
          <h3 className="m-0 text-[0.82rem] font-semibold tracking-[0.13em]">單站檢視</h3>
          <span className="ml-auto text-[0.66rem] tracking-[0.14em] text-ink3">
            批次預測 · 每 30 分更新 · 不觸發即時推論
          </span>
        </div>

        {!selectedUid ? (
          <div className="p-4 text-[0.86rem] text-ink3">
            選好行政區與站點後即自動查詢。畫面以最新一輪批次預測為錨點：往前 9 小時實況、往後 3
            小時預測（q19–q90 區間帶）。資料全部來自資料庫，查詢不打 SageMaker。
          </div>
        ) : error ? (
          <div className="m-4 border-l-2 border-hot bg-hot-wash px-[14px] py-[10px] text-[0.84rem]">
            <b className="tracking-[0.08em]">{error instanceof ApiFail ? error.code : 'ERROR'}</b>
            　{error.message || '請確認後端已啟動、API 位址正確'}
          </div>
        ) : isPending ? (
          <div className="p-4 text-[0.86rem] text-ink3">查詢中…</div>
        ) : day ? (
          <>
            {isFetching && (
              <div className="px-4 pt-2 text-[0.66rem] tracking-[0.14em] text-ink3">更新中…</div>
            )}
            <StationDetail day={day} />
          </>
        ) : null}
      </section>
    </div>
  )
}
