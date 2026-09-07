import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import { AppShell } from './app/AppShell'

const DashboardPage = lazy(() => import('./features/dashboard/DashboardPage'))

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: '*', element: <DashboardPage /> },
    ],
  },
]
