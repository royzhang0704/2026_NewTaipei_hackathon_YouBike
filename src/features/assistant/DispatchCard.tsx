/* 調度建議摘要列 —— 助理訊息底下那一條「N 站 / M 台 ＋ 進行調度」。
   出處：meet/20260912/計劃-調度改為Modal.md

   ★ 2026-09-12 改版：勾選／改台數／確認整段搬到 DispatchModal。
     對話串裡只留「文字答覆 ＋ 一顆按鈕」——
     原本把可送出的控制項攤在訊息裡，會被讀成「LLM 生的建議」（其實候選與
     台數都是後端程式算的，見 dispatch_service.candidates），而且對話一長
     就被捲走。**這條摘要列不會 POST 任何東西**，只負責開窗。

   ★ 這張卡（含 Modal）**不經過 LLM**：SSE 的 {type:'dispatch'} 事件先於
     文案送達，所以 Bedrock 掛掉時「進行調度」照樣點得下去。

   ★ picked / bikes 的 state 留在這裡而不是 Modal 裡（計劃 §3-4）：
     使用者改完台數關掉窗、再打開，要記得他改過什麼。

   ★ ready：Bedrock 文案回來之前整條不顯示（9/12 使用者定案）。
     技術上 {type:'dispatch'} 早到好幾秒、按鈕當下就能用，但先冒出一顆
     「進行調度」再冒出解釋文字，順序讀起來像「系統先叫你做、事後才說明」。
     ⚠ 代價講明：Bedrock 逾時到 done 事件為止，這條都不會出現 ——
       降級路徑仍在（後端 catch 後會送模板文案＋done），只是慢那幾秒。 */
import { useState } from 'react'
import { Check, History, SlidersHorizontal } from 'lucide-react'
import { useDispatchOrders } from '@/api/queries'
import { DispatchModal } from './DispatchModal'
import type { AssistantDispatch } from './types'

interface Props {
  d: AssistantDispatch
  /** 助理這則訊息是否已收尾（= Bedrock 文案到齊）。false 時整條不顯示。 */
  ready?: boolean
  /** 確認成功後通知外層（捲到地圖 / 顯示提示） */
  onDone?: (ids: number[]) => void
}

