import type { Feature, FeatureCollection, Polygon } from 'geojson'
import districtsGeo from '@/assets/newtaipei-districts.json'
import outlineGeo from '@/assets/newtaipei-outline.json'

/* 聚光燈遮罩：一層半透明深色蓋住「不是焦點」的地方。
   - 選某個行政區：焦點＝該區，其他全暗
   - 全部行政區：焦點＝整個新北市（甜甜圈狀，中間的台北市也要暗）
   不需要 turf —— GeoJSON 帶洞多邊形就是 [外環, 內環...]。 */

// 超大矩形，遠大於任何可能的視野（minZoom 8 也蓋得住），縮到最遠也看不到邊
const WORLD_RING: number[][] = [
  [90, 0],
  [160, 0],
  [160, 45],
  [90, 45],
  [90, 0],
]

export const EMPTY_FC: FeatureCollection = { type: 'FeatureCollection', features: [] }

const poly = (rings: number[][][]): Feature<Polygon> => ({
  type: 'Feature',
  properties: {},
  geometry: { type: 'Polygon', coordinates: rings },
})

type Geo = { type: string; coordinates: number[][][] | number[][][][] }
function toPolys(g: Geo): number[][][][] {
  return g.type === 'Polygon' ? [g.coordinates as number[][][]] : (g.coordinates as number[][][][])
}

export function buildMask(town: string): FeatureCollection {
  if (town) {
    // 某個行政區：大矩形挖掉該區外環
    const f = (districtsGeo as unknown as { features: { properties: { town: string }; geometry: Geo }[] }).features.find(
      (x) => x.properties.town === town,
    )
    if (!f) return EMPTY_FC
    const holes = toPolys(f.geometry).map((p) => p[0])
    return { type: 'FeatureCollection', features: [poly([WORLD_RING, ...holes])] }
  }

  // 全部行政區：大矩形挖掉新北市外環，另外把每個洞（台北市）當實心多邊形補暗
  const g = (outlineGeo as unknown as { features: { geometry: Geo }[] }).features[0]?.geometry
  if (!g) return EMPTY_FC
  const features: Feature<Polygon>[] = []
  const outerHoles: number[][][] = []
  for (const p of toPolys(g)) {
    outerHoles.push(p[0]) // 外環當「洞」→ 外圍變暗
    for (const inner of p.slice(1)) features.push(poly([inner])) // 內環（台北市）自己變暗
  }
  features.unshift(poly([WORLD_RING, ...outerHoles]))
  return { type: 'FeatureCollection', features }
}
