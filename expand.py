# -*- coding: utf-8 -*-
"""
多维切片扩采：价格/面积/户型/楼层/装修/楼龄/朝向 交叉组合，逼近全量
复用 crawl_fang 的解析与入库逻辑
"""
import json
import os
import random
import sqlite3
import time
from datetime import datetime

import requests

from crawl_fang import (BASE, UA, COOKIE_FILE, DB, init_db, parse_items,
                        refresh_cookies, to_row, upsert)

PRICE = ["c20-d250", "c250-d280", "c280-d2110", "c2110-d2150", "c2150-d2200", "c2200-d20"]
AREA = ["j20-k250", "j250-k270", "j270-k290", "j290-k2110", "j2110-k2130",
        "j2130-k2150", "j2150-k2200", "j2200-k2300", "j2300-k20"]
LAYOUT = ["g21", "g22", "g23", "g24", "g25", "g299"]
FLOOR = ["f40", "f41", "f42", "f43", "f44"]
DECOR = ["t21", "t22", "t23", "t24", "t25"]
ORIENT = ["p20", "p21", "p22", "p23", "p24", "p25"]
AGE = ["s22", "r22-s25", "r25-s210", "r210"]
BTYPE = ["m21", "m22", "m23"]
PURPOSE = ["a21"]  # 个人房源


def build_slices():
    out = []
    for p in PRICE:
        for g in LAYOUT:
            out.append(f"{p}-{g}")
    for p in PRICE:
        for f in FLOOR:
            out.append(f"{p}-{f}")
    for p in PRICE:
        for t in DECOR:
            out.append(f"{p}-{t}")
    for p in PRICE:
        for o in ORIENT:
            out.append(f"{p}-{o}")
    for a in AREA:
        for g in LAYOUT:
            out.append(f"{a}-{g}")
    for ag in AGE:
        for g in LAYOUT:
            out.append(f"{ag}-{g}")
    for p in PRICE:
        for m in BTYPE:
            out.append(f"{p}-{m}")
    for p in PRICE:
        for a in AREA:
            for g in LAYOUT:
                out.append(f"{p}-{a}-{g}")
    return list(dict.fromkeys(out))


def main():
    pages = int(os.environ.get("PAGES", "2"))
    ckpt_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expand_ckpt.txt")
    start_idx = 0
    if os.path.exists(ckpt_file):
        try:
            start_idx = int(open(ckpt_file).read().strip())
        except Exception:
            start_idx = 0
    cookies = json.load(open(COOKIE_FILE))
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                      "Referer": BASE + "/house/"})
    for k, v in cookies.items():
        s.cookies.set(k, v, domain=".fang.com")

    con = init_db()
    slices = build_slices()
    print(f"[expand] 切片数 {len(slices)}，每片 {pages} 页", flush=True)
    total_new = 0
    start = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"[expand] 起始库内 {start} 条", flush=True)

    for i, code in enumerate(slices, 1):
        if i <= start_idx:
            continue
        for pg in range(1, pages + 1):
            url = f"{BASE}/house/{code}/" if pg == 1 else f"{BASE}/house/{code}/i3{pg}/"
            try:
                r = s.get(url, timeout=25)
            except Exception:
                time.sleep(6)
                continue
            items, _ = parse_items(r.text)
            if not items:
                break
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = [x for x in (to_row(e, now) for e in items) if x]
            n = upsert(con, rows)
            total_new += n
            time.sleep(random.uniform(0.5, 1.0))
        if i % 10 == 0:
            cnt = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            print(f"[expand] {i}/{len(slices)} 切片，库内 {cnt} 条（新增 {total_new}）", flush=True)
            open(ckpt_file, "w").write(str(i))
        if i % 60 == 0:
            time.sleep(random.uniform(6, 12))

    cnt = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"[expand][done] 新增 {total_new}，库内共 {cnt} 条", flush=True)
    con.close()


if __name__ == "__main__":
    main()
