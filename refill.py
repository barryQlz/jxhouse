# -*- coding: utf-8 -*-
"""回填建筑年代、卫数、完整户型"""
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from crawl_fang import BASE, UA, COOKIE_FILE, DB

tl = threading.local()
cnt = {"n": 0, "err": 0}


def sess():
    if not hasattr(tl, "s"):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Referer": BASE + "/house/"})
        for k, v in json.load(open(COOKIE_FILE)).items():
            s.cookies.set(k, v, domain=".fang.com")
        tl.s = s
    return tl.s


def parse(html):
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(j, dict) and j.get("@type") == "Product":
            props = {p.get("name"): p.get("value") for p in j.get("additionalProperty", [])}
            layout = props.get("户型", "")
            lm = re.match(r"(\d+)室(\d+)厅(?:(\d+)卫)?", layout)
            year = re.sub(r"[^\d]", "", props.get("建筑年代", ""))
            return (year[:4] if year else "",
                    layout,
                    lm.group(1) if lm else "",
                    lm.group(2) if lm else "",
                    lm.group(3) if lm and lm.group(3) else "")
    return None


def work(rows):
    out = []
    s = sess()
    for hid, url in rows:
        try:
            h = s.get(url, timeout=15).text
            d = parse(h)
            out.append((hid, d))
        except Exception:
            cnt["err"] += 1
            out.append((hid, None))
        time.sleep(0.03)
    return out


def main():
    con = sqlite3.connect(DB, timeout=30)
    rows = con.execute(
        "SELECT house_id, url FROM listings WHERE IFNULL(build_year,'')='' OR IFNULL(baths,'')=''"
    ).fetchall()
    print(f"[refill] 待回填 {len(rows)} 条", flush=True)
    B = 100
    t0 = time.time()
    for i in range(0, len(rows), B):
        batch = rows[i:i + B]
        with ThreadPoolExecutor(max_workers=10) as ex:
            res = []
            for f in as_completed([ex.submit(work, batch[j::10]) for j in range(10)]):
                res += f.result()
        for hid, d in res:
            if not d:
                continue
            year, layout, rooms, halls, baths = d
            con.execute(
                "UPDATE listings SET build_year=CASE WHEN IFNULL(build_year,'')='' THEN ? ELSE build_year END,"
                "baths=CASE WHEN IFNULL(baths,'')='' THEN ? ELSE baths END,"
                "layout=CASE WHEN IFNULL(layout,'')='' THEN ? ELSE layout END,"
                "rooms=CASE WHEN IFNULL(rooms,'')='' THEN ? ELSE rooms END,"
                "halls=CASE WHEN IFNULL(halls,'')='' THEN ? ELSE halls END "
                "WHERE house_id=?",
                (year, baths, layout, rooms, halls, hid))
        con.commit()
        cnt["n"] += len(res)
        if (i // B) % 10 == 0:
            print(f"[refill] {cnt['n']}/{len(rows)} 用时{(time.time()-t0)/60:.1f}min", flush=True)
    n1 = con.execute("SELECT COUNT(*) FROM listings WHERE IFNULL(build_year,'')<>''").fetchone()[0]
    n2 = con.execute("SELECT COUNT(*) FROM listings WHERE IFNULL(baths,'')<>''").fetchone()[0]
    print(f"[refill][done] 建筑年代 {n1} 条，卫数 {n2} 条", flush=True)
    con.close()


if __name__ == "__main__":
    main()
