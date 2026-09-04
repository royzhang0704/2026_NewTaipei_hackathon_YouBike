import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import type { StationDay } from '@/api/types'
import { hhmm } from '@/lib/format'
import { EChart } from '@/components/EChart'

/* 過去 9h 實況（實線，缺格斷線）＋ 未來 3h 預測（q50 橘虛線 + q19–q90 區間帶）。
   對答案時疊上真實值（金色粗線）。門檻線與「現在」分隔線用 markLine。 */

const C = {
  ink: '#E9EBEE',
  hot: '#EE5A34',
  cold: '#5591F2',
  gold: '#C99A3B',
  ink3: '#6E747E',
  grid: 'rgba(255,255,255,.10)',
}

function buildOption(d: StationDay, showTruth: boolean): EChartsOption {
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

  const markLineData: Record<string, unknown>[] = [
    {
      xAxis: a0,
      lineStyle: { color: 'rgba(255,255,255,.35)', type: 'dashed', width: 1 },
      label: { show: true, formatter: '現在', color: C.ink3, fontSize: 9, position: 'insideEndTop' },
    },
  ]
  if (cap && T) {
    markLineData.push(
      {
        yAxis: T,
        lineStyle: { color: C.hot, type: 'dashed', width: 0.8, opacity: 0.55 },
        label: { show: true, formatter: '15％', color: C.hot, fontSize: 9, position: 'start' },
      },
      {
        yAxis: cap - T,
        lineStyle: { color: C.cold, type: 'dashed', width: 0.8, opacity: 0.55 },
        label: { show: true, formatter: '85％', color: C.cold, fontSize: 9, position: 'start' },
      },
    )
  }

  const series: Record<string, unknown>[] = [
    {
      name: 'band-base',
      type: 'line',
      data: bandBase,
      stack: 'band',
      lineStyle: { opacity: 0 },
      areaStyle: { opacity: 0 },
      symbol: 'none',
      silent: true,
      z: 1,
    },
    {
      name: 'band',
      type: 'line',
      data: bandDelta,
      stack: 'band',
      lineStyle: { opacity: 0 },
      areaStyle: { color: C.hot, opacity: 0.14 },
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
      data: q50Data,
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
    animation: false,
    grid: { left: 34, right: 14, top: 16, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#13161B',
      borderColor: 'rgba(255,255,255,.16)',
      textStyle: { color: C.ink, fontSize: 12 },
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
      axisLine: { lineStyle: { color: 'rgba(255,255,255,.2)' } },
      axisTick: { show: false },
      axisLabel: {
        color: C.ink3,
        fontSize: 9,
        interval: (idx: number) => idx % 4 === 0,
        formatter: (v: string) => hhmm(v),
      },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: cap ? Math.max(cap, ...fc.map((p) => p.q90)) : undefined,
      splitLine: { lineStyle: { color: C.grid } },
      axisLabel: { color: C.ink3, fontSize: 9 },
    },
    series,
  } as EChartsOption
}

export function ForecastChart({ day, showTruth = false }: { day: StationDay; showTruth?: boolean }) {
  const option = useMemo(() => buildOption(day, showTruth), [day, showTruth])
  return <EChart option={option} style={{ height: 240, width: '100%' }} />
}
