-- ════════════════════════════════════════════════════════════
-- 62_link_run_id.sql —— 把明細與風險表接回主檔（加 run_id）
--
--   forecast_history.run_id → forecast_run.id   （9/1 使用者定案）
--   risk_snapshot.run_id    → forecast_run.id
--
-- ★ 為什麼保留 forecast_history 原本的 PK (station_uid, origin, at)
--   而不是改成 (run_id, at)：
--     forecast_repo.latest_origin() 是「某一站 origin <= now 的最大值」，
--     走的就是這支 PK 的前綴。改掉它那句會退化成全表掃（28.8 萬列）。
--   run_id 另給一支索引即可，多一支索引換整條查詢路徑不動。
--   唯一性沒有損失：主檔的 UNIQUE(station_uid, origin) 保證
--   run_id ↔ (站, origin) 一一對應。
--
-- ★ 回填順序（既有 28.8 萬列明細 / 4.8 萬組 (站, origin)）：
--     ① 先從明細聚合出主檔列（predict_status='done'）
--     ② 再把 run_id 寫回明細
--     ③ 最後才加 NOT NULL 與 FK —— 反過來加不上去
--
-- 用法：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/62_link_run_id.sql
-- ════════════════════════════════════════════════════════════
\timing on

BEGIN;

-- ══ 1　加欄（可重跑）══════════════════════════════════════════
ALTER TABLE public.hackathon_backend_forecast_history
  ADD COLUMN IF NOT EXISTS run_id bigint;
ALTER TABLE public.hackathon_backend_risk_snapshot
  ADD COLUMN IF NOT EXISTS run_id bigint;

-- ══ 2　從既有明細回填主檔 ════════════════════════════════════
--   主檔問世前打好的 origin（demo 已經跑過好幾輪）補一列。
--   model_job 取 min()：同一站同一 origin 的 6 格必定同一個模型
--   （upsert 一起寫的），min 只是拿代表值。
INSERT INTO public.hackathon_backend_forecast_run
       (station_uid, origin, predict_status, predicted_at, model_job, slots)
SELECT station_uid, origin, 'done', max(created_at), min(model_job), count(*)
  FROM public.hackathon_backend_forecast_history
 GROUP BY station_uid, origin
    ON CONFLICT (station_uid, origin) DO NOTHING;

-- ══ 3　run_id 寫回明細 ═══════════════════════════════════════
UPDATE public.hackathon_backend_forecast_history h
   SET run_id = r.id
  FROM public.hackathon_backend_forecast_run r
 WHERE r.station_uid = h.station_uid AND r.origin = h.origin
   AND h.run_id IS DISTINCT FROM r.id;

UPDATE public.hackathon_backend_risk_snapshot s
   SET run_id = r.id
  FROM public.hackathon_backend_forecast_run r
 WHERE r.station_uid = s.station_uid AND r.origin = s.origin
   AND s.run_id IS DISTINCT FROM r.id;

COMMIT;

-- ══ 4　約束與索引（回填完才加得上）══════════════════════════
BEGIN;

ALTER TABLE public.hackathon_backend_forecast_history
  ALTER COLUMN run_id SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'hackathon_backend_forecast_history_run_id_fkey') THEN
    ALTER TABLE public.hackathon_backend_forecast_history
      ADD CONSTRAINT hackathon_backend_forecast_history_run_id_fkey
      FOREIGN KEY (run_id) REFERENCES public.hackathon_backend_forecast_run(id)
      ON DELETE CASCADE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'hackathon_backend_risk_snapshot_run_id_fkey') THEN
    ALTER TABLE public.hackathon_backend_risk_snapshot
      ADD CONSTRAINT hackathon_backend_risk_snapshot_run_id_fkey
      FOREIGN KEY (run_id) REFERENCES public.hackathon_backend_forecast_run(id)
      ON DELETE CASCADE;
  END IF;
END $$;

-- FK 的子表側一定要有索引，否則刪主檔列時會對子表全表掃
CREATE INDEX IF NOT EXISTS hackathon_backend_forecast_history_run_id_idx
  ON public.hackathon_backend_forecast_history (run_id);
CREATE INDEX IF NOT EXISTS hackathon_backend_risk_snapshot_run_id_idx
  ON public.hackathon_backend_risk_snapshot (run_id);

COMMENT ON COLUMN public.hackathon_backend_forecast_history.run_id IS
  '★ 主檔 hackathon_backend_forecast_run.id —— 這 6 格是「哪一次預測」產出的。ON DELETE CASCADE：刪主檔那一列，明細跟著走（demo --reset 靠這個一起清乾淨）。(station_uid, origin) 兩欄保留不動，PK 也不動 —— forecast_repo.latest_origin 走的就是那支 PK 前綴，改掉會退化成全表掃。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.run_id IS
  '★ 主檔 hackathon_backend_forecast_run.id。可為 NULL —— 無 cat 又無 proxy 的站根本不會進批打對象，主檔沒有它的列，但風險表每輪仍寫全站（status=''no_forecast''），這種列的 run_id 就是 NULL。';

COMMIT;

ANALYZE public.hackathon_backend_forecast_run;
ANALYZE public.hackathon_backend_forecast_history;
ANALYZE public.hackathon_backend_risk_snapshot;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 回填完整性（明細 run_id 不得有 NULL；主檔列數 = 相異(站,origin)）'
SELECT (SELECT count(*) FROM public.hackathon_backend_forecast_history
         WHERE run_id IS NULL)                                        AS 明細缺run_id,
       (SELECT count(*) FROM public.hackathon_backend_forecast_run)   AS 主檔列數,
       (SELECT count(*) FROM (SELECT 1 FROM public.hackathon_backend_forecast_history
                               GROUP BY station_uid, origin) x)       AS 相異站origin;

\echo
\echo '── run_id 與 (站, origin) 是否一致（期望 0 列不一致）'
SELECT count(*) AS 不一致
  FROM public.hackathon_backend_forecast_history h
  JOIN public.hackathon_backend_forecast_run r ON r.id = h.run_id
 WHERE r.station_uid <> h.station_uid OR r.origin <> h.origin;

\echo
\echo '── FK 與索引'
SELECT conname AS 約束名, pg_get_constraintdef(oid) AS 定義
FROM pg_constraint
WHERE conname LIKE '%run_id_fkey' ORDER BY 1;
