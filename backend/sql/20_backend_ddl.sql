-- ════════════════════════════════════════════════════════════
-- 20_backend_ddl.sql —— 建 4 張 hackathon_backend_* 服務用表
--
-- 為什麼要前綴：
--   把「服務讀的表」和「訓練管線讀的表」切開。訓練管線的表可以重建、
--   重灌、改名，後端只認 hackathon_backend_*，兩邊不互相牽動。
--   代價是 level30 複製一份 2,650 萬行（約 1.5~2 GB）；換到的是之後
--   可以把 baseline / baseline_grid 整組砍掉，後端照跑。
--
-- 用法：
--   psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/20_backend_ddl.sql
-- ════════════════════════════════════════════════════════════
\timing on

BEGIN;

DROP TABLE IF EXISTS public.hackathon_backend_station;
DROP TABLE IF EXISTS public.hackathon_backend_town;
DROP TABLE IF EXISTS public.hackathon_backend_level30;
DROP TABLE IF EXISTS public.hackathon_backend_calendar;

-- ══ 1　站點主檔 ══════════════════════════════════════════════
--   cat 欄先留 NULL，由 30_load_cat_map.sh 灌
CREATE TABLE public.hackathon_backend_station AS
SELECT station_uid, station_name, town_code, town,
       lat, lon, capacity, addr_zh, last_seen,
       NULL::int AS cat
FROM public.station_profile;

ALTER TABLE public.hackathon_backend_station ADD PRIMARY KEY (station_uid);
CREATE INDEX ON public.hackathon_backend_station (town_code);

COMMENT ON TABLE public.hackathon_backend_station IS
  '站點主檔（服務用）。來源 station_profile + meta.json 的 cat 對照。';
COMMENT ON COLUMN public.hackathon_backend_station.cat IS
  '★ DeepAR 的 feat_static_cat。來源：ml-deepar/data/sagemaker_cal_h6/cat_map.csv（與 meta.json 的 names 逐列相符，names_sha ff2249bb812fb7e2，1,550 站）。編號由訓練時的字典序決定，猜錯不會報錯只會全錯。NULL = 訓練後才新增的站，模型沒有它的 embedding，service 要回 STATION_UNKNOWN 而不是丟給 endpoint。';

-- ══ 2　行政區對照 ════════════════════════════════════════════
--   dump 裡沒有這張表，由 station_profile 聚合產生
CREATE TABLE public.hackathon_backend_town AS
SELECT town_code, min(town) AS town, count(*)::int AS station_count
FROM public.station_profile
WHERE town_code IS NOT NULL
GROUP BY town_code;

ALTER TABLE public.hackathon_backend_town ADD PRIMARY KEY (town_code);

COMMENT ON TABLE public.hackathon_backend_town IS
  '新北 29 個行政區。town_code = station_uid 第 8~9 位。TDX 的 LocationTown 欄位全部是空的，這是唯一可用的行政區線索（1,576 站匹配、29 個區碼零衝突）。';

-- ══ 3　水位歷程 ══════════════════════════════════════════════
--   ★ 來源是 baseline_grid，不是 tdx_grid30
--     export_sagemaker_jsonl.sh:183 → trainset_v
--     trainset_view.sql:109-110     → FROM public.baseline_grid
--     佐證：trainset_v 1,587 站 = meta.json 的 1,550 + excluded_stations 37
CREATE TABLE public.hackathon_backend_level30 AS
SELECT station_uid, slot, avail, docks, is_observed,
       -- ★ is_imputed 一律從 0 開始：baseline_grid 裡沒有週期補值，
       --   它的 is_observed=0 全部是 carry-forward。欄位語意見
       --   sql/42_level30_is_imputed.sql 的註解（那支負責既有表的 ALTER）。
       0::smallint AS is_imputed
FROM public.baseline_grid;

ALTER TABLE public.hackathon_backend_level30 ADD PRIMARY KEY (station_uid, slot);
ALTER TABLE public.hackathon_backend_level30
  ALTER COLUMN is_imputed SET NOT NULL,
  ALTER COLUMN is_imputed SET DEFAULT 0;
CREATE INDEX hackathon_backend_level30_real_idx
  ON public.hackathon_backend_level30 (slot)
  WHERE is_imputed = 0 AND avail IS NOT NULL;

COMMENT ON TABLE public.hackathon_backend_level30 IS
  '★ 30 分鐘網格的水位歷程。來源 baseline_grid（= trainset_v 的來源 = h6c-base 的訓練資料）。DeepAR 推論要的 48 格 target 從這裡取 —— context_length=48 是模型結構的一部分，只餵當下 1 格會退化成查表（MAE 1.78 → 4.42）。換來源等於換了輸入分佈，predict.py:63-65 已警告過。';

-- ══ 4　假日檔 ════════════════════════════════════════════════
CREATE TABLE public.hackathon_backend_calendar AS
SELECT d, is_holiday, is_makeup, note
FROM public.dim_calendar;

ALTER TABLE public.hackathon_backend_calendar ADD PRIMARY KEY (d);

COMMENT ON TABLE public.hackathon_backend_calendar IS
  '行政院人事行政總處辦公日曆表 113~116 年（2024~2027）。is_holiday 是唯一進模型的 dynamic_feat —— 它是少數補得出「未來值」的特徵（推論時 dynamic_feat 長度要 = len(target)+H），天氣做不到。';

COMMIT;

ANALYZE public.hackathon_backend_station;
ANALYZE public.hackathon_backend_town;
ANALYZE public.hackathon_backend_level30;
ANALYZE public.hackathon_backend_calendar;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 四張表'
SELECT 'station'  AS t, count(*) AS n, 1594     AS 期望 FROM public.hackathon_backend_station
UNION ALL SELECT 'town',     count(*), 29       FROM public.hackathon_backend_town
UNION ALL SELECT 'level30',  count(*), 26507029 FROM public.hackathon_backend_level30
UNION ALL SELECT 'calendar', count(*), 1461     FROM public.hackathon_backend_calendar
ORDER BY 1;

\echo
\echo '── 水位歷程涵蓋範圍（預期 2025-08-01 ~ 2026-07-31）'
SELECT min(slot), max(slot) FROM public.hackathon_backend_level30;

\echo
\echo '── 假日檔涵蓋範圍（預期 2024 ~ 2027）'
SELECT min(d), max(d), sum(is_holiday) AS 放假日數 FROM public.hackathon_backend_calendar;

\echo
\echo '── cat 覆蓋率（此時應為 0 / 1594，由 30_load_cat_map.sh 灌）'
SELECT count(*) FILTER (WHERE cat IS NOT NULL) AS 有cat, count(*) AS 全部
FROM public.hackathon_backend_station;
