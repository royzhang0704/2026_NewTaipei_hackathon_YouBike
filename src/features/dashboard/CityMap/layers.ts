import type { LayerProps } from 'react-map-gl/maplibre'
import type { StyleSpecification } from 'maplibre-gl'
import type { FilterSpecification } from 'maplibre-gl'
import type { Theme } from '@/stores/useAppStore'

/* 圖層樣式集中在這一檔。加派工路線層時，就在這裡多一組 + 在 CityMap 掛一個 <Layer>。 */

/** 底圖來源。CARTO 常被廣告攔截器 / 企業網擋掉（Failed to fetch (0)）。
    NLSC 是政府圖資、台灣機房、免金鑰，最不會被擋。
    VITE_BASEMAP=none → 只留深色底 + 站點，現場網路不穩時最保險。 */
const BASEMAP = (import.meta.env.VITE_BASEMAP as string) ?? 'nlsc'

const TILE_SOURCES: Record<string, string[]> = {
  nlsc: ['https://wmts.nlsc.gov.tw/wmts/EMAP/default/EPSG:3857/{z}/{y}/{x}'],
  osm: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
  carto: [
    'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
  ],
}

export const HAS_BASEMAP = BASEMAP !== 'none'

/* 底圖與光柵層改用 <Layer> 掛，paint 隨主題切換（見 bgLayer / rasterLayer）。
   style 本體只留 source，layers 空著。 */
export const MAP_STYLE: StyleSpecification = {
  version: 8,
  sources: HAS_BASEMAP
    ? {
        base: {
          type: 'raster',
          tiles: TILE_SOURCES[BASEMAP] ?? TILE_SOURCES.nlsc,
          tileSize: 256,
          // NLSC 低 zoom（全球視野）沒有圖磚，會噴一堆「could not be decoded」
          minzoom: BASEMAP === 'nlsc' ? 7 : 0,
          maxzoom: 20,
          attribution: BASEMAP === 'nlsc' ? '© 內政部國土測繪中心' : '© OpenStreetMap',
        },
      }
    : {},
  layers: [],
}

/** 最底層純色背景（NLSC 圖磚半透明時透出來、被壓淡後的底色）。 */
export const bgLayer = (theme: Theme): LayerProps => ({
  id: 'bg',
  type: 'background',
  paint: { 'background-color': theme === 'light' ? '#edece6' : '#0e1116' },
})

/* NLSC 光柵。資料地圖的底圖要當「安靜的薄墊」，不能跟資料點搶：
   深色：壓暗、去飽和、塞進深底。
   淺色：去飽和殺掉山區綠暈；但 opacity / contrast 不能壓太狠——原本 0.5 / -0.34
   把路網、地名、行政區輪廓都洗到幾乎看不見，失去「這團站點在哪一區」的參考。
   現值：opacity 0.6、對比只微降、最亮處壓到 0.96（不留純白，讓細線有底可站）。 */
export const rasterLayer = (theme: Theme): LayerProps => ({
  id: 'base',
  type: 'raster',
  source: 'base',
  paint:
    theme === 'light'
      ? {
          'raster-opacity': 0.6,
          'raster-saturation': -0.9,
          'raster-brightness-max': 0.96,
          'raster-contrast': -0.16,
        }
      : {
          'raster-opacity': 0.42,
          'raster-saturation': -1,
          'raster-brightness-max': 0.68,
          'raster-contrast': -0.1,
        },
})

const COLOR_BY_SIDE = [
  'match',
  ['get', 'side'],
  'shortage',
  '#EE5A34',
  'full',
  '#5A86D6', // 對齊 --color-cold（去一階飽和）
  '#6A6E75',
] as unknown as FilterSpecification

// 站點各層的基礎過濾；選區時再 AND 上區名
const F_GLOW = [
  'any',
  ['==', ['get', 'level'], 'high'],
  ['==', ['get', 'level'], 'mid'],
] as FilterSpecification
const F_BASE = ['==', ['get', 'level'], 'none'] as FilterSpecification
const F_ALERT = ['!=', ['get', 'level'], 'none'] as FilterSpecification

/** 選區時把基礎過濾 AND 上「只留該區」 */
export function scopeFilter(base: FilterSpecification, town: string): FilterSpecification {
  return town
    ? (['all', base, ['==', ['get', 'town'], town]] as unknown as FilterSpecification)
    : base
}

/** 有選取站(sel)時：選中站 full、其他站 dim；沒選取時全部 full。 */
function dimExpr(sel: string | null, full: number, dim: number): number | unknown[] {
  return sel ? ['case', ['==', ['get', 'uid'], sel], full, dim] : full
}

/** 選取站的淡化倍率：選中站 1、其他站 factor；沒選取時 1。 */
function dimFactor(sel: string | null, factor = 0.36): number | unknown[] {
  return sel ? ['case', ['==', ['get', 'uid'], sel], 1, factor] : 1
}

/** value 依 side 分（滿站 / 缺車），再乘上淡化倍率。 */
function sideDim(
  sel: string | null,
  fullVal: number,
  shortageVal: number,
  factor?: number,
): unknown[] {
  return ['*', ['case', ['==', ['get', 'side'], 'full'], fullVal, shortageVal], dimFactor(sel, factor)]
}

