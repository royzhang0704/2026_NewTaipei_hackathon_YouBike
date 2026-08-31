# ════════════════════════════════════════════════════════════
# jobs/sync_stations.py —— 拉 TDX 站點主檔，upsert hackathon_backend_station
#
# 對應：計劃-TDX排程與初始化.md §4 步驟 ①（初始化流程的第一步，但平常也該定期跑）
#
# 做什麼：
#   ① GET /v2/Bike/Station/City/NewTaipei（$top=10000）
#   ② upsert 主檔：站名／座標／容量／地址／行政區／last_seen
#   ③ 新站自動 cat=NULL → 呼叫既有的 31_load_proxy_cat.sh 補鄰站代理
#
# ★ 絕不動的兩組欄位：
#   - cat            只由 30_load_cat_map.sh 灌。cat 是訓練時字典序決定的
#                    embedding 索引，這裡憑空給值 = 安靜地全錯。
#   - proxy_*        只由 31_load_proxy_cat.sh 灌（含 4 道護欄）。
#                    本腳本只負責「呼叫它」，不重寫一份最近鄰邏輯。
#
# ★ 不刪站：TDX 不再回報的站，level30 還有它的歷史，刪了會變孤兒。
#   改用 last_seen —— 只有這次回報到的站會更新成今天，沒更新的就是下架站。
#
# 用法：
#   uv run python -m jobs.sync_stations --dry-run   # 印差異，不寫 DB、不打全量
#   uv run python -m jobs.sync_stations             # 真跑（會打一次全量 TDX）
#   uv run python -m jobs.sync_stations --force-proxy  # 沒新站也重算代理
# ════════════════════════════════════════════════════════════
import argparse
import re
import subprocess
import sys
from datetime import date

from app import config
from app.repository import job_run_repo
from app.repository.db import get_conn
from app.tdx import client

JOB_NAME = "sync_stations"

# 站名前綴：DB 裡存的是去前綴的乾淨站名
# （慣例出處 meet/20260817/lab/44_load_tdx_raw.sh:202）
_PREFIX = re.compile(r"^YouBike\d+\.\d+_")
# 地址回推行政區：LocationTown 恆空，只能從「新北市○○區」抓
#   兩段式：優先抓「新北市○○區」，抓不到就退而找字串裡任何「○○區」
#   （實測有站的地址不帶縣市前綴）。兩者都失敗才交給 town 表的區碼對照。
_TOWN_IN_ADDR = re.compile(r"新北市\s*(\S{1,3}區)")
_TOWN_LOOSE = re.compile(r"(\S{1,3}區)")

# 回報站數低於現有主檔的這個比例就中止 —— 對齊 Job A 的 80% 判定。
# 漏帶 $top 只會回 30 筆且不報錯，這道護欄就是專門擋那種安靜的半份資料。
MIN_COVERAGE = 0.8


def _pick(row: dict, *names, sub: str | None = None):
    """從 TDX 列取值，同時吃巢狀與扁平兩種形狀。

    v2 JSON 回巢狀（StationName.Zh_tw、StationPosition.PositionLat），
    歷史 CSV 是扁平（StationNameZh_tw、PositionLat）。兩種都收，
    省得哪天換來源就整段重寫。
    """
    for n in names:
        if n in row and row[n] not in (None, ""):
            v = row[n]
            if sub and isinstance(v, dict):
                v = v.get(sub)
            if v not in (None, "", {}):
                return v
    return None


def normalize(row: dict) -> dict | None:
    """TDX 一列 → 主檔一列。缺 StationUID 的列直接丟（沒有主鍵無法寫）。"""
    uid = _pick(row, "StationUID")
    if not uid:
        return None
    name = _pick(row, "StationName", "StationNameZh_tw", sub="Zh_tw")
    addr = _pick(row, "StationAddress", "StationAddressZh_tw", sub="Zh_tw")
    lat = _pick(row, "StationPosition", "PositionLat", sub="PositionLat")
    lon = _pick(row, "StationPosition", "PositionLon", sub="PositionLon")
    cap = _pick(row, "BikesCapacity")
    town = None
    if addr:
        m = _TOWN_IN_ADDR.search(addr) or _TOWN_LOOSE.search(addr)
        town = m.group(1) if m else None
    return {
        "station_uid": uid,
        "station_name": _PREFIX.sub("", name) if name else None,
        "town_code": uid[7:9] if len(uid) >= 9 else None,  # 第 8~9 位（0-based 7:9）
        "town_from_addr": town,
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
        "capacity": int(cap) if cap is not None else None,
        "addr_zh": addr,
    }


