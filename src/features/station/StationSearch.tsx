import { useMemo } from 'react'
import { useStations } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { Combobox, type ComboItem } from '@/components/ui/Combobox'

/** 地圖上的站點快速搜尋：打名字 / 行政區 / UID → 選取並飛過去（flyTo 由 CityMap 處理）。
    只在「目前選了某區、但搜到的站不在那區」時才清掉區篩選，否則直接飛、不繞路。 */
export function StationSearch() {
  const stations = useStations().data
  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectTown = useAppStore((s) => s.selectTown)
  const selectStation = useAppStore((s) => s.selectStation)

  const items = useMemo<ComboItem[]>(
    () =>
      (stations ?? [])
        // 61 個佔位站沒有 name / 座標，不能搜也不能飛過去
        .filter((s) => s.name && Number.isFinite(s.lat) && Number.isFinite(s.lon))
        .map((s) => ({
          value: s.uid,
          label: s.name,
          group: s.town,
          keys: s.uid,
        })),
    [stations],
  )

  return (
    <Combobox
      value={selectedUid}
      onChange={(uid) => {
        if (!uid) return
        const st = stations?.find((s) => s.uid === uid)
        // 選了某區、但搜到的站不在該區 → 清掉區篩選（否則地圖上看不到那個站）
        if (st && townCode && st.town_code !== townCode) selectTown('')
        selectStation(uid)
      }}
      items={items}
      placeholder="搜尋站點…"
      searchPlaceholder="站名、行政區或 UID…"
    />
  )
}
