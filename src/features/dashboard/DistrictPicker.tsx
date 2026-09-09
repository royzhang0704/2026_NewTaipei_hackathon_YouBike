import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTowns } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { Combobox, type ComboItem } from '@/components/ui/Combobox'
import { segChip } from '@/components/ui/segChip'
import { cn } from '@/lib/utils'

/* 地區篩選（單選：29 區選 1，預設全部）：
   - 「全部」＋站數前 6 的常用區＋最多 2 個最近查過的長尾區 → 一列 radio
     （6＋2 的上限讓整列在常見寬度不會撐到換行）
     （role=radiogroup + roving tabindex：整列一個 Tab 停點，方向鍵移動、Home/End 跳頭尾）
   - 其餘行政區收進「更多地區」溢位選單（Radix Popover + cmdk，可打字搜）
   選到溢位區時該鈕直接顯示區名並套上選中樣式。 */

const COMMON_COUNT = 6
const RECENT_KEY = 'yb_recent_towns'
const RECENT_STORE = 3 // 保留幾筆
const RECENT_SHOW = 2 // 實際顯示幾個（扣掉已是常用的）

// 外觀走全站統一的 segChip；這裡只加尺寸。padding 用 rem（≈10/5px @中）→ 切大中小會跟著縮放
const chipClass = (active: boolean) =>
  cn(segChip(active), 'flex-none whitespace-nowrap px-[0.625rem] py-[0.3125rem] text-[0.76rem]')

const loadRecent = (): string[] => {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, RECENT_STORE) : []
  } catch {
    return []
  }
}
const saveRecent = (v: string[]) => {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(v))
  } catch {
    /* 無痛降級 */
  }
}

export function DistrictPicker({ trailing }: { trailing?: ReactNode }) {
  const townCode = useAppStore((s) => s.townCode)
  const selectTown = useAppStore((s) => s.selectTown)
  const { data: towns } = useTowns()

  // 站數由多到少取前 6 名為常用，並以同一順序顯示：操作員最常點的排左邊，
  // 順序符合對城市規模的直覺；站數幾乎不變 → 位置穩定、好記。
  const common = useMemo(
    () =>
      [...(towns ?? [])].sort((a, b) => b.station_count - a.station_count).slice(0, COMMON_COUNT),
    [towns],
  )
  const commonCodes = useMemo(() => new Set(common.map((t) => t.town_code)), [common])

  // 最近查過的長尾區：選到非常用區就記一筆，讓中頻地區第二次起也是一鍵。
  const [recent, setRecent] = useState<string[]>(loadRecent)
  useEffect(() => {
    if (!townCode || commonCodes.has(townCode)) return
    setRecent((prev) => {
      if (prev[0] === townCode) return prev
      const next = [townCode, ...prev.filter((c) => c !== townCode)].slice(0, RECENT_STORE)
      saveRecent(next)
      return next
    })
  }, [townCode, commonCodes])

  const recentTowns = useMemo(() => {
    const byCode = new Map((towns ?? []).map((t) => [t.town_code, t]))
    return recent
      .filter((c) => !commonCodes.has(c) && byCode.has(c))
      .slice(0, RECENT_SHOW)
      .map((c) => byCode.get(c)!)
  }, [recent, commonCodes, towns])

  // 已在一列 chip 上的區（常用＋最近）——溢位選單不再重複列出
  const hiddenCodes = useMemo(
    () => new Set<string>([...commonCodes, ...recentTowns.map((t) => t.town_code)]),
    [commonCodes, recentTowns],
  )
  const overflowActive = !!townCode && !hiddenCodes.has(townCode)

  // 下拉照站數由多到少排：接續上面 chip 的排名（chip＝前 6），右側「N 站」也順向遞減
  const items = useMemo<ComboItem[]>(
    () =>
      [...(towns ?? [])]
        .filter((t) => !hiddenCodes.has(t.town_code))
        .sort((a, b) => b.station_count - a.station_count)
        .map((t) => ({ value: t.town_code, label: t.town, suffix: `${t.station_count} 站` })),
    [towns, hiddenCodes],
  )

  // radio 列的值序：'' = 全部 → 常用 → 最近；roving tabindex 與方向鍵都照這個序
  const radioValues = useMemo(
    () => ['', ...common.map((t) => t.town_code), ...recentTowns.map((t) => t.town_code)],
    [common, recentTowns],
  )
  const btnRefs = useRef<(HTMLButtonElement | null)[]>([])
  const checkedIdx = radioValues.indexOf(townCode) // 選到溢位區時為 -1
  const tabbableIdx = checkedIdx < 0 ? 0 : checkedIdx // 無選中時 Tab 落在「全部」

  const move = (i: number) => {
    const n = radioValues.length
    const j = ((i % n) + n) % n
    btnRefs.current[j]?.focus()
    selectTown(radioValues[j])
  }
  const onKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault()
        move(checkedIdx < 0 ? 0 : checkedIdx + 1)
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        e.preventDefault()
        move(checkedIdx < 0 ? radioValues.length - 1 : checkedIdx - 1)
        break
      case 'Home':
        e.preventDefault()
        move(0)
        break
      case 'End':
        e.preventDefault()
        move(radioValues.length - 1)
        break
    }
  }

  const renderRadio = (code: string, label: string, idx: number, isRecent = false) => (
    <button
      key={code || '__all__'}
      ref={(el) => {
        btnRefs.current[idx] = el
      }}
      type="button"
      role="radio"
      aria-checked={townCode === code}
      // 「最近查過」只用 aria-label 帶（給報讀者）；視覺分辨靠前面那條分隔線，不掛 title
      // ——native tooltip 會浮出來疊到上方 KPI 條，看起來像多一顆徽章。
      aria-label={isRecent ? `${label}（最近查詢）` : undefined}
      tabIndex={idx === tabbableIdx ? 0 : -1}
      onClick={() => selectTown(code)}
      className={chipClass(townCode === code)}
    >
      {label}
    </button>
  )

  return (
    <div className="flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b border-hair px-4 py-[0.5625rem]">
      <span className="kicker flex-none">地區</span>
      <div className="flex flex-wrap items-center gap-[0.375rem]">
        <div
          role="radiogroup"
          aria-label="常用行政區"
          onKeyDown={onKeyDown}
          className="flex flex-wrap items-center gap-[0.375rem]"
        >
          {renderRadio('', '全部', 0)}
          {common.map((t, i) => renderRadio(t.town_code, t.town, i + 1))}
          {recentTowns.length > 0 && (
            <span className="mx-[1px] h-3.5 w-px flex-none bg-hair" aria-hidden />
          )}
          {recentTowns.map((t, i) =>
            renderRadio(t.town_code, t.town, common.length + 1 + i, true),
          )}
        </div>

        <Combobox
          value={overflowActive ? townCode : null}
          onChange={(v) => selectTown(v ?? '')}
          items={items}
          placeholder={`更多地區（${items.length}）`}
          searchPlaceholder="搜尋行政區…"
          emptyRender={(q) => {
            if (!q) return null
            const hit = (towns ?? []).find((t) => hiddenCodes.has(t.town_code) && t.town.includes(q))
            return hit ? `「${hit.town}」已在上方快速選單` : null
          }}
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
