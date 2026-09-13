import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/useAuthStore'
import { AppShell } from '@/app/AppShell'

/** 路由守衛：未登入 → 導去 /login（記住原本要去的位置，登入後帶回）。 */
export function RequireAuth() {
  const user = useAuthStore((s) => s.user)
  const loc = useLocation()
  if (!user) return <Navigate to="/login" replace state={{ from: loc }} />
  return <AppShell />
}
