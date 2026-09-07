import { useMemo, useState } from 'react'
import { useEventListener } from 'usehooks-ts'
import { Search } from 'lucide-react'
import { useStations } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { Combobox, type ComboItem } from '@/components/ui/Combobox'

/** 地圖上的站點快速搜尋：打名字 / 行政區 / UID → 選取並飛過去（flyTo 由 CityMap 處理）。
    只在「目前選了某區、但搜到的站不在那區」時才清掉區篩選，否則直接飛、不繞路。

    未輸入時的預設排序（cmdk 一旦有 query 就改用比對分數，這裡只影響剛打開）：
      1. 目前選中區的站，按站名
      2. 其餘：按行政區碼分組、每區內按站名
    「待處理」的分流交給右邊「主動警示」，搜尋框只管查找 / 傳送，不重複那份清單。 */

export function StationSearch() {
  const stations = useStations().data
  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectTown = useAppStore((s) => s.selectTown)
  const selectStation = useAppStore((s) => s.selectStation)

  const [open, setOpen] = useState(false)
  // 「/」＝聚焦搜尋（GitHub / Slack 慣例）；正在打字的欄位裡不攔
  useEventListener('keydown', (e) => {
    if (e.key !== '/' || open) return
    const el = document.activeElement as HTMLElement | null
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
    e.preventDefault()
    setOpen(true)
  })

  const items = useMemo<ComboItem[]>(() => {
    const valid = (stations ?? []).filter(
      // 61 個佔位站沒有 name / 座標，不能搜也不能飛過去
      (s) => s.name && Number.isFinite(s.lat) && Number.isFinite(s.lon),
    )

    const ordered = [...valid].sort((x, y) => {
      const bx = townCode && x.town_code === townCode ? 0 : 1
      const by = townCode && y.town_code === townCode ? 0 : 1
      if (bx !== by) return bx - by
      if (bx === 1 && x.town_code !== y.town_code) return x.town_code.localeCompare(y.town_code)
      return x.name.localeCompare(y.name, 'zh-Hant')
    })

    return ordered.map((s) => ({ value: s.uid, label: s.name, group: s.town, keys: s.uid }))
  }, [stations, townCode])

  return (
    <Combobox
      open={open}
      onOpenChange={setOpen}
      value={selectedUid}
      onChange={(uid) => {
        if (!uid) return
        const st = stations?.find((s) => s.uid === uid)
        // 選了某區、但搜到的站不在該區 → 清掉區篩選（否則地圖上看不到那個站）
        if (st && townCode && st.town_code !== townCode) selectTown('')
        selectStation(uid)
      }}
      items={items}
      placeholder={townCode ? '搜尋站點（全部行政區）…' : '搜尋站點…'}
      searchPlaceholder="站名、行政區或 UID…"
      // 左側放大鏡 → 跟旁邊「其他行政區」純選單型分開，一眼是「打字查找」
      leadingIcon={<Search className="size-full" />}
      // 右側 kbd 提示「按 / 聚焦」；開啟時自動收起。窄螢幕（多半觸控）不顯示
      trailingHint={
        <kbd className="hidden rounded-[3px] border border-hair px-[4px] py-px font-sans text-[0.68rem] leading-none text-ink3 sm:block">
          /
        </kbd>
      }
      // 固定寬度：選到長站名時 trigger 不會變寬、不再推擠上方的地區 chip 列（label 本來就 truncate）
      triggerClassName="w-[248px] min-w-0 max-w-none"
    />
  )
}
