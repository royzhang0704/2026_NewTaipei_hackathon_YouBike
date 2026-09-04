import { useMemo, useState } from 'react'
import type { StationDay } from '@/api/types'
import { dispatchBar, riskCard } from '@/lib/risk'
import { hhmm } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ForecastChart } from './ForecastChart'

const toneClass: Record<string, string> = {
  none: 'border-hair bg-raise text-ink3',
  low: 'border-hot/25 bg-panel text-hot/80',
  mid: 'border-hot/45 bg-hot-wash text-hot',
  high: 'border-hot bg-hot text-white',
}

export function StationDetail({ day }: { day: StationDay }) {
  const [showTruth, setShowTruth] = useState(false)

  const { station: st, now, risk: k } = day
  const cap = st.capacity
  const avail = now.avail
  const free = now.free
  const t = k?.threshold ?? null

  const pct = avail == null || !cap ? 0 : Math.min(100, Math.round((100 * avail) / cap))
  const gaugeColor =
    avail == null || !cap || t == null
      ? 'rgba(255,255,255,.25)'
      : avail <= t
        ? 'var(--color-hot)'
        : cap - avail <= t
          ? 'var(--color-cold)'
          : 'rgba(255,255,255,.28)'

  const card = useMemo(() => (k ? riskCard(k) : null), [k])
  const bar = useMemo(() => (k ? dispatchBar(k, day.forecast) : null), [k, day.forecast])

  const extremes = day.forecast.length
    ? {
        lo: Math.min(...day.forecast.map((p) => p.q19)),
        hi: Math.max(...day.forecast.map((p) => p.q90)),
      }
    : null
  const hasTruth = (day.truth?.length ?? 0) > 0 && day.forecast.length > 0

  return (
    <div className="px-4 py-[18px]">
      <h2 className="mb-[5px] font-serif text-[1.6rem] leading-[1.12] tracking-[-0.025em]">{st.name}</h2>
      <div className="mb-[15px] text-[0.63rem] tracking-[0.16em] text-ink3">
        {st.town}　·　{cap != null ? `${cap} 席車柱` : '車柱數不明'}　·　基準 {hhmm(day.origin)}
      </div>

      <div className="mb-[14px] flex flex-wrap items-end gap-7">
        <div className="flex flex-col gap-[2px]">
          <span className="kicker">可借車輛</span>
          <span>
            <b className="num text-[2.1rem] font-medium leading-none text-hot">{avail ?? '—'}</b>
            <span className="ml-[5px] text-[0.8rem] text-ink2">台</span>
          </span>
        </div>
        <div className="flex flex-col gap-[2px]">
          <span className="kicker">可還空位</span>
          <span>
            <b className="num text-[2.1rem] font-medium leading-none text-cold">{free ?? '—'}</b>
            <span className="ml-[5px] text-[0.8rem] text-ink2">席</span>
          </span>
        </div>
        <div className="flex flex-col gap-[2px] text-[0.64rem] tracking-[0.1em] text-ink3">
          <span>
            {avail == null
              ? '此格無觀測'
              : `基準 ${hhmm(now.at)}${now.carried ? '（延用前值）' : ''}`}
          </span>
          {t != null && <span>風險門檻 {t} 台／席</span>}
        </div>
      </div>

      <div className="h-2 overflow-hidden rounded-xs border border-hair bg-white/5">
        <i className="block h-full" style={{ width: `${pct}%`, background: gaugeColor }} />
      </div>
      <div className="mt-1 flex justify-between text-[0.64rem] tracking-[0.1em] text-ink3">
        <span>0 台</span>
        <span>可借比例 {pct}％</span>
        <span>{cap ?? '?'} 台</span>
      </div>

      {card && (
        <div className={cn('mt-4 rounded-xs border px-3 py-[9px]', toneClass[card.tone])}>
          <div className="flex items-baseline gap-2">
            <span className={cn('kicker', card.tone === 'high' && 'text-white/80')}>{card.title}</span>
            <span className="font-serif text-[1.15rem] tracking-[0.04em]">{card.level}</span>
          </div>
          <span
            className={cn(
              'mt-[3px] block text-[0.72rem] leading-[1.5]',
              card.tone === 'high' ? 'text-white/80' : 'text-ink2',
            )}
          >
            {card.desc}
          </span>
        </div>
      )}

      {bar && (
        <div
          className={cn(
            'mt-[10px] flex flex-wrap items-baseline gap-[14px] rounded-xs border border-l-[3px] border-edge px-[14px] py-[10px]',
            bar.kind === 'refill' && 'border-l-hot',
            bar.kind === 'remove' && 'border-l-cold',
            bar.kind === 'hold' && 'border-l-ink3',
            bar.kind === 'refill' && bar.urgent && 'bg-hot-wash',
            bar.kind === 'remove' && bar.urgent && 'bg-cold-wash',
          )}
        >
          <span className="kicker">建議調度</span>
          <b
            className={cn(
              'num text-[1.25rem] font-medium',
              bar.kind === 'refill' && 'text-hot',
              bar.kind === 'remove' && 'text-cold',
              bar.kind === 'hold' && 'text-ink2',
            )}
          >
            {bar.label}
          </b>
          <span className="text-[0.74rem] text-ink2">{bar.desc}</span>
        </div>
      )}

      <p className="mb-1 mt-[18px] text-[0.66rem] tracking-[0.14em] text-ink3">
        可借台數　—　過去 9 小時實況＋未來 3 小時預測
      </p>
      <ForecastChart day={day} showTruth={showTruth} />
      <div className="mt-[5px] flex justify-between text-[0.64rem] tracking-[0.1em] text-ink3">
        <span>{hhmm(day.actual[0]?.at)}</span>
        <b className="text-ink2">
          實況（白）／預測 q50（橘虛線）＋ q19–q90 帶{showTruth ? '／實際（金色）' : ''}
        </b>
        <span>
          {day.forecast.length ? hhmm(day.forecast[day.forecast.length - 1].at) : hhmm(day.origin)}
        </span>
      </div>

      {hasTruth && (
        <div className="mt-[14px]">
          <button
            onClick={() => setShowTruth((v) => !v)}
            className={cn(
              'rounded-xs border border-edge bg-transparent px-[22px] py-2 text-[0.86rem] font-medium tracking-[0.06em] text-ink2 hover:border-ink hover:text-ink',
              showTruth && 'border-gold text-gold',
            )}
          >
            {showTruth ? '隱藏實際資料' : '顯示實際資料（對答案）'}
          </button>
        </div>
      )}

      {extremes ? (
        <div className="mt-[14px] border-t border-hair pt-[10px] text-[0.68rem] tracking-[0.06em] text-ink3">
          路徑極值　最低 <b className="text-ink2">{extremes.lo}</b> 台（q19）／最高{' '}
          <b className="text-ink2">{extremes.hi}</b> 台（q90）—— 風險看路徑最低／最高點，不是終點值
        </div>
      ) : (
        <div className="mt-3 border-l-2 border-edge bg-raise px-[14px] py-2 text-[0.78rem] text-ink2">
          {day.forecast_missing || '此站尚無批次預測'}
        </div>
      )}

      <div className="mt-[14px] border-t border-hair pt-[10px] text-[0.68rem] tracking-[0.06em] text-ink3">
        {day.source}
        {day.model_job ? `　·　模型 ${day.model_job}` : ''}
      </div>
    </div>
  )
}
