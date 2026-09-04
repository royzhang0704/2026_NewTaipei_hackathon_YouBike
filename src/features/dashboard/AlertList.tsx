import { useAppStore } from '@/stores/useAppStore'
import { useAlerts } from '@/api/queries'
import { cn } from '@/lib/utils'

const LV: Record<string, string> = { high: '高', mid: '中', low: '低', none: '' }

export function AlertList() {
  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectStation = useAppStore((s) => s.selectStation)

  const { data, isPending, isError } = useAlerts({ limit: 40, town_code: townCode || null })
  const items = data?.items ?? []
  const total = data?.total ?? 0

  if (isError) return <div className="p-4 text-[0.8rem] text-ink3">警示載入失敗，請確認後端。</div>
  if (isPending) return <div className="p-4 text-[0.8rem] text-ink3">載入中…</div>
  if (!items.length)
    return (
      <div className="p-5 text-[0.8rem] leading-[1.7] text-ink3">
        目前範圍內所有站點供需皆落在健康區間。
      </div>
    )

  return (
    <>
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-hair bg-panel px-4 py-[6px] text-[0.64rem] tracking-[0.12em] text-ink3">
        <span>共 {total} 筆待處理</span>
        {total > items.length && <span>顯示前 {items.length}，捲動查看</span>}
      </div>
      <ul className="m-0 list-none p-0">
        {items.map((it, i) => (
          <li
            key={it.station_uid}
            onClick={() => selectStation(it.station_uid)}
            className={cn(
              'relative grid cursor-pointer grid-cols-[2.3em_1fr_auto] items-baseline gap-x-3 border-b border-hair px-4 py-[11px] hover:bg-white/[0.03]',
              selectedUid === it.station_uid && 'bg-white/[0.055]',
            )}
          >
            {selectedUid === it.station_uid && (
              <span className="absolute left-0 top-0 h-full w-[2px] bg-ink" />
            )}
            <span className="num text-[1rem] text-ink3">{String(i + 1).padStart(2, '0')}</span>
            <span>
              <span className="font-serif text-[1.05rem] leading-[1.3] tracking-[-0.015em]">
                {it.name}
                <i className="ml-2 font-sans text-[0.63rem] not-italic tracking-[0.16em] text-ink3">
                  {it.town}
                </i>
              </span>
              <span className="mt-[2px] block text-[0.73rem] text-ink3">
                {it.side === 'shortage' ? '無車可借' : '無位可還'}・{LV[it.level]}風險　·　可借{' '}
                {it.now.avail ?? '—'} ／ {it.capacity ?? '?'} 席
              </span>
            </span>
            <span className="text-right text-[0.66rem] leading-[1.4] tracking-[0.06em] text-ink3">
              <b
                className={cn(
                  'num block text-[1rem] tracking-[-0.02em]',
                  it.side === 'shortage' ? 'text-hot' : 'text-cold',
                )}
              >
                {it.dispatch
                  ? `${it.dispatch.action === 'refill' ? '補' : it.dispatch.action === 'remove' ? '取' : ''}${it.dispatch.bikes} 台`
                  : '—'}
              </b>
              {it.streak && <span>已 {it.streak.hours} 小時</span>}
            </span>
          </li>
        ))}
      </ul>
    </>
  )
}
