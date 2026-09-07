import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import { RequireAuth } from './features/auth/RequireAuth'
import { LoginPage } from './features/auth/LoginPage'

const DashboardPage = lazy(() => import('./features/dashboard/DashboardPage'))

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: <RequireAuth />, // 未登入 → /login；登入後渲染 AppShell
    children: [
      { index: true, element: <DashboardPage /> },
      { path: '*', element: <DashboardPage /> },
    ],
  },
]
