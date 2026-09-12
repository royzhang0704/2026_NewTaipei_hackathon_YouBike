import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { useAppStore, type AlertSide } from '@/stores/useAppStore'
import { useAlerts, useDispatchOrders } from '@/api/queries'
import type { AlertItem, RiskLevel } from '@/api/types'
import { segChip } from '@/components/ui/segChip'
import { cn } from '@/lib/utils'

/** 滿／空的「徹底程度」＝ 可借數 ÷ 容量。用比例而不是台數，是為了不跟下一鍵的容量
    重複計入規模 —— 10 席全滿（100%）比 26 席差 1 台（96%）更該先清。
    資料不全（capacity 為 0／null、或該站本輪沒有觀測值）回 null，排序時墊到最後。 */
const fillRatio = (i: AlertItem) =>
  i.capacity && i.now.avail != null ? i.now.avail / i.capacity : null

/** 清單組內的急迫度：① 已持續越久 → ② 越滿／越空 → ③ 站容量越大。
    ★ ① hours 是後端用 streak_since 換算的，就是卡片上「已 N 小時」（比 streak_n 耐漏批）。
    ★ ② 方向依 side 反著看：滿站比例越高越該取，空站比例越低越該補。
         這支只會被套在「全 full」或「全 shortage」的子陣列上，所以看 a.side 就夠。
    ★ ③ 同樣卡了 1 小時、滿得一樣徹底，就大站先 —— 影響的人次多。
    ★ 中低風險的 hours 恆為 null（9/12 起 streak 只算高風險）→ ① 對它們全是 0，那兩組
      實際上是從②開始排；要讓中低組維持後端原順序，把這支包一層
      `a.level === 'high' ? … : 0` 即可。
    ★ 三鍵都打平就回 0，靠 Array.prototype.sort 的穩定性沿用後端的 bikes → onset。 */
const byUrgency = (a: AlertItem, b: AlertItem) => {
  const h = (b.streak?.hours ?? 0) - (a.streak?.hours ?? 0)
  if (h) return h
  const ra = fillRatio(a)
  const rb = fillRatio(b)
  if (ra !== rb) {
    if (ra == null) return 1                       // 資料不全的墊底，不要插在前面誤導
    if (rb == null) return -1
    return a.side === 'full' ? rb - ra : ra - rb
  }
  return (b.capacity ?? 0) - (a.capacity ?? 0)
}

const SIDES: { key: AlertSide; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'shortage', label: '空站' },
  { key: 'full', label: '滿站' },
]

/** 依嚴重度分組顯示（見下方 grouped）。只列會出現在警示清單裡的三級；
    標題沿用 KpiStrip 已經在用的白話句，全站同一套詞。 */
const LEVEL_GROUPS: { level: Exclude<RiskLevel, 'none'>; title: string }[] = [
  { level: 'high', title: '高風險・已空站或滿站' },
  { level: 'mid', title: '中風險・1 小時內空站或滿站' },
  { level: 'low', title: '低風險' },
]

/* 「已調派」的判定 —— 跟 StationDetail:56 與後端 dispatch_service:103 同一條算式：
   need = 警示的建議台數（risk_snapshot 原始值，**沒有**扣掉已派的量），
   sent = 該站目前所有 active 調度單的台數合計（撤銷過的不算）。

   ★ 只認「全數覆蓋」：sent >= need 才算處理完。派了一半的站仍缺車，
     把它從清單上藏掉等於讓它被遺忘（9/12 使用者定案）。
   ★ need <= 0 不算已調派：那是「本輪沒有建議台數」，跟有人去派過是兩回事，
     照 sent(0) >= need(0) 硬算會把它一起藏掉。 */
function coveredBy(it: AlertItem, sentBy: Map<string, number>) {
  const need = it.dispatch?.bikes ?? 0
  return need > 0 && (sentBy.get(it.station_uid) ?? 0) >= need
}

