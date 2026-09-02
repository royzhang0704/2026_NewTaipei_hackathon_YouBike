# ════════════════════════════════════════════════════════════
# jobs/batch_predict.py —— Job B：批次預測，寫 forecast_history
#
# 對應：計劃-TDX排程與初始化.md §3「Job B」。由 Job A 成功後同程序呼叫，
# 也可以自己跑（補打某個 origin）。
#
# ★ 多站併一個 invoke：DeepAR 的 instances 是陣列，50 站/批 ≈ 32 次 invoke，
#   不是 1,600 次單發（endpoint_repo.invoke_batch）。
#
# ★ 只打「本輪有新鮮資料」的站（anchor == slot）——這是設計決定，理由：
#   level30 沒有本輪列的站（Job A 判定過期的那些），history_repo.tail()
#   會退回更早的 anchor，模型吐的 6 格有一部分落在過去。
#   把它寫成 origin=本輪 的預測是在說謊。這些站的即時預測仍可走 /predict
#   （單站路徑會誠實地用該站自己的 anchor）。跳過數記在 detail。
#
# ★ 單站錯誤不中斷整批：INSUFFICIENT_HISTORY / STATION_UNKNOWN 等
#   逐站記數跳過（計劃 §3 Job B）。一站壞掉不該讓 1,599 站沒有預測。
#
# 用法：
#   ENDPOINT_MOCK=1 uv run python -m jobs.batch_predict          # 全量鏈路
#   uv run python -m jobs.batch_predict --limit 50               # 真 endpoint 只驗 1 批
#   uv run python -m jobs.batch_predict --slot '2026-08-28 15:30'
#   uv run python -m jobs.batch_predict --dry-run                # 只組 payload 不打
#
# ⚠ 真 endpoint 計費中（ml.m5.large）——不用時照 計劃-AWS_Endpoint.md §5 三連刪
# ════════════════════════════════════════════════════════════
import argparse
import sys
import time
from datetime import datetime, timedelta

from app import config
from app.errors import AppError
from app.repository import (endpoint_repo, forecast_run_repo, history_repo,
                            job_run_repo, risk_repo, slot_average_repo,
                            station_repo, sys_config_repo)
from app.repository.db import get_conn
from app.service import risk_service
from app.service.predict_service import build_payload

JOB_NAME = "batch_predict"


