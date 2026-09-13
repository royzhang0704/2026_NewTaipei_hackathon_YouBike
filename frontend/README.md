# 調度中心 — 前端（React）

YouBike 營運調度預測平台。**React 19 + Vite + TypeScript**。

## 跑起來

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173，/api 由 Vite proxy 轉到 127.0.0.1:8000
npm run build          # tsc -b + vite build → dist/
```

後端先啟動。換位址：`VITE_API_TARGET=http://x:8000 npm run dev`，或執行時在頂欄 API 欄填絕對網址。
地圖底圖：預設 NLSC（國土測繪中心）。`VITE_BASEMAP=none` 可只留深色底 + 站點。

## 技術棧

| 用途 | 套件 |
|---|---|
| 路由 | react-router-dom v7 |
| Server state | @tanstack/react-query |
| Client state | zustand（slice 模式，`stores/useAppStore.ts`） |
| 地圖 | react-map-gl/maplibre（宣告式 `<Source>`/`<Layer>`）+ maplibre-gl |
| 圖表 | echarts-for-react（core build，按需註冊） |
| UI primitive | @radix-ui/react-popover + cmdk（shadcn 式 Combobox） |
| 樣式 | Tailwind v4（`@tailwindcss/vite`），token 在 `styles/index.css @theme` |
| icon | lucide-react |
| 小工具 | usehooks-ts |

## 結構

```
src/
  main.tsx / routes.tsx        providers + 路由（/login、/）
  app/AppShell.tsx             頂欄（伺服器時鐘 / 主題切換）+ <Outlet/>
  features/
    auth/
      LoginPage.tsx  RequireAuth.tsx  UserMenu.tsx   登入（VITE_AUTH_MODE = mock | live）
    dashboard/
      DashboardPage.tsx        / —— KPI + 地圖 + 警示 + 單站詳情
      KpiStrip.tsx  AlertList.tsx  DistrictPicker.tsx  OverviewCaption.tsx
      CityMap/
        CityMap.tsx            react-map-gl 宣告式
        layers.ts              圖層樣式 + 底圖 style（含調度路線）
        mask.ts                行政區聚光燈遮罩
        useDistrictFocus.ts    換區對焦（區多邊形 / 站點分布 邊界框）
    station/
      StationDetail.tsx  ForecastChart.tsx  StationSearch.tsx
    assistant/
      AssistantWidget.tsx      調度助理對話（SSE）
      DispatchCard.tsx  DispatchModal.tsx   調度摘要列 / 下單視窗
  components/
    PredictingOverlay.tsx      換輪全頁遮罩
    EChart.tsx  ErrorBoundary.tsx  ShortcutsHelp.tsx
    ui/ Combobox.tsx  segChip.ts
  hooks/  useServerClock.ts  useSlotSync.ts  useUrlSync.ts
  api/    client.ts  types.ts  queryKeys.ts  queries.ts  session.ts
  stores/ useAppStore.ts  useAssistantStore.ts  useAuthStore.ts
  lib/    risk.ts  format.ts  echarts.ts  utils.ts(cn)
  assets/ newtaipei-districts.json  newtaipei-outline.json   （g0v 開放資料）
  styles/ index.css
```

## 擴充點

- **features/ 按功能切**：加新功能 = 新資料夾
- **useAppStore slice**：state 長大就多一個 slice，不動舊的
- **CityMap/layers.ts**：圖層當資料，加一層只是多一個 entry + 一個 `<Layer>`
- **api/queryKeys.ts**：快取 key 集中，endpoint 變多不亂
- **api/client.ts**：唯一 fetch 出入口，之後換 OpenAPI 產生的 client 不動呼叫端

## 待辦

一日回放時間軸。
