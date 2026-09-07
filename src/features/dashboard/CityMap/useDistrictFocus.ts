import { useEffect, useRef } from 'react'
import { LngLatBounds } from 'maplibre-gl'
import type { MapRef } from 'react-map-gl/maplibre'
import type { Station } from '@/api/types'
import districtsGeo from '@/assets/newtaipei-districts.json'

/** 有效的新北市座標（排除 null / 0,0 / 離譜髒資料） */
export const hasCoord = (s: Pick<Station, 'lon' | 'lat'>) =>
  Number.isFinite(s.lon) && Number.isFinite(s.lat) && s.lon > 119 && s.lon < 123 && s.lat > 21 && s.lat < 26

type Feat = { properties: { town: string }; geometry: { type: string; coordinates: number[][][] | number[][][][] } }

/** 行政區多邊形邊界框；name 省略 → 全部 29 區聯集。 */
function districtBounds(name?: string): LngLatBounds | null {
  const feats = (districtsGeo as { features: Feat[] }).features.filter((f) => !name || f.properties.town === name)
  if (!feats.length) return null
  const b = new LngLatBounds()
  for (const f of feats) {
    const polys = (f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates) as number[][][][]
    for (const poly of polys) for (const ring of poly) for (const pt of ring) b.extend(pt as [number, number])
  }
  return b.isEmpty() ? null : b
}

/** 對焦：指定區框該區多邊形；「全部」框站點實際分布（山區無站，多邊形會偏移）。
    townName 變、或 frameNonce +1（點地區 chip / 按「回到範圍」）時重新框。 */
export function useDistrictFocus(
  mapRef: React.RefObject<MapRef | null>,
  ready: boolean,
  townName: string,
  stations: Station[] | undefined,
  frameNonce: number,
) {
  const didInitial = useRef(false)
  const prevNonce = useRef<number | null>(null)

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return

    // 冷載入 / URL 還原視野（第一次跑，或 towns query 晚回來讓 townName 由 '' 補成區名）→
    // 直接「就位」不做動畫，否則會看到地圖從全市 fly 到該區、像閃一下。
    // frameNonce 真的 +1（點地區 chip /「回到範圍」）才是使用者要求的重框 → 用動畫過場。
    const byUser = prevNonce.current !== null && frameNonce !== prevNonce.current
    prevNonce.current = frameNonce
    const duration = byUser ? 700 : 0

    if (townName) {
      const b = districtBounds(townName)
      if (b) map.fitBounds(b, { padding: 48, duration, bearing: 0, pitch: 0 })
      return
    }
    // 全部行政區：框站點分布
    const b = new LngLatBounds()
    for (const s of stations ?? []) if (hasCoord(s)) b.extend([s.lon, s.lat])
    if (!b.isEmpty()) map.fitBounds(b, { padding: 40, duration, bearing: 0, pitch: 0 })
  }, [mapRef, ready, townName, frameNonce])

  // 站點第一次到齊時（load 當下站表通常還沒回來），若停在「全部」補一次對焦（就位、不動畫）
  useEffect(() => {
    const map = mapRef.current
    if (didInitial.current || !map || !ready || townName || !(stations?.length ?? 0)) return
    didInitial.current = true
    const b = new LngLatBounds()
    for (const s of stations!) if (hasCoord(s)) b.extend([s.lon, s.lat])
    if (!b.isEmpty()) map.fitBounds(b, { padding: 40, duration: 0, bearing: 0, pitch: 0 })
  }, [mapRef, ready, townName, stations])
}
