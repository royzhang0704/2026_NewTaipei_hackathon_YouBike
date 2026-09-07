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
    只在「冷載入 / URL 還原」「frameNonce +1（點地區 chip、按回到範圍、切字級）」
    「towns query 晚回來讓 townName 由 '' 補成區名」時重新框。
    跨區選站的靜默換區（setTownQuiet）不重框 —— 鏡頭交給 CityMap 的選站 flyTo。 */
export function useDistrictFocus(
  mapRef: React.RefObject<MapRef | null>,
  ready: boolean,
  townName: string,
  stations: Station[] | undefined,
  frameNonce: number,
) {
  const didInitial = useRef(false)
  const prevNonce = useRef<number | null>(null)
  const prevTown = useRef<string | null>(null)

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return

    const firstRun = prevNonce.current === null
    const nonceChanged = !firstRun && frameNonce !== prevNonce.current
    // towns query 晚回來，townName 由 '' 補成區名 → 屬冷載入就位，不是使用者換區
    const townResolved = prevTown.current === '' && townName !== ''
    prevNonce.current = frameNonce
    prevTown.current = townName

    // 靜默換區（跨區選取站點）：townName 改變但 nonce 未遞增時不重新框景，避免鏡頭大幅拉遠再拉近
    if (!firstRun && !nonceChanged && !townResolved) return

    // 冷載入 / URL 還原 → 直接就位不動畫；使用者按鈕要求的重框（nonce +1）才用動畫過場。
    // prefers-reduced-motion → 一律就位。
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const duration = nonceChanged && !reduce ? 700 : 0

    // 抽屜（單站檢視）關掉後 transform 上可能還留著 right padding，fitBounds 不會去動它
    // → 整張圖會偏左。重框前先歸零，讓 fitBounds 的置中計算對到完整視窗。
    map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 })

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
