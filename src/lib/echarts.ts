/* ECharts 6 按需註冊：只掛折線圖用得到的模組，縮小打包體積。 */
import { use } from 'echarts/core'
import { LineChart, CustomChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkAreaComponent,
  LegendComponent,
  DataZoomComponent,
} from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'

let done = false
export function registerCharts() {
  if (done) return
  // 這裡的 use 是 echarts/core 的模組註冊，不是 React 的 use hook —— lint 誤判
  // oxlint-disable-next-line react-hooks/rules-of-hooks
  use([
    LineChart,
    CustomChart,
    GridComponent,
    TooltipComponent,
    MarkLineComponent,
    MarkAreaComponent,
    LegendComponent,
    DataZoomComponent,
    SVGRenderer,
  ])
  done = true
}
