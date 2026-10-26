# -*- coding: utf-8 -*-
"""
环沪 40 万内二手房清洗与分析
- 剔除公寓/商住、小产权、农村偏远、疑似虚假、重复挂牌、信息严重缺失
- 计算可信度与推荐分，输出 analysis.json 与 Excel
用法: python analyze.py
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from statistics import median

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "sh_around.db")
OUT_JSON = os.path.join(DIR, "analysis.json")
OUT_XLSX = os.path.join(DIR, "环沪40万二手房分析.xlsx")

APARTMENT_WORDS = ["公寓", "soho", "loft", "酒店式", "单身公寓", "商住", "写字楼", "商铺", "办公", "商业"]
NON_RESIDENTIAL_PURPOSE = ["公寓", "写字楼", "商铺", "办公", "商业", "酒店", "商住", "车位", "车库", "厂房", "其它", "其他"]
SMALL_PROP_WORDS = ["小产权", "农民房", "自建房", "宅基地", "村委", "集资房", "统建楼",
                    "无产权", "私人房", "军产", "农村自建", "集体土地"]
RURAL_WORDS = ["乡", "农场", "林场", "自然村", "屯", "农村", "宅基地", "自建"]
CITY_DISTRICTS = {
    "上海": ["金山", "奉贤", "崇明", "浦东", "闵行", "宝山", "嘉定", "松江", "青浦",
             "杨浦", "虹口", "普陀", "长宁", "徐汇", "静安", "黄浦"],
    "昆山": ["玉山", "花桥", "周市", "陆家", "巴城", "千灯", "张浦", "淀山湖", "锦溪", "周庄", "开发区"],
    "太仓": ["城厢", "娄东", "陆渡", "浏河", "浮桥", "沙溪", "璜泾", "双凤", "港区"],
    "苏州": ["吴中", "相城", "吴江", "姑苏", "工业园区", "高新区", "沧浪", "平江", "金阊"],
    "常熟": ["虞山", "东南开发区", "尚湖", "梅李", "海虞", "古里", "支塘", "沙家浜", "碧溪", "辛庄", "董浜"],
    "南通": ["崇川", "港闸", "通州", "海安", "如皋", "如东", "启东", "海门", "开发区", "苏通"],
    "启东": ["汇龙", "吕四", "海复", "寅阳", "北新", "王鲍", "合作", "南阳", "东海", "近海"],
    "海门": ["海门", "滨江", "三星", "叠石桥", "三厂", "临江", "悦来", "余东", "正余", "四甲"],
    "嘉兴": ["南湖", "秀洲", "嘉善", "平湖", "海盐", "海宁", "桐乡",
             "经济开发区", "国际商务区", "运河新区", "秀湖"],
    "湖州": ["吴兴", "南浔", "长兴", "德清", "安吉", "南太湖"],
}
MARKETING_WORDS = ["急售", "抛售", "甩卖", "白菜价", "跳楼价", "特价", "秒杀", "清盘",
                   "工抵房", "抵账房", "内部房源", "捡漏", "一口价", "包过户"]
COMMERCIAL_NAME_WORDS = ["公馆", "广场", "商城", "大厦", "世界城", "新天地", "国贸",
                         "创新中心", "商贸", "商务", "食品城", "SOHO", "酒店", "国际中心",
                         "轻纺城", "商贸城", "服装城", "建材城", "五金城", "小商品城",
                         "汽配城", "农贸城", "家居城", "机电城"]
STRONG_COMMERCIAL = ["食品城", "轻纺城", "商贸城", "服装城", "建材城", "五金城",
                     "小商品城", "汽配城", "农贸城", "家居城", "机电城", "国贸",
                     "创新中心"]
CITIES = ["上海", "昆山", "太仓", "苏州", "常熟", "南通", "启东", "海门", "嘉兴", "湖州"]
CITY_ORDER = {c: i for i, c in enumerate(CITIES)}


def norm(s):
    return re.sub(r"[\s（）()·,，.。\-]", "", str(s or ""))


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def contains_any(text, words):
    t = str(text or "").lower()
    return [w for w in words if w.lower() in t]


def price_band(p):
    if p < 15:
        return "15万以下"
    if p < 20:
        return "15-20万"
    if p < 25:
        return "20-25万"
    if p < 30:
        return "25-30万"
    if p < 35:
        return "30-35万"
    return "35-40万"


def area_band(a):
    if not a:
        return "未知"
    if a < 30:
        return "30㎡以下"
    if a < 50:
        return "30-50㎡"
    if a < 70:
        return "50-70㎡"
    if a < 90:
        return "70-90㎡"
    if a < 120:
        return "90-120㎡"
    return "120㎡以上"


def find_area(city, text):
    for w in sorted(CITY_DISTRICTS.get(city, []), key=len, reverse=True):
        if w in text:
            return w
    return ""


def ensure_columns(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(listings)")}
    need = {
        "area_name": "TEXT", "district_med_unit": "REAL", "community_med_unit": "REAL",
        "city_med_unit": "REAL", "exclude_reason": "TEXT", "risk_label": "TEXT",
        "is_incomplete": "INTEGER DEFAULT 0", "dup_group": "TEXT", "value_score": "REAL",
    }
    for c, t in need.items():
        if c not in cols:
            con.execute(f"ALTER TABLE listings ADD COLUMN {c} {t}")
    con.commit()


def load_rows(con):
    cur = con.execute("SELECT * FROM listings WHERE total_price>0 AND total_price<=40")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def compute_medians(rows):
    city_units, district_units, community_units = {}, {}, {}
    for r in rows:
        up = to_float(r.get("unit_price"))
        if not up or up <= 0:
            continue
        city_units.setdefault(r["city"], []).append(up)
        area = r.get("area_name") or ""
        if area:
            district_units.setdefault((r["city"], area), []).append(up)
        comm = norm(r.get("community"))
        if comm:
            community_units.setdefault((r["city"], comm), []).append(up)
    city_med = {k: median(v) for k, v in city_units.items() if v}
    district_med = {k: median(v) for k, v in district_units.items() if len(v) >= 8}
    community_med = {k: median(v) for k, v in community_units.items() if len(v) >= 3}
    community_n = {k: len(v) for k, v in community_units.items()}
    return city_med, district_med, community_med, community_n


def classify(rows, con):
    city_med, district_med, community_med, community_n = compute_medians(rows)
    today = datetime.now().date()

    for r in rows:
        title = r.get("title") or ""
        desc = r.get("description") or ""
        community = r.get("community") or ""
        block = r.get("block") or ""
        district = r.get("district2") or r.get("district") or ""
        if not district:
            district = find_area(r["city"], " ".join([title, community, block, r.get("address") or ""]))
        if not district:
            first = re.split(r"[\s\|·,，!！]", title.strip())[0]
            bad = any(w in first for w in MARKETING_WORDS + [
                "低总价", "小户型", "精装", "电梯", "首付", "户型", "急卖", "业主", "房东", "降价"])
            if 2 <= len(first) <= 6 and not re.search(r"\d", first) and not bad:
                district = first
        purpose = r.get("purpose") or ""
        text = " ".join([title, desc, community, block, r.get("address") or ""])
        area = to_float(r.get("area"))
        price = to_float(r.get("total_price"))
        unit = to_float(r.get("unit_price"))
        if not unit and area and price:
            unit = round(price * 10000 / area, 0)
            con.execute("UPDATE listings SET unit_price=? WHERE house_id=?", (unit, r["house_id"]))
        r["unit_price"] = unit
        r["area_name"] = district or "未标注"

        reasons, flags = [], []
        apartment = bool(contains_any(text, APARTMENT_WORDS) or
                         (purpose and any(w in purpose for w in NON_RESIDENTIAL_PURPOSE)))
        if apartment:
            reasons.append("公寓/商住/非住宅")
            flags.append("公寓商住")

        small = bool(contains_any(text, SMALL_PROP_WORDS))
        if not small and unit and area and area >= 140 and unit < 2200:
            small = True
            flags.append("低价大面积疑似小产权")
        if small:
            reasons.append("小产权/无产权")
            flags.append("小产权")

        rural = bool(contains_any(text, RURAL_WORDS))
        if re.search(r"(?<!新)村", text):
            rural = True
        dist_center = to_float(r.get("dist_center"))
        dist_sh = to_float(r.get("dist_sh"))
        if not rural and "镇" in (block + community + district) and dist_center and dist_center > 22:
            rural = True
        if not rural and dist_center and dist_center > 45:
            rural = True
        if not rural and dist_sh and dist_sh > 165:
            rural = True
        if rural:
            reasons.append("农村/偏远")
            flags.append("农村偏远")

        incomplete = not community or not area or not price or not unit
        if incomplete:
            reasons.append("信息缺失")
            flags.append("信息缺失")

        fake = False
        ck = (r["city"], norm(community))
        dk = (r["city"], r["area_name"])
        r["city_med_unit"] = city_med.get(r["city"])
        r["district_med_unit"] = district_med.get(dk)
        r["community_med_unit"] = community_med.get(ck)
        if not apartment and not small:
            try:
                nrooms = int(float(r.get("rooms") or 0))
            except (TypeError, ValueError):
                nrooms = 0
            comm_text = community + " " + title
            if nrooms <= 1 and any(w in comm_text for w in COMMERCIAL_NAME_WORDS):
                apartment = True
                reasons.append("公寓/商住/非住宅")
                flags.append("名称疑似商住公寓")
            if any(w in community for w in STRONG_COMMERCIAL):
                apartment = True
                reasons.append("公寓/商住/非住宅")
                flags.append("名称疑似商业公寓")
        if unit and area and price:
            calc = price * 10000 / area
            if abs(calc - unit) / max(unit, 1) > 0.25:
                fake = True
                flags.append("单价与总价不符")
            if unit < 1500 or unit > 150000:
                fake = True
                flags.append("单价异常")
            if area < 10 or area > 500:
                fake = True
                flags.append("面积异常")
            if price < 5:
                fake = True
                flags.append("总价异常")
            dm = r["district_med_unit"]
            cm = r["community_med_unit"]
            if dm and unit < dm * 0.45:
                fake = True
                flags.append("远低于区县均价")
            if cm and community_n.get(ck, 0) >= 3 and unit < cm * 0.52:
                fake = True
                flags.append("远低于小区均价")
        if fake:
            reasons.append("疑似虚假/异常低价")
            flags.append("疑似虚假")

        dup = False
        if community and area and price:
            r["dup_group"] = f"{r['city']}|{norm(community)}|{round(area)}|{round(price)}"
        else:
            r["dup_group"] = ""

        stale = False
        ld = (r.get("list_date") or "")[:10]
        if ld:
            try:
                if datetime.strptime(ld, "%Y-%m-%d").date() < today - timedelta(days=730):
                    stale = True
                    flags.append("挂牌超2年")
            except ValueError:
                pass

        marketing = contains_any(text, MARKETING_WORDS)
        if marketing:
            flags.append("营销标题")

        risk = 100
        risk -= 55 * (1 if fake else 0)
        risk -= 70 * (1 if (apartment or small) else 0)
        risk -= 60 * (1 if rural else 0)
        risk -= 25 * (1 if dup else 0)
        risk -= 15 * (1 if stale else 0)
        risk -= 8 * (1 if incomplete else 0)
        risk -= 4 * min(len(marketing), 2)
        if dm and unit:
            dev = unit / dm - 1
            if dev < -0.3:
                risk -= 12
            elif dev < -0.15:
                risk -= 4
        risk = max(0, min(100, risk))
        if (r.get("authentic") or "") == "是":
            risk = min(100, risk + 4)
            flags.append("平台认证")

        value = 0
        if dm and unit:
            value = max(-50, min(50, (dm - unit) / dm * 100))
        freshness = 0
        if ld:
            try:
                days = (today - datetime.strptime(ld, "%Y-%m-%d").date()).days
                freshness = 12 if days <= 180 else (6 if days <= 365 else 0)
            except ValueError:
                freshness = 0
        rank = round(risk * 0.55 + value * 0.25 + freshness + (6 if r.get("elevator") == "有" else 0), 1)

        r["is_apartment"] = int(apartment)
        r["is_small_prop"] = int(small)
        r["is_rural"] = int(rural)
        r["is_fake"] = int(fake)
        r["is_duplicate"] = int(dup)
        r["is_stale"] = int(stale)
        r["is_incomplete"] = int(incomplete)
        r["risk_score"] = risk
        r["value_score"] = round(value, 1)
        r["rank_score"] = rank
        r["risk_flags"] = "|".join(dict.fromkeys(flags))
        r["exclude_reason"] = "|".join(dict.fromkeys(reasons))
        r["risk_label"] = ("高风险" if risk < 45 else "需核实" if risk < 70 else
                           "较可信" if risk < 88 else "可信")

    groups = {}
    for r in rows:
        if r.get("dup_group"):
            groups.setdefault(r["dup_group"], []).append(r)

    def completeness(r):
        return sum(1 for k in ("purpose", "lat", "lon", "list_date", "elevator",
                               "decoration", "build_year", "district2", "block") if r.get(k))

    for g in groups.values():
        if len(g) < 2:
            continue
        g.sort(key=lambda r: (completeness(r), r.get("risk_score") or 0, r.get("list_date") or ""),
               reverse=True)
        for r in g[1:]:
            r["is_duplicate"] = 1
            flags = [x for x in (r.get("risk_flags") or "").split("|") if x] + ["重复挂牌"]
            r["risk_flags"] = "|".join(dict.fromkeys(flags))
            reasons = [x for x in (r.get("exclude_reason") or "").split("|") if x] + ["重复挂牌(多中介)"]
            r["exclude_reason"] = "|".join(dict.fromkeys(reasons))
            r["risk_score"] = max(0, (r.get("risk_score") or 100) - 25)
            r["rank_score"] = round((r.get("rank_score") or 0) - 14, 1)
            r["risk_label"] = ("高风险" if r["risk_score"] < 45 else "需核实" if r["risk_score"] < 70
                               else "较可信" if r["risk_score"] < 88 else "可信")

    for r in rows:
        con.execute(
            "UPDATE listings SET area_name=?,unit_price=?,city_med_unit=?,district_med_unit=?,"
            "community_med_unit=?,is_apartment=?,is_small_prop=?,is_rural=?,is_fake=?,"
            "is_duplicate=?,is_stale=?,is_incomplete=?,risk_score=?,value_score=?,rank_score=?,"
            "risk_flags=?,exclude_reason=?,risk_label=?,dup_group=?,analysis_time=? WHERE house_id=?",
            (r["area_name"], r["unit_price"], r["city_med_unit"], r["district_med_unit"],
             r["community_med_unit"], r["is_apartment"], r["is_small_prop"], r["is_rural"],
             r["is_fake"], r["is_duplicate"], r["is_stale"], r["is_incomplete"],
             r["risk_score"], r["value_score"], r["rank_score"], r["risk_flags"],
             r["exclude_reason"], r["risk_label"], r.get("dup_group") or "",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r["house_id"]))
    con.commit()
    return rows


def summarize(rows):
    total = len(rows)
    excluded = {
        "公寓/商住/非住宅": sum(1 for r in rows if r["is_apartment"]),
        "小产权/无产权": sum(1 for r in rows if r["is_small_prop"]),
        "农村/偏远": sum(1 for r in rows if r["is_rural"]),
        "疑似虚假/异常": sum(1 for r in rows if r["is_fake"]),
        "重复挂牌": sum(1 for r in rows if r["is_duplicate"]),
        "挂牌超2年": sum(1 for r in rows if r["is_stale"]),
        "信息缺失": sum(1 for r in rows if r["is_incomplete"]),
    }
    clean = [r for r in rows if not any(
        r[k] for k in ("is_apartment", "is_small_prop", "is_rural", "is_fake", "is_duplicate", "is_incomplete"))
        and not r["is_stale"]]

    def agg(keyf, items):
        out = {}
        for r in items:
            k = keyf(r) or "未知"
            out.setdefault(k, {"n": 0, "prices": [], "units": []})
            out[k]["n"] += 1
            if r.get("total_price"):
                out[k]["prices"].append(r["total_price"])
            if r.get("unit_price"):
                out[k]["units"].append(r["unit_price"])
        return [{"key": k, "count": v["n"],
                 "median_price": round(median(v["prices"]), 1) if v["prices"] else None,
                 "median_unit": round(median(v["units"])) if v["units"] else None}
                for k, v in sorted(out.items(), key=lambda x: -x[1]["n"])]

    cities = agg(lambda r: r["city"], clean)
    cities.sort(key=lambda x: CITY_ORDER.get(x["key"], 99))
    districts = agg(lambda r: f"{r['city']}·{r['area_name']}", clean)[:30]
    price_bands = agg(lambda r: price_band(r["total_price"]), clean)
    area_bands = agg(lambda r: area_band(r.get("area")), clean)
    rooms = agg(lambda r: (str(int(float(r["rooms"]))) + "室") if r.get("rooms") else "未知", clean)
    comms = agg(lambda r: f"{r['city']}·{r['community']}" if r.get("community") else None, clean)[:30]

    def sample(items, n=8):
        return [{k: r.get(k) for k in
                 ("house_id", "city", "area_name", "block", "community", "title", "total_price",
                  "unit_price", "area", "layout", "list_date", "risk_score", "risk_flags",
                  "exclude_reason", "url")}
                for r in sorted(items, key=lambda x: x["total_price"])[:n]]

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_in_budget": total,
        "clean_count": len(clean),
        "excluded": excluded,
        "cities": cities,
        "districts": districts,
        "price_bands": price_bands,
        "area_bands": area_bands,
        "rooms": rooms,
        "communities": comms,
        "samples": {
            "fake": sample([r for r in rows if r["is_fake"]]),
            "apartment": sample([r for r in rows if r["is_apartment"]]),
            "rural": sample([r for r in rows if r["is_rural"]]),
        },
        "method": [
            "价格：仅保留总价 ≤40 万且总价、面积可计算的房源",
            "公寓/商住：房屋用途或标题/描述含公寓、SOHO、LOFT、写字楼、商铺、商住等",
            "小产权：标题/描述/地址含小产权、农民房、自建房、宅基地、村委、集资房等",
            "农村偏远：含村/乡/农场等关键词，或距所属城市中心 >45km、距上海 >165km",
            "疑似虚假：单价与总价明显不符、单价 <1500 元/㎡、面积异常，或单价低于区县中位 45%、低于小区中位 52%",
            "重复挂牌：同城市+同小区+同面积+同总价视为多中介重复挂牌，仅保留一条",
            "挂牌超2年：视为可能已失效，默认不进入精选",
        ],
    }


def export_excel(rows, summary):
    import pandas as pd
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    def frame(items):
        data = []
        for i, r in enumerate(items, 1):
            data.append({
                "序号": i, "城市": r.get("city"), "区县/板块": r.get("area_name"),
                "片区": r.get("block"), "小区": r.get("community"), "标题": r.get("title"),
                "总价(万)": r.get("total_price"), "单价(元/㎡)": r.get("unit_price"),
                "面积(㎡)": r.get("area"), "户型": r.get("layout"), "楼层": r.get("floor_desc"),
                "电梯": r.get("elevator"), "装修": r.get("decoration"), "房屋用途": r.get("purpose"),
                "建成年代": r.get("build_year"), "挂牌时间": r.get("list_date"),
                "距上海市中心(km)": r.get("dist_sh"), "距本市中心(km)": r.get("dist_center"),
                "可信度": r.get("risk_score"), "评级": r.get("risk_label"),
                "推荐分": r.get("rank_score"), "风险标记": r.get("risk_flags"),
                "排除原因": r.get("exclude_reason"), "房源链接": r.get("url"),
            })
        return pd.DataFrame(data)

    clean = [r for r in rows if not any(
        r[k] for k in ("is_apartment", "is_small_prop", "is_rural", "is_fake", "is_duplicate", "is_incomplete"))
        and not r["is_stale"]]
    clean.sort(key=lambda r: (-(r.get("rank_score") or 0), r.get("total_price") or 999))
    excluded = [r for r in rows if r not in clean]
    excluded.sort(key=lambda r: r.get("risk_score") or 0)

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        frame(clean).to_excel(w, sheet_name="精选房源", index=False)
        frame(rows).to_excel(w, sheet_name="40万内全部", index=False)
        frame(excluded).to_excel(w, sheet_name="已过滤房源", index=False)
        pd.DataFrame(summary["cities"]).rename(columns={
            "key": "城市", "count": "精选套数", "median_price": "中位总价(万)",
            "median_unit": "中位单价(元/㎡)"}).to_excel(w, sheet_name="城市统计", index=False)
        pd.DataFrame(summary["districts"]).rename(columns={
            "key": "城市·区县", "count": "精选套数", "median_price": "中位总价(万)",
            "median_unit": "中位单价(元/㎡)"}).to_excel(w, sheet_name="区县统计", index=False)
        pd.DataFrame(summary["communities"]).rename(columns={
            "key": "城市·小区", "count": "精选套数", "median_price": "中位总价(万)",
            "median_unit": "中位单价(元/㎡)"}).to_excel(w, sheet_name="小区统计", index=False)
        pd.DataFrame({"过滤项": list(summary["excluded"].keys()),
                      "套数": list(summary["excluded"].values())}).to_excel(w, sheet_name="过滤统计", index=False)
        pd.DataFrame({"说明": summary["method"]}).to_excel(w, sheet_name="清洗规则", index=False)

        for ws in w.book.worksheets:
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F3B73")
                cell.alignment = Alignment(horizontal="center", vertical="center")
            for i, col in enumerate(ws.columns, 1):
                width = max((len(str(c.value)) for c in col[:200] if c.value is not None), default=8)
                ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 42)
            if ws.title not in ("清洗规则",):
                ws.auto_filter.ref = ws.dimensions
    print(f"[excel] -> {OUT_XLSX}")


def main():
    con = sqlite3.connect(DB, timeout=60)
    ensure_columns(con)
    rows = load_rows(con)
    print(f"[analyze] 40万内 {len(rows)} 条", flush=True)
    rows = classify(rows, con)
    summary = summarize(rows)
    json.dump(summary, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[analyze] 清洗后 {summary['clean_count']} 条，排除 {summary['total_in_budget']-summary['clean_count']} 条")
    for k, v in summary["excluded"].items():
        print(f"  - {k}: {v}")
    for c in summary["cities"]:
        print(f"  {c['key']}: {c['count']} 套，中位总价 {c['median_price']} 万，中位单价 {c['median_unit']} 元/㎡")
    export_excel(rows, summary)
    con.close()


if __name__ == "__main__":
    main()
