# -*- coding: utf-8 -*-
"""
房天下 嘉兴二手房 全量采集器
- Cookie 复用（requests 高速）+ 失效时 Playwright 自动续期
- 价格段/面积段切片枚举，SQLite 落库，按 houseid 去重
用法:
  python crawl_fang.py --refresh          # 仅刷新cookie
  python crawl_fang.py --maxpages 100     # 全量采集
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime

import requests

BASE = "https://jx.esf.fang.com"
CITY = "嘉兴"
DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "fang.db")
COOKIE_FILE = os.path.join(DIR, "cookies.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

PRICE_SLICES = [
    ("50万以下", "c20-d250"),
    ("50-80万", "c250-d280"),
    ("80-110万", "c280-d2110"),
    ("110-150万", "c2110-d2150"),
    ("150-200万", "c2150-d2200"),
    ("200万以上", "c2200-d20"),
]
AREA_SLICES = [
    ("50㎡以下", "j20-k250"), ("50-70㎡", "j250-k270"), ("70-90㎡", "j270-k290"),
    ("90-110㎡", "j290-k2110"), ("110-130㎡", "j2110-k2130"), ("130-150㎡", "j2130-k2150"),
    ("150-200㎡", "j2150-k2200"), ("200-300㎡", "j2200-k2300"), ("300㎡以上", "j2300-k20"),
]
DISTRICTS = ["南湖", "秀洲", "经济开发区", "嘉善", "桐乡", "平湖", "海宁", "海盐", "国际商务区", "运河新区", "秀湖"]


def refresh_cookies():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True,
                              args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = b.new_context(user_agent=UA, locale="zh-CN", viewport={"width": 1440, "height": 900})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        page.reload(wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        json.dump(ck, open(COOKIE_FILE, "w"), ensure_ascii=False)
        b.close()
    print(f"[cookie] 已刷新 {len(ck)} 项", flush=True)
    return ck


def make_session(cookies):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                      "Referer": BASE + "/house/"})
    for k, v in cookies.items():
        s.cookies.set(k, v, domain=".fang.com")
    return s


def parse_items(html):
    for b in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(b)
        except Exception:
            continue
        if isinstance(j, dict) and j.get("@type") == "ItemList":
            return j.get("itemListElement", []), j.get("numberOfItems")
    return [], None


def find_district(text):
    for d in DISTRICTS:
        if d in text:
            return d
    return ""


def to_row(el, now):
    it = el.get("item", {})
    url = it.get("url", "")
    m = re.search(r"(\d+_\d+)\.htm", url)
    if not m:
        return None
    props = {p.get("name"): p.get("value") for p in it.get("additionalProperty", [])}
    title = it.get("name", "")
    desc = it.get("description", "")
    floor_txt = props.get("楼层", "")
    mf = re.match(r"(低层|中层|高层|底层|顶层)（共(\d+)层）", floor_txt)
    floor_desc = mf.group(1) if mf else floor_txt
    total_floor = mf.group(2) if mf else ""
    area = re.sub(r"[^\d.]", "", props.get("面积", ""))
    year = re.sub(r"[^\d]", "", props.get("建筑年代", ""))
    layout = props.get("户型", "")
    lm = re.match(r"(\d+)室(\d+)厅(?:(\d+)卫)?", layout)
    off = it.get("offers", {}) or {}
    return {
        "house_id": m.group(1),
        "url": url,
        "title": title,
        "district": find_district(title) or find_district(desc),
        "community": props.get("小区", ""),
        "layout": layout,
        "rooms": lm.group(1) if lm else "",
        "halls": lm.group(2) if lm else "",
        "baths": lm.group(3) if lm and lm.group(3) else "",
        "area": area,
        "orientation": props.get("朝向", ""),
        "floor_desc": floor_desc,
        "total_floor": total_floor,
        "build_year": year,
        "total_price": off.get("price", ""),
        "unit_price": off.get("unitPrice", ""),
        "image": it.get("image", ""),
        "source": "房天下",
        "city": CITY,
        "crawl_time": now,
    }


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS listings(
        house_id TEXT PRIMARY KEY, url TEXT, title TEXT, city TEXT, district TEXT,
        community TEXT, layout TEXT, rooms TEXT, halls TEXT, baths TEXT, area REAL,
        orientation TEXT, floor_desc TEXT, total_floor TEXT, build_year TEXT,
        total_price REAL, unit_price REAL, image TEXT, source TEXT, crawl_time TEXT,
        detail_done INTEGER DEFAULT 0, extra TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_price ON listings(total_price)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_district ON listings(district)")
    con.commit()
    return con


def upsert(con, rows):
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = (f"INSERT OR IGNORE INTO listings({','.join(cols)}) "
           f"VALUES({','.join('?' * len(cols))})")
    cur = con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    con.commit()
    return cur.rowcount


def crawl(s, con, slices, maxpages):
    total_new = 0
    for sname, code in slices:
        consecutive_empty = 0
        hit_cap = True
        for pg in range(1, maxpages + 1):
            url = f"{BASE}/house/{code}/" if pg == 1 else f"{BASE}/house/{code}/i3{pg}/"
            try:
                r = s.get(url, timeout=25)
            except Exception as e:
                print(f"  [{sname}] p{pg} ERR {type(e).__name__}", flush=True)
                time.sleep(8)
                continue
            if r.status_code != 200 or "__jsl_clearance" in r.text[:200]:
                print(f"  [{sname}] p{pg} 状态异常 {r.status_code}", flush=True)
                break
            items, _ = parse_items(r.text)
            if not items:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    hit_cap = False
                    break
                time.sleep(5)
                continue
            consecutive_empty = 0
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = [x for x in (to_row(e, now) for e in items) if x]
            n = upsert(con, rows)
            total_new += n
            print(f"[{sname}] p{pg:>3d} 抓取{len(rows):>3d} 新增{n:>3d} 累计{total_new}", flush=True)
            if pg >= maxpages:
                hit_cap = True
                break
            time.sleep(random.uniform(0.55, 1.15))
        print(f"[{sname}] 结束，本切片新增 {total_new}；需细分={hit_cap}", flush=True)
        if hit_cap:
            # 该价格段触顶，用面积段交叉细分
            for aname, acode in AREA_SLICES:
                sub = f"{code}-{acode}"
                consecutive_empty = 0
                for pg in range(1, maxpages + 1):
                    url = f"{BASE}/house/{sub}/" if pg == 1 else f"{BASE}/house/{sub}/i3{pg}/"
                    try:
                        r = s.get(url, timeout=25)
                    except Exception:
                        time.sleep(8)
                        continue
                    items, _ = parse_items(r.text)
                    if not items:
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            break
                        time.sleep(4)
                        continue
                    consecutive_empty = 0
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    rows = [x for x in (to_row(e, now) for e in items) if x]
                    n = upsert(con, rows)
                    total_new += n
                    print(f"[{sname}/{aname}] p{pg:>3d} 抓取{len(rows):>3d} 新增{n:>3d} 累计{total_new}", flush=True)
                    time.sleep(random.uniform(0.5, 1.0))
    return total_new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--maxpages", type=int, default=100)
    args = ap.parse_args()

    if args.refresh:
        refresh_cookies()
        if not args.maxpages:
            return

    cookies = json.load(open(COOKIE_FILE)) if os.path.exists(COOKIE_FILE) else refresh_cookies()
    s = make_session(cookies)
    con = init_db()

    r = s.get(BASE + "/house/", timeout=25)
    items, total = parse_items(r.text)
    if not items:
        print("[!] cookie 失效，重新获取", flush=True)
        cookies = refresh_cookies()
        s = make_session(cookies)
        r = s.get(BASE + "/house/", timeout=25)
        items, total = parse_items(r.text)
        if not items:
            print("[!] 仍失败，退出", flush=True)
            sys.exit(1)
    print(f"[start] 全站声明房源数 numberOfItems={total}", flush=True)

    t0 = time.time()
    new = crawl(s, con, PRICE_SLICES, args.maxpages)
    cnt = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"[done] 本次新增 {new}，库内共 {cnt} 条，耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    con.close()


if __name__ == "__main__":
    main()
