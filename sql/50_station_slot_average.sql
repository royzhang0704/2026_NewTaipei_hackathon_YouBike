-- ════════════════════════════════════════════════════════════
-- hackathon_backend_station_slot_average
--   「該站在某個時刻的歷史平均水位」—— 站 × 平日假日 × 48 時刻。
--
-- 用途：naive baseline。DeepAR 要有價值，就得贏過「查歷史同時段平均」
--       這條零成本的路。順便給前端疊常態水位灰帶、給無預測站當 fallback。
--
-- 期間：2025-08-01 ～ 2026-04-30（273 天）
--   ★ 終點卡在訓練截止日 —— demo 回放的「現在」是 2026-05-01 之後，
--     平均值若吃到 5 月就等於偷看答案，baseline 會不公平地變強。
--
-- 來源：baseline_grid（= trainset_v 的來源 = 模型吃的同一份資料）
--   ★ 只取 is_observed = 1。全期 1,965 萬格裡真觀測只有 740 萬（37.7%），
--     另外 760 萬格是 is_observed=0 但 avail 有值的 carry-forward 補值，
--     gap_slots 最大到 9,194 格（191 天）—— 那是停擺半年的站一路延用同一個
--     數字。算進平均等於讓同一筆觀測投票幾千次。
--
-- is_holiday：來自 dim_calendar（行政院公告，含補班補假），不是用星期算的。
--   與模型唯一的 dynamic feature 同一個定義，兩邊才可比。
--   ★ 為什麼一定要切：同一站 08:00 平日均 19.9 / 假日均 9.4，
--     18:00 平日均 1.6 / 假日均 5.7。合起來平均會把通勤潮汐整個抹平。
--   ★ 為什麼不再細切星期：1534 × 7 × 48 = 51.5 萬桶，每桶剩約 7 筆，沒意義。
-- ════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS public.hackathon_backend_station_slot_average;

CREATE TABLE public.hackathon_backend_station_slot_average (
  station_uid  text         NOT NULL,
  is_holiday   smallint     NOT NULL,     -- 0 平日 / 1 放假
  tod          time         NOT NULL,     -- 00:00 ~ 23:30，48 格
  n            integer      NOT NULL,     -- 樣本數，可信度看這欄
  avg_avail    numeric(6,2) NOT NULL,     -- ★ 本體：平均可借
  med_avail    numeric(6,2),
  p10_avail    numeric(6,2),
  p90_avail    numeric(6,2),
  sd_avail     numeric(6,2),              -- n=1 時為 NULL（樣本標準差算不出來）
  avg_docks    numeric(6,2),
  PRIMARY KEY (station_uid, is_holiday, tod)
);

INSERT INTO public.hackathon_backend_station_slot_average
SELECT
  g.station_uid,
  c.is_holiday,
  g.slot::time                                                       AS tod,
  count(*)                                                           AS n,
  round(avg(g.avail)::numeric, 2)                                    AS avg_avail,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY g.avail)::numeric, 2) AS med_avail,
  round(percentile_cont(0.1) WITHIN GROUP (ORDER BY g.avail)::numeric, 2) AS p10_avail,
  round(percentile_cont(0.9) WITHIN GROUP (ORDER BY g.avail)::numeric, 2) AS p90_avail,
  round(stddev_samp(g.avail)::numeric, 2)                            AS sd_avail,
  round(avg(g.docks)::numeric, 2)                                    AS avg_docks
FROM public.baseline_grid g
JOIN public.dim_calendar  c ON c.d = g.slot::date
WHERE g.is_observed = 1
  AND g.slot >= '2025-08-01'
  AND g.slot <  '2026-05-01'
  AND g.avail IS NOT NULL
GROUP BY 1, 2, 3;

ANALYZE public.hackathon_backend_station_slot_average;

COMMENT ON TABLE public.hackathon_backend_station_slot_average IS
  '站 × 平日假日 × 48 時刻的歷史平均水位（naive baseline）。來源 baseline_grid 且僅 is_observed=1，期間 2025-08-01 ~ 2026-04-30（= 模型訓練截止，之後的資料不吃，否則 baseline 等於偷看答案）。查詢端請用 n 把關';
COMMENT ON COLUMN public.hackathon_backend_station_slot_average.is_holiday IS
  '0 平日 / 1 放假。取自 dim_calendar 行政院公告（含補班補假），非由星期推算 —— 與模型的 dynamic feature 同定義';
COMMENT ON COLUMN public.hackathon_backend_station_slot_average.n IS
  '★ 該桶的真觀測樣本數。全表約 10.6% 的桶 n<10、33.9% 的桶 n<30，觀測覆蓋率本來就只有 37.7% 且各站不均。建議 n>=10 才採信，不足就退回不顯示 —— 寫入時刻意不砍，留著才分得出「沒資料」與「沒這站」';
COMMENT ON COLUMN public.hackathon_backend_station_slot_average.avg_docks IS
  '期間平均車柱數。會擴柱，所以不等於 hackathon_backend_station.capacity 的現況值';

INSERT INTO public.hackathon_backend_sys_config (key, value, note) VALUES
  ('slot_average_window', '2025-08-01/2026-04-30',
   'hackathon_backend_station_slot_average 的統計期間（終點 = 模型訓練截止日）'),
  ('slot_average_built_at', now()::text,
   'hackathon_backend_station_slot_average 最後一次重算的時間')
ON CONFLICT (key) DO UPDATE
  SET value = EXCLUDED.value, note = EXCLUDED.note, updated_at = now();
