/* 調度候選卡 —— 助理訊息裡可勾選、可改台數、可確認的那張卡。
   ★ 從 AssistantWidget 拆出來（那支已 812 行）；調度相關的 UI 一律放這裡，不要往回塞。

   ★ 這張卡**不經過 LLM**：候選與台數都是後端程式算的，SSE 的 {type:'dispatch'}
     事件先於文案送達。所以 Bedrock 掛掉時，卡片與確認按鈕照常可用。 */
import { useMemo, useState } from 'react'
import { AlertTriangle, Check, Loader2, Minus, Plus } from 'lucide-react'
import { useCreateDispatch } from '@/api/queries'
import { cn } from '@/lib/utils'
import type { AssistantDispatch } from './types'

const dist = (m: number | null) =>
  m === null ? '—' : m < 1000 ? `${m} m` : `${(m / 1000).toFixed(1)} km`

interface Props {
  d: AssistantDispatch
  /** 確認成功後通知外層（捲到地圖 / 顯示提示） */
  onDone?: (ids: number[]) => void
}

export function DispatchCard({ d, onDone }: Props) {
  const { anchor, items, shortfall } = d
  const actWord = anchor.action === 'refill' ? '補' : '取'
  const roleWord = anchor.action === 'refill' ? '調出' : '接收'

  // 勾選與台數各自獨立：取消勾選不該把使用者改過的台數也一起丟掉
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(items.filter((i) => i.selected).map((i) => i.uid)),
  )
  const [bikes, setBikes] = useState<Record<string, number>>(
    () => Object.fromEntries(items.map((i) => [i.uid, i.bikes || Math.min(i.supply, anchor.need)])),
  )
  const [done, setDone] = useState<number[] | null>(null)
  const create = useCreateDispatch()

  const chosen = useMemo(() => items.filter((i) => picked.has(i.uid)), [items, picked])
  const total = chosen.reduce((s, i) => s + (bikes[i.uid] ?? 0), 0)
  const gap = anchor.need - total

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

  function confirm() {
    create.mutate(
      {
        anchor_uid: anchor.uid,
        action: anchor.action,
        items: chosen.map((i) => ({ uid: i.uid, bikes: bikes[i.uid] ?? 0 })),
      },
      {
        onSuccess: (r) => {
          setDone(r.ids)
          onDone?.(r.ids)
        },
      },
    )
  }

  if (done) {
    return (
      <div className="mt-2 flex items-center gap-2 rounded-xs border border-info/40 bg-info-wash px-2.5 py-2 text-[0.78rem] text-ink">
        <Check className="size-[14px] flex-none text-info" aria-hidden />
        已送出 {done.length} 筆調度，合計 {total} 台 — 地圖上已標出路線。
      </div>
    )
  }

  return (
    <div className="mt-2 overflow-hidden rounded-xs border border-hair">
      <div className="flex items-baseline justify-between gap-2 bg-raise px-2.5 py-1">
        <span className="text-[0.66rem] font-semibold tracking-[0.08em] text-ink3">
          調度來源 · 可{roleWord}的站
        </span>
        <span className="tabular-nums text-[0.66rem] text-ink3">
          {anchor.name} 需{actWord} {anchor.need} 台
          {anchor.already > 0 && <span className="ml-1">（已派 {anchor.already}）</span>}
        </span>
      </div>

      <ul className="max-h-[260px] divide-y divide-hair overflow-y-auto">
        {items.map((i) => {
          const on = picked.has(i.uid)
          const n = bikes[i.uid] ?? 0
          return (
            <li key={i.uid} className={cn('px-2.5 py-2', !on && 'opacity-55')}>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(i.uid)}
                  aria-label={`選擇 ${i.name}`}
                  className="size-[13px] flex-none accent-[var(--color-info)]"
                />
                <span className="min-w-0 flex-1 truncate text-[0.78rem] text-ink">{i.name}</span>
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
                    onClick={() => step(i.uid, -1, i.supply)}
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
                    onClick={() => step(i.uid, 1, i.supply)}
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
        <p className="m-0 flex items-start gap-1.5 border-t border-hair px-2.5 py-1.5 text-[0.7rem] leading-[1.5] text-ink3">
          <AlertTriangle className="mt-[2px] size-[11px] flex-none text-ink3" aria-hidden />
          尚缺 {Math.max(gap, shortfall)} 台，建議由調度中心的備用車補入。
        </p>
      )}

      {create.isError && (
        <p className="m-0 border-t border-hair bg-hot-wash px-2.5 py-1.5 text-[0.7rem] leading-[1.5] text-hot">
          {/* ★ 直接用後端訊息，不自行改寫 —— DISPATCH_STALE 的文字已經是寫給調度員看的 */}
          {create.error instanceof Error ? create.error.message : '調度送出失敗，請重新詢問。'}
        </p>
      )}

      <div className="flex items-center justify-between gap-2 border-t border-hair px-2.5 py-1.5">
        <span className="tabular-nums text-[0.7rem] text-ink3">
          已選 {chosen.length} 站 · 合計{' '}
          <b className={cn('font-medium', total > 0 && 'text-ink')}>{total}</b> 台
        </span>
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
            <Loader2 className="size-[12px] animate-spin motion-reduce:animate-none" aria-hidden />
          ) : (
            <Check className="size-[12px]" aria-hidden />
          )}
          確認調度
        </button>
      </div>
    </div>
  )
}
