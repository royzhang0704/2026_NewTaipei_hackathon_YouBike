# ════════════════════════════════════════════════════════════
# hist_repo —— 歷史 API CSV 的落地：守門 → 30 分重採樣 → 補進兩張表
#
# 出處：meet/20260828/計劃-排程自癒與level30滾動視窗.md §1
#      「載入路徑不變：歷史 API CSV → 既有 baseline 守門＋重採樣管線
#        → upsert actual_history + level30，只補缺格、不覆蓋既有值」
#
# ★ 規則一條都不是新發明，全部照抄既有管線 ——
#   換一套守門或重採樣規則 = 換了模型的輸入分佈（predict.py:63-65 的警告）：
#
#     三道守門      ml-deepar/sql/baseline_create_import.sql（IMPORT 段的 CASE）
#     格內最後一筆  ml-deepar/sql/baseline_resample_export.sh（obs CTE，DISTINCT ON）
#     carry ≤6 格   同上（f CTE 的 gap_slots > 6 → NULL）
#     is_observed   同上（格點層語意＝這格是不是實測，不是守門結果）
#
#   ⚠ 那兩支是「重建整張 baseline_grid」的批次，不能拿來補一天。
#     本檔是同一組規則的日期範圍版，逐條對照著寫，改動任一邊都要同步。
#
# ★ 兩處與 baseline 管線刻意不同，理由寫在這裡免得日後被當成 bug：
#   1. docks 取主檔 capacity，不是「當天站位檔的容量」——
#      歷史 API 的 Availability 只有車況，沒有站位檔（那是另一支端點）。
#      Job A 也是這樣寫（pull_realtime.write()），兩條寫入路徑一致。
#   2. carry-forward 不跨視窗邊界：每站的骨架只長到「本批 CSV 內」
#      自己的首末回報。往前 carry 需要視窗外的資料，這裡沒有 ——
#      寧可留缺格 NULL，不要造一條假的水平線。
#
# 用法：
#   n = hist_repo.stage_csv(csv_text)          # 灌 staging（全 text，不轉型）
#   stats = hist_repo.ingest(d0, d1)           # 守門+重採樣+補格
#   rep = hist_repo.gap_report(d0, d1)         # 缺格率（Job C 判定 d）
#
# 冒煙（不打 TDX，用 raw/importdata 的同格式日檔）：
#   uv run python -m app.repository.hist_repo --file <某個歷史 CSV>
# ════════════════════════════════════════════════════════════
import csv
import io
from datetime import date, timedelta

from app import config
from app.errors import AppError
from app.repository.db import get_conn

# staging 兩張：原文層與格內最後一筆。都是暫存，每次 ingest 重建。
# ★ 用實體表而不是 CTE：27 萬列 × 10 天在一條 SQL 裡跑完，出錯時
#   什麼都查不到。落成表之後可以直接 psql 進去看是哪一步壞的。
STAGE = "hackathon_backend_hist_stage"
OBS = "hackathon_backend_hist_obs"

# 歷史 API CSV 需要的欄位。★ 與 raw/importdata 的 590 個日檔同名 ——
# 那批檔就是同一支 API 的產物（計劃-TDX排程與初始化.md §4 ②）。
HIST_COLS = ("StationUID", "ServiceStatus", "AvailableRentBikes",
             "AvailableReturnBikes", "SrcUpdateTime")

# 三道守門的正規式，逐字照抄 baseline_create_import.sql。
# ⚠ 改這裡等於改模型輸入分佈，改之前先讀那支的註解。
_RE_TS = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$'
_RE_NUM = r'^\d+$'

# date_bin 的原點，照抄 baseline_resample_export.sh。
# ★ 原點換了整條時間軸會偏移半小時，而且不會報錯。
_BIN_ORIGIN = "2026-01-01 00:00:00"

# carry-forward 上限，照抄重採樣規則第 2 條（6 格 = 3 小時）
_CARRY_MAX = 6


