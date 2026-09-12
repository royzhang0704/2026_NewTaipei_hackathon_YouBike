import { lazy, Suspense, useEffect, useMemo, useRef } from 'react'
import { AlertTriangle, ExternalLink, MapPin, Truck } from 'lucide-react'
import type { StationDay } from '@/api/types'
import { useDispatchOrders, useHealth, useStations } from '@/api/queries'
import { useAppStore } from '@/stores/useAppStore'
import { useAssistantStore } from '@/stores/useAssistantStore'
import { actionBlock } from '@/lib/risk'
import { hhmm, mdhm } from '@/lib/format'
import { cn } from '@/lib/utils'

// echarts 只有這裡用 → 拆成獨立 chunk，選站展開單站檢視時才下載（主包少 ~150KB gzip）
const ForecastChart = lazy(() =>
  import('./ForecastChart').then((m) => ({ default: m.ForecastChart })),
)

/* ── 調度入口（行動卡底部）────────────────────────────────────
   ★ 這是調度功能的**唯一入口**（計劃-調度確認.md §16）：助理可以打字問，
     但「發起一次調度」只有從這裡按。入口收斂在單站頁的行動卡上，
     因為要派幾台、從哪派，都以「這一站」為錨點。 */
function DispatchEntry({
  uid,
  name,
  actionKind,
  need,
}: {
  uid: string
  name: string
  actionKind: 'refill' | 'remove' | 'hold' | 'none'
  need: number | null
}) {
  const askDispatch = useAssistantStore((s) => s.askDispatch)
  const highlightOrder = useAppStore((s) => s.highlightOrder)
  const { data: health } = useHealth()
  const { data: orders } = useDispatchOrders()

  // hold（預計自行消退）與 none（無風險）沒有調度可發起 —— 後端也會擋，
  // 但按鈕根本不該出現：讓人按下去才被拒絕是最糟的回饋。
  if (actionKind !== 'refill' && actionKind !== 'remove') return null

  /* ★ 只有「虛擬時間不會跑掉」時才開放調度。擋在**按鈕層級**，不是確認層級
       —— 不能讓使用者問完 Bedrock、勾完選才被擋。
       （60x 回放下一格 = 30 真實秒，讀完文案早已跨輪，寫入必定 DISPATCH_STALE。）

     ★ 0 與 1 都放行：計劃原訂「只有 1x」，但 demo_tail_speed = 0 的語意是
       **停表**（走完 demo_until 之後不再前進，見 sys_config_repo.effective_now），
       虛擬時間根本不動，跨輪機率是零 —— 比 1x 還安全。照字面只認 1 的話，
       停表狀態下整個調度入口會全部點不到。真正要擋的是 > 1 的快轉。 */
  const speed = health?.demo_tail_speed
  const fast = speed != null && speed > 1
  const mine = (orders?.items ?? []).filter((o) => o.anchor_uid === uid)
  const sent = mine.reduce((s, o) => s + o.bikes, 0)
  const left = need == null ? null : Math.max(0, need - sent)
  const froms = mine
    .map((o) => (actionKind === 'refill' ? o.from.name : o.to.name))
    .filter(Boolean)

  return (
    <div className="mt-[10px] border-t border-hair pt-[9px]">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={fast}
          title={fast ? '回放加速中，請切回 1x 再進行調度' : undefined}
          onClick={() => askDispatch({ anchorUid: uid, anchorName: name, action: actionKind })}
          className={cn(
            'flex items-center gap-[5px] rounded-xs border px-2 py-[3px] text-[0.72rem]',
            fast
              ? 'cursor-not-allowed border-edge text-ink3 opacity-55'
              : 'border-info/55 text-info hover:bg-info-wash',
          )}
        >
          <Truck className="size-[12px]" aria-hidden />
          {actionKind === 'refill' ? '找車來補' : '找站調出'}
        </button>
        {fast && (
          <span className="text-[0.66rem] text-ink3">回放加速中，切回 1x 才能調度</span>
        )}
      </div>

      {sent > 0 && (
        <p className="m-0 mt-[6px] flex flex-wrap items-baseline gap-x-2 text-[0.7rem] leading-[1.5] text-ink3">
          <span>
            已調度 <b className="num font-medium text-info">{sent}</b> 台
            {froms.length > 0 && <span> · 來自 {froms.join('、')}</span>}
          </span>
          {left != null && left > 0 && (
            <span>
              還缺 <b className="num font-medium text-ink2">{left}</b> 台
            </span>
          )}
          <button
            type="button"
            onClick={() => highlightOrder(mine[0].id)}
            className="text-info underline underline-offset-2"
          >
            查看
          </button>
        </p>
      )}
    </div>
  )
}

