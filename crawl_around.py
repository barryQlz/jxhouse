# -*- coding: utf-8 -*-
"""
环沪（上海周边）总价 40 万以内二手房采集器
- 城市：上海 / 昆山 / 太仓 / 苏州 / 常熟 / 南通 / 启东 / 海门 / 嘉兴 / 湖州
- 列表页：schema.org JSON-LD，价格筛选 c20-d240（<=40万）
- 详情页：补全房屋用途、装修、电梯、挂牌时间、经纬度等
用法:
  python crawl_around.py --list-only
  python crawl_around.py
  python crawl_around.py --city jx --limit 100
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "sh_around.db")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

CITIES = [
    ("上海", "sh"),
    ("昆山", "ks"),
    ("太仓", "tc"),
    ("苏州", "sz"),
    ("常熟", "changshu"),
    ("南通", "nt"),
    ("启东", "qidong"),
    ("海门", "haimen"),
    ("嘉兴", "jx"),
    ("湖州", "huzhou"),
]
PRICE_CODE = "c20-d240"

SH_CENTER = (31.2304, 121.4737)
CITY_CENTER = {
    "上海": (31.2304, 121.4737), "昆山": (31.3856, 120.9807), "太仓": (31.4577, 121.1306),
    "苏州": (31.2989, 120.5853), "常熟": (31.6538, 120.7521), "南通": (31.9802, 120.8943),
    "启东": (31.8102, 121.6575), "海门": (31.8697, 121.1769), "嘉兴": (30.7522, 120.7500),
    "湖州": (30.8943, 120.0868),
}

NEW_COLS = {
    "city_slug": "TEXT", "district2": "TEXT", "block": "TEXT", "decoration": "TEXT",
    "purpose": "TEXT", "elevator": "TEXT", "elevator_ratio": "TEXT", "heating": "TEXT",
    "structure": "TEXT", "mortgage": "TEXT", "list_date": "TEXT", "valid_from": "TEXT",
    "last_trade": "TEXT", "agent": "TEXT", "authentic": "TEXT",
    "address": "TEXT", "lat": "REAL", "lon": "REAL", "description": "TEXT",
    "dist_sh": "REAL", "dist_center": "REAL",
    "is_apartment": "INTEGER DEFAULT 0", "is_small_prop": "INTEGER DEFAULT 0",
    "is_rural": "INTEGER DEFAULT 0", "is_fake": "INTEGER DEFAULT 0",
    "is_duplicate": "INTEGER DEFAULT 0", "is_stale": "INTEGER DEFAULT 0",
    "risk_score": "INTEGER DEFAULT 0", "risk_flags": "TEXT", "rank_score": "REAL",
    "analysis_time": "TEXT",
}

lock = threading.Lock()
local = threading.local()
stat = {"detail_ok": 0, "detail_err": 0}


def base_url(slug):
    return f"https://{slug}.esf.fang.com"


def otherid_cookie():
    now = datetime.now(timezone(timedelta(hours=8)))
    raw = "ETFio#dr" + now.strftime("%Y%m%d%H") + "lzkrZlt"
    return hashlib.md5(raw.encode()).hexdigest()


def make_session(slug):
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": base_url(slug) + "/house/",
    })
    s.cookies.set("otherid", otherid_cookie(), domain=".fang.com")
    return s


def sess(slug):
    if not hasattr(local, "s"):
        local.s = make_session(slug)
    return local.s


def parse_items(html):
    for b in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(b)
        except Exception:
            continue
        if isinstance(j, dict) and j.get("@type") == "ItemList":
            return j.get("itemListElement", []), j.get("numberOfItems")
    return [], None


def lab(html, name):
    m = re.search(r'<span class="lab">' + re.escape(name) + r'</span><span class="rcont">(.*?)</span>', html, re.S)
    if not m:
        return ""
    t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return "" if t in ("暂无数据", "-", "暂无") else t


def haversine(a, b):
    if not a or not b:
        return None
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    d = 2 * math.asin(math.sqrt(math.sin((lat2 - lat1) / 2) ** 2 +
                                math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2))
    return round(6371 * d, 1)


def to_row(el, city, slug, now):
    it = el.get("item", {})
    url = it.get("url", "")
    m = re.search(r"(\d+_\d+)\.htm", url)
    if not m:
        return None
    props = {p.get("name"): p.get("value") for p in it.get("additionalProperty", [])}
    floor_txt = props.get("楼层", "")
    mf = re.match(r"(低层|中层|高层|底层|顶层)（共(\d+)层）", floor_txt)
    layout = props.get("户型", "")
    lm = re.match(r"(\d+)室(\d+)厅(?:(\d+)卫)?", layout)
    off = it.get("offers", {}) or {}
    area = re.sub(r"[^\d.]", "", props.get("面积", ""))
    year = re.sub(r"[^\d]", "", props.get("建筑年代", ""))
    return {
        "house_id": f"{slug}:{m.group(1)}",
        "url": url,
        "title": it.get("name", ""),
        "city": city,
        "city_slug": slug,
        "district": "",
        "community": props.get("小区", ""),
        "layout": layout,
        "rooms": lm.group(1) if lm else "",
        "halls": lm.group(2) if lm else "",
        "baths": lm.group(3) if lm and lm.group(3) else "",
        "area": area,
        "orientation": props.get("朝向", ""),
        "floor_desc": mf.group(1) if mf else floor_txt,
        "total_floor": mf.group(2) if mf else "",
        "build_year": year[:4],
        "total_price": off.get("price", ""),
        "unit_price": off.get("unitPrice", ""),
        "image": it.get("image", ""),
        "source": "房天下",
        "crawl_time": now,
        "description": it.get("description", ""),
    }


def init_db():
    con = sqlite3.connect(DB, timeout=60)
    con.execute("""CREATE TABLE IF NOT EXISTS listings(
        house_id TEXT PRIMARY KEY, url TEXT, title TEXT, city TEXT, district TEXT,
        community TEXT, layout TEXT, rooms TEXT, halls TEXT, baths TEXT, area REAL,
        orientation TEXT, floor_desc TEXT, total_floor TEXT, build_year TEXT,
        total_price REAL, unit_price REAL, image TEXT, source TEXT, crawl_time TEXT,
        detail_done INTEGER DEFAULT 0, extra TEXT)""")
    existing = {r[1] for r in con.execute("PRAGMA table_info(listings)")}
    for c, t in NEW_COLS.items():
        if c not in existing:
            con.execute(f"ALTER TABLE listings ADD COLUMN {c} {t}")
    con.execute("CREATE INDEX IF NOT EXISTS idx_city ON listings(city)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_price ON listings(total_price)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_district ON listings(district2)")
    con.commit()
    return con


def upsert(con, rows):
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = (f"INSERT INTO listings({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
           f"ON CONFLICT(house_id) DO UPDATE SET "
           + ",".join(f"{c}=excluded.{c}" for c in cols if c != "house_id"))
    cur = con.executemany(sql, [tuple(r.get(c, "") for c in cols) for r in rows])
    con.commit()
    return len(rows)


def fetch_list(s, url, retries=4):
    for attempt in range(retries):
        try:
            r = s.get(url, timeout=25)
            if r.status_code == 200 and "captcha" not in r.url and "__jsl_clearance" not in r.text[:400]:
                items, total = parse_items(r.text)
                if items:
                    return items, total
            time.sleep(3 + attempt * 3)
        except Exception:
            time.sleep(3 + attempt * 3)
    return [], None


def crawl_city(con, city, slug, maxpages=100):
    s = make_session(slug)
    first = f"{base_url(slug)}/house/{PRICE_CODE}/"
    items, total = fetch_list(s, first)
    if not items:
        print(f"[{city}] 首页重试后仍无数据（可能触发验证码），跳过", flush=True)
        return 0, 0
    pages = min(max(int((total or len(items)) / 60) + 1, 1), maxpages)
    print(f"[{city}] 平台声明 {total} 条，计划 {pages} 页", flush=True)
    got = 0
    for pg in range(1, pages + 1):
        if pg > 1:
            url = f"{base_url(slug)}/house/{PRICE_CODE}-i3{pg}/"
            items, _ = fetch_list(s, url)
        if not items:
            print(f"[{city}] p{pg} 空页/验证码，提前结束", flush=True)
            break
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [x for x in (to_row(e, city, slug, now) for e in items) if x]
        upsert(con, rows)
        got += len(rows)
        if pg % 10 == 0 or pg == pages:
            print(f"[{city}] p{pg}/{pages} 累计 {got}", flush=True)
        time.sleep(random.uniform(0.25, 0.55))
    return got, total


def parse_detail(html):
    for b in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(b)
        except Exception:
            continue
        if isinstance(j, dict) and j.get("@type") == "Product":
            props = {p.get("name"): p.get("value") for p in j.get("additionalProperty", [])}
            addr = j.get("address", {}) or {}
            off = j.get("offers", {}) or {}
            geo = j.get("geo", {}) or {}
            try:
                lat = float(geo.get("latitude"))
                lon = float(geo.get("longitude"))
            except (TypeError, ValueError):
                lat = lon = None
            layout = props.get("户型", "")
            lm = re.match(r"(\d+)室(\d+)厅(?:(\d+)卫)?", layout)
            year = re.sub(r"[^\d]", "", props.get("建筑年代", ""))
            return {
                "district2": addr.get("addressRegion", ""),
                "block": addr.get("addressDistrict", ""),
                "address": addr.get("streetAddress", ""),
                "description": j.get("description", ""),
                "decoration": props.get("装修情况", ""),
                "list_date": props.get("挂牌时间", ""),
                "purpose": props.get("房屋用途", ""),
                "elevator": lab(html, "有无电梯"),
                "elevator_ratio": lab(html, "梯户比例"),
                "heating": lab(html, "供暖方式"),
                "structure": lab(html, "房屋结构"),
                "mortgage": lab(html, "抵押信息"),
                "last_trade": lab(html, "上次交易"),
                "valid_from": (off.get("validFrom") or "")[:10],
                "layout": layout,
                "rooms": lm.group(1) if lm else "",
                "halls": lm.group(2) if lm else "",
                "baths": lm.group(3) if lm else "",
                "build_year": year[:4],
                "area": re.sub(r"[^\d.]", "", props.get("建筑面积", "")),
                "lat": lat,
                "lon": lon,
            }
    return None


def _pc(html, key):
    m = re.search(r"pageConfig\." + re.escape(key) + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^;\n]*))", html)
    if not m:
        return ""
    v = next((x for x in m.groups() if x is not None), "")
    return v.strip().strip(";").strip()


def parse_pageconfig(html):
    proj = _pc(html, "projname")
    price = _pc(html, "price")
    if not proj and not price:
        return None
    area = _pc(html, "area")
    room, hall, toilet = _pc(html, "room"), _pc(html, "hall"), _pc(html, "toilet")
    layout = ""
    if room:
        layout = f"{room}室{hall or '0'}厅" + (f"{toilet}卫" if toilet else "")
    year = _pc(html, "createtime")
    ym = re.search(r"(19|20)\d{2}", year)
    list_date = _pc(html, "inserttime")[:10]
    authentic = "是" if _pc(html, "isauthentichouse") in ("1", "true", "True") \
        or _pc(html, "iscredible") in ("1", "true", "True") else ""
    return {
        "district2": _pc(html, "district"),
        "block": _pc(html, "comarea"),
        "address": _pc(html, "address"),
        "description": _pc(html, "housetitle") or f"{proj} {layout} {area}㎡",
        "decoration": "",
        "list_date": list_date,
        "purpose": _pc(html, "purpose"),
        "elevator": lab(html, "有无电梯"),
        "elevator_ratio": lab(html, "梯户比例"),
        "heating": lab(html, "供暖方式"),
        "structure": lab(html, "房屋结构"),
        "mortgage": lab(html, "抵押信息"),
        "last_trade": lab(html, "上次交易"),
        "valid_from": list_date,
        "layout": layout,
        "rooms": room,
        "halls": hall,
        "baths": toilet,
        "build_year": ym.group(0) if ym else "",
        "area": area,
        "lat": None,
        "lon": None,
        "authentic": authentic,
        "community": proj,
    }


def detail_worker(slug, rows):
    out = []
    for hid, url in rows:
        d = None
        for attempt in range(3):
            s = make_session(slug)
            try:
                r = s.get(url, timeout=(5, 15))
                if (r.status_code == 200 and "captcha" not in r.url
                        and "check.html" not in r.url):
                    d = parse_detail(r.text) or parse_pageconfig(r.text)
                    if d:
                        break
            except Exception:
                pass
            time.sleep(1 + attempt * 2 + random.uniform(0, 1))
        out.append((hid, d))
        time.sleep(random.uniform(0.12, 0.3))
    return out


def enrich(con, city=None, limit=0, workers=12):
    q = "SELECT house_id, url, city_slug, city FROM listings WHERE IFNULL(detail_done,0)=0"
    args = []
    if city:
        q += " AND city=?"
        args.append(city)
    q += " ORDER BY total_price ASC"
    if limit:
        q += f" LIMIT {limit}"
    rows = con.execute(q, args).fetchall()
    if not rows:
        print("[detail] 无待补全记录", flush=True)
        return
    print(f"[detail] 待补全 {len(rows)} 条，线程 {workers}", flush=True)
    groups = {}
    for hid, url, slug, c in rows:
        groups.setdefault(slug, []).append((hid, url))
    t0 = time.time()
    done_total = 0
    for slug, sub in groups.items():
        print(f"[detail] {slug} {len(sub)} 条", flush=True)
        B = workers * 8
        for i in range(0, len(sub), B):
            batch = sub[i:i + B]
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(detail_worker, slug, batch[j::workers]) for j in range(workers)]
                res = []
                for f in as_completed(futs):
                    res += f.result()
            ok_rows = []
            for hid, d in res:
                if not d:
                    stat["detail_err"] += 1
                    continue
                stat["detail_ok"] += 1
                c = con.execute("SELECT city FROM listings WHERE house_id=?", (hid,)).fetchone()
                city_name = c[0] if c else ""
                lat, lon = d.get("lat"), d.get("lon")
                center = CITY_CENTER.get(city_name)
                d["dist_sh"] = haversine((lon, lat), (SH_CENTER[1], SH_CENTER[0])) if lat and lon else None
                d["dist_center"] = haversine((lon, lat), (center[1], center[0])) if (lat and lon and center) else None
                ok_rows.append((
                    d["district2"], d["block"], d["decoration"], d["list_date"], d["purpose"],
                    d["elevator"], d["elevator_ratio"], d["heating"], d["structure"], d["mortgage"],
                    d["last_trade"], d["valid_from"], d["address"], d["lat"], d["lon"], d["description"],
                    d["dist_sh"], d["dist_center"], d.get("authentic") or "", d.get("community") or "",
                    d["layout"], d["rooms"], d["halls"], d["baths"],
                    d["build_year"], d["area"], hid))
            con.executemany(
                "UPDATE listings SET district2=?,block=?,decoration=?,list_date=?,purpose=?,"
                "elevator=?,elevator_ratio=?,heating=?,structure=?,mortgage=?,last_trade=?,"
                "valid_from=?,address=?,lat=?,lon=?,description=?,dist_sh=?,dist_center=?,"
                "authentic=?,community=COALESCE(NULLIF(?,''),community),"
                "layout=COALESCE(NULLIF(?,''),layout),"
                "rooms=COALESCE(NULLIF(?,''),rooms),"
                "halls=COALESCE(NULLIF(?,''),halls),"
                "baths=COALESCE(NULLIF(?,''),baths),"
                "build_year=COALESCE(NULLIF(?,''),build_year),"
                "area=COALESCE(CAST(NULLIF(?,'') AS REAL),area),"
                "detail_done=1 WHERE house_id=?",
                ok_rows)
            con.commit()
            done_total += len(res)
            if (i // B) % 5 == 0:
                el = time.time() - t0
                print(f"[detail] {done_total}/{len(rows)} 成功{stat['detail_ok']} "
                      f"失败{stat['detail_err']} 用时{el/60:.1f}min", flush=True)
    print(f"[detail][done] 成功 {stat['detail_ok']} 失败 {stat['detail_err']}", flush=True)


def import_legacy_jiaxing(con):
    old = os.path.join(DIR, "fang.db")
    if not os.path.exists(old):
        print("[import] 未找到 fang.db，跳过")
        return 0
    src = sqlite3.connect(old, timeout=30)
    src.row_factory = sqlite3.Row
    rows = src.execute("SELECT * FROM listings WHERE total_price<=40 AND total_price>0").fetchall()
    src.close()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = []
    for r in rows:
        d = dict(r)
        desc = d.get("title") or f"{d.get('community','')} {d.get('layout','')} {d.get('area','')}㎡"
        data.append((
            "jx:" + str(d["house_id"]), d.get("url") or "", d.get("title") or "",
            "嘉兴", "jx", d.get("district") or "", d.get("community") or "",
            d.get("layout") or "", d.get("rooms") or "", d.get("halls") or "", d.get("baths") or "",
            d.get("area") or 0, d.get("orientation") or "", d.get("floor_desc") or "",
            d.get("total_floor") or "", d.get("build_year") or "", d.get("total_price") or 0,
            d.get("unit_price") or 0, d.get("image") or "", "房天下(历史库)", d.get("crawl_time") or now,
            1, d.get("district2") or "", d.get("block") or "", d.get("decoration") or "",
            d.get("list_date") or "", d.get("purpose") or "", d.get("elevator") or "",
            d.get("elevator_ratio") or "", d.get("heating") or "", d.get("structure") or "",
            d.get("mortgage") or "", d.get("last_trade") or "", d.get("valid_from") or "",
            desc, d.get("agent") or "",
        ))
    con.executemany(
        "INSERT OR IGNORE INTO listings(house_id,url,title,city,city_slug,district,community,"
        "layout,rooms,halls,baths,area,orientation,floor_desc,total_floor,build_year,total_price,"
        "unit_price,image,source,crawl_time,detail_done,district2,block,decoration,list_date,"
        "purpose,elevator,elevator_ratio,heating,structure,mortgage,last_trade,valid_from,"
        "description,agent) VALUES(" + ",".join("?" * 36) + ")", data)
    con.commit()
    print(f"[import] 嘉兴历史库 <=40万 导入 {len(data)} 条", flush=True)
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="", help="城市中文名或域名前缀")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--import-jx", action="store_true")
    ap.add_argument("--detail-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--maxpages", type=int, default=100)
    args = ap.parse_args()

    con = init_db()
    if args.import_jx:
        import_legacy_jiaxing(con)
    cities = CITIES
    if args.city:
        cities = [c for c in CITIES if args.city in (c[0], c[1])]
        if not cities:
            print("未找到城市", args.city)
            return

    t0 = time.time()
    if not args.detail_only:
        for city, slug in cities:
            got, total = crawl_city(con, city, slug, args.maxpages)
            print(f"[{city}][list] 入库 {got} / 平台 {total}", flush=True)
        cnt = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        print(f"[list][done] 库内 {cnt} 条，用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)

    if not args.list_only:
        enrich(con, args.city, args.limit, args.workers)
        cnt = con.execute("SELECT COUNT(*) FROM listings WHERE detail_done=1").fetchone()[0]
        print(f"[all][done] 已补全 {cnt} 条，总用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    con.close()


if __name__ == "__main__":
    main()