export const STATION_LAYERS = {
  // 高風險站雷達 ping（半徑/透明度由 rAF 每幀更新）。z<10.5（全市視野）時
  // rAF 會把透明度壓成 0；縮放到單區尺度才動，不管是點 chip 還是手動放大。
  pulse: (town: string): LayerProps => ({
    id: 'st-pulse',
    type: 'circle',
    source: 'stations',
    filter: scopeFilter(['==', ['get', 'level'], 'high'] as FilterSpecification, town),
    paint: {
      'circle-color': COLOR_BY_SIDE as never,
      'circle-radius': 6,
      'circle-opacity': 0,
      'circle-blur': 0.35,
    },
  }),
  glow: (town: string): LayerProps => ({
    id: 'st-glow',
    type: 'circle',
    source: 'stations',
    filter: scopeFilter(F_GLOW, town),
    paint: {
      'circle-color': COLOR_BY_SIDE as never,
      // 低 zoom 縮小成色調、放大才變暈影
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 5, 12, 14, 15, 32],
      'circle-opacity': [
        'interpolate',
        ['linear'],
        ['zoom'],
        9,
        ['case', ['==', ['get', 'level'], 'high'], 0.1, 0.05],
        13,
        ['case', ['==', ['get', 'level'], 'high'], 0.16, 0.08],
      ],
      'circle-blur': 1,
    },
  }),
  // 健康站＝空心白圈。半徑與線粗依比例尺縮放：城市視野細（密集區才不糊成一片），
  // 放大到單區正常粗細。
  base: (town: string, sel: string | null, theme: Theme): LayerProps => ({
    id: 'st-base',
    type: 'circle',
    source: 'stations',
    filter: scopeFilter(F_BASE, town),
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 1.8, 11, 2.4, 13, 4, 15, 6],
      // 淺色：近白實心填 + 深細環 → 在忙碌底圖上也是清楚的「甜甜圈」；深色：原本的透明填
      'circle-color': theme === 'light' ? 'rgba(255,255,255,0.92)' : 'rgba(233,235,238,0.14)',
      'circle-opacity': dimExpr(sel, 1, 0.4),
      'circle-opacity-transition': { duration: 160 },
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 9, 0.6, 11, 0.9, 13, 1.4, 15, 1.6],
      'circle-stroke-color': theme === 'light' ? '#33383f' : '#e9ebee',
      'circle-stroke-opacity': dimExpr(sel, 0.85, 0.4),
      'circle-stroke-opacity-transition': { duration: 160 },
    } as never,
  }),
  // 形狀編碼：缺車＝實心圓、滿站＝空心圈（藍粗框）。灰階下也分得出（實心 vs 空心）。
  alert: (town: string, sel: string | null, theme: Theme): LayerProps => ({
    id: 'st-alert',
    type: 'circle',
    source: 'stations',
    filter: scopeFilter(F_ALERT, town),
    paint: {
      'circle-color': COLOR_BY_SIDE as never,
      'circle-radius': [
        'interpolate',
        ['linear'],
        ['zoom'],
        9,
        ['case', ['==', ['get', 'level'], 'high'], 5, 3],
        14,
        ['case', ['==', ['get', 'level'], 'high'], 12, 7],
      ],
      // 滿站 fill 幾乎透明（空心），缺車 fill 實心；再乘選取淡化倍率
      'circle-opacity': sideDim(sel, 0.12, 0.9),
      'circle-opacity-transition': { duration: 160 },
      'circle-stroke-width': ['case', ['==', ['get', 'side'], 'full'], 2.4, 1],
      // 缺車實心點的分隔暈：深色底用深暈、淺色底用白暈
      'circle-stroke-color': [
        'case',
        ['==', ['get', 'side'], 'full'],
        '#5A86D6',
        theme === 'light' ? 'rgba(255,255,255,0.9)' : 'rgba(11,13,16,0.7)',
      ],
      'circle-stroke-opacity': dimFactor(sel),
      'circle-stroke-opacity-transition': { duration: 160 },
    } as never,
  }),
  selected: (uid: string | null, theme: Theme): LayerProps => ({
    id: 'st-selected',
    type: 'circle',
    source: 'stations',
    filter: ['==', ['get', 'uid'], uid ?? '__none__'] as FilterSpecification,
    paint: {
      'circle-radius': 14,
      'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-width': 2,
      'circle-stroke-color': theme === 'light' ? '#1a1c20' : '#ffffff',
    },
  }),
}

export const DISTRICT_LAYERS = {
  // 「全部行政區」時把整個新北市外框畫出來（跟選單區的外框一致）
  cityOutline: (town: string): LayerProps => ({
    id: 'city-line',
    type: 'line',
    source: 'city',
    layout: { visibility: town ? 'none' : 'visible' },
    paint: { 'line-color': '#EE5A34', 'line-width': 1.4, 'line-opacity': 0.7 },
  }),
  fill: (town: string): LayerProps => ({
    id: 'district-fill',
    type: 'fill',
    source: 'districts',
    filter: ['==', ['get', 'town'], town || '__none__'] as FilterSpecification,
    paint: { 'fill-color': '#EE5A34', 'fill-opacity': 0.06 },
  }),
  line: (theme: Theme): LayerProps => ({
    id: 'district-line',
    type: 'line',
    source: 'districts',
    paint: {
      // 行政區界＝這張圖的主要「定位」層，兩個主題原本都太細（淺 0.16/0.6、深 0.13/0.6）→ 一起加深加粗
      'line-color': theme === 'light' ? 'rgba(24,26,30,0.3)' : 'rgba(255,255,255,0.2)',
      'line-width': theme === 'light' ? 0.8 : 0.7,
    },
  }),
  lineSel: (town: string): LayerProps => ({
    id: 'district-line-sel',
    type: 'line',
    source: 'districts',
    filter: ['==', ['get', 'town'], town || '__none__'] as FilterSpecification,
    paint: { 'line-color': '#EE5A34', 'line-width': 1.6, 'line-opacity': 0.9 },
  }),
}

export const INTERACTIVE_LAYER_IDS = ['st-alert', 'st-base']
