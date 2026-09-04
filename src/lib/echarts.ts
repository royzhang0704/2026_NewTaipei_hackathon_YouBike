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
