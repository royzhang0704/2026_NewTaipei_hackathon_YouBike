import { useMemo } from 'react'
import { useMediaQuery } from 'usehooks-ts'
import type { EChartsOption } from 'echarts'
import type { StationDay } from '@/api/types'
import { hhmm } from '@/lib/format'
import { FONT_PCT, useAppStore } from '@/stores/useAppStore'
import { EChart } from '@/components/EChart'

/* 過去 9h 實況（實線，缺格斷線）＋ 未來 3h 預測（q50 橘虛線 + q19–q90 區間帶）。
   對答案時疊上真實值（金色粗線）。門檻線與「現在」分隔線用 markLine。 */

type Palette = ReturnType<typeof palette>

/* echarts option 為 JS 物件，無法取得 CSS 變數，故切換主題時重新讀取 documentElement 的
   computed style（ForecastChart 在會隨主題切換的右欄，用 root 主題正確）。 */
function palette() {
  const s = getComputedStyle(document.documentElement)
  const v = (n: string, fb: string) => s.getPropertyValue(n).trim() || fb
  return {
    ink: v('--color-ink', '#E9EBEE'),
    hot: v('--color-hot', '#EE5A34'),
    cold: v('--color-cold', '#5591F2'),
    gold: v('--color-gold', '#C99A3B'),
    ink3: v('--color-ink3', '#868C96'),
    grid: v('--color-hair', 'rgba(255,255,255,.10)'),
    axis: v('--color-edge', 'rgba(255,255,255,.2)'),
    panel: v('--color-panel', '#13161B'),
    edge: v('--color-edge', 'rgba(255,255,255,.16)'),
  }
}