# ════════════════════════════════════════════════════════════
# 1　staging
# ════════════════════════════════════════════════════════════
def _ensure_stage() -> None:
    """建 staging 表（UNLOGGED：暫存資料不值得寫 WAL，重建就好）。"""
    with get_conn().cursor() as cur:
        cur.execute(f"""
            CREATE UNLOGGED TABLE IF NOT EXISTS {STAGE} (
              station_uid      text,
              service_status   text,
              avail_rent       text,
              avail_return     text,
              src_update_time  text
            )""")
        # ★ 全欄 text：守門的前提是「還沒轉型」。在 COPY 階段就轉 int，
        #   髒列會讓整批 COPY 失敗，而不是被標記後留下來查。
        cur.execute(f"COMMENT ON TABLE {STAGE} IS "
                    "'歷史 API CSV 的原文暫存（全欄 text，未守門未轉型）。"
                    "由 app/repository/hist_repo.py 每次 ingest 重建，非營運資料。'")


def stage_csv(csv_text: str) -> dict:
    """把歷史 API 回的 CSV 灌進 staging，回 {"rows", "short", "columns"}。

    ★ 欄位靠「標頭名字」對，不靠位置 —— 歷史 API 哪天多回一欄，
      按位置取會整批錯位而且不報錯。缺任一必要欄位直接丟例外。
    ★ 標頭第一格可能帶 UTF-8 BOM（raw/importdata 的日檔就是），要剝掉。
    """
    rdr = csv.reader(io.StringIO(csv_text))
    header = next(rdr, None)
    if not header:
        raise AppError("TDX_HIST_EMPTY", 502, "歷史 API 回應沒有標頭列（可能是空回應）")

    norm = [h.strip().lstrip("﻿").strip('"').strip().lower() for h in header]
    missing = [c for c in HIST_COLS if c.lower() not in norm]
    if missing:
        raise AppError("TDX_HIST_BAD_FORMAT", 502,
                       f"歷史 API CSV 缺欄位 {missing}；實際標頭 {header[:12]}")
    idx = [norm.index(c.lower()) for c in HIST_COLS]
    need = max(idx) + 1

    _ensure_stage()
    n, short = 0, 0
    with get_conn().cursor() as cur:
        cur.execute(f"TRUNCATE {STAGE}")
        with cur.copy(f"COPY {STAGE} (station_uid, service_status, avail_rent, "
                      f"avail_return, src_update_time) FROM STDIN") as cp:
            for row in rdr:
                if len(row) < need:
                    short += 1          # 截斷列：記數不丟，數量異常時要看得到
                    continue
                cp.write_row([row[i] for i in idx])
                n += 1
        cur.execute(f"ANALYZE {STAGE}")
    return {"rows": n, "short": short, "columns": list(HIST_COLS)}


