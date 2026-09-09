import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowDown, ArrowUp } from 'lucide-react'
import { useAppStore, type AlertLevel, type AlertSide } from '@/stores/useAppStore'
import { useAlerts } from '@/api/queries'
import { cn } from '@/lib/utils'

type Counts = { high: number; mid: number; shortage: number; full: number }
type Deltas = Partial<Counts>

/** 千分位。待補台數動輒四位數，不分位要數位數。 */
const nf = (n: number) => n.toLocaleString('en-US')

/** 記住上一批（同一 scope）的計數，換 origin 時算變化量。
    換行政區 = scope 變 → 清掉（不能拿全市比單區）；首次載入也沒 baseline。
    回放模式下 origin 隨播放時鐘推進，delta 會跟著動。 */
function useCountsDelta(counts: Counts | null, origin: string | undefined, scope: string): Deltas {
  const prev = useRef<{ scope: string; origin: string; counts: Counts } | null>(null)
  const [deltas, setDeltas] = useState<Deltas>({})

  useEffect(() => {
    if (!counts || !origin) return
    const p = prev.current
    if (p && p.scope === scope && p.origin !== origin) {
      setDeltas({
        high: counts.high - p.counts.high,
        mid: counts.mid - p.counts.mid,
        shortage: counts.shortage - p.counts.shortage,
        full: counts.full - p.counts.full,
      })
    } else if (!p || p.scope !== scope) {
      setDeltas({})
    }
    prev.current = { scope, origin, counts }
  }, [counts, origin, scope])

  return deltas
}

