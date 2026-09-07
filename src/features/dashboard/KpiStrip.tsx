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

  // 高/中風險用 summary（伺服器算）；缺車/滿站用 items 現算 —— 跟主動警示 tab 同一套計法，數字對得上
  const counts = useMemo<Counts | null>(
    () =>
      s
        ? {
            high: s.high,
            mid: s.mid,
            shortage: items.filter((i) => i.side === 'shortage').length,
            full: items.filter((i) => i.side === 'full').length,
          }
        : null,
    [s, items],
  )
  const deltas = useCountsDelta(counts, data?.origin, townCode)

  if (isError && !data) {
    return (
      <div role="status" className="border border-edge bg-panel px-4 py-3 text-[0.8rem] text-hot">
        警示資料連線異常，KPI 暫無法顯示。請確認後端。
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
    /** 點了套用該篩選並捲到清單（四格都可點，對應主動警示的篩選） */
    filter: Partial<{ side: AlertSide; level: AlertLevel }>
    active: boolean
  }

  const tiles: Tile[] = [
    {
      k: '高風險',
      v: counts.high,
      u: '站',
      sub: '已越線',
      tone: 'hot',
      dk: 'high',
      filter: { side: 'all', level: 'high' },
      active: alertLevel === 'high' && alertSide === 'all',
    },
    {
      k: '中風險',
      v: counts.mid,
      u: '站',
      sub: '1 小時內越線',
      tone: 'hot',
      dk: 'mid',
      filter: { side: 'all', level: 'mid' },
      active: alertLevel === 'mid' && alertSide === 'all',
    },
    {
      k: '缺車',
      v: counts.shortage,
      u: '站',
      sub: `建議補 ${nf(s.refill.bikes)} 台`,
      tone: 'hot',
      dk: 'shortage',
      filter: { side: 'shortage', level: 'all' },
      active: alertSide === 'shortage' && alertLevel === 'all',
    },
    {
      k: '滿站',
      v: counts.full,
      u: '站',
      sub: `建議取 ${nf(s.remove.bikes)} 台`,
      tone: 'cold',
      dk: 'full',
      filter: { side: 'full', level: 'all' },
      active: alertSide === 'full' && alertLevel === 'all',
    },
  ]

  return (
    <div role="group" aria-label="供需概況" className="border border-edge">
      {isError && (
        <p role="status" className="border-b border-hair bg-panel px-4 py-[6px] text-[0.68rem] tracking-[0.04em] text-hot">
          連線異常，以下數字可能非最新
        </p>
      )}

      {/* 資料信心度：模型覆蓋率（掉 >5% → 琥珀 + 警示圖示，不靠顏色單獨表意）＋「暫不派車」註記。
          不放「資料截至」——那在「供需概況」副標已有。 */}
      <p
        className={cn(
          'flex flex-wrap items-center gap-x-2 border-b border-hair bg-panel px-4 py-[7px] text-[0.68rem] tracking-[0.04em]',
          coverageLow ? 'text-hot' : 'text-ink3',
        )}
      >
        {coverageLow && <AlertTriangle className="size-[11px] flex-none" aria-hidden />}
        <span>
          模型覆蓋 {nf(covered)} / {nf(total)} 站
        </span>
        {coverageLow && (
          <>
            <span aria-hidden>·</span>
            <span>{nf(s.no_forecast)} 站無預測數據，未納入下列統計</span>
          </>
        )}
        {s.hold > 0 && (
          <>
            <span aria-hidden>·</span>
            <span>缺車站另有 {nf(s.hold)} 站預期自行退燒、未計入</span>
          </>
        )}
      </p>

      {/* gap-px + bg-hair：格線交給 1px 間隙，2 / 4 欄都自動對齊 */}
      <div className="grid grid-cols-2 gap-px bg-hair lg:grid-cols-4">
        {tiles.map((t) => {
          const d = deltas[t.dk]
          const moved = !!d
          const deltaLabel = moved ? `，較上一批次${d! > 0 ? '增加' : '減少'} ${Math.abs(d!)}` : ''
          const label = `${t.k}，${nf(t.v)} ${t.u}，${t.sub}${deltaLabel}，${
            t.active ? '按下取消篩選' : '按下篩選警示清單'
          }`

          const inner = (
            <>
              <span
                className={cn('absolute inset-y-0 left-0 w-[2px]', t.tone === 'hot' ? 'bg-hot' : 'bg-cold')}
                aria-hidden
              />
              <span
                className={cn(
                  'mb-[9px] flex items-center gap-1 text-[0.68rem] tracking-[0.16em]',
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
              <div className="mt-[9px] flex items-center gap-x-2 text-[0.71rem] text-ink3">
                <span>{t.sub}</span>
                {moved && (
                  <span
                    key={`d${d}`}
                    aria-hidden
                    className={cn(
                      'kpi-rise inline-flex items-center gap-[1px] tabular-nums',
                      d! > 0 ? 'text-hot' : 'text-ink2',
                    )}
                  >
                    {d! > 0 ? (
                      <ArrowUp className="size-[10px]" />
                    ) : (
                      <ArrowDown className="size-[10px]" />
                    )}
                    {Math.abs(d!)}
                  </span>
                )}
              </div>
            </>
          )

          return (
            <button
              key={t.k}
              type="button"
              // active 再按 → 取消篩選（回全部），跟 AlertList 嚴重度 chip 的 toggle-off 一致
              onClick={() => setAlertFilter(t.active ? { side: 'all', level: 'all' } : t.filter)}
              aria-pressed={t.active}
              aria-controls="alert-list"
              aria-label={label}
              className={cn(
                'relative bg-panel px-4 py-3 text-left transition-colors hover:bg-raise focus-visible:-outline-offset-2',
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
