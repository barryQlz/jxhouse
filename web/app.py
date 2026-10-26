# -*- coding: utf-8 -*-
"""环沪 40 万内二手房查询站 —— Flask 后端"""
import json
import os
import sqlite3
from statistics import median

from flask import Flask, g, jsonify, render_template, request

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)
DB = os.path.join(ROOT, "sh_around.db")
ANALYSIS = os.path.join(ROOT, "analysis.json")

app = Flask(__name__, template_folder=os.path.join(DIR, "templates"),
            static_folder=os.path.join(DIR, "static"))

SELECT_COLS = """
house_id, title, city, city_slug, COALESCE(NULLIF(area_name,''), NULLIF(district2,''),
NULLIF(district,''), '未标注') AS area_name, block, community, layout, rooms, halls, baths,
area, orientation, floor_desc, total_floor, total_price, unit_price, build_year, decoration,
elevator, elevator_ratio, purpose, heating, structure, mortgage, list_date, last_trade, authentic,
lat, lon, dist_sh, dist_center, risk_score, risk_label, risk_flags, exclude_reason,
rank_score, value_score, is_apartment, is_small_prop, is_rural, is_fake, is_duplicate,
is_stale, is_incomplete, age_years, loan_status, gjj_status, loan_note, loan_years,
down_payment, can_loan, can_gjj, url, image, source, crawl_time
"""

EXCLUDE_COLS = {
    "ex_apartment": "is_apartment",
    "ex_small": "is_small_prop",
    "ex_rural": "is_rural",
    "ex_fake": "is_fake",
    "ex_dup": "is_duplicate",
    "ex_stale": "is_stale",
    "ex_incomplete": "is_incomplete",
}


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


@app.after_request
def no_keepalive(resp):
    resp.headers["Connection"] = "close"
    return resp


def build_comm_stats():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("""CREATE TABLE IF NOT EXISTS comm_stats(
        city TEXT, community TEXT, n INTEGER, med_unit REAL, med_price REAL,
        PRIMARY KEY(city, community))""")
    con.execute("DELETE FROM comm_stats")
    rows = con.execute(
        "SELECT city, community, unit_price, total_price FROM listings "
        "WHERE total_price>0 AND IFNULL(community,'')<>''").fetchall()
    bucket = {}
    for c, comm, up, tp in rows:
        bucket.setdefault((c, comm), [[], []])
        if up:
            bucket[(c, comm)][0].append(up)
        if tp:
            bucket[(c, comm)][1].append(tp)
    data = [(c, comm, len(v[0]) or len(v[1]),
             median(v[0]) if v[0] else None, median(v[1]) if v[1] else None)
            for (c, comm), v in bucket.items()]
    con.executemany("INSERT OR REPLACE INTO comm_stats VALUES(?,?,?,?,?)", data)
    con.commit()
    con.close()
    print(f"[init] 小区统计 {len(data)} 个", flush=True)


