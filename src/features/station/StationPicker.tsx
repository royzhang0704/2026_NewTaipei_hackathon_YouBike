import { useMemo } from 'react'
import { useStations, useTowns } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { Combobox, type ComboItem } from '@/components/ui/Combobox'

const ALL = '__all__'

export function StationPicker({ onSelect }: { onSelect?: (uid: string) => void }) {
  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectTown = useAppStore((s) => s.selectTown)
  const selectStation = useAppStore((s) => s.selectStation)

  const { data: towns, isPending: townsPending } = useTowns()
  const { data: stations, isPending: stationsPending, isError } = useStations()

  const townItems = useMemo<ComboItem[]>(() => {
    const total = stations?.length ?? 0
    return [
      { value: ALL, label: '全部行政區', suffix: `${total} 站` },
      ...(towns ?? []).map((t) => ({
        value: t.town_code,
        label: t.town,
        suffix: `${t.station_count} 站`,
      })),
    ]
  }, [towns, stations])

  const stationItems = useMemo<ComboItem[]>(
    () =>
      (stations ?? [])
        .filter((s) => s.name && (!townCode || s.town_code === townCode))
        .map((s) => ({
          value: s.uid,
          label: s.name,
          group: s.town,
          keys: s.uid,
          suffix: s.model_known ? undefined : s.proxy_available ? '代理' : '無模型',
        })),
    [stations, townCode],
  )

  const disabled = townsPending || stationsPending || isError

  return (
    <div className="flex flex-wrap items-end gap-[10px]">
      <label className="flex min-w-0 flex-col gap-[6px]">
        <span className="kicker">行政區</span>
        <Combobox
          value={townCode || ALL}
          onChange={(v) => selectTown(v && v !== ALL ? v : '')}
          items={townItems}
          disabled={disabled}
          placeholder="載入中…"
          searchPlaceholder="搜尋行政區…"
        />
      </label>

      <label className="flex min-w-0 flex-col gap-[6px]">
        <span className="kicker">站點</span>
        <Combobox
          value={selectedUid}
          onChange={(v) => {
            selectStation(v)
            if (v) onSelect?.(v)
          }}
          items={stationItems}
          disabled={disabled}
          placeholder={isError ? '後端連不上' : '— 選站點 —'}
          searchPlaceholder="搜尋站名、行政區或 UID…"
        />
      </label>
    </div>
  )
}
