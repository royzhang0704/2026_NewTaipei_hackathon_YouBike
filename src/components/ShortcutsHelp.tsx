import { Fragment, useState } from 'react'
import { useEventListener } from 'usehooks-ts'
import { X } from 'lucide-react'

/* 「?」叫出的快捷鍵總表。掛在 AppShell，全頁可用。
   刻意手刻小 modal（不引 Radix Dialog）：內容是 3 行唯讀清單，不值得多一個依賴。 */

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ['/'], label: '聚焦站點搜尋' },
  { keys: ['A'], label: '開 / 關調度助理' },
  { keys: ['Esc'], label: '關閉最上層浮層（助理 → 單站檢視）' },
  { keys: ['?'], label: '開 / 關這份快捷鍵' },
]

function isTyping() {
  const el = document.activeElement as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
}

export function ShortcutsHelp() {
  const [open, setOpen] = useState(false)

  useEventListener('keydown', (e) => {
    if (e.key === '?' && !isTyping()) {
      e.preventDefault()
      setOpen((v) => !v)
    } else if (e.key === 'Escape' && open) {
      // 擋住 DashboardPage 的 Esc（清除選取）——靠 AppShell 比 route 子層先掛，這個 listener 先跑
      e.stopImmediatePropagation()
      setOpen(false)
    }
  })

  if (!open) return null

  return (
    <div
      className="anim-fade fixed inset-0 z-50 flex items-center justify-center bg-bg/60 p-4 backdrop-blur-[1px]"
      onClick={() => setOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-title"
        onClick={(e) => e.stopPropagation()}
        className="anim-dialog w-full max-w-[320px] rounded-sm border border-edge bg-panel p-5 shadow-[0_20px_60px_rgba(0,0,0,.5)]"
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id="shortcuts-title" className="kicker m-0">
            鍵盤快捷鍵
          </h2>
          <button
            type="button"
            autoFocus
            onClick={() => setOpen(false)}
            aria-label="關閉"
            className="flex size-6 flex-none items-center justify-center rounded-xs border border-hair text-ink3 hover:text-ink"
          >
            <X className="size-[13px]" />
          </button>
        </div>
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-[10px] text-[0.8rem]">
          {SHORTCUTS.map((s) => (
            <Fragment key={s.label}>
              <dt className="flex gap-1">
                {s.keys.map((k) => (
                  <kbd
                    key={k}
                    className="rounded-[3px] border border-hair px-[6px] py-px font-sans text-[0.7rem] text-ink2"
                  >
                    {k}
                  </kbd>
                ))}
              </dt>
              <dd className="m-0 self-center text-ink2">{s.label}</dd>
            </Fragment>
          ))}
        </dl>
      </div>
    </div>
  )
}