def eligible_stations() -> list[dict]:
    """對象：cat 非 NULL，或有指向「有 cat 站」的 proxy（計劃 §3 Job B 步驟 1）。

    ★ 條件與 build_payload() 第 1 步一致 —— 這裡先篩掉，是為了不要對
      注定丟 STATION_UNKNOWN 的站白跑一輪查詢。真正的把關仍在 build_payload。
    """
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT s.station_uid, s.capacity
              FROM hackathon_backend_station s
         LEFT JOIN hackathon_backend_station p ON p.station_uid = s.proxy_station_uid
             WHERE s.cat IS NOT NULL OR p.cat IS NOT NULL
             ORDER BY s.station_uid
        """)
        return cur.fetchall()


def clip_factory(cap: int | None):
    """後處理的 clip —— 規則與 predict_service.predict_one 第 5 步逐字相同
    （負二項支撐 0..∞，小站會預測出超過車樁的台數；capacity 缺值只夾下界）。

    ⚠ 這是刻意的重複：硬性約束禁止改動 predict_one，所以不能把它抽成
      共用函式再讓兩邊呼叫。改其中一邊時，另一邊必須跟著改。
      出處：app/service/predict_service.py 的 clip()。
    """
    def clip(v: float) -> float:
        v = max(float(v), 0.0)
        return round(min(v, cap) if cap is not None else v, 2)
    return clip


def collect(stations: list[dict], slot: datetime) -> tuple[list, dict, dict]:
    """逐站組 payload。回傳 (items, stats, skips)。

    items 每筆 = {"uid", "cap", "instance"}；順序即送出順序，
    invoke_batch 靠位置對回站號。
    skips  = {station_uid: 原因}——主檔要為這些站寫 predict_status='skipped'
             （9/1）：「處理過但沒打」與「還沒處理」必須分得開。
    """
    items, stats, skips = [], {}, {}
    ctx_filled = []

    def bump(k):
        stats[k] = stats.get(k, 0) + 1

    for st in stations:
        try:
            b = build_payload(st["station_uid"], at=slot)
        except AppError as e:
            bump(e.code)                     # INSUFFICIENT_HISTORY / STATION_UNKNOWN…
            skips[st["station_uid"]] = e.code
            continue
        except Exception as e:               # 沒預期到的錯也不能中斷整批
            bump(f"UNEXPECTED:{type(e).__name__}")
            skips[st["station_uid"]] = f"UNEXPECTED:{type(e).__name__}"
            continue

        if b["history"]["anchor"] != slot:
            bump("STALE_ANCHOR")             # 本輪沒有新鮮資料的站，見檔頭
            skips[st["station_uid"]] = "STALE_ANCHOR"
            continue

        inst = b["payload"]["instances"][0]
        ctx_filled.append(sum(v is not None for v in inst["target"]))
        # 週期補值的量要看得到 —— context 看起來滿了，但有一部分不是實測
        stats["imputed_slots"] = stats.get("imputed_slots", 0) + b["imputed_slots"]
        stats["still_missing"] = stats.get("still_missing", 0) + b["missing_slots"]
        items.append({"uid": st["station_uid"], "cap": st["capacity"],
                      "instance": inst})

    if ctx_filled:
        ctx_filled.sort()
        stats["context_min"] = ctx_filled[0]
        stats["context_median"] = ctx_filled[len(ctx_filled) // 2]
    return items, stats, skips


def predict(items: list, batch_size: int) -> tuple[list, dict]:
    """分批 invoke。回傳 (rows, stats)；rows 是 forecast_history 的列。"""
    step = timedelta(minutes=config.FREQ_MIN)
    rows, stats = [], {"batches": 0, "invoke_sec": 0.0, "mock": None}

    for i in range(0, len(items), batch_size):
        chunk = items[i:i + batch_size]
        payload = {
            "instances": [c["instance"] for c in chunk],
            "configuration": {"num_samples": config.NUM_SAMPLES,
                              "output_types": ["quantiles"],
                              "quantiles": [config.Q_LO, config.Q_MID, config.Q_HI]},
        }
        t0 = time.time()
        preds = endpoint_repo.invoke_batch(payload)
        stats["invoke_sec"] += time.time() - t0
        stats["batches"] += 1

        for c, p in zip(chunk, preds):
            stats["mock"] = p["mock"]
            clip = clip_factory(c["cap"])
            # anchor 已在 collect() 確認 == slot，所以 at = slot + 30m×(k+1)
            anchor = datetime.strptime(c["instance"]["start"], "%Y-%m-%d %H:%M:%S") \
                     + step * (len(c["instance"]["target"]) - 1)
            for k in range(config.H):
                rows.append((c["uid"], anchor, anchor + step * (k + 1),
                             clip(p["q19"][k]), clip(p["q50"][k]), clip(p["q90"][k])))
    # rows 的第 1 欄是 uid，upsert() 會把它換成主檔的 run_id
    stats["invoke_sec"] = round(stats["invoke_sec"], 2)
    return rows, stats


def upsert(rows: list, model_job: str, ids: dict[str, int]) -> int:
    """明細寫入。同 (站, origin, at) 重跑覆蓋。

    ★ run_id 掛回主檔（9/1）：rows 的第 1 欄是 uid，這裡換成 ids[uid]。
      主檔必須先寫好才拿得到 id —— FK 擋著，順序不能顛倒。
    """
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.executemany(
            """
            INSERT INTO hackathon_backend_forecast_history
                   (station_uid, origin, at, q19, q50, q90, model_job, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station_uid, origin, at) DO UPDATE SET
                   q19 = EXCLUDED.q19, q50 = EXCLUDED.q50, q90 = EXCLUDED.q90,
                   model_job = EXCLUDED.model_job, run_id = EXCLUDED.run_id,
                   created_at = now()
            """, [r + (model_job, ids[r[0]]) for r in rows])
        return cur.rowcount


# ════════════════════════════════════════════════════════════
# 階段二：風險判定 —— 寫 hackathon_backend_risk_snapshot
#
# ★ 只讀 DB，不打 SageMaker。輸入是 forecast_history + level30 +
#   station_slot_average，全都在庫裡 —— 所以改演算法時可以用
#   --risk-only 掃過去的 origin 重判，endpoint 一次都不用叫。
# ★ 判定邏輯本身一律走 app/service/risk_service.py，與單站頁
#   /stations/{uid}/day 共用同一份，這裡只負責「一次餵全站」。
# ════════════════════════════════════════════════════════════
def write_risk(origin: datetime, algo_ver: str = None) -> dict:
    """對某個 origin 判全站風險並寫入快照表。回傳統計 dict。"""
    algo_ver = algo_ver or config.RISK_ALGO_VER
    step = timedelta(minutes=config.FREQ_MIN)

    # ── 一次撈齊五筆，逐站查是 1,599 次 round-trip，不可以 ──
    stations = station_repo.all_stations()
    with get_conn().cursor() as cur:
        cur.execute("SELECT station_uid, at, q19, q50, q90 "
                    "  FROM hackathon_backend_forecast_history "
                    " WHERE origin = %s ORDER BY station_uid, at", (origin,))
        fc: dict[str, list] = {}
        for r in cur.fetchall():
            fc.setdefault(r["station_uid"], []).append(r)
    with get_conn().cursor() as cur:
        cur.execute("SELECT id, station_uid FROM hackathon_backend_forecast_run "
                    " WHERE origin = %s", (origin,))
        run_ids = {r["station_uid"]: r["id"] for r in cur.fetchall()}
    now_all = history_repo.anchor_all(origin)
    ts_list = [origin] + [origin + step * (k + 1) for k in range(config.H)]
    avg_all = slot_average_repo.series_all(ts_list)
    prv = risk_repo.prev(origin - step)

    origin_ts = origin.strftime("%Y-%m-%d %H:%M:%S")
    rows, c = [], dict.fromkeys(
        ("risk_high", "risk_mid", "risk_low", "risk_none", "risk_no_forecast",
         "refill_stations", "refill_bikes", "remove_stations", "remove_bikes",
         "hold_stations"), 0)

    for st in stations:
        uid = st["station_uid"]
        f = fc.get(uid)
        n = now_all.get(uid)
        cur_avail = n["avail"] if n else None
        carried = bool(n and n["slot"] != origin)

        if f:
            # 樣本不足的桶直接剔掉 —— 寧可退回門檻算法，也不要拿 n=3 當目標
            avg = {ts: v for ts in ts_list
                   if (v := avg_all.get((uid, ts))) and v["n"] >= config.SLOT_AVG_MIN_N}
            rk = risk_service.risk(f, st["capacity"], cur_avail, origin_ts, avg, origin)
        else:
            rk = None

        if rk is None:
            # capacity 缺值也判不出風險（threshold 回 None）—— 與沒預測同義
            status, lv, sh, fu, d = "no_forecast", None, None, None, None
            side = conf = onset = crossed = base = act = bikes = basis = None
            thr = confl = None
        else:
            status = "ok"
            lv = risk_service.LEVEL_N[rk["overall"]]
            sh, fu = rk["shortage"], rk["full"]
            ls, lf = risk_service.LEVEL_N[sh["level"]], risk_service.LEVEL_N[fu["level"]]
            # 較嚴重的一側；同級時缺車優先（與 dispatch 的取邊規則相同）
            s_ = sh if ls >= lf else fu
            side = ("shortage" if ls >= lf else "full") if lv else None
            conf, onset, crossed = s_["confidence"], s_["onset"], s_["now_crossed"]
            thr, base, confl = rk["threshold"], rk["baseline"], rk.get("conflict")
            d = rk["dispatch"] or {}
            act, bikes, basis = d.get("action"), d.get("bikes"), d.get("basis")

        # ── streak 遞推（9/1 定案：overall 口徑，不分缺車／滿站兩側）──
        p = prv.get(uid)
        same_ver = bool(p and p["algo_ver"] == algo_ver)
        if status == "no_forecast":
            # ★ 不歸零也不遞增：漏打一站不代表風險消失，但也沒有證據說又持續了一輪
            streak_n = p["streak_n"] if same_ver else 0
            streak_since = p["streak_since"] if same_ver else None
        elif lv >= 1:
            # 上一輪有風險（含「上一輪漏批但更早之前有風險」的延續）才累加
            cont = same_ver and (p["level_n"] or 0) >= 1
            cont = cont or (same_ver and p["status"] == "no_forecast" and p["streak_n"] >= 1)
            streak_n = (p["streak_n"] + 1) if cont else 1
            streak_since = (p["streak_since"] or origin) if cont else origin
        else:
            streak_n, streak_since = 0, None

        rows.append((run_ids.get(uid), origin, uid, status, lv,
                     risk_service.LEVEL_N[sh["level"]] if sh else None,
                     risk_service.LEVEL_N[fu["level"]] if fu else None,
                     side, confl, conf, thr, onset, cur_avail, carried, crossed,
                     base, act, bikes, basis, streak_n, streak_since, algo_ver))

        key = {None: "risk_no_forecast", 3: "risk_high", 2: "risk_mid",
               1: "risk_low", 0: "risk_none"}[lv]
        c[key] += 1
        if act in ("refill", "remove"):
            c[f"{act}_stations"] += 1
            c[f"{act}_bikes"] += bikes or 0
        elif act == "hold":
            c["hold_stations"] += 1

    risk_repo.upsert_many(rows)
    # 主檔沒有 batch 層的列了 —— 蓋章是「該 origin 全部主檔列一起蓋」
    c["marked"] = forecast_run_repo.mark_risk(origin, algo_ver)
    c["stations"] = len(rows)
    return c


def run(slot: datetime, batch_size: int = config.PREDICT_BATCH_SIZE,
        limit: int | None = None, dry_run: bool = False,
        force: bool = False, risk_only: bool = False) -> str:
    """跑一輪批打。Job A 觸發時呼叫這支；回傳一句話摘要。

    ★ 兩階段（9/1）：① 預測寫 forecast_history ② 風險判定寫 risk_snapshot，
      完成狀態各自記在主檔 forecast_run 的 predict_status / risk_done_at。
    ★ 冪等（§3）：同 origin 已預測過就不重打（--force 覆寫）；
      風險已用同一個 algo_ver 判過就不重判。--risk-only 只跑階段二。
    """
    run_id = None if dry_run else job_run_repo.start(JOB_NAME, slot)
    try:
        # 冪等要在打之前判，所以先算出「這一輪應該是誰打的」
        expect_job = ("MOCK:" if config.MOCK else "") + config.MODEL_INFO["job"]

        if risk_only:
            print("   跳過階段一：--risk-only")
            detail = {"skip_predict": "--risk-only"}
            n_risk = _maybe_risk(slot, force, detail)
            if run_id:
                job_run_repo.finish(run_id, "skipped",
                                    error="預測跳過：--risk-only", detail=detail)
            return "預測跳過（--risk-only）" + (f"；風險判定 {n_risk}" if n_risk else "")

        stations = eligible_stations()
        if limit:
            stations = stations[:limit]

        # ★ 站層冪等（9/1）：主檔說這站這輪已用同一個模型打完，就不重打。
        #   skipped 的站不擋 —— STALE_ANCHOR 是「本輪還沒有新鮮資料」，
        #   資料補到了就該再試一次。這一層同時把「整輪重跑」也解決了：
        #   全部打過 → 沒有站要打 → 自然 skipped，不必再判一次整輪。
        done = set() if (force or dry_run) else forecast_run_repo.done_uids(slot, expect_job)
        if done:
            stations = [st for st in stations if st["station_uid"] not in done]
            print(f"   站層冪等：{len(done)} 站已用 {expect_job} 打過，本輪只補其餘")

        items, s1, skips = collect(stations, slot)
        print(f"   對象 {len(stations)} 站 → 可打 {len(items)}"
              f"（跳過 {len(stations) - len(items)}：{s1}）")

        if not items:
            msg = ("這一輪全部站都已預測過" if done and not stations
                   else "沒有任何站可打（全部被跳過）")
            if run_id:
                if skips:
                    forecast_run_repo.upsert_many(
                        [(u, slot, "skipped", None, expect_job, r, 0)
                         for u, r in skips.items()])
                detail = {**s1, "skipped_by_idempotence": len(done)}
                _maybe_risk(slot, force, detail)
                job_run_repo.finish(run_id, "skipped", stations_ok=0,
                                    error=msg, detail=detail)
            return msg

        rows, s2 = predict(items, batch_size)
        # ★ mock 的列要能從 model_job 認出來，別混進驗收統計
        model_job = ("MOCK:" if s2["mock"] else "") + config.MODEL_INFO["job"]

        # ★ 順序不能顛倒：主檔先寫才有 id，明細的 run_id FK 擋著。
        #   打完才寫主檔（不先佔位 running）—— 中斷的證據由 job_run 的殘列提供，
        #   1,500 列寫兩次不值得。
        now_ts = datetime.now()
        ids = forecast_run_repo.upsert_many(
            [(it["uid"], slot, "done", now_ts, model_job, None, config.H)
             for it in items] +
            [(u, slot, "skipped", None, model_job, r, 0) for u, r in skips.items()])
        n = upsert(rows, model_job, ids)

        detail = {**s1, **s2, "stations": len(items), "rows": n}
        if sys_config_repo.is_virtual():
            detail["virtual_now"] = str(sys_config_repo.effective_now())
        _maybe_risk(slot, force, detail)
        # ★ 預測終點 = origin + H×30 分。前端拿它顯示「預測涵蓋到幾點」，
        #   維運拿它跟 now 比就知道預測有沒有斷。mock 的不寫（那不是真預測）。
        if not s2["mock"]:
            sys_config_repo.set(sys_config_repo.K_FORECAST_END,
                                slot + timedelta(minutes=config.FREQ_MIN * config.H))
        if run_id:
            job_run_repo.finish(run_id, "success", stations_ok=len(items),
                                rows_written=n, detail=detail)
        return (f"{len(items)} 站 × {config.H} 格 = {n} 列，"
                f"{s2['batches']} 批 / {s2['invoke_sec']}s"
                f"{'（MOCK）' if s2['mock'] else ''}")

    except Exception as e:
        # ★ 主檔是站層的，整輪失敗沒有「一列」可以標 failed ——
        #   失敗的證據在 job_run（status='failed' + error）。主檔維持原樣，
        #   下一輪站層冪等會自然重打（沒寫成 done 的站都還在待打清單裡）。
        if run_id:
            job_run_repo.finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
        raise


def _maybe_risk(slot: datetime, force: bool, detail: dict) -> str | None:
    """階段二的守門：判過就不重判，失敗不拖垮整輪。

    ★ 風險寫入失敗不可讓整輪變 failed —— 預測已經寫進去了，那才是主產出。
      predict_status 維持 done、risk_done_at 留 NULL，下一輪或 --risk-only
      會自動補判。這正是兩階段狀態分開存的用處。
    """
    if not force and forecast_run_repo.risk_done(slot, config.RISK_ALGO_VER):
        print(f"   跳過階段二：已用 {config.RISK_ALGO_VER} 判過")
        return None
    try:
        c = write_risk(slot)
        msg = (f"{c['stations']} 站（高 {c['risk_high']}／中 {c['risk_mid']}／"
               f"低 {c['risk_low']}／無 {c['risk_none']}／"
               f"無預測 {c['risk_no_forecast']}）；"
               f"補車 {c['refill_stations']} 站 {c['refill_bikes']} 台／"
               f"取車 {c['remove_stations']} 站 {c['remove_bikes']} 台／"
               f"暫不派車 {c['hold_stations']} 站")
        print(f"   風險判定：{msg}")
        detail.update({f"risk_{k}" if not k.startswith("risk") else k: v
                       for k, v in c.items()})
        return msg
    except Exception as e:                       # noqa: BLE001
        detail["risk_error"] = f"{type(e).__name__}: {e}"
        print(f"   ⚠ 風險判定失敗（預測仍有效）：{detail['risk_error']}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Job B：批次預測寫 forecast_history")
    ap.add_argument("--slot", default=None,
                    help="origin（ISO；預設取 level30 最新 slot）")
    ap.add_argument("--limit", type=int, default=None,
                    help="只打前 N 站（真 endpoint 驗 1 批用 --limit 50）")
    ap.add_argument("--batch", type=int, default=config.PREDICT_BATCH_SIZE)
    ap.add_argument("--dry-run", action="store_true", help="只組 payload 不打 endpoint")
    ap.add_argument("--force", action="store_true",
                    help="無視冪等重跑（站層仍只補缺的站；換模型時全打）")
    ap.add_argument("--risk-only", action="store_true",
                    help="只跑階段二風險判定（不打 endpoint）。改演算法後回填用，"
                         "★ 要照 origin 由舊到新逐輪跑，streak 才接得起來")
    a = ap.parse_args()

    if a.slot:
        slot = datetime.fromisoformat(a.slot)
    else:
        with get_conn().cursor() as cur:
            cur.execute("SELECT max(slot) AS s FROM hackathon_backend_level30")
            slot = cur.fetchone()["s"]
    print(f"── origin {slot}｜mock={config.MOCK}｜batch={a.batch}"
          f"｜algo={config.RISK_ALGO_VER}")

    try:
        print(f"   {run(slot, a.batch, a.limit, a.dry_run, a.force, a.risk_only)}")
    except Exception as e:
        print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        raise

    if not a.dry_run:
        with get_conn().cursor() as cur:
            cur.execute("""
                SELECT count(*) AS 列數, count(DISTINCT station_uid) AS 站數,
                       count(*) FILTER (WHERE NOT (q19 <= q50 AND q50 <= q90)) AS 分位數交叉,
                       min(at) AS 最早, max(at) AS 最晚
                  FROM hackathon_backend_forecast_history WHERE origin = %s""", (slot,))
            r = cur.fetchone()
        print(f"✓ origin={slot}：{r['站數']} 站 / {r['列數']} 列"
              f"（每站 {r['列數'] // max(r['站數'], 1)} 格）｜"
              f"q19≤q50≤q90 交叉 {r['分位數交叉']} 列｜{r['最早']} ~ {r['最晚']}")
        st = forecast_run_repo.round_stats(slot)
        if st["stations"]:
            print(f"  主檔：{st['stations']} 列（done {st['done']}／"
                  f"skipped {st['skipped']}）｜已判風險 {st['risk_done']} 列"
                  f"｜algo={st['algo_ver']}｜model={st['model_job']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
