/* 調度 Modal —— 勾選來源站、調台數、送出。
   出處：meet/20260912/計劃-調度改為Modal.md §1

   ★ 為什麼從對話串搬出來（§0）：候選與台數是**後端程式算的**
     （dispatch_service.candidates：同區優先 → 5km 跨區 → 一趟搬得完優先
     → 由近而遠分配），完全不經 LLM。攤在助理訊息裡會被讀成「LLM 生的建議」，
     而且對話一長就被捲走 —— 一個會 POST 出去的決策介面不該有這種身世。

   ★ 這裡是**唯一**會打 POST /api/v1/dispatch/orders 的地方。
     摘要列上的「進行調度」只負責開窗，不送任何東西。

   ★ 勾選與台數的 state 不在這裡，在 DispatchCard —— 關窗再開要記得
     使用者改過的數字（§3-4）。本檔純呈現 ＋ 送出。 */
import { useEffect, useRef } from 'react'
import { AlertTriangle, Check, Loader2, Minus, Plus, X } from 'lucide-react'
import { useCreateDispatch } from '@/api/queries'
import { cn } from '@/lib/utils'
import type { AssistantDispatch } from './types'

const dist = (m: number | null) =>
  m === null ? '—' : m < 1000 ? `${m} m` : `${(m / 1000).toFixed(1)} km`

interface Props {
  d: AssistantDispatch
  picked: Set<string>
  bikes: Record<string, number>
  onToggle: (uid: string) => void
  onStep: (uid: string, delta: number, supply: number) => void
  onClose: () => void
  /** 送出成功：交還 order id 與合計台數，由 DispatchCard 收尾（關窗＋換成已送出） */
  onDone: (ids: number[], total: number) => void
}