function buildOption(
  d: StationDay,
  showTruth: boolean,
  C: Palette,
  fs: number,
  animate: boolean,
): EChartsOption {
  // echarts 的字級是 JS 數字、不吃 rem，所以照文字大小設定手動乘上去
  // 基準 10 / 12（軸標原本 9 偏小，跟全站小字階梯一起抬）
  const f9 = Math.round(10 * fs)
  const f12 = Math.round(12 * fs)
  const act = d.actual
  const fc = d.forecast
  const truth = d.truth ?? []
  const cap = d.station.capacity
  const T = d.risk?.threshold ?? null
  const a0 = act.length - 1
  const anchorVal = act[a0]?.avail ?? null

  const xLabels = [...act.map((p) => p.at), ...fc.map((p) => p.at)]

  const actualData = xLabels.map((_, i) => (i < act.length ? act[i].avail : null))
  const q50Data = xLabels.map((_, i) => {
    if (i === a0) return anchorVal
    if (i > a0) return fc[i - a0 - 1]?.q50 ?? null
    return null
  })
  const bandBase = xLabels.map((_, i) => {
    if (i === a0) return anchorVal
    if (i > a0) return fc[i - a0 - 1]?.q19 ?? null
    return null
  })
  const bandDelta = xLabels.map((_, i) => {
    if (i === a0) return 0
    if (i > a0) {
      const p = fc[i - a0 - 1]
      return p ? p.q90 - p.q19 : null
    }
    return null
  })
  const truthData = xLabels.map((_, i) => {
    if (i === a0) return anchorVal
    if (i > a0) return truth[i - a0 - 1]?.avail ?? null
    return null
  })

  /* 自適應 y 軸（業界慣例：監控時序預設 auto，但加護欄）：
     - 下限永遠 0（數量指標，浮動基線會把小波動放大成假訊號）
     - 上限 = ceil(max(資料, 低水位) × 1.35)，取整、封頂在容量
     - 高水位若落在可視範圍外 → 不硬撐 y 軸，改在頂端釘「高水位 85% ↑」
     小站（整天貼底）的圖因此放大到有意義的區間，不再是一條扁線。 */
  const dataMax = Math.max(
    anchorVal ?? 0,
    ...act.map((p) => p.avail ?? 0),
    ...fc.map((p) => p.q90),
    1,
  )
  const niceCeil = (x: number) => {
    const step = x <= 25 ? 5 : x <= 100 ? 10 : 25
    return Math.ceil(x / step) * step
  }
  let yMax: number | undefined
  let highOnEdge = false
  if (cap && T != null) {
    // y 軸貼著「資料 + 低水位」，不為了塞高水位而撐高（那會製造大片空白、且讓
    // 操作相似的站長得完全不同）。高水位線只在它本來就落在這範圍內時才畫。
    yMax = Math.min(cap, niceCeil(Math.max(dataMax, T) * 1.35 + 1))
    highOnEdge = cap - T > yMax // 資料離滿站遠 → 只在角落留一行備註、不畫線
  }

  const markLineData: Record<string, unknown>[] = [
    // 「現在」＝過去 / 未來的分界。值標在垂直線「頂端」——錨點那個路口太擠
    // （實況尾、預測頭、分界線、預測帶起點全在那），放上下左右都會撞。
    {
      xAxis: a0,
      lineStyle: { color: C.ink3, type: 'dashed', width: 1, opacity: 0.8 },
      label: {
        show: true,
        formatter: `現在 ${anchorVal ?? '—'}`,
        position: 'end',
        color: C.ink,
        fontSize: f9,
        fontWeight: 'bold',
      },
    },
  ]
  if (cap && T != null) {
    // 門檻線 label 用台數（跟 y 軸同單位、跟業界一致）；「= 容量 15%／85%」的規則
    // 在 StationDetail 上方 meta 行講一次，不在每條線重複。
    markLineData.push({
      yAxis: T,
      lineStyle: { color: C.hot, type: 'dashed', width: 0.8, opacity: 0.55 },
      label: { show: true, formatter: `低水位 ${T} 台`, color: C.hot, fontSize: f9, position: 'end' },
    })
    if (highOnEdge) {
      // 高水位遠在可視範圍外 → 不當門檻線畫（貼在某條 y 刻度旁會被誤讀成「那條線」）。
      // 掛在頂端 y 刻度的「隱形水平線」上、label position:'end' → 跟正常「高水位 N 台」同一種
      // 排版（水平、在右邊距），只是釘在圖頂 + 加「↑」表示真值更高。
      markLineData.push({
        yAxis: yMax,
        lineStyle: { opacity: 0 },
        label: {
          show: true,
          formatter: `高水位 ${cap - T} 台 ↑`,
          color: C.cold,
          fontSize: f9,
          opacity: 0.85,
          position: 'end',
          // 往上推離頂端刻度，浮在 plot 上方 → 不會被當成「最上面那條線 = 高水位」
          offset: [0, -14],
        },
      })
    } else {
      markLineData.push({
        yAxis: cap - T,
        lineStyle: { color: C.cold, type: 'dashed', width: 0.8, opacity: 0.55 },
        label: { show: true, formatter: `高水位 ${cap - T} 台`, color: C.cold, fontSize: f9, position: 'end' },
      })
    }
  }


  /* ② 危險區淡色：低水位以下 = 缺車危險、高水位以上 = 滿站危險（高水位線有畫才蓋）。
     極淡（0.07），僅作背景線索；使曲線是否進入危險帶一目了然，且不干擾預測帶（0.2）。 */
  const dangerArea =
    cap && T != null
      ? {
          silent: true,
          data: [
            [{ yAxis: 0, itemStyle: { color: C.hot, opacity: 0.07 } }, { yAxis: T }],
            ...(highOnEdge
              ? []
              : [
                  [
                    { yAxis: cap - T, itemStyle: { color: C.cold, opacity: 0.07 } },
                    { yAxis: yMax as number },
                  ],
                ]),
          ],
        }
      : undefined

  const series: Record<string, unknown>[] = [
    {
      name: 'band-base',
      type: 'line',
      data: bandBase,
      stack: 'band',
      // q19 下緣：極淡實線描邊，讓「可能範圍」看得出邊界
      lineStyle: { color: C.hot, width: 0.8, opacity: 0.3 },
      areaStyle: { opacity: 0 },
      symbol: 'none',
      silent: true,
      z: 1,
      markArea: dangerArea,
    },
    {
      name: 'band',
      type: 'line',
      data: bandDelta,
      stack: 'band',
      lineStyle: { color: C.hot, width: 0.8, opacity: 0.3 }, // q90 上緣描邊
      areaStyle: { color: C.hot, opacity: 0.2 },
      symbol: 'none',
      silent: true,
      z: 1,
    },
    {
      name: '實況',
      type: 'line',
      data: actualData,
      connectNulls: false,
      showSymbol: false,
      lineStyle: { color: C.ink, width: 1.6 },
      itemStyle: { color: C.ink },
      z: 5,
      markLine: { symbol: 'none', silent: true, data: markLineData },
    },
    {
      name: '預測 q50',
      type: 'line',
      // 錨點（index a0＝「現在」）那格帶 per-point 樣式：做大、實況同色 ink（不標字，
      // 值放在垂直線頂端；標在這個擁擠路口一定被預測線 / 帶擋到）
      data: q50Data.map((v, i) =>
        i === a0 && v != null
          ? {
              value: v,
              symbolSize: 10,
              itemStyle: { color: C.ink, borderColor: C.panel, borderWidth: 2 },
            }
          : v,
      ),
      connectNulls: false,
      showSymbol: true,
      symbolSize: 5,
      lineStyle: { color: C.hot, width: 1.8, type: 'dashed' },
      itemStyle: { color: C.hot },
      z: 6,
    },
  ]

  if (showTruth && truth.length) {
    series.push({
      name: '實際',
      type: 'line',
      data: truthData,
      connectNulls: false,
      showSymbol: true,
      symbolSize: 6,
      lineStyle: { color: C.gold, width: 5, opacity: 0.35 },
      itemStyle: { color: C.gold, opacity: 0.55 },
      z: 3,
    })
  }

  return {
    // 換站時 StationDetail 重掛 → EChart 重新 init → 首次 setOption 播「初始」動畫（線條描繪）。
    // animationDurationUpdate: 0 → 同站的 theme / 字級 / 回放 tick 更新一律瞬間、不重播。
    animation: animate,
    animationDuration: 480,
    animationDurationUpdate: 0,
    animationEasing: 'cubicOut',
    // 邊距隨字級縮放：軸標 / 右側水位 label 在「大」檔會變寬，固定值會被切。
    // right 抓「低水位 NN 台」（高水位邊界那條改釘 insideEndTop、不進 margin）→ md 72、大 ~90。
    grid: {
      left: Math.round(34 * fs),
      right: Math.round(84 * fs), // 容得下最長的「高水位 NN 台 ↑」
      top: Math.round(28 * fs), // 頂端 headroom：容「現在 N」＋往上推的「高水位 ↑」
      bottom: Math.round(24 * fs),
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: C.panel,
      borderColor: C.edge,
      textStyle: { color: C.ink, fontSize: f12 },
      // 十字準星：游標掃過去即時對到時間 + 值（桌機後台用滑鼠讀值的主要手段）
      axisPointer: {
        type: 'line',
        lineStyle: { color: C.ink3, width: 1, type: 'dashed' },
        label: { show: true, backgroundColor: C.ink3, color: C.panel, fontSize: f9, formatter: (o: { value: string }) => hhmm(o.value) },
      },
      formatter: (params: unknown) => {
        const p = Array.isArray(params) ? params[0] : params
        const i = (p as { dataIndex?: number })?.dataIndex
        if (i == null) return ''
        const at = hhmm(xLabels[i])
        if (i < act.length) {
          const a = act[i]
          if (a.avail == null) return `${at}　此格無觀測`
          return `${at}　${a.carried ? '延用前值' : '實測'} <b>${a.avail}</b> 台`
        }
        const f = fc[i - act.length]
        if (!f) return at
        let s = `${at}　預測 <b>${f.q50}</b> 台<br>區間 ${f.q19} – ${f.q90}`
        if (showTruth) {
          const tv = truth[i - act.length]?.avail
          if (tv != null) {
            const err = f.q50 - tv
            s += `<br><span style="color:${C.gold}">實際 ${tv} 台　誤差 ${err > 0 ? '+' : ''}${err}${err === 0 ? '（命中）' : ''}</span>`
          }
        }
        return s
      },
    },
    xAxis: {
      type: 'category',
      data: xLabels,
      boundaryGap: false,
      axisLine: { lineStyle: { color: C.axis } },
      axisTick: { show: false },
      axisLabel: {
        color: C.ink3,
        fontSize: f9,
        // 每格挑一個 + hideOverlap：大字級 label 變寬，固定 %4 會黏在一起 → 讓 echarts 再自動剔掉相撞的
        interval: (idx: number) => idx % Math.round(4 * fs) === 0,
        hideOverlap: true,
        formatter: (v: string) => hhmm(v),
      },
    },
    yAxis: {
      type: 'value',
      // 不放軸名：單位「台」從上方大數字 / 風險卡 / 水位線已經很清楚，放這只是讓圖頂更擠
      min: 0,
      max: yMax, // 自適應（見上方）；無 cap/T 時 undefined = echarts auto
      // yMax 一定是 5 或 10 的倍數 → 給能整除的 interval，刻度才乾淨（不會 0/3/6/9/10）
      interval:
        yMax == null ? undefined : yMax <= 5 ? 1 : yMax <= 10 ? 2 : yMax <= 25 ? 5 : 10,
      splitLine: { lineStyle: { color: C.grid } },
      axisLabel: { color: C.ink3, fontSize: f9 },
    },
    series,
  } as EChartsOption
}

