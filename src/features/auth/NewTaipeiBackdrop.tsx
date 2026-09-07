import { useMemo, useState } from 'react'
import districtsGeo from '@/assets/newtaipei-districts.json'
import outlineGeo from '@/assets/newtaipei-outline.json'

/* 登入頁背景：新北市 29 行政區輪廓（線性投影，非 Mercator —— 範圍小、作裝飾足夠）。
   動態：一次性 draw-on（描出輪廓）＋ 持續型（市界光點巡邏、整體極慢漂移、隨機站點 ping）。
   ping 每次循環結束重新隨機取一個落在行政區內的點位與大小。純裝飾、aria-hidden；
   減少動態時只保留靜態輪廓。 */

const VB_W = 1000
const VB_H = 866
const DRAW_S = 2.8 // 對齊 index.css 的 ntp-draw 時長：畫完地圖線才啟動其他動畫
const PING_COUNT = 7

interface Feat {
  geometry: { type: string; coordinates: number[][][] | number[][][][] }
}

const polysOf = (f: Feat): number[][][][] =>
  (f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates) as number[][][][]

/** 攤平所有 ring（含孔）—— 給描邊路徑用 */
function allRings(fc: { features: Feat[] }): number[][][] {
  const out: number[][][] = []
  for (const f of fc.features) for (const poly of polysOf(f)) for (const ring of poly) out.push(ring as number[][])
  return out
}

/** 每個 polygon 的外環 —— 給「點是否落在陸地」判定用 */
function outerRings(fc: { features: Feat[] }): number[][][] {
  const out: number[][][] = []
  for (const f of fc.features) for (const poly of polysOf(f)) out.push(poly[0] as number[][])
  return out
}

function pointInRing(x: number, y: number, ring: number[][]): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0]
    const yi = ring[i][1]
    const xj = ring[j][0]
    const yj = ring[j][1]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

function build() {
  const districtsFc = districtsGeo as unknown as { features: Feat[] }
  const districtRings = allRings(districtsFc)
  const outlineRings = allRings(outlineGeo as unknown as { features: Feat[] })

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

  // 投影後的陸地外環 + 各自的 bbox（隨機取點時先在 bbox 內取樣，再判是否落在環內）
  const land = outerRings(districtsFc).map((ring) => {
    const r = ring.map((pt) => [px(pt[0]), py(pt[1])] as [number, number])
    let a = Infinity
    let b = Infinity
    let c = -Infinity
    let d = -Infinity
    for (const [x, y] of r) {
      if (x < a) a = x
      if (x > c) c = x
      if (y < b) b = y
      if (y > d) d = y
    }
    return { r, bb: [a, b, c, d] as const }
  })

  return { districts: toPath(districtRings), outline: toPath(outlineRings), land }
}

// 中央卡片的概略 footprint（viewBox 座標）—— 避免 ping 落在卡片後方看不到
const inCard = (x: number, y: number) => x > 336 && x < 664 && y > 248 && y < 608

type Land = ReturnType<typeof build>['land']

function randPoint(land: Land): { cx: number; cy: number } {
  for (let t = 0; t < 240; t++) {
    const cell = land[(Math.random() * land.length) | 0]
    const [a, b, c, d] = cell.bb
    const x = a + Math.random() * (c - a)
    const y = b + Math.random() * (d - b)
    if (!inCard(x, y) && pointInRing(x, y, cell.r)) return { cx: +x.toFixed(1), cy: +y.toFixed(1) }
  }
  return { cx: 470, cy: 150 }
}

const randSize = () => +(3 + Math.random() * 6).toFixed(1) // 峰值半徑 3–9
const randDur = () => +(3.2 + Math.random() * 1.8).toFixed(2) // 3.2–5s

interface Ping {
  cx: number
  cy: number
  size: number
  dur: number
}

export function NewTaipeiBackdrop() {
  const { districts, outline, land } = useMemo(build, [])
  const [pings, setPings] = useState<Ping[]>(() =>
    Array.from({ length: PING_COUNT }, () => ({ ...randPoint(land), size: randSize(), dur: randDur() })),
  )

  // 每個 ping 一個循環結束（此時 opacity ≈ 0）就換位置與大小；dur / delay 固定不動，避免動畫重置
  const repick = (i: number) =>
    setPings((prev) => {
      const next = prev.slice()
      next[i] = { ...next[i], ...randPoint(land), size: randSize() }
      return next
    })

  return (
    <svg
      aria-hidden
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      preserveAspectRatio="xMidYMid meet"
      className="ntp-backdrop pointer-events-none absolute inset-0 h-full w-full"
    >
      <g className="ntp-drift">
        {/* 行政區線：靜態結構，載入時描出來 */}
        <g fill="none" stroke="var(--color-edge)" strokeWidth={1} strokeLinejoin="round">
          {districts.map((d, i) => (
            <path key={i} d={d} pathLength={1} className="ntp-path" />
          ))}
        </g>

        {/* 市界：一段短亮 dash 沿邊界繞圈。畫完地圖線才開始跑 */}
        <g fill="none" stroke="var(--color-hot)" strokeWidth={1.6} strokeLinecap="round">
          {outline.map((d, i) => (
            <path key={i} d={d} className="ntp-flow" style={{ animationDelay: `${DRAW_S + i * 1.2}s` }} />
          ))}
        </g>

        {pings.map((p, i) => (
          <circle
            key={i}
            cx={p.cx}
            cy={p.cy}
            r={1}
            fill="var(--color-hot)"
            className="ntp-ping"
            onAnimationIteration={() => repick(i)}
            style={
              {
                animationDelay: `${DRAW_S + i * 0.7}s`,
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