export function DispatchModal({ d, picked, bikes, onToggle, onStep, onClose, onDone }: Props) {
  const { anchor, items, shortfall } = d
  const actWord = anchor.action === 'refill' ? '補' : '取'
  const roleWord = anchor.action === 'refill' ? '調出' : '接收'
  const closeRef = useRef<HTMLButtonElement>(null)
  const create = useCreateDispatch()

  const chosen = items.filter((i) => picked.has(i.uid))
  const total = chosen.reduce((s, i) => s + (bikes[i.uid] ?? 0), 0)
  const gap = anchor.need - total

  /* Esc 關窗。★ 一定要 capture ＋ stopImmediatePropagation（計劃 §3-1）：
     AssistantWidget 也在 window 上聽 Esc（那支會關掉整個助理面板），而它
     掛得比本元件早 —— 同為 bubble 的話它先跑，按一次 Esc 會連面板一起收掉。
     capture 階段永遠先於 bubble，與掛載順序無關，所以這裡才擋得住。 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopImmediatePropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  // 焦點落在關閉鈕（安全選項為預設，同 ShortcutsHelp）
  useEffect(() => {
    closeRef.current?.focus()
  }, [])

  function confirm() {
    create.mutate(
      {
        anchor_uid: anchor.uid,
        action: anchor.action,
        items: chosen.map((i) => ({ uid: i.uid, bikes: bikes[i.uid] ?? 0 })),
      },
      { onSuccess: (r) => onDone(r.ids, total) },
    )
  }

  return (
    /* ★ z-50 但 render 在 AssistantWidget 內 —— 同層靠 DOM 順序決勝，
       AppShell 把 PredictingOverlay 放在最後，所以「正在現算」的全頁遮罩
       蓋得住這扇窗。不然會出現對著過期資料下單（計劃 §3-2）。 */
    <div
      className="anim-fade fixed inset-0 z-50 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-[1px]"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="dispatch-modal-title"
        onClick={(e) => e.stopPropagation()}
        className="anim-dialog flex max-h-[min(560px,calc(100vh-3rem))] w-full max-w-[420px] flex-col overflow-hidden rounded-sm border border-edge bg-panel shadow-[0_20px_60px_rgba(0,0,0,.5)]"
      >
        <div className="flex flex-none items-start justify-between gap-2 border-b border-hair px-3 py-2">
          <div className="min-w-0">
            <h2 id="dispatch-modal-title" className="kicker m-0">
              調度來源 · 可{roleWord}的站
            </h2>
            <p className="m-0 mt-0.5 truncate text-[0.7rem] text-ink3">
              <span className="text-ink2">{anchor.name}</span> 需{actWord}{' '}
              <span className="tabular-nums">{anchor.need}</span> 台
              {anchor.already > 0 && (
                <span className="ml-1">（已派 {anchor.already}）</span>
              )}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="關閉"
            className="flex size-6 flex-none items-center justify-center rounded-xs border border-hair text-ink3 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus)]"
          >
            <X className="size-[13px]" aria-hidden />
          </button>
        </div>

        <ul className="min-h-0 flex-1 divide-y divide-hair overflow-y-auto">
          {items.map((i) => {
            const on = picked.has(i.uid)
            const n = bikes[i.uid] ?? 0
            return (
              <li key={i.uid} className={cn('px-3 py-2', !on && 'opacity-55')}>
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => onToggle(i.uid)}
                    aria-label={`選擇 ${i.name}`}
                    className="size-[13px] flex-none accent-[var(--color-info)]"
                  />
                  <span className="min-w-0 flex-1 truncate text-[0.8rem] text-ink">{i.name}</span>
                  <span className="flex-none tabular-nums text-[0.7rem] text-ink3">
                    {dist(i.distance_m)}
                  </span>
                </div>

                <div className="mt-1 flex items-center gap-2 pl-[21px]">
                  {i.cross_town && (
                    <span className="rounded-xs border border-info/50 px-[4px] py-px text-[0.6rem] leading-none tracking-[0.06em] text-info">
                      跨區 · {i.town}
                    </span>
                  )}
                  {i.note && <span className="truncate text-[0.66rem] text-ink3">{i.note}</span>}
                  <span className="ml-auto flex flex-none items-center gap-1">
                    <span className="tabular-nums text-[0.66rem] text-ink3">上限 {i.supply}</span>
                    <button
                      type="button"
                      disabled={!on || n <= 2}
                      onClick={() => onStep(i.uid, -1, i.supply)}
                      aria-label={`${i.name} 減少一台`}
                      className="grid size-[18px] place-items-center rounded-xs border border-edge text-ink2 disabled:opacity-35"
                    >
                      <Minus className="size-[10px]" aria-hidden />
                    </button>
                    <b className="w-[22px] text-center tabular-nums text-[0.8rem] font-medium text-ink">
                      {n}
                    </b>
                    <button
                      type="button"
                      disabled={!on || n >= i.supply}
                      onClick={() => onStep(i.uid, 1, i.supply)}
                      aria-label={`${i.name} 增加一台`}
                      className="grid size-[18px] place-items-center rounded-xs border border-edge text-ink2 disabled:opacity-35"
                    >
                      <Plus className="size-[10px]" aria-hidden />
                    </button>
                  </span>
                </div>
              </li>
            )
          })}
        </ul>

        {/* shortfall 由後端算（候選全加起來仍湊不滿）；gap 是使用者改動後的即時差額 */}
        {(shortfall > 0 || gap > 0) && (
          <p className="m-0 flex flex-none items-start gap-1.5 border-t border-hair px-3 py-1.5 text-[0.7rem] leading-[1.5] text-ink3">
            <AlertTriangle className="mt-[2px] size-[11px] flex-none text-ink3" aria-hidden />
            尚缺 {Math.max(gap, shortfall)} 台，建議由調度中心的備用車補入。
          </p>
        )}

        {/* ★ 失敗訊息留在窗內、不關窗（計劃 §3-5）——關掉就看不到為什麼失敗。
            直接用後端訊息不自行改寫：DISPATCH_STALE 的文字已經是寫給調度員看的。 */}
        {create.isError && (
          <p className="m-0 flex-none border-t border-hair bg-hot-wash px-3 py-1.5 text-[0.7rem] leading-[1.5] text-hot">
            {create.error instanceof Error ? create.error.message : '調度送出失敗，請重新詢問。'}
          </p>
        )}

        <div className="flex flex-none items-center justify-between gap-2 border-t border-hair px-3 py-2">
          <span className="tabular-nums text-[0.72rem] text-ink3">
            已選 {chosen.length} 站 · 合計{' '}
            <b className={cn('font-medium', total > 0 && 'text-ink')}>{total}</b> 台
          </span>
          <span className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={onClose}
              className="rounded-xs border border-edge px-2.5 py-1 text-[0.74rem] text-ink2 hover:text-ink"
            >
              取消
            </button>
            <button
              type="button"
              disabled={!chosen.length || create.isPending}
              onClick={confirm}
              className={cn(
                'flex items-center gap-1 rounded-xs px-2.5 py-1 text-[0.74rem] font-medium',
                'bg-info text-white disabled:opacity-40',
              )}
            >
              {create.isPending ? (
                <Loader2
                  className="size-[12px] animate-spin motion-reduce:animate-none"
                  aria-hidden
                />
              ) : (
                <Check className="size-[12px]" aria-hidden />
              )}
              確認調度
            </button>
          </span>
        </div>
      </div>
    </div>
  )
}
