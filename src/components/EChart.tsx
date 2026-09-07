import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import type { EChartsOption } from 'echarts'
import { registerCharts } from '@/lib/echarts'

/** echarts/core 的薄 wrapper：mount 時 init，option 變更時 setOption，容器縮放時 resize。
    模組由 registerCharts()（idempotent）在此註冊 —— 整個 echarts 只在這個 chunk 裡，
    StationDetail lazy-load ForecastChart 時才下載，不進主包。 */
export function EChart({
  option,
  className,
  style,
}: {
  option: EChartsOption
  className?: string
  style?: React.CSSProperties
}) {
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!elRef.current) return
    registerCharts()
    const chart = echarts.init(elRef.current, undefined, { renderer: 'svg' })
    chartRef.current = chart
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(elRef.current)
    return () => {
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={elRef} className={className} style={style} />
}