# ════════════════════════════════════════════════════════════
# 2　守門 + 重採樣 + 補格
# ════════════════════════════════════════════════════════════
def ingest(d0: date, d1: date) -> dict:
    """把 staging 的內容守門、重採樣成 30 分格，補進兩張表。

    d0 / d1：台北日，含頭含尾。只有 slot 落在 [d0 00:00, d1+1 00:00) 的格會寫。

    ★ 「只補缺格、不覆蓋既有值」是兩張表各自的規則，語意不同：
        actual_history  ON CONFLICT DO NOTHING —— 那是「TDX 當下的認知」
                        的快照，Job A 當時記下的才是原始證據，不該被事後
                        補的資料改寫。沒有列才補。
        level30         只在既有列 avail IS NULL（= 缺格）時才寫進去。
                        既有真值一律不動 —— 假值蓋掉真值是 07-10 餵食故障
                        那類污染的來源，而且事後分不出來。

    回 {"gated", "dropped", "obs", "actual_new", "level30_new", "stations"}。
    """
    t0 = f"{d0} 00:00:00"
    t1 = f"{d1 + timedelta(days=1)} 00:00:00"
    out: dict = {}

    with get_conn().transaction(), get_conn().cursor() as cur:
        # ── ① 守門 + 格內最後一筆 ──────────────────────────────
        cur.execute(f"DROP TABLE IF EXISTS {OBS}")
        cur.execute(f"""
            CREATE UNLOGGED TABLE {OBS} AS
            SELECT DISTINCT ON (station_uid, slot)
                   station_uid, slot, src_ts, avail, ret, service_status
              FROM (
                SELECT s.station_uid,
                       (s.src_update_time::timestamptz AT TIME ZONE %(tz)s) AS src_ts,
                       date_bin('30 minutes',
                                (s.src_update_time::timestamptz AT TIME ZONE %(tz)s),
                                %(origin)s::timestamp)                       AS slot,
                       s.avail_rent::int   AS avail,
                       s.avail_return::int AS ret,
                       s.service_status
                  FROM {STAGE} s
                 WHERE s.station_uid <> ''
                   AND s.src_update_time ~ %(re_ts)s
                   AND s.avail_rent      ~ %(re_num)s
                   AND s.avail_return    ~ %(re_num)s
              ) x
             WHERE slot >= %(t0)s::timestamp AND slot < %(t1)s::timestamp
             -- 格內最後一筆（不取平均）：預測標的是那一刻的水位
             ORDER BY station_uid, slot, src_ts DESC
            """, {"tz": config.TZ_TAIPEI, "origin": _BIN_ORIGIN,
                  "re_ts": _RE_TS, "re_num": _RE_NUM, "t0": t0, "t1": t1})
        cur.execute(f"ALTER TABLE {OBS} ADD PRIMARY KEY (station_uid, slot)")
        cur.execute(f"ANALYZE {OBS}")

        cur.execute(f"SELECT count(*) AS n, count(DISTINCT station_uid) AS s FROM {OBS}")
        r = cur.fetchone()
        out["obs"], out["stations"] = r["n"], r["s"]

        cur.execute(f"SELECT count(*) AS n FROM {STAGE}")
        staged = cur.fetchone()["n"]
        cur.execute(f"""
            SELECT count(*) AS n FROM {STAGE} s
             WHERE s.station_uid <> ''
               AND s.src_update_time ~ %s AND s.avail_rent ~ %s AND s.avail_return ~ %s
            """, (_RE_TS, _RE_NUM, _RE_NUM))
        out["gated"] = cur.fetchone()["n"]
        out["dropped"] = staged - out["gated"]     # 守門沒過的列數（要看得到）

        # ── ② actual_history：一格一列的原始快照 ────────────────
        cur.execute(f"""
            INSERT INTO hackathon_backend_actual_history
                   (station_uid, slot, avail, return_slots,
                    service_status, src_update_time)
            SELECT o.station_uid, o.slot, o.avail, o.ret,
                   -- CSV 的 ServiceStatus 是中文字串，本欄是 int 0/1/2
                   -- （40_scheduler_tables.sql 的欄位註解）。認不得就留 NULL，
                   -- 不要硬塞 0 —— 0 的語意是「停止服務」，不是「不知道」。
                   CASE o.service_status
                        WHEN '正常營運' THEN 1
                        WHEN '停止營運' THEN 0
                        WHEN '暫停營運' THEN 2
                        ELSE NULLIF(regexp_replace(o.service_status, '[^0-9]', '', 'g'),
                                    '')::int
                   END,
                   o.src_ts AT TIME ZONE %(tz)s
              FROM {OBS} o
            ON CONFLICT (station_uid, slot) DO NOTHING
            """, {"tz": config.TZ_TAIPEI})
        out["actual_new"] = cur.rowcount

        # ── ③ level30：補骨架 + carry-forward ≤6 格 ─────────────
        out["level30_new"] = _fill_level30(cur, OBS)

    return out


