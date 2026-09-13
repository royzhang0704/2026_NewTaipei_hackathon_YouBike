import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowDown, ArrowUp } from 'lucide-react'
import { useAppStore, type AlertSide } from '@/stores/useAppStore'
import { useAlerts } from '@/api/queries'
import { cn } from '@/lib/utils'

type Counts = { high: number; mid: number; shortage: number; full: number; highFull: number; highShortage: number }
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
        highFull: counts.highFull - p.counts.highFull,
        highShortage: counts.highShortage - p.counts.highShortage,
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

  // 六格數字全部從同一份 items 現算 →「所有／高風險」跟「滿站／空站」是同一集合的兩種切法，
  // 加起來永遠對得上。被截斷時：level 相關的數字改回 summary（伺服器算、沒砍，但沒有
  // 「高風險＋方向」的組合統計，只能現算，會偏少，有下方「另有 N 筆」提示可看出資料不完整）。
  const counts = useMemo<Counts | null>(() => {
    if (!s) return null
    const n = (pred: (i: (typeof items)[number]) => boolean) => items.filter(pred).length
    return {
      high: truncated ? s.high : n((i) => i.level === 'high'),
      mid: truncated ? s.mid : n((i) => i.level === 'mid'),
      shortage: n((i) => i.side === 'shortage'),
      full: n((i) => i.side === 'full'),
      highFull: n((i) => i.level === 'high' && i.side === 'full'),
      highShortage: n((i) => i.level === 'high' && i.side === 'shortage'),
    }
  }, [s, items, truncated])

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
    /** side：只切供需方向（保留目前的風險篩選不動）；
        combo：同時套用「方向＋高風險」兩個條件（點第二次都清回全部）。 */
    axis: 'side' | 'combo'
    val: AlertSide
    active: boolean
  }

  // 「所有 X」跟「高風險 X」互斥，一次只會有一格亮：
  //   所有 X   → side 對得上、且 level 是「全部」才算 active（不含只挑高風險的狀態）
  //   高風險 X → side + level 都對得上才算 active（這格本身就是「滿站∩高風險」的交集）
  const tiles: Tile[] = (
    [
      { k: '所有滿站', v: counts.full, u: '站', sub: `建議取 ${nf(s.remove.bikes)} 台`, tone: 'cold', dk: 'full', axis: 'side', val: 'full' },
      { k: '高風險滿站', v: counts.highFull, u: '站', sub: '', tone: 'cold', dk: 'highFull', axis: 'combo', val: 'full' },
      { k: '所有空站', v: counts.shortage, u: '站', sub: `建議補 ${nf(s.refill.bikes)} 台`, tone: 'hot', dk: 'shortage', axis: 'side', val: 'shortage' },
      { k: '高風險空站', v: counts.highShortage, u: '站', sub: '', tone: 'hot', dk: 'highShortage', axis: 'combo', val: 'shortage' },
    ] as const
  ).map((t) => ({
    ...t,
    active:
      t.axis === 'combo'
        ? alertSide === t.val && alertLevel === 'high'
        : alertSide === t.val && alertLevel === 'all',
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
              {/* 標題列：四格標題都短，字距統一，四格對齊、頂端不留空。 */}
              <span
                className={cn(
                  'mb-[9px] flex min-h-[0.95rem] flex-wrap items-center gap-x-1 text-[0.68rem] tracking-[0.16em]',
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
              // side 格「所有 X」：切方向軸、同時把 level 清回全部（不然選過「高風險 X」
              // 後再點「所有 X」，畫面會悄悄還在套著 level='high'，跟「所有」的字面矛盾）。
              // combo 格「高風險 X」：同時套用方向＋高風險；再按一次（active）→ 兩條軸都清回全部。
              onClick={() =>
                setAlertFilter(
                  t.axis === 'combo'
                    ? t.active
                      ? { side: 'all', level: 'all' }
                      : { side: t.val, level: 'high' }
                    : { side: t.active ? 'all' : t.val, level: 'all' },
                )
              }
              aria-pressed={t.active}
              aria-controls="alert-list"
              aria-label={label}
              className={cn(
                'relative flex flex-col bg-panel px-4 py-3 text-left transition-colors hover:bg-raise focus-visible:-outline-offset-2',
                // 高風險兩格沒有 sub 說明列，垂直置中、留白平均分上下；有 sub 則跟其他格一樣三列填滿
                !t.sub && !delta && 'justify-center',
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
