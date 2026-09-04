import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import { AppShell } from './app/AppShell'

const DashboardPage = lazy(() => import('./features/dashboard/DashboardPage'))
const StationPage = lazy(() => import('./features/station/StationPage'))

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'station/:uid?', element: <StationPage /> },
      { path: '*', element: <DashboardPage /> },
    ],
  },
]
