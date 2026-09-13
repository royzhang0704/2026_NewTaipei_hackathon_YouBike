import { defineConfig, type Plugin } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'

/* maplibre-gl 的地圖渲染跑在 Web Worker，worker 的網址是「執行時」才拼出來的：
 *   const t = url.endsWith('-dev.mjs') ? 'maplibre-gl-worker-dev.mjs' : 'maplibre-gl-worker.mjs'
 *   new URL(`./${t}`, import.meta.url)
 * Vite 只認得字面量寫法（官方：「the URL string must be static so it can be analyzed,
 * otherwise the code will be left as is」https://vite.dev/guide/assets.html），
 * 這種變數組出來的路徑靜態分析看不到 → build 不會輸出那顆檔案。
 *
 * 症狀：dev 正常（dev server 直接從 node_modules 拿），一 build 上線就
 * GET /assets/maplibre-gl-worker.mjs 404 → 地圖整個不渲染。
 * 若前面掛了 SPA 的「404 → /index.html → 200」規則，還會偽裝成
 * 「Failed to load module script: ... MIME type of "text/html"」，更難查。
 *
 * 解法：build 時把 worker 與它 import 的 shared 原封不動複製進 assets/。
 * 兩顆都要 —— worker 檔頭就寫著 import "./maplibre-gl-shared.mjs"。 */
function maplibreWorker(): Plugin {
  const files = ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']
  return {
    name: 'copy-maplibre-worker',
    apply: 'build',
    generateBundle() {
      const require = createRequire(import.meta.url)
      for (const f of files) {
        // 走 package exports 的 "./dist/*"，不要 resolve 套件主入口（它沒有 CJS main）
        this.emitFile({
          type: 'asset',
          fileName: `assets/${f}`,
          source: readFileSync(require.resolve(`maplibre-gl/dist/${f}`)),
        })
      }
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), maplibreWorker()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  // maplibre-gl 自帶 web worker；Vite 預打包（含透過 react-map-gl 間接引入）會弄壞它
  // → 地圖空白 / tile「could not be decoded」。三個一起排除才乾淨。
  optimizeDeps: { exclude: ['maplibre-gl', 'react-map-gl', '@vis.gl/react-maplibre'] },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
})