export function KpiStrip() {
  const townCode = useAppStore((s) => s.townCode)
  const alertSide = useAppStore((s) => s.alertSide)
  const alertLevel = useAppStore((s) => s.alertLevel)
  const setAlertFilter = useAppStore((s) => s.setAlertFilter)

  // limit:1000 跟 AlertList 同 key → 共用快取，不多打一次
  const { data, isError, isPending } = useAlerts({ limit: 1000, town_code: townCode || null })
  const s = data?.summary
  const items = useMemo(() => data?.items ?? [], [data])

  // items 是警示清單（level!==none），limit:1000；正常遠低於此。爆量被砍時 data.total > items.length。
  const truncated = (data?.total ?? 0) > items.length

  // 四格全部從同一份 items 現算 → 「依時機（現在/預測）」與「依方向（缺車/滿站）」是同一集合的兩種切法、
  // 加起來永遠對得上。被截斷時：時機兩格改回 summary（伺服器算、沒砍），方向兩格照現算（會偏少但有下方「另有 N 筆」提示）。
  const counts = useMemo<Counts | null>(() => {
    if (!s) return null
    const n = (pred: (i: (typeof items)[number]) => boolean) => items.filter(pred).length
    return {
      high: truncated ? s.high : n((i) => i.level === 'high'),
      mid: truncated ? s.mid : n((i) => i.level === 'mid'),
      shortage: n((i) => i.side === 'shortage'),
      full: n((i) => i.side === 'full'),
    }
  }, [s, items, truncated])

  // 時機兩格的「缺／滿」拆分（填 caption、也讓兩軸加總對得上）。截斷時現算不可信 → 不給。
  const split = useMemo(() => {
    if (truncated) return null
    const c = (lvl: string, side: string) =>
      items.filter((i) => i.level === lvl && i.side === side).length
    return {
      high: `缺 ${c('high', 'shortage')} · 滿 ${c('high', 'full')}`,
      mid: `缺 ${c('mid', 'shortage')} · 滿 ${c('mid', 'full')}`,
    }
  }, [items, truncated])
  const deltas = useCountsDelta(counts, data?.origin, townCode)

  if (isError && !data) {
    return (
      <div role="status" className="border border-edge bg-panel px-4 py-3 text-[0.8rem] text-hot">
        目前無法連線至資料服務，供需概況暫時無法顯示。
      </div>
    )
  }

  if (isPending || !s || !counts) {
    return (
      <div role="status" aria-label="載入供需概況…" className="border border-edge">
        <div className="h-[1.85rem] border-b border-hair bg-panel" />
        <div className="grid grid-cols-2 gap-px bg-hair lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-panel px-4 py-3">
              <div className="mb-[9px] h-[0.68rem] w-16 rounded-xs bg-ink/[0.06]" />
              <div className="h-[1.85rem] w-14 rounded-xs bg-ink/[0.09]" />
              <div className="mt-[9px] h-[0.71rem] w-14 rounded-xs bg-ink/[0.05]" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  const total = s.stations
  const covered = total - s.no_forecast
  const coverageLow = total > 0 && s.no_forecast / total > 0.05

  type Tile = {
    k: string
    v: number
    u: string
    sub: string
    tone: 'hot' | 'cold'
    dk: keyof Counts
    /** 這格對應哪一條篩選軸：風險（高/中）或供需方向（缺車/滿站） */
    axis: 'side' | 'level'
    val: AlertSide | AlertLevel
    active: boolean
  }

  // 兩條獨立的軸：點某格只切自己那條軸、保留另一條 → 缺車＋高風險可同時成立、兩格都標「篩選中」，
  // 與下方主動警示面板同一個 store、同一套 toggle 行為。
  // 嚴重度兩格的白話整句就放在 k（標題列），跟「缺車／滿站」同一個位置 → 四格結構一致、頂端不留空。
  const tiles: Tile[] = (
    [
      { k: '已空站或滿站', v: counts.high, u: '站', sub: split?.high ?? '', tone: 'hot', dk: 'high', axis: 'level', val: 'high' },
      { k: '1 小時內空站或滿站', v: counts.mid, u: '站', sub: split?.mid ?? '', tone: 'hot', dk: 'mid', axis: 'level', val: 'mid' },
      { k: '空站', v: counts.shortage, u: '站', sub: `建議補 ${nf(s.refill.bikes)} 台`, tone: 'hot', dk: 'shortage', axis: 'side', val: 'shortage' },
      { k: '滿站', v: counts.full, u: '站', sub: `建議取 ${nf(s.remove.bikes)} 台`, tone: 'cold', dk: 'full', axis: 'side', val: 'full' },
    ] as const
  ).map((t) => ({
    ...t,
    active: t.axis === 'level' ? alertLevel === t.val : alertSide === t.val,
  }))

  return (
    <div role="group" aria-label="供需概況" className="border border-edge">
      {isError && (
        <p role="status" className="border-b border-hair bg-panel px-4 py-[6px] text-[0.68rem] tracking-[0.04em] text-hot">
          連線不穩，以下數字可能非最新
        </p>
      )}

      {/* 資料信心度：模型覆蓋率（掉 >5% → 琥珀 + 警示圖示，不靠顏色單獨表意）＋「暫不派車」註記。
          不放「資料截至」——那在「供需概況」副標已有。
          每段各自 whitespace-nowrap，靠 flex gap 分隔（不用「·」）→ 窄視窗 / 大字級只在段之間換行，不會斷句、不會孤立分隔點。
          警示色只落在圖示＋覆蓋率本身；兩段註解維持 ink3，避免整行都是琥珀在喊。 */}
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-hair bg-panel px-4 py-[7px] text-[0.68rem] tracking-[0.04em] text-ink3">
        {coverageLow && <AlertTriangle className="size-[11px] flex-none text-hot" aria-hidden />}
        <span className={cn('whitespace-nowrap', coverageLow && 'font-medium text-hot')}>
          模型覆蓋 {nf(covered)} / {nf(total)} 站
        </span>
        {coverageLow && (
          <span className="whitespace-nowrap">{nf(s.no_forecast)} 站無預測數據，未納入下列統計</span>
        )}
        {s.hold > 0 && (
          <span className="whitespace-nowrap">空站另有 {nf(s.hold)} 站預期自行退燒、未計入</span>
        )}
      </p>

      {/* gap-px + bg-hair：格線交給 1px 間隙，2 / 4 欄都自動對齊 */}
      <div className="grid grid-cols-2 gap-px bg-hair lg:grid-cols-4">
        {tiles.map((t) => {
          const d = deltas[t.dk]
          const moved = !!d
          const deltaLabel = moved ? `，較上一批次${d! > 0 ? '增加' : '減少'} ${Math.abs(d!)}` : ''
          const label = `${t.k}，${nf(t.v)} ${t.u}${t.sub ? `，${t.sub}` : ''}${deltaLabel}，${
            t.active ? '按下取消篩選' : '按下篩選警示清單'
          }`
          // 嚴重度兩格（axis==='level'）：標題列放白話整句、字距收窄；說明列僅在有變化量時出現。
          // 缺車/滿站：短標題＋建議台數的標準三列。
          const primary = t.axis === 'level'

          const delta = moved && (
            <span
              key={`d${d}`}
              aria-hidden
              className={cn(
                'kpi-rise inline-flex items-center gap-[1px] tabular-nums',
                d! > 0 ? 'text-hot' : 'text-ink2',
              )}
            >
              {d! > 0 ? <ArrowUp className="size-[10px]" /> : <ArrowDown className="size-[10px]" />}
              {Math.abs(d!)}
            </span>
          )

          const inner = (
            <>
              <span
                className={cn('absolute inset-y-0 left-0 w-[2px]', t.tone === 'hot' ? 'bg-hot' : 'bg-cold')}
                aria-hidden
              />
              {/* 標題列：短標題（缺車/滿站）或白話整句（嚴重度兩格）都放這，四格對齊、頂端不留空。
                  整句較長 → 收窄字距、允許必要時折行。 */}
              <span
                className={cn(
                  'mb-[9px] flex min-h-[0.95rem] flex-wrap items-center gap-x-1 text-[0.68rem]',
                  primary ? 'tracking-[0.02em]' : 'tracking-[0.16em]',
                  t.active ? 'text-ink' : 'text-ink3',
                )}
              >
                {t.k}
                {t.active && (
                  <span className="font-normal tracking-normal text-[0.68rem] text-ink3">篩選中</span>
                )}
              </span>
              <div
                // key 綁 delta 值：新一批數字有動就重掛 → kpi-flash 重播
                key={moved ? `v${d}` : 'v'}
                className={cn(
                  'flex items-baseline font-serif text-[1.85rem] leading-none tracking-[-0.035em] tabular-nums',
                  moved && 'kpi-flash',
                  t.tone === 'hot' ? 'text-hot' : 'text-cold',
                )}
              >
                {nf(t.v)}
                <span className="ml-1.5 font-sans text-[0.76rem] font-normal tracking-normal text-ink3">
                  {t.u}
                </span>
              </div>
              {/* 說明列：一般格放建議台數；嚴重度格只在有變化量時出現，否則收掉、底部留白當呼吸空間 */}
              {(t.sub || delta) && (
                <div className="mt-[9px] flex items-center gap-x-2 text-[0.71rem] text-ink3">
                  {t.sub ? <span>{t.sub}</span> : null}
                  {delta}
                </div>
              )}
            </>
          )

          return (
            <button
              key={t.k}
              type="button"
              // 只切自己那條軸；active 再按 → 該軸回「全部」，跟 AlertList chip 的 toggle-off 一致
              onClick={() =>
                setAlertFilter(
                  t.axis === 'side'
                    ? { side: t.active ? 'all' : (t.val as AlertSide) }
                    : { level: t.active ? 'all' : (t.val as AlertLevel) },
                )
              }
              aria-pressed={t.active}
              aria-controls="alert-list"
              aria-label={label}
              className={cn(
                'relative flex flex-col bg-panel px-4 py-3 text-left transition-colors hover:bg-raise focus-visible:-outline-offset-2',
                // 嚴重度格少一列時（截斷、無「缺／滿」拆分）垂直置中，留白平均分上下；有拆分則跟其他格一樣三列填滿
                primary && !t.sub && 'justify-center',
                t.active && 'bg-raise',
              )}
            >
              {inner}
            </button>
          )
        })}
      </div>
    </div>
  )
}