def _town_of(r: dict) -> str:
    """新站最終會寫進去的 town：town 表（區碼）優先，地址回推次之。"""
    with get_conn().cursor() as cur:
        cur.execute("SELECT town FROM hackathon_backend_town WHERE town_code = %s",
                    (r["town_code"],))
        hit = cur.fetchone()
    return (hit and hit["town"]) or r["town_from_addr"] or "⚠ 查無"


def diff_against_db(rows: list[dict]) -> dict:
    """比對主檔，回傳 {new, changed, missing} —— dry-run 與 log 都用這個。"""
    with get_conn().cursor() as cur:
        cur.execute("SELECT station_uid, station_name, lat, lon, capacity, addr_zh "
                    "FROM hackathon_backend_station")
        cur_rows = {r["station_uid"]: r for r in cur.fetchall()}

    new, changed = [], []
    for r in rows:
        old = cur_rows.get(r["station_uid"])
        if old is None:
            new.append(r)
            continue
        d = {k: (old[k], r[k]) for k in ("station_name", "capacity")
             if r[k] is not None and str(old[k]) != str(r[k])}
        # 座標比到小數 5 位（≈1 公尺）—— TDX 偶爾會有末位抖動，不算異動
        for k in ("lat", "lon"):
            if r[k] is not None and (old[k] is None or abs(float(old[k]) - r[k]) > 1e-5):
                d[k] = (old[k], r[k])
        if d:
            changed.append((r["station_uid"], r["station_name"], d))

    fetched = {r["station_uid"] for r in rows}
    missing = [uid for uid in cur_rows if uid not in fetched]
    return {"new": new, "changed": changed, "missing": missing, "db_total": len(cur_rows)}


def upsert(rows: list[dict]) -> int:
    """一個交易寫完主檔 + 行政區表。回傳寫入列數。

    ★ ON CONFLICT 的 SET 清單刻意不含 cat / proxy_* —— 見檔頭。
    ★ town 的來源優先序：既有 town 表（1,576 站已驗證零衝突）
      > 地址回推 > NULL。不讓地址字串蓋掉已驗證的對照。
    """
    today = date.today()
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.executemany(
            """
            INSERT INTO hackathon_backend_station
                   (station_uid, station_name, town_code, town,
                    lat, lon, capacity, addr_zh, last_seen, cat)
            VALUES (%(station_uid)s, %(station_name)s, %(town_code)s,
                    COALESCE((SELECT town FROM hackathon_backend_town
                               WHERE town_code = %(town_code)s), %(town_from_addr)s),
                    %(lat)s, %(lon)s, %(capacity)s, %(addr_zh)s, %(today)s, NULL)
            ON CONFLICT (station_uid) DO UPDATE SET
                   station_name = COALESCE(EXCLUDED.station_name, hackathon_backend_station.station_name),
                   town_code    = COALESCE(EXCLUDED.town_code,    hackathon_backend_station.town_code),
                   town         = COALESCE(hackathon_backend_station.town, EXCLUDED.town),
                   lat          = COALESCE(EXCLUDED.lat,          hackathon_backend_station.lat),
                   lon          = COALESCE(EXCLUDED.lon,          hackathon_backend_station.lon),
                   capacity     = COALESCE(EXCLUDED.capacity,     hackathon_backend_station.capacity),
                   addr_zh      = COALESCE(EXCLUDED.addr_zh,      hackathon_backend_station.addr_zh),
                   last_seen    = EXCLUDED.last_seen
            """,
            [{**r, "today": today} for r in rows])
        n = cur.rowcount

        # 行政區表跟著補：新區碼 insert、station_count 重算
        # （town 名稱優先用主檔裡已有的中文名，全 NULL 才留空）
        cur.execute("""
            INSERT INTO hackathon_backend_town (town_code, town, station_count)
            SELECT town_code, min(town), count(*)::int
              FROM hackathon_backend_station
             WHERE town_code IS NOT NULL
             GROUP BY town_code
            ON CONFLICT (town_code) DO UPDATE SET
                   town = COALESCE(hackathon_backend_town.town, EXCLUDED.town),
                   station_count = EXCLUDED.station_count
        """)
    return n


def run_proxy_script() -> tuple[bool, str]:
    """呼叫既有的 31_load_proxy_cat.sh（唯一的代理邏輯出處）。"""
    path = config.BASE_DIR / "sql" / "31_load_proxy_cat.sh"
    p = subprocess.run(["bash", str(path)], capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr)


