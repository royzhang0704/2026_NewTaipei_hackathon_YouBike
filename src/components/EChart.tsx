import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import type { EChartsOption } from 'echarts'

/** echarts/core 的薄 wrapper：mount 時 init，option 變更時 setOption，容器縮放時 resize。
    模組（LineChart / GridComponent / SVGRenderer…）在 lib/echarts.ts 統一註冊。 */
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