def build_filters():
    p = request.args
    where, args = ["total_price>0", "total_price<=40"], []

    q = (p.get("q") or "").strip()
    if q:
        where.append("(community LIKE ? OR title LIKE ? OR block LIKE ? OR area_name LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like, like]

    cities = [d for d in (p.get("city") or "").split(",") if d]
    if cities:
        where.append("city IN (%s)" % ",".join("?" * len(cities)))
        args += cities

    districts = [d for d in (p.get("district") or "").split(",") if d]
    if districts:
        where.append("area_name IN (%s)" % ",".join("?" * len(districts)))
        args += districts

    for key, col, op in (("pmin", "total_price", ">="), ("pmax", "total_price", "<="),
                         ("amin", "area", ">="), ("amax", "area", "<=")):
        v = p.get(key)
        if v not in (None, ""):
            try:
                where.append(f"{col} {op} ?")
                args.append(float(v))
            except ValueError:
                pass

    rooms = [r for r in (p.get("rooms") or "").split(",") if r]
    if rooms:
        cond = []
        for r in rooms:
            if r == "5":
                cond.append("CAST(IFNULL(rooms,0) AS INT)>=5")
            else:
                cond.append("CAST(IFNULL(rooms,0) AS INT)=?")
                args.append(int(r))
        where.append("(" + " OR ".join(cond) + ")")

    if p.get("elevator") == "1":
        where.append("elevator='有'")
    elif p.get("elevator") == "0":
        where.append("elevator='无'")

    deco = [d for d in (p.get("decoration") or "").split(",") if d]
    if deco:
        where.append("decoration IN (%s)" % ",".join("?" * len(deco)))
        args += deco

    if p.get("purpose") == "住宅":
        where.append("purpose LIKE '%住宅%'")
    elif p.get("purpose") == "非住宅":
        where.append("IFNULL(purpose,'') NOT LIKE '%住宅%'")

    loan = p.get("loan") or ""
    if loan == "commercial":
        where.append("IFNULL(can_loan,0)=1")
    elif loan == "gjj":
        where.append("IFNULL(gjj_status,'') IN ('可公积金','公积金待核')")
    elif loan == "gjj_ok":
        where.append("IFNULL(can_gjj,0)=1")
    elif loan == "none":
        where.append("IFNULL(loan_status,'')='不可贷款'")

    for key, col in EXCLUDE_COLS.items():
        if p.get("all") == "1":
            continue
        if p.get(key, "1") == "1":
            where.append(f"IFNULL({col},0)=0")

    if p.get("only_fav") == "1":
        ids = [i for i in (p.get("ids") or "").split(",") if i]
        if not ids:
            where.append("1=0")
        else:
            where.append("house_id IN (%s)" % ",".join("?" * len(ids)))
            args += ids

    if p.get("mode") == "excluded":
        where = ["total_price>0", "total_price<=40",
                 "(IFNULL(is_apartment,0)=1 OR IFNULL(is_small_prop,0)=1 OR "
                 "IFNULL(is_rural,0)=1 OR IFNULL(is_fake,0)=1 OR IFNULL(is_duplicate,0)=1 OR "
                 "IFNULL(is_stale,0)=1 OR IFNULL(is_incomplete,0)=1)"]
        args = []
        if q:
            where.append("(community LIKE ? OR title LIKE ? OR block LIKE ?)")
            args += [f"%{q}%"] * 3
        if cities:
            where.append("city IN (%s)" % ",".join("?" * len(cities)))
            args += cities

    return " WHERE " + " AND ".join(where), args


SORTS = {
    "recommend": "t.rank_score DESC, t.total_price ASC",
    "price_asc": "t.total_price ASC",
    "price_desc": "t.total_price DESC",
    "unit_asc": "t.unit_price ASC",
    "unit_desc": "t.unit_price DESC",
    "area_desc": "t.area DESC",
    "area_asc": "t.area ASC",
    "newest": "IFNULL(t.list_date,'') DESC",
    "deviation": "dev IS NULL, dev ASC",
    "trust": "t.risk_score DESC, t.total_price ASC",
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/meta")
def meta():
    con = db()
    total = con.execute("SELECT COUNT(*) FROM listings WHERE total_price>0 AND total_price<=40").fetchone()[0]
    clean_cond = ("IFNULL(is_apartment,0)=0 AND IFNULL(is_small_prop,0)=0 AND "
                  "IFNULL(is_rural,0)=0 AND IFNULL(is_fake,0)=0 AND "
                  "IFNULL(is_duplicate,0)=0 AND IFNULL(is_stale,0)=0 AND "
                  "IFNULL(is_incomplete,0)=0")
    clean = con.execute(f"SELECT COUNT(*) FROM listings WHERE total_price>0 AND total_price<=40 AND {clean_cond}").fetchone()[0]
    cities = con.execute(f"""
        SELECT city c, COUNT(*) n FROM listings
        WHERE total_price>0 AND total_price<=40 AND {clean_cond}
        GROUP BY city ORDER BY n DESC""").fetchall()
    districts = con.execute(f"""
        SELECT city||'·'||area_name d, COUNT(*) n FROM listings
        WHERE total_price>0 AND total_price<=40 AND {clean_cond}
        GROUP BY city, area_name ORDER BY n DESC""").fetchall()
    decos = con.execute("""
        SELECT decoration d, COUNT(*) c FROM listings
        WHERE IFNULL(decoration,'')<>'' AND is_apartment=0 AND is_small_prop=0
          AND is_rural=0 AND is_fake=0 AND is_duplicate=0
        GROUP BY d ORDER BY c DESC""").fetchall()
    last = con.execute("SELECT MAX(crawl_time) FROM listings").fetchone()[0]
    analyzed = con.execute("SELECT MAX(analysis_time) FROM listings").fetchone()[0]
    excluded = {
        "公寓/商住": con.execute("SELECT COUNT(*) FROM listings WHERE total_price<=40 AND is_apartment=1").fetchone()[0],
        "小产权": con.execute("SELECT COUNT(*) FROM listings WHERE total_price<=40 AND is_small_prop=1").fetchone()[0],
        "农村/偏远": con.execute("SELECT COUNT(*) FROM listings WHERE total_price<=40 AND is_rural=1").fetchone()[0],
        "疑似虚假": con.execute("SELECT COUNT(*) FROM listings WHERE total_price<=40 AND is_fake=1").fetchone()[0],
        "重复挂牌": con.execute("SELECT COUNT(*) FROM listings WHERE total_price<=40 AND is_duplicate=1").fetchone()[0],
        "挂牌超2年": con.execute("SELECT COUNT(*) FROM listings WHERE total_price<=40 AND is_stale=1").fetchone()[0],
        "信息缺失": con.execute("SELECT COUNT(*) FROM listings WHERE total_price<=40 AND is_incomplete=1").fetchone()[0],
    }
    loan_stats = {
        "可商贷": con.execute(f"SELECT COUNT(*) FROM listings WHERE total_price>0 AND total_price<=40 AND {clean_cond} AND IFNULL(can_loan,0)=1").fetchone()[0],
        "可公积金": con.execute(f"SELECT COUNT(*) FROM listings WHERE total_price>0 AND total_price<=40 AND {clean_cond} AND IFNULL(can_gjj,0)=1").fetchone()[0],
        "公积金待核": con.execute(f"SELECT COUNT(*) FROM listings WHERE total_price>0 AND total_price<=40 AND {clean_cond} AND IFNULL(gjj_status,'')='公积金待核'").fetchone()[0],
        "不可按揭": con.execute(f"SELECT COUNT(*) FROM listings WHERE total_price>0 AND total_price<=40 AND {clean_cond} AND IFNULL(loan_status,'')='不可贷款'").fetchone()[0],
    }
    return jsonify({
        "total": total, "clean": clean, "crawl_time": last, "analysis_time": analyzed,
        "excluded": excluded, "loans": loan_stats,
        "cities": [{"name": r["c"], "count": r["n"]} for r in cities],
        "districts": [{"name": r["d"], "count": r["n"]} for r in districts][:40],
        "decorations": [{"name": r["d"], "count": r["c"]} for r in decos][:8],
    })


@app.route("/api/analysis")
def analysis():
    if not os.path.exists(ANALYSIS):
        return jsonify({"error": "分析结果尚未生成"}), 404
    return jsonify(json.load(open(ANALYSIS, encoding="utf-8")))


@app.route("/api/items")
def items():
    con = db()
    where, args = build_filters()
    sort = SORTS.get(request.args.get("sort", "recommend"), SORTS["recommend"])
    size = min(max(int(request.args.get("size", 30)), 1), 200)
    page = max(int(request.args.get("page", 0)), 0)
    base = f"SELECT {SELECT_COLS} FROM listings{where}"
    rows = con.execute(
        f"SELECT t.*, c.med_unit, c.n, "
        f"CASE WHEN t.unit_price>0 AND c.med_unit>0 THEN t.unit_price*1.0/c.med_unit-1 END AS dev "
        f"FROM ({base}) t LEFT JOIN comm_stats c ON t.city=c.city AND t.community=c.community "
        f"ORDER BY {sort} LIMIT ? OFFSET ?",
        args + [size, page * size]
    ).fetchall()

    count_sql = f"SELECT COUNT(*) FROM listings{where}"
    total = con.execute(count_sql, args).fetchone()[0]

    out = []
    for r in rows:
        d = dict(r)
        if d.get("unit_price") and d.get("med_unit"):
            d["deviation"] = round(d["unit_price"] / d["med_unit"] - 1, 4)
        else:
            d["deviation"] = None
        d["price_per_sqm"] = d.get("unit_price")
        out.append(d)
    return jsonify({"total": total, "items": out})


@app.route("/api/community")
def community():
    con = db()
    name = request.args.get("name", "")
    city = request.args.get("city", "")
    row = con.execute("SELECT * FROM comm_stats WHERE community=? AND (?='' OR city=?) LIMIT 1",
                      (name, city, city)).fetchone()
    q = "SELECT house_id,total_price,unit_price,area,layout,floor_desc,list_date,risk_score,url " \
        "FROM listings WHERE community=?"
    args = [name]
    if city:
        q += " AND city=?"
        args.append(city)
    q += " AND total_price<=40 ORDER BY total_price ASC LIMIT 60"
    items_ = con.execute(q, args).fetchall()
    info = dict(row) if row else {}
    info["items"] = [dict(i) for i in items_]
    info["city"] = city
    info["area_name"] = con.execute(
        "SELECT IFNULL(area_name,'') FROM listings WHERE community=? LIMIT 1", (name,)).fetchone()[0]
    info["block"] = con.execute(
        "SELECT IFNULL(block,'') FROM listings WHERE community=? AND IFNULL(block,'')<>'' LIMIT 1",
        (name,)).fetchone()[0]
    return jsonify(info)


@app.route("/api/stats")
def stats():
    con = db()
    rows = con.execute("""
        SELECT city, area_name d, COUNT(*) n, ROUND(AVG(total_price),1) avg_price,
               ROUND(AVG(unit_price)) avg_unit
        FROM listings WHERE total_price>0 AND total_price<=40
          AND IFNULL(is_apartment,0)=0 AND IFNULL(is_small_prop,0)=0
          AND IFNULL(is_rural,0)=0 AND IFNULL(is_fake,0)=0 AND IFNULL(is_duplicate,0)=0
        GROUP BY city, area_name ORDER BY n DESC""").fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    build_comm_stats()
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
