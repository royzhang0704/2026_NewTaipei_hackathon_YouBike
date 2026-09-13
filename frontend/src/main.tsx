import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import './styles/index.css'
import { routes } from './routes'

// 主題 data-theme 由 index.html 的 inline script 在 first paint 前寫好（避免白色閃爍）；
// store 的 readInitialTheme() 直接讀它。
// echarts 改成「選站看 ForecastChart」時才動態載入（見 EChart / StationDetail），不進主包。

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

const router = createBrowserRouter(routes)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