/** 站名 + 行政區／車柱數／基準時刻 + 地址列。正常與「查無資料」兩種畫面共用。 */
function StationHead({ st, origin }: { st: StationDay['station']; origin: string | null }) {
  // st 只有精簡欄位；地址 / 座標從已快取的 useStations() 用 uid 撈
  const stations = useStations().data
  const full = useMemo(() => stations?.find((s) => s.uid === st.uid), [stations, st.uid])
  const hasCoord = full != null && Number.isFinite(full.lat) && Number.isFinite(full.lon)
  const cap = st.capacity

  // 選站後把焦點帶到標題（元件按 uid 重掛 → 每次換站都移）。
  // 報讀者會唸出「站名, 標題」；滑鼠使用者看不到框（tabIndex -1 + outline-none）。
  const headingRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    // preventScroll：捲動交給 DashboardPage 既有的 scrollIntoView，這裡只移焦點（給報讀者）
    headingRef.current?.focus({ preventScroll: true })
  }, [])

  return (
    <>
      <h2
        ref={headingRef}
        tabIndex={-1}
        className="mb-[5px] font-serif text-[1.6rem] leading-[1.12] tracking-[-0.025em] outline-none"
      >
        {st.name}
      </h2>
      <div className="mb-[12px] text-[0.7rem] tracking-[0.1em] text-ink3">
        {st.town}　·　{cap != null ? `${cap} 席車柱` : '車柱數不明'}
        {origin && `　·　基準時刻 ${mdhm(origin)}`}
      </div>

      {(full?.addr || hasCoord) && (
        // 地址 + 在地圖開啟：調度員派車要知道確切位置。座標從 useStations 撈、外開 Google 地圖
        <div className="-mt-[7px] mb-[12px] flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.72rem] leading-[1.5] text-ink3">
          {full?.addr && (
            <span className="inline-flex items-baseline gap-[5px]">
              <MapPin className="size-[12px] flex-none translate-y-[2px]" aria-hidden />
              {full.addr}
            </span>
          )}
          {hasCoord && (
            <a
              href={`https://www.google.com/maps/search/?api=1&query=${full!.lat},${full!.lon}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-[3px] text-ink2 underline decoration-hair underline-offset-2 hover:text-ink hover:decoration-edge"
            >
              在地圖開啟
              <ExternalLink className="size-[11px]" aria-hidden />
            </a>
          )}
        </div>
      )}
    </>
  )
}

/** 資料來源 / 模型 provenance（label–value 兩欄） */
function Provenance({ day }: { day: StationDay }) {
  return (
    <dl className="mt-[14px] grid grid-cols-[3.2em_1fr] gap-x-3 gap-y-[3px] border-t border-hair pt-[10px] text-[0.68rem] leading-[1.55] text-ink3">
      <dt className="tracking-[0.1em]">資料</dt>
      {/* break-keep：中文連續字不斷（不會「本／查詢」拆字），只在空格 / 「；」等標點斷 */}
      <dd className="m-0 break-keep tracking-[0.02em]">{day.source}</dd>
      {day.model_job && (
        <>
          <dt className="tracking-[0.1em]">模型</dt>
          <dd className="m-0 break-all font-mono tracking-normal">{day.model_job}</dd>
        </>
      )}
    </dl>
  )
}

export function StationDetail({ day }: { day: StationDay }) {
  const { station: st, now, risk: k } = day
  const cap = st.capacity
  const avail = now?.avail ?? null
  const free = now?.free ?? null
  const t = k?.threshold ?? null

  const pct = avail == null || !cap ? 0 : Math.min(100, Math.round((100 * avail) / cap))
  const gaugeColor =
    avail == null || !cap || t == null
      ? 'var(--color-calm)'
      : avail <= t
        ? 'var(--color-hot)'
        : cap - avail <= t
          ? 'var(--color-cold)'
          : 'var(--color-calm)'

  const ab = useMemo(() => (k ? actionBlock(k) : null), [k])

  const fcLast = day.forecast.length ? day.forecast[day.forecast.length - 1] : null

  // 整站在這個時刻查無資料（停站期間）：後端回 200 + now / origin / risk = null。
  // 站況（站名 / 行政區 / 車柱數 / 地址）照給；數字、量表、預測圖一律不渲染。
  if (!now) {
    return (
      <div className="anim-slide px-4 py-[18px]">
        <StationHead st={st} origin={day.origin} />
        <p className="mt-[6px] border-l-2 border-edge bg-raise px-[14px] py-2 text-[0.82rem] leading-[1.5] text-ink2">
          {day.actual_missing || '查無該站歷史'}
        </p>
        <Provenance day={day} />
      </div>
    )
  }

  return (
    <div className="anim-slide px-4 py-[18px]">
      <StationHead st={st} origin={day.origin} />

      {ab && (
        // 「行動」卡＝單站檢視的 headline：嚴重度 + 建議調度在最上，why 一句，支撐數字弱化一行。
        // 移到姓名下方（原本在量表之後）→ 調度員先看到「要不要派、派幾台」，再看下面的現況數字。
        // 左框分級、不用整塊深色實心（深色模式白字對比不足）；深色文字配淡 wash，兩主題都過 AA。
        <div
          className={cn(
            'mb-[16px] rounded-xs border border-l-[3px] border-edge px-[14px] py-3',
            ab.tone === 'high' && 'border-l-hot bg-hot-wash',
            ab.tone === 'mid' && 'border-l-hot bg-hot-wash',
            ab.tone === 'low' && 'border-l-hot',
            ab.tone === 'none' && 'border-l-calm bg-raise',
          )}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h3 className="m-0 flex items-baseline gap-[6px] text-[0.8rem] font-normal">
              {/* 警示三角只給 mid / high，low（1–3 小時內才越線）不掛，signal 才有層次 */}
              {(ab.tone === 'high' || ab.tone === 'mid') && (
                <AlertTriangle className="size-[14px] flex-none self-center text-hot" aria-hidden />
              )}
              <span
                className={cn(
                  'font-serif text-[1.15rem] tracking-[0.02em]',
                  ab.tone === 'high' && 'font-medium text-hot',
                  ab.tone === 'mid' && 'text-hot',
                  ab.tone === 'low' && 'text-ink',
                  ab.tone === 'none' && 'text-ink3',
                )}
              >
                {ab.levelWord}
              </span>
              {ab.sideWord && (
                <span className="text-[0.72rem] tracking-[0.08em] text-ink3">{ab.sideWord}</span>
              )}
            </h3>
            {ab.action && (
              <b
                className={cn(
                  'num flex items-baseline gap-2 text-[1.4rem] font-medium leading-none',
                  ab.actionKind === 'refill' && 'text-hot',
                  ab.actionKind === 'remove' && 'text-cold',
                  ab.actionKind === 'hold' && 'text-ink2',
                )}
              >
                {ab.action}
                {ab.urgent && (
                  <span className="rounded-xs border border-hot px-[5px] py-px font-sans text-[0.64rem] font-semibold leading-none tracking-[0.08em] text-hot">
                    立即
                  </span>
                )}
              </b>
            )}
          </div>

          <p className="m-0 mt-[6px] text-[0.75rem] leading-[1.5] text-ink2">{ab.why}</p>
          {ab.support && (
            <p className="m-0 mt-[3px] text-[0.68rem] leading-[1.4] tracking-[0.02em] text-ink3">
              {ab.support}
            </p>
          )}

          <DispatchEntry
            uid={st.uid}
            name={st.name}
            actionKind={ab.actionKind}
            need={day.risk?.dispatch?.bikes ?? null}
          />
        </div>
      )}

      <div className="mb-[14px] flex flex-wrap items-end gap-7">
        <div className="flex flex-col gap-[2px]">
          <span className="kicker">可借車輛</span>
          <span>
            <b className="num text-[1.9rem] font-medium leading-none text-hot">{avail ?? '—'}</b>
            <span className="ml-[5px] text-[0.8rem] text-ink2">台</span>
          </span>
        </div>
        <div className="flex flex-col gap-[2px]">
          <span className="kicker">可還空位</span>
          <span>
            <b className="num text-[1.9rem] font-medium leading-none text-cold">{free ?? '—'}</b>
            <span className="ml-[5px] text-[0.8rem] text-ink2">席</span>
          </span>
        </div>
        {(avail == null || now.carried) && (
          <div className="flex flex-col gap-[2px] text-[0.7rem] tracking-[0.08em] text-ink3">
            {avail == null ? <span>此格無觀測</span> : <span>觀測延用 {hhmm(now.at)}</span>}
          </div>
        )}
      </div>

      {/* 軌道底色用 ink/6%（深色→淡白、淺色→淡黑），不寫死 bg-white/5 */}
      <div className="h-2 overflow-hidden rounded-xs border border-hair bg-ink/[0.06]">
        <i className="block h-full" style={{ width: `${pct}%`, background: gaugeColor }} />
      </div>
      <div className="mt-1 flex justify-between text-[0.7rem] tracking-[0.08em] text-ink3">
        <span>0 台</span>
        <span>可借比例 {pct}％</span>
        <span>{cap ?? '?'} 台</span>
      </div>

      {/* ForecastChart 仍支援 showTruth（疊上事後實際值「對答案」）；demo 版刻意不接，
          避免評審把「回放日實際值」誤解成「未來實際資料」。正式版要用再把開關接回。 */}
      <figure className="m-0 mt-[18px]">
        <Suspense
          fallback={
            <div className="h-[240px] w-full animate-pulse rounded-xs bg-ink/[0.05]" aria-hidden />
          }
        >
          <ForecastChart day={day} />
        </Suspense>

        {/* figcaption：結論 → 圖例 → 定義，由重要到參考；不用分隔線，綁成一組圖說 */}
        <figcaption className="mt-[8px]">
          {fcLast ? (
            <p className="m-0 text-[0.7rem] leading-[1.6] tracking-[0.04em] text-ink2">
              預測末端 約 <b className="text-ink">{fcLast.q50}</b> 台（可能{' '}
              <b className="text-ink">{fcLast.q19}</b>–<b className="text-ink">{fcLast.q90}</b> 台）
            </p>
          ) : (
            <p className="m-0 border-l-2 border-edge bg-raise px-[14px] py-2 text-[0.78rem] text-ink2">
              {day.forecast_missing || '此站尚無批次預測'}
            </p>
          )}
          {/* 圖例色塊用 CSS 畫（對齊圖表實際線型），不用 ┈ / ▨ 這種跨字型會 tofu 的 glyph */}
          <p className="m-0 mt-[4px] flex flex-wrap gap-x-4 gap-y-1 text-[0.7rem] tracking-[0.04em] text-ink3">
            <span className="inline-flex items-center gap-[5px]">
              <span aria-hidden className="inline-block h-[2px] w-[14px] flex-none bg-ink2" />
              實況
            </span>
            <span className="inline-flex items-center gap-[5px]">
              <span
                aria-hidden
                className="inline-block h-0 w-[14px] flex-none border-t border-dashed border-hot"
              />
              預測
            </span>
            <span className="inline-flex items-center gap-[5px]">
              <span
                aria-hidden
                className="inline-block h-[9px] w-[14px] flex-none rounded-[1px] bg-hot/15"
              />
              可能範圍
            </span>
          </p>
          {t != null && (
            <p className="m-0 mt-[2px] text-[0.7rem] leading-[1.6] tracking-[0.04em] text-ink3">
              低／高水位 ＝ 空站／滿站風險線（容量 15%／85%）
            </p>
          )}
        </figcaption>
      </figure>

      <Provenance day={day} />
    </div>
  )
}
