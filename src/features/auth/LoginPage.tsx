import { useEffect, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Loader2, Moon, Sun } from 'lucide-react'
import { useAuthStore } from '@/stores/useAuthStore'
import { useAppStore } from '@/stores/useAppStore'
import { cn } from '@/lib/utils'
import { authMode } from './api'
import { NewTaipeiBackdrop } from './NewTaipeiBackdrop'

export function LoginPage() {
  const user = useAuthStore((s) => s.user)
  const login = useAuthStore((s) => s.login)
  const submitting = useAuthStore((s) => s.submitting)
  const error = useAuthStore((s) => s.error)
  const clearError = useAuthStore((s) => s.clearError)
  const theme = useAppStore((s) => s.theme)
  const toggleTheme = useAppStore((s) => s.toggleTheme)
  const nav = useNavigate()
  const loc = useLocation()

  const isMock = authMode() === 'mock'
  const [account, setAccount] = useState(isMock ? 'IM_TEST' : '')
  // 非弱密碼，避免瀏覽器一直跳「帳密外洩」警告（mock 模式任意帳密皆可登入）
  const [password, setPassword] = useState(isMock ? '1qaz@WSX3edc' : '')

  // 離開登入頁時清掉殘留的錯誤
  useEffect(() => () => clearError(), [clearError])

  if (user) return <Navigate to="/" replace />

  const from = (loc.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!account.trim() || submitting) return
    try {
      await login(account, password)
      nav(from, { replace: true })
    } catch {
      /* 錯誤已寫進 store.error，畫面會顯示 */
    }
  }

  return (
    <div className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-bg px-4">
      {/* 卡片後方一圈極淡暖光，增加深度、把視線帶到中央 */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-[44%] size-[720px] -translate-x-1/2 -translate-y-1/2 rounded-full"
        style={{
          background:
            'radial-gradient(circle, color-mix(in srgb, var(--color-hot) 8%, transparent), transparent 68%)',
        }}
      />
      {/* 新北市行政區輪廓（線性投影）+ ping 點綴 —— 產品識別 + 一點科技感 */}
      <NewTaipeiBackdrop />

      <button
        type="button"
        onClick={toggleTheme}
        aria-label={theme === 'dark' ? '切換為淺色主題' : '切換為深色主題'}
        className="absolute right-4 top-4 z-10 flex size-8 items-center justify-center rounded-xs border border-control bg-panel text-ink3 hover:text-ink"
      >
        {theme === 'dark' ? <Sun className="size-[15px]" /> : <Moon className="size-[15px]" />}
      </button>

      <form
        onSubmit={submit}
        aria-labelledby="login-title"
        className="relative z-10 w-full max-w-[416px] rounded-sm border border-edge bg-panel p-9 shadow-[0_24px_72px_rgba(0,0,0,.4)]"
      >
        <div className="mb-7 flex items-center gap-3">
          <span className="flex size-5 flex-none items-center justify-center text-hot" aria-hidden>
            <svg viewBox="0 0 16 16" fill="none" className="size-full">
              <circle cx="8" cy="8" r="6.25" stroke="currentColor" strokeWidth="1.8" />
              <circle cx="8" cy="8" r="2.4" fill="currentColor" />
            </svg>
          </span>
          <span className="text-[1rem] font-semibold tracking-[0.01em] text-ink">新北市 YouBike</span>
          <span className="border-l border-hair pl-3 text-[0.74rem] tracking-[0.16em] text-ink2">
            調度中心
          </span>
        </div>

        <h1 id="login-title" className="m-0 mb-1 text-[1.2rem] font-semibold tracking-[0.01em] text-ink">
          登入
        </h1>
        <p className="mb-7 text-[0.8rem] leading-[1.6] text-ink3">輸入帳號密碼進入調度主控台。</p>

        <label htmlFor="login-account" className="mb-1.5 block text-[0.75rem] tracking-[0.06em] text-ink3">
          帳號
        </label>
        <input
          id="login-account"
          autoFocus
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          disabled={submitting}
          value={account}
          onChange={(e) => {
            setAccount(e.target.value)
            if (error) clearError()
          }}
          className="mb-4 w-full rounded-xs border border-control bg-bg px-3 py-2.5 text-[0.9rem] text-ink outline-none focus-visible:border-ink2 disabled:opacity-50"
        />

        <label htmlFor="login-password" className="mb-1.5 block text-[0.75rem] tracking-[0.06em] text-ink3">
          密碼
        </label>
        <input
          id="login-password"
          type="password"
          autoComplete="current-password"
          disabled={submitting}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
            if (error) clearError()
          }}
          className="w-full rounded-xs border border-control bg-bg px-3 py-2.5 text-[0.9rem] text-ink outline-none focus-visible:border-ink2 disabled:opacity-50"
        />

        <p
          role="alert"
          className={cn(
            'overflow-hidden text-[0.75rem] leading-[1.5] text-hot transition-all',
            error ? 'mt-2 max-h-10' : 'mt-0 max-h-0',
          )}
        >
          {error}
        </p>

        <button
          type="submit"
          disabled={!account.trim() || submitting}
          className="mt-6 flex w-full items-center justify-center gap-2 rounded-xs bg-ink py-2.5 text-[0.9rem] font-semibold tracking-[0.06em] text-bg hover:opacity-90 active:opacity-80 disabled:opacity-40"
        >
          {submitting && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {submitting ? '登入中…' : '登入'}
        </button>

        {isMock && (
          <p className="mt-4 text-center text-[0.68rem] leading-[1.7] text-ink3">
            展示版本 · 尚未接入身分驗證
          </p>
        )}
      </form>
    </div>
  )
}