def _fill_level30(cur, src: str) -> int:
    """把 src（欄位含 station_uid / slot / avail 的實測格）長成 level30 的
    骨架並 carry-forward ≤6 格，補進 level30。回寫入列數。

    ★ 抽成共用函式的理由：ingest()（歷史 API CSV）與 rebuild_level30()
      （從 actual_history 重跑）必須用**同一套**重採樣規則。
      寫兩份的話，哪天只改了其中一份，兩條路徑產出的 level30 就不一樣了，
      而且不會報錯 —— 那等於模型的輸入分佈偷偷分岔。

    照抄 baseline_resample_export.sh 的 span / sk / j / f 四段。
    ★ JOIN 主檔取 capacity：主檔沒有的站寫不進去（level30.docks 要有值），
      這與 Job A 一致 —— 新站由 sync_stations 先進主檔。
    """
    cur.execute(f"""
            WITH span AS (
              -- 每站的有效區間 = 自己在本批資料裡的首末回報。
              -- 站上線前 / 視窗外不造假格點
              SELECT station_uid, min(slot) AS t0, max(slot) AS t1
                FROM {src} GROUP BY station_uid
            ), sk AS (
              SELECT s.station_uid, g.slot
                FROM span s,
                     LATERAL generate_series(s.t0, s.t1, INTERVAL '30 min') g(slot)
            ), j AS (
              -- island 編號：每遇到一格實測就 +1，同一 grp 內共用那格的值
              SELECT sk.station_uid, sk.slot, o.avail,
                     count(o.avail) OVER (PARTITION BY sk.station_uid ORDER BY sk.slot
                                          ROWS UNBOUNDED PRECEDING) AS grp
                FROM sk LEFT JOIN {src} o
                  ON o.station_uid = sk.station_uid AND o.slot = sk.slot
            ), f AS (
              SELECT station_uid, slot, avail AS avail_obs,
                     first_value(avail) OVER pg              AS avail_c,
                     (row_number() OVER pg - 1)::smallint    AS gap_slots
                FROM j
              WINDOW pg AS (PARTITION BY station_uid, grp ORDER BY slot)
            )
            INSERT INTO hackathon_backend_level30
                   (station_uid, slot, avail, docks, is_observed)
            SELECT f.station_uid, f.slot,
                   -- 連缺超過 6 格就留 NULL，不硬填出假的水平線
                   CASE WHEN f.gap_slots > %(carry)s THEN NULL ELSE f.avail_c END,
                   st.capacity,
                   -- 格點層的 is_observed：1=這格有實測，0=carry 填的
                   (f.avail_obs IS NOT NULL)::int::smallint
              FROM f JOIN hackathon_backend_station st
                     ON st.station_uid = f.station_uid
            ON CONFLICT (station_uid, slot) DO UPDATE
               SET avail       = EXCLUDED.avail,
                   docks       = EXCLUDED.docks,
                   is_observed = EXCLUDED.is_observed,
                   is_imputed  = 0          -- 真值進來就不再是補值
             -- ★ 只補缺格 + 蓋掉所有補值：既有「真值」一律不動
             --   （避免事後補的值蓋掉當時實測），但 is_imputed <> 0 的格
             --   必須讓得出來 —— 那正是補值存在的前提（42_..._is_imputed.sql
             --   欄位註解的硬規則 ①），少了這條真值永遠回不來。
             --
             -- ★ 2026-09-02：條件從 `= 1` 放寬成 `<> 0`。is_imputed 變三態
             --   之後（2 = 43_level30_carry.sql 灌的無限 carry），寫死 `= 1`
             --   會讓 Job C 回補的真值蓋不掉那 79 萬格 carry，真值永遠進不來。
             --   出處：meet/20260902/計劃-level30灌歷史與無限carry.md 決策 8。
             WHERE (hackathon_backend_level30.avail IS NULL
                    OR hackathon_backend_level30.is_imputed <> 0)
               AND EXCLUDED.avail IS NOT NULL
        """, {"carry": _CARRY_MAX})
    return cur.rowcount


def rebuild_level30(d0: date, d1: date) -> dict:
    """從 actual_history 重建 level30 的滾動視窗（★ 不打 TDX、不花點數）。

    actual_history 是「TDX 當下的認知」的原始快照（一格一列、不 carry）；
    level30 是模型的輸入（carry ≤6 格、缺格 NULL）。兩張表的關係就是
    「原始 → 重採樣」，所以 level30 被清掉、或重採樣規則改版時，
    從 actual_history 重跑就好 —— 這正是那張表存在的理由
    （40_scheduler_tables.sql 的表註解寫得很清楚）。

    ★ 必須沿用 Job A 的新鮮度規則：SrcUpdateTime 距 slot 超過
      STALE_MAX_HOURS 的快照不進 level30。actual_history 刻意保留了
      那些過期快照當證據（pull_realtime 的檔頭），照單全收會把站台斷訊
      期間 TDX 回的舊值變成一條假的水平線餵給模型。

    回 {"obs", "stations", "level30_new"}。
    """
    t0 = f"{d0} 00:00:00"
    t1 = f"{d1 + timedelta(days=1)} 00:00:00"
    out: dict = {}
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {OBS}")
        cur.execute(f"""
            CREATE UNLOGGED TABLE {OBS} AS
            SELECT station_uid, slot, avail
              FROM hackathon_backend_actual_history
             WHERE slot >= %(t0)s::timestamp AND slot < %(t1)s::timestamp
               AND avail IS NOT NULL
               -- ★ 新鮮度：與 Job A 同一條規則（pull_realtime.parse_rows）
               AND src_update_time IS NOT NULL
               AND slot - (src_update_time AT TIME ZONE %(tz)s)
                   <= make_interval(hours => %(stale)s)
            """, {"t0": t0, "t1": t1, "tz": config.TZ_TAIPEI,
                  "stale": config.STALE_MAX_HOURS})
        cur.execute(f"ALTER TABLE {OBS} ADD PRIMARY KEY (station_uid, slot)")
        cur.execute(f"ANALYZE {OBS}")
        cur.execute(f"SELECT count(*) n, count(DISTINCT station_uid) s FROM {OBS}")
        r = cur.fetchone()
        out["obs"], out["stations"] = r["n"], r["s"]
        out["level30_new"] = _fill_level30(cur, OBS)
    return out


