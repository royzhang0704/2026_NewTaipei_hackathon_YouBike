import { useMemo } from 'react'
import districtsGeo from '@/assets/newtaipei-districts.json'
import outlineGeo from '@/assets/newtaipei-outline.json'

/* 登入頁背景：新北市 29 行政區輪廓（線性投影，非 Mercator —— 範圍小、當裝飾夠了）。
   動態：一次性 draw-on（描出輪廓）＋ 持續型（市界光點巡邏、整體極慢漂移、站點 ping）。
   純裝飾、aria-hidden；減少動態時只保留靜態輪廓。 */

const VB_W = 1000
const VB_H = 866
// 對齊 index.css 的 ntp-draw 時長：畫完地圖線才啟動 ping / 市界光點
const DRAW_S = 2.8

interface Feat {
  geometry: { type: string; coordinates: number[][][] | number[][][][] }
}

/** 把一組 GeoJSON feature 的所有 ring 攤平出來 */
function collectRings(fc: { features: Feat[] }): number[][][] {
  const rings: number[][][] = []
  for (const f of fc.features) {
    const polys = (
      f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates
    ) as number[][][][]
    for (const poly of polys) for (const ring of poly) rings.push(ring as number[][])
  }
  return rings
}

function build() {
  const districtRings = collectRings(districtsGeo as unknown as { features: Feat[] })
  const outlineRings = collectRings(outlineGeo as unknown as { features: Feat[] })

  // bbox 用行政區那份（較密、較準），市界共用同一個投影才對得齊
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const ring of districtRings)
    for (const pt of ring) {
      if (pt[0] < minX) minX = pt[0]
      if (pt[0] > maxX) maxX = pt[0]
      if (pt[1] < minY) minY = pt[1]
      if (pt[1] > maxY) maxY = pt[1]
    }

  const pad = 26
  const s = Math.min((VB_W - pad * 2) / (maxX - minX), (VB_H - pad * 2) / (maxY - minY))
  const ox = (VB_W - (maxX - minX) * s) / 2
  const oy = (VB_H - (maxY - minY) * s) / 2
  const px = (x: number) => ox + (x - minX) * s
  const py = (y: number) => VB_H - (oy + (y - minY) * s) // 螢幕 Y 往下 → 翻轉

  const toPath = (rings: number[][][]) =>
    rings.map((ring) => {
      let d = ''
      ring.forEach((pt, i) => {
        d += `${i ? 'L' : 'M'}${px(pt[0]).toFixed(1)} ${py(pt[1]).toFixed(1)}`
      })
      return `${d}Z`
    })

  return { districts: toPath(districtRings), outline: toPath(outlineRings) }
}

// ★ 座標＝各行政區在此投影下的實際質心（已逐點驗證落在該區多邊形內），
//   避開中央卡片 footprint，周邊分散。size（擴散峰值半徑）/ dur / delay 錯開 → 有大有小、不同步。
const PINGS = [
  { cx: 265, cy: 170, delay: 0.0, dur: 3.6, size: 5 }, // 淡水區
  { cx: 448, cy: 134, delay: 1.4, dur: 3.9, size: 3.5 }, // 金山區（小）
  { cx: 732, cy: 288, delay: 0.8, dur: 4.4, size: 7 }, // 瑞芳區（大）
  { cx: 855, cy: 383, delay: 2.6, dur: 3.7, size: 4 }, // 貢寮區
  { cx: 258, cy: 400, delay: 3.4, dur: 4.6, size: 8.5 }, // 板橋區（大）
  { cx: 203, cy: 569, delay: 4.6, dur: 3.8, size: 4.5 }, // 三峽區
  { cx: 366, cy: 691, delay: 5.4, dur: 4.2, size: 5.5 }, // 烏來區（地圖最南，穩定在卡片下方）
]

export function NewTaipeiBackdrop() {
  const { districts, outline } = useMemo(build, [])

  return (
    <svg
      aria-hidden
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      preserveAspectRatio="xMidYMid meet"
      className="ntp-backdrop pointer-events-none absolute inset-0 h-full w-full"
    >
      {/* 整體極慢漂移，讓靜止畫面「活著」 */}
      <g className="ntp-drift">
        {/* 行政區線：靜態結構，載入時描出來 */}
        <g fill="none" stroke="var(--color-edge)" strokeWidth={1} strokeLinejoin="round">
          {districts.map((d, i) => (
            <path key={i} d={d} pathLength={1} className="ntp-path" />
          ))}
        </g>

        {/* 市界：疊一條會「跑光點」的線 —— 一段短亮 dash 沿著邊界繞圈。畫完地圖線才開始跑 */}
        <g fill="none" stroke="var(--color-hot)" strokeWidth={1.6} strokeLinecap="round">
          {outline.map((d, i) => (
            <path key={i} d={d} className="ntp-flow" style={{ animationDelay: `${DRAW_S + i * 1.2}s` }} />
          ))}
        </g>

        {PINGS.map((p, i) => (
          <circle
            key={i}
            cx={p.cx}
            cy={p.cy}
            r={1}
            fill="var(--color-hot)"
            className="ntp-ping"
            style={
              {
                animationDelay: `${DRAW_S + p.delay}s`,
                animationDuration: `${p.dur}s`,
                '--ping-max': `${p.size}`,
              } as React.CSSProperties
            }
          />
        ))}
      </g>
    </svg>
  )
}
