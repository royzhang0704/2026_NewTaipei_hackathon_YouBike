import { useEffect, useMemo, useRef } from 'react'
import { useAppStore, type AlertSide } from '@/stores/useAppStore'
import { useAlerts } from '@/api/queries'
import { segChip } from '@/components/ui/segChip'
import { cn } from '@/lib/utils'

const LV: Record<string, string> = { high: '高', mid: '中', low: '低', none: '' }

const SIDES: { key: AlertSide; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'shortage', label: '缺車' },
  { key: 'full', label: '滿站' },
]

export function AlertList() {
  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectStation = useAppStore((s) => s.selectStation)

  // 篩選狀態在 store：上方 KPI 也能寫（點 KPI = 套用該篩選）
  const side = useAppStore((s) => s.alertSide)
  const level = useAppStore((s) => s.alertLevel)
  const setAlertFilter = useAppStore((s) => s.setAlertFilter)

  // 換地區 / 換篩選 → 清單內容整批換掉，把外層捲軸帶回頂端（否則會停在舊位置看到空白）
  const topRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    topRef.current?.scrollIntoView({ block: 'nearest' })
  }, [townCode, side, level])

  // 抓整包（跟 CityMap 的 {limit:1000, town_code} 同 key，共用快取不多打），側別 / 等級全在前端篩。
  // 1000 是後端硬上限（station_controller Query le=1000）；實際警示數遠低於此，unloaded 只是防呆。
  const { data, isPending, isError } = useAlerts({ limit: 1000, town_code: townCode || null })
  const all = useMemo(() => data?.items ?? [], [data])

  const count = useMemo(
    () => ({
      all: all.length,
      shortage: all.filter((i) => i.side === 'shortage').length,
      full: all.filter((i) => i.side === 'full').length,
    }),
    [all],
  )

  const filtered = useMemo(() => {
    let list = side === 'all' ? all : all.filter((i) => i.side === side)
    if (level !== 'all') list = list.filter((i) => i.level === level)
    return list
  }, [all, side, level])

  // 報讀者狀態訊息：篩選一變、count 一變就唸「（高風險・）缺車・共 N 筆待處理」
  const scopeLabel = [
    level === 'high' ? '高風險' : level === 'mid' ? '中風險' : '',
    side === 'shortage' ? '缺車' : side === 'full' ? '滿站' : '',
  ]
    .filter(Boolean)
    .join('・')
  // 後端 limit 1000；真的更多才提示（一般全新北也就幾百筆）
  const unloaded = data ? Math.max(0, data.total - all.length) : 0

  if (isError) return <div className="p-4 text-[0.8rem] text-ink3">警示載入失敗，請確認後端。</div>
  if (isPending) return <div className="p-4 text-[0.8rem] text-ink3">載入中…</div>

  return (
    <>
      <div ref={topRef} aria-hidden />
      <div className="sticky top-0 z-10 border-b border-hair bg-panel">
        {/* 方向（全部/缺車/滿站）＋ 嚴重度（高/中）。嚴重度兩顆包成一組 → 大字級塞不下時
            整組一起換到第二行（不會只有「中風險」落單），讀起來就是乾淨的兩排。 */}
        <div className="flex flex-wrap items-center gap-[6px] px-3 pt-[7px]">
          {SIDES.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => setAlertFilter({ side: key })}
              aria-pressed={side === key}
              className={cn(segChip(side === key), 'px-2 py-[3px] text-[0.68rem]')}
            >
              {label} <span className="tabular-nums opacity-70">{count[key]}</span>
            </button>
          ))}
          {/* 嚴重度：高 / 中 互斥 toggle（點目前選中的 → 回「全部風險」）。跟上方 KPI 連動同一個 store 值 */}
          <div className="flex gap-[6px]">
            {(['high', 'mid'] as const).map((lv) => (
              <button
                key={lv}
                type="button"
                onClick={() => setAlertFilter({ level: level === lv ? 'all' : lv })}
                aria-pressed={level === lv}
                className={cn(segChip(level === lv), 'px-2 py-[3px] text-[0.68rem]')}
              >
                {lv === 'high' ? '高風險' : '中風險'}
              </button>
            ))}
          </div>
        </div>
        <div
          role="status"
          className="flex items-center justify-between px-4 py-[5px] text-[0.68rem] tracking-[0.06em] text-ink3"
        >
          <span>
            {scopeLabel && `${scopeLabel}・`}共 {filtered.length} 筆待處理
          </span>
          {unloaded > 0 && <span>資料庫另有 {unloaded} 筆，請用地區縮小</span>}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="p-5 text-[0.8rem] leading-[1.7] text-ink3">
          {all.length === 0
            ? '目前範圍內所有站點供需皆落在健康區間。'
            : '此篩選條件下沒有待處理站點。'}
        </div>
      ) : (
        <ul className="m-0 list-none p-0">
          {filtered.map((it, i) => {
            const sel = selectedUid === it.station_uid
            // anim-row：key 穩定 → 只有「新出現的站」重掛播放（換批後看得出哪站新冒出來）；
            // 首次載入時整批淡入；不處理離場，以免引入 AnimatePresence 依賴。
            return (
              <li key={it.station_uid} className="anim-row border-b border-hair">
                {/* 原本是 <li onClick>：鍵盤 Tab 不到、Enter 無效、報讀者不當它可互動。
                    改成原生 <button> → 免寫 keydown 就有 Enter/Space、focus 樣式吃全域 :focus-visible。 */}
                <button
                  type="button"
                  aria-pressed={sel}
                  onClick={() => selectStation(it.station_uid)}
                  className={cn(
                    'relative grid w-full cursor-pointer grid-cols-[2.3em_1fr_auto] items-baseline gap-x-3 px-4 py-[11px] text-left hover:bg-white/[0.03]',
                    sel && 'bg-white/[0.055]',
                  )}
                >
                  {sel && <span className="absolute left-0 top-0 h-full w-[2px] bg-ink" />}
                  <span className="num text-[1rem] text-ink3">{String(i + 1).padStart(2, '0')}</span>
                  <span className="min-w-0">
                    {/* 固定 3 行：站名 ／ 行政區·風險 ／ 可借 X／Y。每段內容都短、whitespace-nowrap，
                        不截斷、不因字級或邊界忽上忽下。缺車/滿站由右欄「補/取」與上方分頁表示。 */}
                    <span className="block font-serif text-[1.05rem] leading-[1.3] tracking-[-0.015em]">
                      {it.name}
                    </span>
                    <span className="mt-[2px] block whitespace-nowrap text-[0.73rem] tracking-[0.04em] text-ink3">
                      {it.town}　·　{LV[it.level]}風險
                    </span>
                    <span className="mt-[1px] block whitespace-nowrap text-[0.73rem] tracking-[0.04em] text-ink3">
                      可借 {it.now.avail ?? '—'}／{it.capacity ?? '?'}
                    </span>
                  </span>
                  <span className="whitespace-nowrap text-right text-[0.7rem] leading-[1.4] tracking-[0.04em] text-ink3">
                    <b
                      className={cn(
                        'num block text-[1rem] tracking-[-0.02em]',
                        it.side === 'shortage' ? 'text-hot' : 'text-cold',
                      )}
                    >
                      {it.dispatch
                        ? `${it.dispatch.action === 'refill' ? '補 ' : it.dispatch.action === 'remove' ? '取 ' : ''}${it.dispatch.bikes} 台`
                        : '—'}
                    </b>
                    {it.streak && <span>已 {it.streak.hours} 小時</span>}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}