# ════════════════════════════════════════════════════════════
# 3　週期補值（值落表，標 is_imputed=1）
# ════════════════════════════════════════════════════════════
def impute_weekly(d0: date, d1: date) -> dict:
    """把視窗內的洞用「一週前同 slot 的真值」補進 level30，標 is_imputed=1。

    對應計劃-排程自癒 §2「週期補值」；★ 8/28 使用者定案改為**值落表**。
    每輪 Job A 寫完當下那格之後呼叫，讓 level30 隨時是可服務狀態。

    三條硬規則（sql/42_level30_is_imputed.sql 的欄位註解是權威出處）：
      ① 真值可以覆蓋補值 —— 在 _fill_level30() 與 Job A 的 upsert 裡
      ② 缺格率只算真值 —— 在 gap_report() 裡
      ③ 補值的來源必須是真值（is_imputed = 0）——就在下面這段 SQL 裡。
         少了它，上上週的一個數字會沿著每週的洞一路傳下去，
         而且每一格看起來都「有值」。

    ★ 骨架只長到「該站在視窗內首末**真值**」之間 —— 兩側都要有真值才補。
      站台從半夜掛到現在的那種洞，兩側沒有真值，刻意不補：
      那是斷訊不是缺格，補上去等於把故障藏起來
      （同 Job A 不寫過期站進 level30 的理由）。

    回 {"filled", "stations"}。
    """
    t0 = f"{d0} 00:00:00"
    t1 = f"{d1 + timedelta(days=1)} 00:00:00"
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.execute("""
            WITH win AS (
              -- 每站在視窗內的真值區間（★ 只看真值，補值不算）
              SELECT station_uid, min(slot) AS t0, max(slot) AS t1
                FROM hackathon_backend_level30
               WHERE slot >= %(t0)s::timestamp AND slot < %(t1)s::timestamp
                 AND avail IS NOT NULL AND is_imputed = 0
               GROUP BY station_uid
            ), sk AS (
              SELECT w.station_uid, g.slot
                FROM win w,
                     LATERAL generate_series(w.t0, w.t1, INTERVAL '30 min') g(slot)
            ), hole AS (
              -- 骨架上還沒有真值的格 = 要補的洞（含既有的補值，可refresh）
              SELECT sk.station_uid, sk.slot
                FROM sk LEFT JOIN hackathon_backend_level30 l
                       ON l.station_uid = sk.station_uid AND l.slot = sk.slot
               WHERE l.avail IS NULL OR l.is_imputed = 1
            )
            INSERT INTO hackathon_backend_level30
                   (station_uid, slot, avail, docks, is_observed, is_imputed)
            SELECT h.station_uid, h.slot, src.avail, st.capacity, 0, 1
              FROM hole h
              JOIN hackathon_backend_level30 src
                ON src.station_uid = h.station_uid
               AND src.slot = h.slot - make_interval(days => %(back)s)
               AND src.avail IS NOT NULL
               AND src.is_imputed = 0        -- ★ 硬規則 ③：不拿補值再去補值
              JOIN hackathon_backend_station st
                ON st.station_uid = h.station_uid
            ON CONFLICT (station_uid, slot) DO UPDATE
               SET avail = EXCLUDED.avail, docks = EXCLUDED.docks,
                   is_observed = 0, is_imputed = 1
             WHERE hackathon_backend_level30.avail IS NULL
                OR hackathon_backend_level30.is_imputed = 1
            """, {"t0": t0, "t1": t1, "back": config.IMPUTE_LOOKBACK_DAYS})
        filled = cur.rowcount
        cur.execute("""
            SELECT count(DISTINCT station_uid) AS n
              FROM hackathon_backend_level30
             WHERE slot >= %s::timestamp AND slot < %s::timestamp AND is_imputed = 1
            """, (t0, t1))
        return {"filled": filled, "stations": cur.fetchone()["n"]}


