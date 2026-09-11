# 調度中心 — 前端（React）

YouBike 營運調度預測平台。**React 19 + Vite + TypeScript**。
（`../frontend/` 是先前的 Vue 版，留著對照，對齊後可刪。）

## 跑起來

```bash
cd web
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
  main.tsx / routes.tsx        providers + 路由
  app/AppShell.tsx             頂欄（健康輪詢時鐘 / API 欄）+ <Outlet/>
  features/
    dashboard/
      DashboardPage.tsx        / —— KPI + 地圖 + 警示 + 單站詳情
      KpiStrip.tsx  AlertList.tsx
      CityMap/
        CityMap.tsx            react-map-gl 宣告式
        layers.ts              圖層樣式 + 底圖 style（加路線層 = 加一組）
        useDistrictFocus.ts    換區對焦（區多邊形 / 站點分布 邊界框）
    station/
      StationPage.tsx          /station/:uid
      StationPicker.tsx  StationDetail.tsx  ForecastChart.tsx
  components/ui/Combobox.tsx   Radix Popover + cmdk 泛用下拉
  api/    client.ts  types.ts  queryKeys.ts  queries.ts
  stores/ useAppStore.ts
  lib/    risk.ts  format.ts  echarts.ts  utils.ts(cn)
  assets/ newtaipei-districts.json   （g0v 開放資料，簡化 ~62KB）
  styles/ index.css
```

`api/client.ts`、`api/types.ts`、`lib/risk.ts`、`lib/format.ts`、`lib/echarts.ts` 與 Vue 版共用同一份（框架無關，直接搬）。

## 擴充點

- **features/ 按功能切**：加「調度助理」「一日回放」「路線規劃」= 新資料夾
- **useAppStore slice**：state 長大就多一個 slice，不動舊的
- **CityMap/layers.ts**：圖層當資料，加派工路線只是多一個 entry + 一個 `<Layer>`
- **api/queryKeys.ts**：快取 key 集中，endpoint 變多不亂
- **api/client.ts**：唯一 fetch 出入口，之後換 OpenAPI 產生的 client 不動呼叫端

## 待辦

調度助理面板（接 `/ask`）、一日回放時間軸、調度路線圖層、淺色主題。