def main() -> int:
    # ★ demo 回放斷路（8/31）：主檔是「現在」的站，demo 活在 2026-05，
    #   同步進來會污染回放（新站沒有 4 月歷史，cat 也對不上訓練字典）
    from app.repository import sys_config_repo
    if sys_config_repo.is_demo():
        print("✗ demo 回放模式生效中，TDX job 停用"
              "（uv run python -m jobs.demo --stop 後恢復）", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description="拉 TDX 站點主檔並 upsert")
    ap.add_argument("--dry-run", action="store_true",
                    help="只印差異，不寫 DB、不記 job_run（仍會打一次 TDX）")
    ap.add_argument("--top", type=int, default=config.TDX_TOP_ALL,
                    help=f"$top（預設 {config.TDX_TOP_ALL}；開發期測試請用 3）")
    ap.add_argument("--force-proxy", action="store_true",
                    help="即使沒有新站也重跑 31_load_proxy_cat.sh")
    a = ap.parse_args()

    run_id = None if a.dry_run else job_run_repo.start(JOB_NAME)
    try:
        raw, bytes_in = client.get(config.TDX_PATH_STATION,
                                   {"$select": config.TDX_SELECT_STATION,
                                    "$top": a.top})
        rows = [r for r in (normalize(x) for x in raw) if r]
        print(f"── TDX 回 {len(raw)} 列 → 可用 {len(rows)} 站")
        print(f"   {client.LAST_ENCODING}｜記進 job_run.bytes_in 的是「線上」那個")

        d = diff_against_db(rows)
        print(f"   主檔現有 {d['db_total']} 站｜新站 {len(d['new'])}｜"
              f"異動 {len(d['changed'])}｜這次沒回報 {len(d['missing'])}")

        # ★ 護欄：半份資料不准寫（多半是漏帶 $top，回 30 筆還不報錯）
        if a.top >= config.TDX_TOP_ALL and len(rows) < d["db_total"] * MIN_COVERAGE:
            msg = (f"回報站數 {len(rows)} < 主檔 {d['db_total']} × {MIN_COVERAGE:.0%}，"
                   f"疑似截斷回應，不寫入")
            print(f"   ✗ {msg}")
            if run_id:
                job_run_repo.finish(run_id, "skipped", stations_ok=len(rows),
                                    bytes_in=bytes_in, error=msg)
            return 1

        for r in d["new"]:
            print(f"   ＋新站 {r['station_uid']} {r['station_name']}"
                  f"｜區碼 {r['town_code']}→{_town_of(r)}"
                  f"｜capacity={r['capacity']}｜{r['addr_zh'] or '⚠ 無地址'}")
        for uid, name, ch in d["changed"][:20]:
            print(f"   ~異動 {uid} {name}：" +
                  "、".join(f"{k} {o}→{n}" for k, (o, n) in ch.items()))
        if len(d["changed"]) > 20:
            print(f"   …另有 {len(d['changed']) - 20} 站異動")
        if d["missing"]:
            print(f"   －未回報（不刪，last_seen 停在舊日期）：{d['missing'][:5]}"
                  f"{' …' if len(d['missing']) > 5 else ''}")

        if a.dry_run:
            print("\n（--dry-run：以上都沒有寫進 DB）")
            return 0

        written = upsert(rows)
        print(f"\n── upsert 完成 {written} 列")

        # ── 新站 → 補鄰站代理 ──
        detail = {"new": len(d["new"]), "changed": len(d["changed"]),
                  "missing": len(d["missing"])}
        if d["new"] or a.force_proxy:
            with get_conn().cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM hackathon_backend_station "
                            "WHERE cat IS NULL AND (lat IS NULL OR lon IS NULL)")
                blind = cur.fetchone()["n"]
            if blind:
                # 31 的護欄會直接 RAISE，先在這裡講清楚是哪種問題
                detail["proxy_skipped"] = f"{blind} 個無 cat 站缺座標"
                print(f"   ⚠ 跳過代理：{detail['proxy_skipped']}（先補座標再手動跑 31）")
            else:
                print(f"── 有 {len(d['new'])} 個新站，跑 31_load_proxy_cat.sh 補代理")
                ok, out = run_proxy_script()
                tail = "\n".join(out.strip().splitlines()[-12:])
                print(tail)
                detail["proxy_ok"] = ok
                if not ok:
                    raise RuntimeError(f"31_load_proxy_cat.sh 失敗：{tail[-300:]}")
        else:
            print("── 沒有新站，不動代理（要重算請加 --force-proxy）")

        with get_conn().cursor() as cur:
            cur.execute("SELECT count(*) AS total, count(cat) AS with_cat, "
                        "       count(proxy_station_uid) AS with_proxy "
                        "FROM hackathon_backend_station")
            s = cur.fetchone()
        print(f"\n✓ 主檔 {s['total']} 站｜有 cat {s['with_cat']}｜有代理 {s['with_proxy']}"
              f"｜無 cat 也無代理 {s['total'] - s['with_cat'] - s['with_proxy']}")

        job_run_repo.finish(run_id, "success", stations_ok=len(rows),
                            rows_written=written, bytes_in=bytes_in, detail=detail)
        return 0

    except Exception as e:
        print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        if run_id:
            job_run_repo.finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
