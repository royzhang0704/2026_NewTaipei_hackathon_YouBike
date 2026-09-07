import { useMemo, type ReactNode } from 'react'
import { useTowns } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { Combobox, type ComboItem } from '@/components/ui/Combobox'
import { segChip } from '@/components/ui/segChip'
import { cn } from '@/lib/utils'

/* 地區篩選：常用區（依站數前段）永遠可見一列，其餘 29 區收進「其他行政區」
   溢位選單（Radix Popover + cmdk，可打字搜）。選到溢位區時該鈕直接顯示區名。 */

const COMMON_COUNT = 8

// 外觀走全站統一的 segChip；這裡只加尺寸。padding 用 rem（≈10/5px @中）→ 切大中小會跟著縮放
const chipClass = (active: boolean) =>
  cn(segChip(active), 'flex-none whitespace-nowrap px-[0.625rem] py-[0.3125rem] text-[0.76rem]')

export function DistrictPicker({ trailing }: { trailing?: ReactNode }) {
  const townCode = useAppStore((s) => s.townCode)
  const selectTown = useAppStore((s) => s.selectTown)
  const { data: towns } = useTowns()

  // 依站數取前段當常用；顯示時再按區碼排，順序才穩定好記
  const common = useMemo(() => {
    const list = towns ?? []
    return [...list]
      .sort((a, b) => b.station_count - a.station_count)
      .slice(0, COMMON_COUNT)
      .sort((a, b) => a.town_code.localeCompare(b.town_code))
  }, [towns])

  const commonCodes = useMemo(() => new Set(common.map((t) => t.town_code)), [common])
  const overflowActive = !!townCode && !commonCodes.has(townCode)

  const items = useMemo<ComboItem[]>(
    () => (towns ?? []).map((t) => ({ value: t.town_code, label: t.town, suffix: `${t.station_count} 站` })),
    [towns],
  )

  return (
    <div className="flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b border-hair px-4 py-[0.5625rem]">
      <span className="kicker flex-none">地區</span>
      <div role="group" aria-label="行政區篩選" className="flex flex-wrap items-center gap-[0.375rem]">
        <button
          type="button"
          aria-pressed={!townCode}
          onClick={() => selectTown('')}
          className={chipClass(!townCode)}
        >
          全部行政區
        </button>

        {common.map((t) => (
          <button
            key={t.town_code}
            type="button"
            aria-pressed={townCode === t.town_code}
            onClick={() => selectTown(t.town_code)}
            className={chipClass(townCode === t.town_code)}
          >
            {t.town}
          </button>
        ))}

        <Combobox
          value={overflowActive ? townCode : null}
          onChange={(v) => selectTown(v ?? '')}
          items={items}
          placeholder="其他行政區"
          searchPlaceholder="搜尋行政區…"
          triggerClassName={cn(
            'w-auto min-w-0 max-w-[190px] gap-2 px-[0.625rem] py-[0.3125rem] text-[0.76rem]',
            segChip(overflowActive),
            overflowActive ? 'hover:bg-ink' : 'hover:bg-transparent',
          )}
        />
      </div>

      {trailing && <div className="ml-auto flex-none">{trailing}</div>}
    </div>
  )
}