def imputed_count(station_uid: str, t0, t1) -> int:
    """某站某區間（含頭含尾）有幾格是週期補值 —— 給 /predict 誠實標註用。

    ★ 只算 is_imputed = 1。無限 carry（=2）走 carried_count()，兩個數字
      刻意分開報：週期補值還有日內節律，長 carry 沒有 —— 07-11 那天
      全市幾乎整天是同一個數字，合報會把這個可信度差異碾掉。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM hackathon_backend_level30 "
            " WHERE station_uid = %s AND slot BETWEEN %s AND %s AND is_imputed = 1",
            (station_uid, t0, t1))
        return cur.fetchone()["n"]


def carried_count(station_uid: str, t0, t1) -> int:
    """某站某區間（含頭含尾）有幾格是無限 carry（is_imputed = 2）。

    ★ 2026-09-02 新增。43_level30_carry.sql 把 4~7 月的洞全部 carry 掉之後，
      /predict 的 48 格 context 不再有 null（missing_slots 恆為 0），
      「這格是延用來的」變成唯一還說得出口的可信度訊號。
      出處：meet/20260902/計劃-level30灌歷史與無限carry.md 決策 11。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM hackathon_backend_level30 "
            " WHERE station_uid = %s AND slot BETWEEN %s AND %s AND is_imputed = 2",
            (station_uid, t0, t1))
        return cur.fetchone()["n"]


# ════════════════════════════════════════════════════════════
# 4　缺格偵測（Job C 判定 d）
# ════════════════════════════════════════════════════════════
def gap_report(d0: date, d1: date) -> dict:
    """視窗 [d0, d1]（含頭含尾，台北日）內的逐日缺格率。

    ★ 分母 = 「視窗內應有格數」= 現役站數 × 48（計劃-排程自癒 §4）。
      現役站 = 主檔 last_seen >= d0 的站；sync_stations 還沒跑過導致
      一個都沒有時，退回主檔全部（寧可高估分母 = 高估缺格率，
      也不要因為分母 0 而永遠判定「沒缺」）。
    ★ 分子只算 avail IS NOT NULL **且 is_imputed = 0** 的列 ——
      「有列但 avail 是 NULL」對模型是缺格；「週期補值填的格」雖然有值，
      但那是我們自己猜的，不能拿來證明「資料齊了」。

    回 {"stations", "slots_per_day", "days": [...], "gap_days": [...],
        "have", "expect", "gap_rate"}。
    """
    slots = 24 * 60 // config.FREQ_MIN
    with get_conn().cursor() as cur:
        cur.execute("SELECT count(*) FILTER (WHERE last_seen >= %s) AS active, "
                    "       count(*) AS total FROM hackathon_backend_station", (d0,))
        r = cur.fetchone()
        n_st = r["active"] or r["total"]

        cur.execute("""
            SELECT g.d::date AS d, count(l.avail) AS have
              FROM generate_series(%s::date, %s::date, INTERVAL '1 day') g(d)
              LEFT JOIN hackathon_backend_level30 l
                     ON l.slot >= g.d
                    AND l.slot <  g.d + INTERVAL '1 day'
                    AND l.avail IS NOT NULL
                    -- ★ 硬規則 ②：週期補值不算「有資料」。算進去的話
                    --   缺格率會被自己補的值灌水，Job C 再也不觸發，
                    --   真值就永遠回不來（42_..._is_imputed.sql 欄位註解）
                    AND l.is_imputed = 0
             GROUP BY 1 ORDER BY 1
            """, (d0, d1))
        rows = cur.fetchall()

    expect_day = n_st * slots
    days = []
    for r in rows:
        rate = 1.0 - (r["have"] / expect_day if expect_day else 0.0)
        days.append({"d": r["d"], "have": r["have"], "expect": expect_day,
                     "gap_rate": round(max(rate, 0.0), 4)})
    have = sum(d["have"] for d in days)
    expect = expect_day * len(days)
    return {
        "stations": n_st, "slots_per_day": slots, "days": days,
        "gap_days": [d["d"] for d in days
                     if d["gap_rate"] > config.BACKFILL_GAP_THRESHOLD],
        "have": have, "expect": expect,
        "gap_rate": round(1.0 - (have / expect if expect else 0.0), 4),
    }


