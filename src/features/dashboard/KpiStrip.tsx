import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { useAppStore } from '@/stores/useAppStore'
import { useAlerts } from '@/api/queries'
import type { AlertsSummary } from '@/api/types'
import { cn } from '@/lib/utils'

type DeltaKey = 'high' | 'mid' | 'refill' | 'remove'
type Deltas = Partial<Record<DeltaKey, number>>

/** 記住上一批（同一 scope）的 summary，換 origin 時算各數字變化量。
    換行政區 = scope 變 → 清掉（不能拿全市跟單區比）；首次載入也沒 baseline。
    回放模式下 origin 隨播放時鐘推進，delta 會跟著動。 */
function useSummaryDelta(
  summary: AlertsSummary | undefined,
  origin: string | undefined,
  scope: string,
): Deltas {
  const prev = useRef<{ scope: string; origin: string; summary: AlertsSummary } | null>(null)
  const [deltas, setDeltas] = useState<Deltas>({})

  useEffect(() => {
    if (!summary || !origin) return
    const p = prev.current
    if (p && p.scope === scope && p.origin !== origin) {
      setDeltas({
        high: summary.high - p.summary.high,
        mid: summary.mid - p.summary.mid,
        refill: summary.refill.stations - p.summary.refill.stations,
        remove: summary.remove.stations - p.summary.remove.stations,
      })
    } else if (!p || p.scope !== scope) {
      setDeltas({})
    }
    prev.current = { scope, origin, summary }
  }, [summary, origin, scope])

  return deltas
}

export function KpiStrip() {
  const townCode = useAppStore((s) => s.townCode)
  const { data, isError, isPending } = useAlerts({ limit: 1, town_code: townCode || null })
  const s = data?.summary
  const deltas = useSummaryDelta(s, data?.origin, townCode)

  // 連線異常時原本每格顯示「—」，會被讀成「真的 0 站」。明講是離線。
  // 有 data = 背景 refetch 失敗但還有上一次結果 → 續顯示數字，只標「可能非最新」。
  if (isError && !data) {
    return (
      <div
        role="status"
        className="border border-edge bg-panel px-4 py-3 text-[0.8rem] text-hot"
      >
        警示資料連線異常，KPI 暫無法顯示。請確認後端。
      </div>
    )
  }

  // 首次載入（尚無任何資料）：骨架，不顯示一排會被誤讀成「真的 0」的「—」
  if (isPending) {
    return (
      <div
        role="status"
        aria-label="載入供需概況…"
        className="grid grid-cols-2 gap-px border border-edge bg-hair sm:grid-cols-3 lg:grid-cols-6"
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="bg-panel px-4 py-3">
            <div className="mb-[9px] h-[0.68rem] w-14 rounded-xs bg-ink/[0.06]" />
            <div className="h-[1.85rem] w-10 rounded-xs bg-ink/[0.09]" />
            <div className="mt-[9px] h-[0.71rem] w-16 rounded-xs bg-ink/[0.05]" />
          </div>
        ))}
      </div>
    )
  }

  const figs: {
    k: string
    v: number | string
    u: string
    d: string
    tone: 'hot' | 'cold' | ''
    dk?: DeltaKey
  }[] = [
    {
      k: '監測站點',
      v: s?.stations ?? '—',
      u: '站',
      d: `含預測 ${(s?.stations ?? 0) - (s?.no_forecast ?? 0)} 站`,
      tone: '',
    },
    { k: '高風險', v: s?.high ?? '—', u: '站', d: '需立即處理', tone: s?.high ? 'hot' : '', dk: 'high' },
    { k: '中風險', v: s?.mid ?? '—', u: '站', d: '1 小時內越線', tone: s?.mid ? 'hot' : '', dk: 'mid' },
    {
      k: '需補車',
      v: s?.refill.stations ?? '—',
      u: '站',
      d: `共 ${s?.refill.bikes ?? 0} 台`,
      tone: s?.refill.stations ? 'hot' : '',
      dk: 'refill',
    },
    {
      k: '需取車',
      v: s?.remove.stations ?? '—',
      u: '站',
      d: `共 ${s?.remove.bikes ?? 0} 台`,
      tone: s?.remove.stations ? 'cold' : '',
      dk: 'remove',
    },
    { k: '暫不派車', v: s?.hold ?? '—', u: '站', d: '會自行退燒', tone: '' },
  ]

  // gap-px + bg-hair：格線交給 1px 間隙，任何欄數（2 / 3 / 6）都自動對齊，不必再按斷點
  // 算 nth-child。原本 (i+1)%6 寫死 6 欄，在 2 / 3 欄佈局下每列最右格會多畫一條 border-r，
  // 貼著外框變雙線。
  return (
    <div className="border border-edge">
      {isError && (
        <p role="status" className="border-b border-hair bg-panel px-4 py-[6px] text-[0.68rem] tracking-[0.04em] text-hot">
          連線異常，以下數字可能非最新
        </p>
      )}
      <div className="grid grid-cols-2 gap-px bg-hair sm:grid-cols-3 lg:grid-cols-6">
        {figs.map((f) => {
          const d = f.dk ? deltas[f.dk] : undefined
          const moved = !!d // 非 0 = 這批有變動 → 觸發閃爍 + 徽章
          return (
            <div key={f.k} className="relative bg-panel px-4 py-3">
              {f.tone && (
                <span
                  className={cn(
                    'absolute inset-y-0 left-0 w-[2px]',
                    f.tone === 'hot' ? 'bg-hot' : 'bg-cold',
                  )}
                />
              )}
              <span className="mb-[9px] block text-[0.68rem] tracking-[0.16em] text-ink3">{f.k}</span>
              <div
                // key 綁 delta 值：新一批數字有動就重掛 → kpi-flash 重播
                key={moved ? `v${d}` : 'v'}
                className={cn(
                  // 數字 2rem → 1.85rem：標籤抬到 0.68rem 後，收緊「標籤↔數字」比例
                  'flex items-baseline font-serif text-[1.85rem] leading-none tracking-[-0.035em] tabular-nums',
                  moved && 'kpi-flash',
                  f.tone === 'hot' ? 'text-hot' : f.tone === 'cold' ? 'text-cold' : 'text-ink',
                )}
              >
                {f.v}
                {/* 單位：sans、降一階明度、清掉數字用的負字距，跟大數字拉開層次也不貼死 */}
                <span className="ml-1.5 font-sans text-[0.76rem] font-normal tracking-normal text-ink3">
                  {f.u}
                </span>
              </div>
              <div className="mt-[9px] flex items-center gap-x-2 text-[0.71rem] text-ink3">
                <span>{f.d}</span>
                {moved && (
                  <span
                    key={`d${d}`}
                    aria-label={`較上批${d! > 0 ? '增加' : '減少'} ${Math.abs(d!)}`}
                    className={cn(
                      'kpi-rise inline-flex items-center gap-[1px] tabular-nums',
                      d! > 0 ? 'text-hot' : 'text-ink2',
                    )}
                  >
                    {d! > 0 ? (
                      <ArrowUp className="size-[10px]" aria-hidden />
                    ) : (
                      <ArrowDown className="size-[10px]" aria-hidden />
                    )}
                    {Math.abs(d!)}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
