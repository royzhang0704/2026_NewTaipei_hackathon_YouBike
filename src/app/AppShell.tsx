import { Suspense, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useHealth } from '@/api/queries'
import { getApiBase, setApiBase } from '@/api/client'
import { fmtClock, mdhm } from '@/lib/format'
import { useServerClock } from '@/hooks/useServerClock'
import { useSlotSync } from '@/hooks/useSlotSync'
import { cn } from '@/lib/utils'

function navClass({ isActive }: { isActive: boolean }) {
  return cn(
    'flex items-center border-l border-hair px-4 text-[0.72rem] tracking-[0.14em] no-underline',
    isActive ? 'text-ink' : 'text-ink3',
  )
}

export function AppShell() {
  const { data: health, isError, refetch: refetchHealth, dataUpdatedAt } = useHealth()
  const [apiInput, setApiInput] = useState(getApiBase())

  const { time, speed } = useServerClock(health?.now, isError, refetchHealth, dataUpdatedAt)
  useSlotSync(time)

  // 顯示的時間離真實現在差 >5 分鐘 = 回放的歷史時間軸（不是「現在」）
  const replaying = !!time && Math.abs(time.getTime() - Date.now()) > 5 * 60_000
  const label = isError
    ? '離線'
    : replaying && speed >= 1.3
      ? `資料時間　×${Math.round(speed)}`
      : '資料時間'
  const clock = isError ? '離線' : time ? fmtClock(time) : '—'
  const dataAge = health?.current_slot
    ? `資料到 ${mdhm(health.current_slot)}　·　預測到 ${health.forecast_end ? mdhm(health.forecast_end) : '—'}`
    : ''

  function applyApi() {
    setApiBase(apiInput)
    location.reload()
  }

  return (
    <>
      <header className="sticky top-0 z-30 flex items-stretch border-b border-edge bg-bg">
        <NavLink to="/" className="flex items-center gap-3 border-r border-hair px-[22px] py-[14px] no-underline">
          <span className="font-serif text-[1.4rem] leading-none tracking-[-0.03em] text-ink">
            You<i className="text-hot not-italic">Bike</i>
          </span>
          <span className="border-l border-hair pl-3 text-[0.62rem] leading-[1.5] tracking-[0.2em] text-ink3">
            調度中心
            <br />
            9h 實況＋3h 預測
          </span>
        </NavLink>

        <div className="flex items-center gap-[9px] px-5 py-[14px] text-[0.7rem] tracking-[0.16em] text-ink2">
          <span
            className={cn('h-[6px] w-[6px]', replaying ? 'bg-cold' : 'bg-hot')}
            style={{ animation: 'beat 2.1s infinite' }}
          />
          <span>{label}</span>
          <span className="ml-1 border-l border-hair pl-3 font-serif text-[1.05rem] tracking-[-0.02em] tabular-nums text-ink">
            {clock}
          </span>
        </div>

        <span className="hidden flex-1 md:block" />

        <nav className="hidden items-stretch md:flex">
          <NavLink to="/" end className={navClass}>
            總覽
          </NavLink>
          <NavLink to="/station" className={navClass}>
            單站檢視
          </NavLink>
        </nav>

        <div className="hidden items-center gap-2 border-l border-hair px-4 py-[10px] lg:flex">
          <span className="kicker">API</span>
          <input
            value={apiInput}
            spellCheck={false}
            placeholder="（相對路徑）"
            onChange={(e) => setApiInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && applyApi()}
            onBlur={applyApi}
            className="w-[180px] rounded-xs border border-hair bg-raise px-[9px] py-[5px] text-[0.72rem] tracking-[0.03em] text-ink2 outline-none focus:border-edge"
          />
        </div>

        {dataAge && (
          <div className="hidden items-center border-l border-hair px-4 text-[0.64rem] tracking-[0.1em] text-ink3 xl:flex">
            {dataAge}
          </div>
        )}
      </header>

      <Suspense fallback={<div className="p-8 text-sm text-ink3">載入中…</div>}>
        <Outlet />
      </Suspense>

      <style>{`@keyframes beat { 50% { opacity: 0.25; } }`}</style>
    </>
  )
}