# ════════════════════════════════════════════════════════════
# 冒煙：uv run python -m app.repository.hist_repo --file <歷史 CSV>
#   ★ 不打 TDX。raw/importdata 的日檔與歷史 API 回應同格式，
#     拿它驗守門與重採樣，等於用真資料驗規則而不花點數。
#   ★ 預設 --dry-run（只灌 staging + 守門統計，不寫營運表）。
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    from datetime import datetime

    ap = argparse.ArgumentParser(description="歷史 CSV 落地冒煙（不打 TDX）")
    ap.add_argument("--file", help="歷史 API 同格式的 CSV 檔")
    ap.add_argument("--write", action="store_true",
                    help="真的寫進 actual_history / level30（預設只驗守門）")
    ap.add_argument("--gap", nargs=2, metavar=("D0", "D1"),
                    help="只印該區間的缺格率，不讀檔")
    ap.add_argument("--rebuild", nargs=2, metavar=("D0", "D1"),
                    help="從 actual_history 重建該區間的 level30（不打 TDX）")
    a = ap.parse_args()

    if a.gap:
        rep = gap_report(date.fromisoformat(a.gap[0]), date.fromisoformat(a.gap[1]))
        print(f"現役站 {rep['stations']}｜每日應有 {rep['stations'] * rep['slots_per_day']:,} 格")
        for d in rep["days"]:
            bar = "█" * int(d["gap_rate"] * 40)
            print(f"  {d['d']}  有值 {d['have']:>7,} / {d['expect']:>7,}  "
                  f"缺格 {d['gap_rate']:>7.2%} {bar}")
        print(f"整體缺格率 {rep['gap_rate']:.2%}｜超門檻的日子 {len(rep['gap_days'])} 天")
        raise SystemExit(0)

    if a.rebuild:
        d0, d1 = (date.fromisoformat(a.rebuild[0]), date.fromisoformat(a.rebuild[1]))
        print(f"── 從 actual_history 重建 level30 {d0} ~ {d1}（不打 TDX）")
        r = rebuild_level30(d0, d1)
        print(f"   實測格 {r['obs']:,}｜{r['stations']} 站"
              f"　→ level30 寫入 {r['level30_new']:,} 列")
        rep = gap_report(d0, d1)
        for x in rep["days"]:
            print(f"     {x['d']}  {x['have']:>7,} / {x['expect']:>7,}"
                  f"  缺格 {x['gap_rate']:>7.2%}")
        print(f"   整體缺格率 {rep['gap_rate']:.2%}")
        raise SystemExit(0)

    text = open(a.file, encoding="utf-8-sig").read()
    st = stage_csv(text)
    print(f"── staging {st['rows']:,} 列（截斷列 {st['short']}）")

    with get_conn().cursor() as cur:
        cur.execute(f"SELECT min(substring(src_update_time,1,10)) AS a, "
                    f"       max(substring(src_update_time,1,10)) AS b FROM {STAGE}")
        r = cur.fetchone()
    print(f"   涵蓋 {r['a']} ~ {r['b']}")
    d0, d1 = (datetime.fromisoformat(r["a"]).date(),
              datetime.fromisoformat(r["b"]).date())

    if not a.write:
        with get_conn().cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {STAGE} s WHERE s.station_uid <> '' "
                        f"AND s.src_update_time ~ %s AND s.avail_rent ~ %s "
                        f"AND s.avail_return ~ %s", (_RE_TS, _RE_NUM, _RE_NUM))
            g = cur.fetchone()["n"]
        print(f"   守門通過 {g:,} / {st['rows']:,}（{g / max(st['rows'], 1):.2%}）")
        print("\n（未加 --write：沒有寫進 actual_history / level30）")
        raise SystemExit(0)

    s = ingest(d0, d1)
    print(f"── 守門 {s['gated']:,} 通過／{s['dropped']:,} 擋下")
    print(f"   格內最後一筆 {s['obs']:,} 格｜{s['stations']} 站")
    print(f"   actual_history 新增 {s['actual_new']:,} 列")
    print(f"   level30 補格 {s['level30_new']:,} 列")