export function DispatchCard({ d, ready = true, onDone }: Props) {
  const { anchor, items } = d
  const actWord = anchor.action === 'refill' ? '補' : '取'
  const roleWord = anchor.action === 'refill' ? '調出' : '接收'

  const { data: orders } = useDispatchOrders()

  // 勾選與台數各自獨立：取消勾選不該把使用者改過的台數也一起丟掉
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(items.filter((i) => i.selected).map((i) => i.uid)),
  )
  const [bikes, setBikes] = useState<Record<string, number>>(
    () => Object.fromEntries(items.map((i) => [i.uid, i.bikes || Math.min(i.supply, anchor.need)])),
  )
  const [open, setOpen] = useState(false)
  const [done, setDone] = useState<{ n: number; total: number } | null>(null)

  /* ★ 這張卡是**快照**：候選、need、supply 都是訊息產生當下算的。
       對話往上捲三則、隔十分鐘再點「進行調度」，它照樣是那時候的數字。
       所以要拿即時的調度單對一次 —— useDispatchOrders 是 live query
       （?status=active，撤銷過的不算），StationDetail 也用同一支。

     ★ 比法跟後端同一條算式：anchor.already = 快照當下該站的 active 台數
       （dispatch_service:102）。現在的值不一樣 → 這張卡的建議已經不成立。
       ⚠ 不只擋「已派滿」：派了一部分也要擋。快照的 need 與預勾台數是照
         舊的餘裕算的，放行等於讓人用過期的數字再派一次（後端 supply 夠的話
         會照單全收，變成超派）。 */
  const sentNow = (orders?.items ?? [])
    .filter((o) => o.anchor_uid === anchor.uid)
    .reduce((s, o) => s + o.bikes, 0)
  const moved = sentNow - anchor.already

  const chosen = items.filter((i) => picked.has(i.uid))
  const total = chosen.reduce((s, i) => s + (bikes[i.uid] ?? 0), 0)

  function toggle(uid: string) {
    setPicked((s) => {
      const n = new Set(s)
      if (n.has(uid)) n.delete(uid)
      else n.add(uid)
      return n
    })
  }

  function step(uid: string, delta: number, supply: number) {
    setBikes((b) => {
      // 下界 2 = config.DISPATCH_MIN_BIKES（後端也擋，這裡只是別讓人按到無效值）
      const next = Math.min(supply, Math.max(2, (b[uid] ?? 0) + delta))
      return { ...b, [uid]: next }
    })
  }

  // ★ 等文案。hooks 已全部跑完才早退 —— 早退擺在 useState 之前會違反 hooks 規則。
  if (!ready) return null

  if (done) {
    return (
      <div className="mt-2 flex items-center gap-2 rounded-xs border border-info/40 bg-info-wash px-2.5 py-2 text-[0.78rem] text-ink">
        <Check className="size-[14px] flex-none text-info" aria-hidden />
        已送出 {done.n} 筆調度，合計 {done.total} 台 — 地圖上已標出路線。
      </div>
    )
  }

  /* need = 0：本輪建議台數已被既有的 active 調度單吃完
     （dispatch_service:103 need = max(0, bikes − already)）。
     ★ 這時後端仍會回一整串候選站，預勾是空的 —— 照原樣渲染會變成
       「調度建議 0 站 / 0 台」配一顆點得下去的按鈕，開進去也只能對著
       全不勾的清單發呆。沒有要調的車就別給入口。 */
  if (anchor.need <= 0) {
    return (
      <p className="m-0 mt-2 rounded-xs border border-hair bg-raise px-2.5 py-1.5 text-[0.7rem] leading-[1.5] text-ink3">
        {anchor.name} 本輪不需再調度
        {anchor.already > 0 && `——建議${actWord}的台數已由既有的 ${anchor.already} 台調度單覆蓋`}。
      </p>
    )
  }

  /* 快照產生後調度單有異動 → 這則歷史訊息的建議已經不成立。
     ★ 兩個方向都擋：moved > 0 是期間又派了車（再派會超派），
       moved < 0 是有人撤銷了（現在缺的比卡片上寫的多，照卡片派會少派）。
       兩種都該回去重問，讓後端用最新一輪重算，而不是拿舊數字硬送。
     ★ 後端在寫入時也會擋（dispatch_service:187 用最新一輪重驗，不符回
       DISPATCH_STALE），但那是「勾完、按下確認才被拒絕」—— 白等一場。 */
  if (moved !== 0) {
    return (
      <p className="m-0 mt-2 flex items-start gap-1.5 rounded-xs border border-hair bg-raise px-2.5 py-1.5 text-[0.7rem] leading-[1.5] text-ink3">
        <History className="mt-[2px] size-[11px] flex-none" aria-hidden />
        <span>
          這則建議產生後，{anchor.name} 的調度單已有異動（目前已派{' '}
          <span className="tabular-nums">{sentNow}</span> 台
          {anchor.already > 0 && (
            <span>
              ，當時 <span className="tabular-nums">{anchor.already}</span> 台
            </span>
          )}
          ）。請重新詢問以取得最新建議。
        </span>
      </p>
    )
  }

  /* 一台都調不出來：後端在這個分支會直接用文案講清楚（events.py:158），
     這裡再給一顆開不出東西的按鈕只是誤導 —— 補一行註記就好。 */
  if (!items.length) {
    return (
      <p className="m-0 mt-2 rounded-xs border border-hair bg-raise px-2.5 py-1.5 text-[0.7rem] leading-[1.5] text-ink3">
        附近沒有可{roleWord}的餘裕站，建議由調度中心的備用車補入。
      </p>
    )
  }

  return (
    <>
      <div className="mt-2 flex items-center justify-between gap-2 rounded-xs border border-hair bg-raise px-2.5 py-1.5">
        <span className="min-w-0">
          <span className="text-[0.66rem] font-semibold tracking-[0.08em] text-ink3">
            調度建議
          </span>
          <span className="ml-1.5 tabular-nums text-[0.74rem] text-ink2">
            {chosen.length} 站 / {total} 台
          </span>
          <span className="ml-1.5 tabular-nums text-[0.66rem] text-ink3">
            （{anchor.name} 需{actWord} {anchor.need} 台
            {anchor.already > 0 && `，已派 ${anchor.already}`}）
          </span>
        </span>
        {/* ★ 一站都沒勾 → 不給開窗：Modal 裡的「確認調度」本來就會 disabled，
            讓人點進去再發現不能送，不如在這裡就講清楚。 */}
        <button
          type="button"
          disabled={!chosen.length}
          title={chosen.length ? undefined : '目前沒有選定的來源站'}
          onClick={() => setOpen(true)}
          className="flex flex-none items-center gap-1 rounded-xs border border-control bg-panel px-2 py-1 text-[0.72rem] font-medium text-ink hover:bg-hair disabled:opacity-40 disabled:hover:bg-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus)]"
        >
          <SlidersHorizontal className="size-[11px]" aria-hidden />
          進行調度
        </button>
      </div>

      {open && (
        <DispatchModal
          d={d}
          picked={picked}
          bikes={bikes}
          onToggle={toggle}
          onStep={step}
          onClose={() => setOpen(false)}
          onDone={(ids, sent) => {
            setDone({ n: ids.length, total: sent })
            setOpen(false)
            onDone?.(ids)
          }}
        />
      )}
    </>
  )
}
