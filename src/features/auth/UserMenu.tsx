import { useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, LogOut } from 'lucide-react'
import { useAuthStore } from '@/stores/useAuthStore'

/** 頭欄帳號入口：頭像 + 姓名 → 下拉（姓名 / 角色 + 登出）。取代原本不可點的靜態 chip。 */
export function UserMenu() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const nav = useNavigate()
  const [open, setOpen] = useState(false)

  if (!user) return null

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      {/* 無框、頭像為主：帳號入口跟旁邊的設定控制項（有框盒）在視覺上分開 —— GitHub / Linear / Vercel 都這樣。
          角色移到下拉裡；trigger 只留頭像 + 姓名（<sm 只留頭像）。 */}
      <Popover.Trigger
        aria-label={`帳號選單，目前為 ${user.name}`}
        className="group flex h-8 items-center gap-2 rounded-xs px-1.5 text-ink hover:bg-raise data-[state=open]:bg-raise"
      >
        <span
          className="flex size-[26px] flex-none items-center justify-center rounded-full bg-ink text-[0.62rem] font-semibold text-bg"
          aria-hidden
        >
          {user.name.slice(0, 1)}
        </span>
        <span className="hidden whitespace-nowrap text-[0.74rem] font-medium sm:inline">
          {user.name}
        </span>
        <ChevronDown
          className="size-3 flex-none text-ink3 transition-transform group-data-[state=open]:rotate-180"
          aria-hidden
        />
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          className="z-40 w-[220px] overflow-hidden rounded-xs border border-edge bg-panel shadow-[0_10px_30px_rgba(0,0,0,.5)]"
        >
          <div className="border-b border-hair px-3 py-[10px]">
            <div className="text-[0.82rem] font-medium text-ink">{user.name}</div>
            <div className="mt-[2px] text-[0.68rem] tracking-[0.04em] text-ink3">{user.role}</div>
            {/* 帳號 handle —— 真實調度台看得到「現在是哪個帳號」才能對稽核 */}
            <div className="mt-[3px] font-mono text-[0.64rem] text-ink3">{user.account}</div>
          </div>
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              logout()
              nav('/login', { replace: true })
            }}
            className="flex w-full items-center gap-2 px-3 py-[10px] text-left text-[0.8rem] text-ink2 hover:bg-raise hover:text-ink"
          >
            <LogOut className="size-[14px]" aria-hidden />
            登出
          </button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
