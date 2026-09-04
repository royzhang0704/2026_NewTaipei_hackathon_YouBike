import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
      '/healthz': { target: API_TARGET, changeOrigin: true },
    },
  },
})
