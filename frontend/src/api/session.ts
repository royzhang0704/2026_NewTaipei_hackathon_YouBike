/* 存取權杖的中繼站 —— 刻意不 import 任何東西，避免 client ↔ authStore 循環相依。
   - useAuthStore 登入 / 登出 / 還原時呼叫 setSessionToken()
   - api() 每次請求讀 getSessionToken() 帶 Authorization header
   - api() 收到 401 呼叫 notifyUnauthorized() → authStore 註冊的 handler 清 session、導登入頁 */

let token: string | null = null
let onUnauthorized: (() => void) | null = null

export const setSessionToken = (t: string | null) => {
  token = t
}
export const getSessionToken = () => token

export const setUnauthorizedHandler = (fn: (() => void) | null) => {
  onUnauthorized = fn
}
export const notifyUnauthorized = () => onUnauthorized?.()