/* echarts 畫的是 SVG，對螢幕報讀者是空白。用 day 的數字組一句摘要，
   套在 role="img" 容器上 → 看不到圖的人也拿得到「走勢 + 會不會越線」。 */
function summarize(day: StationDay): string {
  const fc = day.forecast
  if (!fc.length) return day.forecast_missing || '此站尚無批次預測'

  const act = day.actual
  const start = act[act.length - 1]?.avail ?? null
  const endQ50 = fc[fc.length - 1].q50
  const lo = Math.min(...fc.map((p) => p.q19))
  const hi = Math.max(...fc.map((p) => p.q90))
  const t = day.risk?.threshold ?? null
  const cap = day.station.capacity ?? null

  const dir =
    start == null ? '' : endQ50 > start ? '上升' : endQ50 < start ? '下降' : '持平'

  let cross = ''
  if (t != null) {
    const short = fc.find((p) => p.q19 <= t)
    const surplus = cap != null ? fc.find((p) => p.q90 >= cap - t) : undefined
    if (short) cross = `預測約 ${hhmm(short.at)} 可借量觸及缺車門檻 ${t} 台。`
    else if (surplus) cross = `預測約 ${hhmm(surplus.at)} 觸及滿站門檻。`
    else cross = '預測期間不會缺車或滿站。'
  }

  return (
    `可借車輛預測，未來約三小時。` +
    `起點 ${start ?? '不明'} 台，預測中位數至 ${endQ50} 台${dir ? `（${dir}）` : ''}。` +
    `可能區間 ${lo} 至 ${hi} 台。${cross}`
  )
}

export function ForecastChart({ day, showTruth = false }: { day: StationDay; showTruth?: boolean }) {
  const theme = useAppStore((s) => s.theme)
  const fontScale = useAppStore((s) => s.fontScale)
  const reduce = useMediaQuery('(prefers-reduced-motion: reduce)')
  // theme / fontScale 進 deps：切主題時 palette() 重讀 CSS 變數，切字級時重算 echarts 字級
  const option = useMemo(
    () => buildOption(day, showTruth, palette(), FONT_PCT[fontScale], !reduce),
    [day, showTruth, theme, fontScale, reduce],
  )
  const label = useMemo(() => summarize(day), [day])
  return (
    <div role="img" aria-label={label}>
      <EChart option={option} style={{ height: 240, width: '100%' }} />
    </div>
  )
}
