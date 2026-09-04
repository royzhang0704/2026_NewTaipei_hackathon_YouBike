import { useAppStore } from '@/stores/useAppStore'
import { useAlerts } from '@/api/queries'
import { cn } from '@/lib/utils'

export function KpiStrip() {
  const townCode = useAppStore((s) => s.townCode)
  const { data } = useAlerts({ limit: 1, town_code: townCode || null })
  const s = data?.summary

  const figs = [
    {
      k: '監測站點',
      v: s?.stations ?? '—',
      u: '站',
      d: `含預測 ${(s?.stations ?? 0) - (s?.no_forecast ?? 0)} 站`,
      tone: '' as const,
    },
    { k: '高風險', v: s?.high ?? '—', u: '站', d: '需立即處理', tone: s?.high ? 'hot' : '' },
    { k: '中風險', v: s?.mid ?? '—', u: '站', d: '1 小時內越線', tone: s?.mid ? 'hot' : '' },
    {
      k: '需補車',
      v: s?.refill.stations ?? '—',
      u: '站',
      d: `共 ${s?.refill.bikes ?? 0} 台`,
      tone: s?.refill.stations ? 'hot' : '',
    },
    {
      k: '需取車',
      v: s?.remove.stations ?? '—',
      u: '站',
      d: `共 ${s?.remove.bikes ?? 0} 台`,
      tone: s?.remove.stations ? 'cold' : '',
    },
    { k: '暫不派車', v: s?.hold ?? '—', u: '站', d: '會自行退燒', tone: '' },
  ]

  return (
    <div className="grid grid-cols-2 border border-edge bg-panel sm:grid-cols-3 lg:grid-cols-6">
      {figs.map((f, i) => (
        <div
          key={f.k}
          className={cn('relative border-hair px-4 py-3', (i + 1) % 6 !== 0 && 'border-r')}
        >
          {f.tone && (
            <span
              className={cn(
                'absolute inset-y-0 left-0 w-[2px]',
                f.tone === 'hot' ? 'bg-hot' : 'bg-cold',
              )}
            />
          )}
          <span className="mb-[9px] block text-[0.6rem] tracking-[0.18em] text-ink3">{f.k}</span>
          <div
            className={cn(
              'font-serif text-[2rem] leading-none tracking-[-0.035em] tabular-nums',
              f.tone === 'hot' ? 'text-hot' : f.tone === 'cold' ? 'text-cold' : 'text-ink',
            )}
          >
            {f.v}
            <span className="ml-1 font-sans text-[0.78rem] text-ink2">{f.u}</span>
          </div>
          <div className="mt-[9px] text-[0.71rem] text-ink3">{f.d}</div>
        </div>
      ))}
    </div>
  )
}
