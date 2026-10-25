# -*- coding: utf-8 -*-
"""
贝壳找房（嘉兴）二手房采集器
- 真实浏览器（Playwright）绕过 hip 滑块
- 按区域翻页，CSV 增量落盘，按房源ID去重
用法: python scrape.py [--only 区域slug] [--maxpages N]
"""
import argparse
import csv
import os
import random
import re
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

BASE = "https://jx.ke.com"
CITY = "嘉兴"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)
CSV_PATH = os.path.join(OUT, "jiaxing_ershoufang.csv")

FIELDS = [
    "房源ID", "来源", "链接", "标题", "城市", "区县", "板块", "小区",
    "户型", "室", "厅", "卫", "建筑面积㎡", "朝向", "楼层", "总楼层", "楼层类别",
    "总价万", "单价元每平", "建成年代", "装修", "电梯", "建筑类型",
    "标签", "关注人数", "带看次数", "发布时长", "VR", "采集时间",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = {runtime: {}};
"""

seen = set()


def load_seen():
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("房源ID"):
                    seen.add(row["房源ID"])
    print(f"[init] 已有记录 {len(seen)} 条", flush=True)


def append_rows(rows):
    new = [r for r in rows if r["房源ID"] not in seen]
    for r in new:
        seen.add(r["房源ID"])
    if not new:
        return 0
    exists = os.path.exists(CSV_PATH) and os.path.getsize(CSV_PATH) > 0
    with open(CSV_PATH, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        for r in new:
            w.writerow(r)
    return len(new)


def is_captcha(page, html):
    return ("hip.ke.com/captcha" in page.url) or ("<title>CAPTCHA" in html) or \
           (len(html) < 20000 and "captcha" in html.lower())


def goto(page, url, retries=4):
    for i in range(retries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(random.randint(800, 1600))
            html = page.content()
        except Exception as e:
            print(f"  [warn] goto异常 {e}", flush=True)
            time.sleep(20)
            continue
        if not is_captcha(page, html):
            return html
        wait = 60 + i * 60 + random.randint(0, 30)
        print(f"  [captcha] 触发验证，等待 {wait}s 后重试 ({i+1}/{retries})", flush=True)
        time.sleep(wait)
        page.goto(BASE + "/ershoufang/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(random.randint(3, 6))
    return None


def clean(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def parse_list(html, district_hint=""):
    out = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    blocks = html.split('<li class="clear"')[1:]
    for b in blocks:
        b = b.split("</li>")[0]
        m = re.search(r"/ershoufang/(\d+)\.html", b)
        if not m:
            continue
        lid = m.group(1)
        url = f"{BASE}/ershoufang/{lid}.html"

        title = ""
        mt = re.search(r'class="title">(.*?)</div>', b, re.S)
        if mt:
            title = clean(mt.group(1))

        pos = re.search(r'class="positionInfo">(.*?)</div>', b, re.S)
        pos_txt, community = "", ""
        if pos:
            pos_txt = clean(pos.group(1)).replace("|", " ")
            anchors = re.findall(r"<a[^>]*>([^<]+)</a>", pos.group(1))
            anchors = [clean(a) for a in anchors if clean(a)]
            if anchors:
                community = anchors[-1]
                if not district_hint and len(anchors) >= 2:
                    district_hint = anchors[0]

        hi = re.search(r'class="houseInfo">(.*?)</div>', b, re.S)
        hi_txt = clean(hi.group(1)) if hi else ""
        parts = [p.strip() for p in re.split(r"[|｜]", hi_txt) if p.strip()]

        layout = rooms = halls = area = orient = floor_desc = total_floor = ""
        build_year = decoration = elevator = btype = ""
        for p in parts:
            if re.fullmatch(r"\d+室\d+厅(\d+卫)?", p):
                layout = p
                lm = re.match(r"(\d+)室(\d+)厅(?:(\d+)卫)?", p)
                rooms, halls = lm.group(1), lm.group(2)
            elif "平米" in p or "㎡" in p:
                am = re.search(r"([\d.]+)", p)
                area = am.group(1) if am else ""
            elif p in ("东", "南", "西", "北", "东南", "西南", "东北", "西北", "南北"):
                orient = p
            elif "楼层" in p:
                fm = re.match(r"(低|中|高)楼层\(共(\d+)层\)", p)
                if fm:
                    floor_desc, total_floor = fm.group(1), fm.group(2)
            elif re.search(r"(年建|年建成)", p):
                ym = re.search(r"(\d{4})", p)
                build_year = ym.group(1) if ym else ""
            elif p in ("精装", "简装", "毛坯", "豪华装修", "中装"):
                decoration = p
            elif "电梯" in p:
                elevator = p
            elif p in ("板楼", "塔楼", "板塔结合", "平房"):
                btype = p

        for kw, slot in (("精装", "精装"), ("简装", "简装"), ("毛坯", "毛坯")):
            if not decoration and kw in hi_txt:
                decoration = slot

        tp = re.search(r'class="totalPrice[^"]*"[^>]*>(.*?)</div>', b, re.S)
        total_price = ""
        if tp:
            pm = re.search(r"([\d.]+)", clean(tp.group(1)))
            total_price = pm.group(1) if pm else ""

        up = re.search(r'class="unitPrice[^"]*"[^>]*>(.*?)</div>', b, re.S)
        unit_price = ""
        if up:
            um = re.search(r"([\d,]+)", clean(up.group(1)))
            unit_price = um.group(1).replace(",", "").replace("元", "") if um else ""

        tags = [clean(t) for t in re.findall(r'class="tag"[^>]*>(.*?)</span>', b, re.S)]
        tags = [t for t in tags if t]

        follow = re.search(r"class=\"followInfo\">(.*?)</div>", b, re.S)
        follow_txt = clean(follow.group(1)) if follow else ""
        fc = re.search(r"(\d+)人关注", follow_txt)
        vc = re.search(r"(\d+)次带看", follow_txt)
        pub = re.search(r"([\d]+(?:个月|天|小时|分钟)以前发布|刚刚发布|昨天发布|今天发布)", follow_txt)

        vr = "是" if ("VR" in b or "vr_logo" in b) else ""

        district = ""
        sub = ""
        for t in [x.strip() for x in re.split(r"[\s/]+", pos_txt) if x.strip()]:
            if t.endswith(("区", "县", "市")) and t != community:
                district = t
                break
        anchors_all = [clean(a) for a in re.findall(r"<a[^>]*>([^<]+)</a>", pos.group(1))] if pos else []
        if len(anchors_all) >= 3:
            sub = anchors_all[1]

        out.append({
            "房源ID": lid, "来源": "贝壳", "链接": url, "标题": title,
            "城市": CITY, "区县": district or district_hint, "板块": sub, "小区": community,
            "户型": layout, "室": rooms, "厅": halls, "卫": "",
            "建筑面积㎡": area, "朝向": orient,
            "楼层": floor_desc, "总楼层": total_floor, "楼层类别": floor_desc,
            "总价万": total_price, "单价元每平": unit_price,
            "建成年代": build_year, "装修": decoration, "电梯": elevator, "建筑类型": btype,
            "标签": "|".join(tags),
            "关注人数": fc.group(1) if fc else "",
            "带看次数": vc.group(1) if vc else "",
            "发布时长": pub.group(1) if pub else "",
            "VR": vr, "采集时间": now,
        })
    return out


def get_districts(page):
    html = goto(page, BASE + "/ershoufang/")
    if not html:
        return []
    # 区域筛选项形如：<a href="/ershoufang/nanhuqu/" title="嘉兴南湖区在售二手房 ">南湖区</a>
    links = re.findall(
        r'href="(/ershoufang/[a-z0-9]+/)"[^>]*title="[^"]*?在售二手房[^"]*"[^>]*>([^<]+)</a>',
        html)
    res, seen_slug = [], set()
    for href, name in links:
        name = clean(name)
        slug = href.strip("/").split("/")[-1]
        if not name or slug in seen_slug:
            continue
        seen_slug.add(slug)
        res.append((name, href))
    return res


def get_total_pages(html):
    m = re.search(r'"totalPage":(\d+)', html)
    return int(m.group(1)) if m else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--maxpages", type=int, default=100)
    args = ap.parse_args()

    load_seen()
    total_new = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage", "--lang=zh-CN"],
        )
        ctx = browser.new_context(
            user_agent=UA, locale="zh-CN", timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
        )
        ctx.add_init_script(STEALTH_JS)
        page = ctx.new_page()
        page.set_default_timeout(45000)

        districts = get_districts(page)
        print("[districts]", districts, flush=True)
        if args.only:
            districts = [d for d in districts if args.only in d[1] or args.only in d[0]]

        for name, href in districts:
            first_url = BASE + href
            html = goto(page, first_url)
            if not html:
                print(f"[skip] {name} 首页失败", flush=True)
                continue
            total_pages = min(get_total_pages(html), args.maxpages)
            rows = parse_list(html, name)
            n = append_rows(rows)
            total_new += n
            print(f"[{name}] p1/{total_pages} +{n}  累计新增 {total_new}", flush=True)

            for pg in range(2, total_pages + 1):
                url = f"{BASE}{href}pg{pg}/"
                html = goto(page, url)
                if not html:
                    print(f"[{name}] p{pg} 失败，跳过", flush=True)
                    continue
                if get_total_pages(html) == 1 and len(parse_list(html)) == 0:
                    print(f"[{name}] p{pg} 空页，结束", flush=True)
                    break
                rows = parse_list(html, name)
                n = append_rows(rows)
                total_new += n
                print(f"[{name}] p{pg}/{total_pages} +{n}  累计新增 {total_new}", flush=True)
                time.sleep(random.uniform(1.2, 2.8))
                if pg % 15 == 0:
                    time.sleep(random.uniform(8, 15))

        browser.close()

    print(f"[done] 本次新增 {total_new}，文件共 {len(seen)} 条 -> {CSV_PATH}", flush=True)


if __name__ == "__main__":
    main()