export function AlertList() {
  const townCode = useAppStore((s) => s.townCode)
  const selectedUid = useAppStore((s) => s.selectedUid)
  const selectStation = useAppStore((s) => s.selectStation)

  // 篩選狀態在 store：上方 KPI 也能寫（點 KPI = 套用該篩選）
  const side = useAppStore((s) => s.alertSide)
  const level = useAppStore((s) => s.alertLevel)
  const hideCovered = useAppStore((s) => s.alertHideCovered)
  const setAlertFilter = useAppStore((s) => s.setAlertFilter)

  // 換地區 / 換篩選 → 清單內容整批換掉，把外層捲軸帶回頂端（否則會停在舊位置看到空白）
  const topRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    topRef.current?.scrollIntoView({ block: 'nearest' })
  }, [townCode, side, level, hideCovered])

  // sticky 表頭（篩選列＋狀態列）會浮在捲動區上緣 → 量它的高度當作 row 的 scroll-margin-top，
  // 否則捲到上方的目標列會被表頭遮掉一半。字級 / chip 換行會改變高度，用 ResizeObserver 追。
  const headRef = useRef<HTMLDivElement>(null)
  const [headH, setHeadH] = useState(56)
  useEffect(() => {
    const el = headRef.current
    if (!el) return
    const sync = () => setHeadH(el.offsetHeight)
    sync()
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 從地圖 / 抽屜選站 → 把清單裡對應那列帶進可視範圍（block:nearest：已看得到就不動；
  // 不在目前篩選結果內 → 找不到 ref、安靜略過）。只在 selectedUid 真的變化時觸發，不搬焦點。
  const rowRefs = useRef(new Map<string, HTMLButtonElement>())
  const prevSel = useRef<string | null>(null)
  useEffect(() => {
    const uid = selectedUid
    if (uid && uid !== prevSel.current) {
      const el = rowRefs.current.get(uid)
      if (el) {
        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
        el.scrollIntoView({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' })
      }
    }
    prevSel.current = uid
  }, [selectedUid])

  // 抓整包（跟 CityMap 的 {limit:1000, town_code} 同 key，共用快取不多打），側別 / 等級全在前端篩。
  // 1000 是後端硬上限（station_controller Query le=1000）；實際警示數遠低於此，unloaded 只是防呆。
  const { data, isPending, isError } = useAlerts({ limit: 1000, town_code: townCode || null })
  const all = useMemo(() => data?.items ?? [], [data])

  /* 調度單：一次全撈的 live query（?status=active），StationDetail / CityMap /
     DispatchCard 共用同一份快取，不會多打一次。跨輪更新由 useSlotSync 在
     遮罩解鎖時 invalidate —— 這裡不需要自己輪詢。 */
  const { data: orders } = useDispatchOrders()
  const sentBy = useMemo(() => {
    const m = new Map<string, number>()
    // 同一站可能有多筆來源單（N 站 → 1 站），要加總不是取代
    for (const o of orders?.items ?? []) m.set(o.anchor_uid, (m.get(o.anchor_uid) ?? 0) + o.bikes)
    return m
  }, [orders])

  // 方向 / 等級先篩；「排除已調派」單獨一層，才算得出被它藏起來幾筆
  const byScope = useMemo(() => {
    let list = side === 'all' ? all : all.filter((i) => i.side === side)
    if (level !== 'all') list = list.filter((i) => i.level === level)
    return list
  }, [all, side, level])

  const coveredCount = useMemo(
    () => byScope.filter((i) => coveredBy(i, sentBy)).length,
    [byScope, sentBy],
  )
  const filtered = useMemo(
    () => (hideCovered ? byScope.filter((i) => !coveredBy(i, sentBy)) : byScope),
    [byScope, hideCovered, sentBy],
  )

  // 依嚴重度分組（高→中→低）；序號在分組後重編，跟畫面上看到的順序一致
  // （不是原陣列位置），組內同一批不夾雜其他嚴重度的站。
  //
  // ★ 9/13：組內不再沿用 API 順序 —— 改以「已持續多久 → 越滿／越空 → 站容量」排
  //   （滿站、空站各自排，方向見 byUrgency）。
  //   後端 ORDER_BY（risk_repo）把 streak 排在可借數之後，於是「可借 0 台、
  //   剛亮燈半小時」會壓過「可借 2 台、已持續 5 小時」，跟調度的急迫感相反。
  //   側別分層保留：滿站整組仍在空站之前（滿站清出來的車正好是附近空站的調度
  //   來源，先處理滿站等於一次動作緩解兩邊）—— 動的只是每一側**組內**的順序。
  //   排序鍵見 byUrgency；三鍵都平才沿用後端的 bikes → onset（靠 sort 的穩定性，
  //   不自己重排）。
  //   ⚠ 中低風險的 hours 恆為 null（9/12 起 streak 只算高風險）→ 那兩組等於從
  //     「越滿／越空」開始排；只想動高風險組的話看 byUrgency 上的註記。
  const grouped = useMemo(() => {
    let no = 0
    return LEVEL_GROUPS.map((g) => {
      const rows = filtered.filter((it) => it.level === g.level)
      // 後端保證同一等級內滿站已全部排在空站之前，所以照側別切開再接回去不會打亂側別分層
      const full = rows.filter((it) => it.side === 'full').sort(byUrgency)
      const shortage = rows.filter((it) => it.side === 'shortage').sort(byUrgency)
      return { ...g, rows: [...full, ...shortage].map((it) => ({ it, no: ++no })) }
    }).filter((g) => g.rows.length > 0)
  }, [filtered])

  // 報讀者狀態訊息：篩選一變、count 一變就唸「（高風險・）空站・共 N 筆待處理」
  const scopeLabel = [
    level === 'high' ? '高風險' : level === 'mid' ? '中風險' : '',
    side === 'shortage' ? '空站' : side === 'full' ? '滿站' : '',
  ]
    .filter(Boolean)
    .join('・')
  // 後端 limit 1000；真的更多才提示（一般全新北也就幾百筆）
  const unloaded = data ? Math.max(0, data.total - all.length) : 0

  if (isError) return <div className="p-4 text-[0.8rem] text-ink3">目前無法載入警示資料，請稍後再試。</div>
  if (isPending) return <div className="p-4 text-[0.8rem] text-ink3">載入中…</div>

  return (
    <>
      <div ref={topRef} aria-hidden />
      <div ref={headRef} className="sticky top-0 z-10 border-b border-hair bg-panel">
        {/* 兩條互斥軸：方向（全部/缺車/滿站）與風險（高/中）。各自包成 role=group、中間一條
            分隔線 → 視覺與報讀者都看得出「這是兩個單選」，不會誤以為四顆可同時選。
            大字級塞不下時整組一起換到第二行（不會只有「中風險」落單）。
            分頁不帶計數：數字全交給下方狀態列與上方 KPI，避免不吃嚴重度的徽章跟畫面對不上。 */}
        <div className="flex flex-wrap items-center gap-[6px] px-3 pt-[7px]">
          <div role="group" aria-label="供需方向" className="flex items-center gap-[6px]">
            {SIDES.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                onClick={() => setAlertFilter({ side: key })}
                aria-pressed={side === key}
                className={cn(segChip(side === key), 'px-2 py-[3px] text-[0.68rem]')}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="mx-0.5 h-3.5 w-px flex-none self-center bg-hair" aria-hidden />
          {/* 風險等級：高 / 中 互斥 toggle（點目前選中的 → 回「全部風險」）。跟上方 KPI 連動同一個 store 值 */}
          <div role="group" aria-label="風險等級" className="flex items-center gap-[6px]">
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
          {/* 排除已調派：ml-auto 推到這排的右端（換行時落在第二排右端，不會擠壓左邊兩組）。
              ★ 不放計數在 chip 上 —— 同上一段的體例，數字一律交給下方狀態列，
                避免畫面上出現兩個定義不同的「N」。 */}
          <button
            type="button"
            onClick={() => setAlertFilter({ hideCovered: !hideCovered })}
            aria-pressed={hideCovered}
            title="隱藏建議台數已被既有調度單全數覆蓋的站。派了一部分的站仍會留著（那站還缺車）。"
            className={cn(segChip(hideCovered), 'ml-auto px-2 py-[3px] text-[0.68rem]')}
          >
            排除已調派
          </button>
        </div>
        <div
          role="status"
          className="flex items-center justify-between px-4 py-[5px] text-[0.68rem] tracking-[0.06em] text-ink3"
        >
          <span>
            {scopeLabel && `${scopeLabel}・`}共 {filtered.length} 筆待處理
            {hideCovered && coveredCount > 0 && `（已隱藏 ${coveredCount} 筆已調派）`}
          </span>
          {unloaded > 0 && <span>資料庫另有 {unloaded} 筆，請用地區縮小</span>}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="p-5 text-[0.8rem] leading-[1.7] text-ink3">
          {all.length === 0
            ? '目前範圍內所有站點供需皆落在健康區間。'
            : hideCovered && coveredCount > 0
              ? `此篩選條件下的 ${coveredCount} 站都已調派，沒有待處理站點。`
              : '此篩選條件下沒有待處理站點。'}
        </div>
      ) : (
        <ul className="m-0 list-none p-0">
          {grouped.map((g) => (
            <Fragment key={g.level}>
              {/* 分組標題：純資訊、非互動列。序號在分組後重編，跟畫面看到的順序一致，
                  不會因為原始陣列裡夾雜別的嚴重度而跳號。 */}
              <li>
                <div className="border-b border-t border-hair bg-raise px-4 py-[6px] text-[0.68rem] tracking-[0.06em] text-ink3">
                  {g.title}
                </div>
              </li>
              {g.rows.map(({ it, no }) => {
                const sel = selectedUid === it.station_uid
                const isShort = it.side === 'shortage'
                // 缺車看「可借」、滿站看「空位」——各自真正吃緊的那個數字，跟右欄方向色一致。
                const capVal = isShort ? it.now.avail : it.now.free
                const cap = it.capacity ?? 0
                // 已調派 / 還缺：算式同 coveredBy（頂端）。need 是未扣除的原始建議台數。
                const sent = sentBy.get(it.station_uid) ?? 0
                const left = Math.max(0, (it.dispatch?.bikes ?? 0) - sent)
                const pct = cap > 0 && capVal != null ? Math.min(100, Math.max(0, (capVal / cap) * 100)) : 0
                // 整列底色：色相＝方向（暖缺車／冷滿站），濃淡＝嚴重度（高風險原色、中風險減半、低風險不上色）
                // ——沿用既有的 hot-wash/cold-wash token，不另開一套風險專屬色相。
                const washClass =
                  g.level === 'high'
                    ? isShort
                      ? 'bg-hot-wash'
                      : 'bg-cold-wash'
                    : g.level === 'mid'
                      ? isShort
                        ? 'bg-hot-wash-weak'
                        : 'bg-cold-wash-weak'
                      : undefined
                return (
                  <li key={it.station_uid} className="anim-row border-b border-hair">
                    {/* 原本是 <li onClick>：鍵盤 Tab 不到、Enter 無效、報讀者不當它可互動。
                        改成原生 <button> → 免寫 keydown 就有 Enter/Space、focus 樣式吃全域 :focus-visible。 */}
                    <button
                      ref={(el) => {
                        if (el) rowRefs.current.set(it.station_uid, el)
                        else rowRefs.current.delete(it.station_uid)
                      }}
                      type="button"
                      aria-pressed={sel}
                      style={{ scrollMarginTop: headH + 6 }}
                      onClick={() => selectStation(it.station_uid)}
                      className={cn(
                        'relative grid w-full cursor-pointer grid-cols-[2.3em_1fr_auto] items-baseline gap-x-3 gap-y-1 border-l-2 px-4 py-[11px] text-left hover:bg-white/[0.03]',
                        washClass,
                        g.level === 'low' ? 'border-l-transparent' : isShort ? 'border-l-hot/50' : 'border-l-cold/50',
                        sel && 'bg-white/[0.055]',
                      )}
                    >
                      {sel && <span className="absolute left-0 top-0 h-full w-[2px] bg-ink" />}
                      <span className="num text-[1rem] text-ink3">{String(no).padStart(2, '0')}</span>
                      <span className="min-w-0">
                        {/* 行政區小標在站名上方；風險等級跟調車來源已移除／移到分組標題與右欄，
                            這裡只留「這站是哪裡、叫什麼」兩行。 */}
                        <span className="block whitespace-nowrap text-[0.73rem] tracking-[0.04em] text-ink3">
                          {it.town}
                        </span>
                        <span className="mt-[1px] block font-serif text-[1.05rem] leading-[1.3] tracking-[-0.015em]">
                          {it.name}
                        </span>
                      </span>
                      <span className="whitespace-nowrap text-right tracking-[0.04em]">
                        {/* 這列存在的理由：建議補／取 N 台，最大、粗體、依方向上色 + 箭頭（不只靠顏色，
                            色盲也能靠圖示分辨方向）。已 X 小時是輔助資訊。 */}
                        <b
                          className={cn(
                            'num inline-flex items-center gap-[3px] text-[1rem] tracking-[-0.02em]',
                            isShort ? 'text-hot' : 'text-cold',
                          )}
                        >
                          {it.dispatch &&
                            (isShort ? (
                              <ArrowDown className="size-3" aria-hidden />
                            ) : (
                              <ArrowUp className="size-3" aria-hidden />
                            ))}
                          {it.dispatch
                            ? `${it.dispatch.action === 'refill' ? '建議補 ' : it.dispatch.action === 'remove' ? '建議取 ' : ''}${it.dispatch.bikes} 台`
                            : '—'}
                        </b>
                        {/* 已調派：掛在建議台數正下方，因為它修正的就是上面那個數字 ——
                            「建議取 9 台」本身是 risk_snapshot 的原始值，不會因為派過車而減少
                            （後端 need = max(0, bikes − already) 是調度 API 才算的，警示不算）。
                            沿用 StationDetail 的 text-info，全站「已調度／已調派」同一個顏色。 */}
                        {sent > 0 && (
                          <span className="block text-[0.68rem] leading-[1.4] text-info">
                            已調派 <span className="num">{sent}</span> 台
                            {left > 0 && <span className="text-ink3"> · 還缺 {left}</span>}
                          </span>
                        )}
                        {/* 9/12 起 streak 只算高風險——中低風險 hours 恆為 null，不顯示這行
                            （it.streak 物件本身一定存在，判斷式要看 hours 有沒有值，不是看物件存不存在） */}
                        {it.streak?.hours != null && (
                          <span className="block text-[0.68rem] leading-[1.4] text-ink3">
                            已 {it.streak.hours} 小時
                          </span>
                        )}
                      </span>
                      {/* 可借／空位＋容量比例條：跨第 2、3 欄，落在站名與行動數字下方（不是序號下方）。 */}
                      <div className="col-span-2 col-start-2 flex items-center gap-2">
                        <span className="num flex-none whitespace-nowrap text-[0.73rem] text-ink3">
                          {isShort ? '可借' : '空位'} {capVal ?? '—'}／{it.capacity ?? '?'}
                        </span>
                        <span
                          className="h-[3px] min-w-[32px] flex-1 overflow-hidden rounded-full bg-hair"
                          aria-hidden
                        >
                          <span
                            className={cn('block h-full rounded-full', isShort ? 'bg-hot' : 'bg-cold')}
                            style={{ width: `${pct}%` }}
                          />
                        </span>
                      </div>
                    </button>
                  </li>
                )
              })}
            </Fragment>
          ))}
        </ul>
      )}
    </>
  )
}
