import { create } from 'zustand'
import { authLogin, authLogout } from '@/features/auth/api'
import { setSessionToken, setUnauthorizedHandler } from '@/api/session'
import type { AuthUser } from '@/features/auth/types'

export type { AuthUser }

/* 登入狀態。元件（LoginPage / RequireAuth / UserMenu）只跟這個 store 對話，
   換成真後端只需改 features/auth/api.ts（VITE_AUTH_MODE=live）＋ 後端做 /api/v1/auth/*。
   session（token / 401 handler）走 api/session.ts，避免 client ↔ store 循環相依。 */

const KEY = 'yb_auth'

interface Stored {
  user: AuthUser
  token: string
}

function readStored(): { user: AuthUser | null; token: string | null } {
  try {
    const raw = localStorage.getItem(KEY)
    const s = raw ? (JSON.parse(raw) as Stored) : null
    if (s && typeof s.user?.account === 'string' && typeof s.token === 'string') {
      return { user: s.user, token: s.token }
    }
  } catch {
    /* 隱私模式 / 壞資料 → 視為未登入 */
  }
  return { user: null, token: null }
}

function persist(v: Stored | null) {
  try {
    if (v) localStorage.setItem(KEY, JSON.stringify(v))
    else localStorage.removeItem(KEY)
  } catch {
    /* 隱私模式：本次仍可用，重整會回登入頁 */
  }
}

interface AuthSlice {
  user: AuthUser | null
  token: string | null
  /** 登入請求進行中 */
  submitting: boolean
  /** 上一次登入失敗訊息（顯示在登入頁） */
  error: string | null
  /** 成功 → 更新 user/token；失敗 → 設 error 並 throw（呼叫端可攔） */
  login: (account: string, password: string) => Promise<void>
  logout: () => void
  clearError: () => void
}

const restored = readStored()

export const useAuthStore = create<AuthSlice>((set, get) => ({
  user: restored.user,
  token: restored.token,
  submitting: false,
  error: null,

  login: async (account, password) => {
    set({ submitting: true, error: null })
    try {
      const { token, user } = await authLogin(account, password)
      persist({ user, token })
      setSessionToken(token)
      set({ user, token, submitting: false, error: null })
    } catch (e) {
      set({ submitting: false, error: e instanceof Error ? e.message : '登入失敗，請再試一次' })
      throw e
    }
  },

  logout: () => {
    authLogout(get().token) // 盡力通知後端註銷，不擋前端
    persist(null)
    setSessionToken(null)
    set({ user: null, token: null, error: null })
  },

  clearError: () => set({ error: null }),
}))

// 還原時把 token 交給 api()，並註冊 401 處理：任一支 API 未授權 → 靜默清 session，
// RequireAuth 會把使用者導回 /login。
setSessionToken(restored.token)
setUnauthorizedHandler(() => {
  persist(null)
  setSessionToken(null)
  useAuthStore.setState({ user: null, token: null })
})
