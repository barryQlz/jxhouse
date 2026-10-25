# -*- coding: utf-8 -*-
"""详情页补全：板块/装修/挂牌时间/电梯/梯户比/抵押/房屋用途"""
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from crawl_fang import BASE, UA, COOKIE_FILE, DB, refresh_cookies

NEW_COLS = {
    "district2": "TEXT", "block": "TEXT", "decoration": "TEXT", "list_date": "TEXT",
    "purpose": "TEXT", "elevator": "TEXT", "elevator_ratio": "TEXT",
    "heating": "TEXT", "structure": "TEXT", "mortgage": "TEXT", "last_trade": "TEXT",
    "agent": "TEXT", "valid_from": "TEXT",
}
lock = threading.Lock()
local = threading.local()
counter = {"done": 0, "err": 0}


def get_session(cookies):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                      "Referer": BASE + "/house/"})
    for k, v in cookies.items():
        s.cookies.set(k, v, domain=".fang.com")
    return s


def sess():
    if not hasattr(local, "s"):
        local.s = get_session(json.load(open(COOKIE_FILE)))
    return local.s


def lab(html, name):
    m = re.search(r'<span class="lab">' + re.escape(name) + r'</span><span class="rcont">(.*?)</span>', html, re.S)
    if not m:
        return ""
    t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return "" if t in ("暂无数据", "-", "暂无") else t


def parse_detail(html):
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(j, dict) and j.get("@type") == "Product":
            props = {p.get("name"): p.get("value") for p in j.get("additionalProperty", [])}
            addr = j.get("address", {}) or {}
            off = j.get("offers", {}) or {}
            return {
                "district2": addr.get("addressRegion", ""),
                "block": addr.get("addressDistrict", ""),
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
            }
    return None


def worker(rows):
    out = []
    s = sess()
    for hid, url in rows:
        try:
            r = s.get(url, timeout=20)
            if r.status_code != 200 or 'type="application/ld+json"' not in r.text:
                counter["err"] += 1
                out.append((hid, None))
                continue
            d = parse_detail(r.text)
            out.append((hid, d))
        except Exception:
            counter["err"] += 1
            out.append((hid, None))
        time.sleep(0.05)
    return out


def main():
    con = sqlite3.connect(DB, timeout=30)
    existing = {r[1] for r in con.execute("PRAGMA table_info(listings)")}
    for c, t in NEW_COLS.items():
        if c not in existing:
            con.execute(f"ALTER TABLE listings ADD COLUMN {c} {t}")
    con.commit()

    limit = int(os.environ.get("LIMIT", "0"))
    q = "SELECT house_id, url FROM listings WHERE detail_done=0 ORDER BY total_price ASC"
    if limit:
        q += f" LIMIT {limit}"
    rows = con.execute(q).fetchall()
    print(f"[enrich] 待补全 {len(rows)} 条", flush=True)

    BATCH = 80
    t0 = time.time()
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(worker, batch[j::8]) for j in range(8)]
            results = []
            for f in as_completed(futs):
                results += f.result()
        for hid, d in results:
            if d is None:
                con.execute("UPDATE listings SET detail_done=-1 WHERE house_id=?", (hid,))
                continue
            con.execute(
                "UPDATE listings SET district2=?,block=?,decoration=?,list_date=?,purpose=?,"
                "elevator=?,elevator_ratio=?,heating=?,structure=?,mortgage=?,last_trade=?,"
                "valid_from=?,detail_done=1 WHERE house_id=?",
                (d["district2"], d["block"], d["decoration"], d["list_date"], d["purpose"],
                 d["elevator"], d["elevator_ratio"], d["heating"], d["structure"],
                 d["mortgage"], d["last_trade"], d["valid_from"], hid))
        con.commit()
        counter["done"] += len(results)
        if (i // BATCH) % 10 == 0:
            el = time.time() - t0
            print(f"[enrich] {counter['done']}/{len(rows)} 失败{counter['err']} "
                  f"用时{el/60:.1f}min", flush=True)
    print(f"[enrich][done] 补全 {counter['done']} 条，失败 {counter['err']}", flush=True)
    con.close()


if __name__ == "__main__":
    main()
