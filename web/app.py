# -*- coding: utf-8 -*-
"""嘉兴二手房查询站 —— Flask 后端（局域网访问，移动端优先）"""
import os
import sqlite3
from statistics import median

from flask import Flask, g, jsonify, render_template, request

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)
DB = os.path.join(ROOT, "fang.db")

app = Flask(__name__, template_folder=os.path.join(DIR, "templates"),
            static_folder=os.path.join(DIR, "static"))

VIEW_SQL = """
SELECT house_id, title, community, COALESCE(NULLIF(district2,''), NULLIF(district,''), '未标注') AS district,
       block, layout, rooms, halls, baths, area, orientation, floor_desc, total_floor,
       total_price, unit_price, build_year, decoration, elevator, elevator_ratio,
       purpose, heating, structure, mortgage, list_date, last_trade, url, image
FROM listings
"""


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


def build_comm_stats():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("""CREATE TABLE IF NOT EXISTS comm_stats(
        community TEXT PRIMARY KEY, n INTEGER, med_unit REAL, med_price REAL)""")
    if con.execute("SELECT COUNT(*) FROM comm_stats").fetchone()[0] > 0:
        con.close()
        return
    rows = con.execute("SELECT community, unit_price, total_price FROM listings").fetchall()
    bucket = {}
    for c, up, tp in rows:
        if not c:
            continue
        bucket.setdefault(c, [[], []])
        if up:
            bucket[c][0].append(up)
        if tp:
            bucket[c][1].append(tp)
    data = [(c, len(v[0]) or len(v[1]),
             median(v[0]) if v[0] else None,
             median(v[1]) if v[1] else None) for c, v in bucket.items()]
    con.executemany("INSERT OR REPLACE INTO comm_stats VALUES(?,?,?,?)", data)
    con.commit()
    con.close()
    print(f"[init] 小区统计 {len(data)} 个", flush=True)


def build_filters():
    p = request.args
    where, args = [], []

    q = (p.get("q") or "").strip()
    if q:
        where.append("(community LIKE ? OR title LIKE ? OR block LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like]

    districts = [d for d in (p.get("district") or "").split(",") if d]
    if districts:
        where.append("COALESCE(NULLIF(district2,''), NULLIF(district,''), '未标注') IN (%s)"
                     % ",".join("?" * len(districts)))
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

    if p.get("only_fav") == "1":
        ids = [i for i in (p.get("ids") or "").split(",") if i]
        if not ids:
            where.append("1=0")
        else:
            where.append("house_id IN (%s)" % ",".join("?" * len(ids)))
            args += ids

    sql = " WHERE " + " AND ".join(where) if where else ""
    return sql, args


SORTS = {
    "price_asc": "t.total_price ASC",
    "price_desc": "t.total_price DESC",
    "unit_asc": "t.unit_price ASC",
    "unit_desc": "t.unit_price DESC",
    "area_desc": "t.area DESC",
    "area_asc": "t.area ASC",
    "newest": "IFNULL(t.list_date,'') DESC",
    "deviation": "dev IS NULL, dev ASC",
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/meta")
def meta():
    con = db()
    total = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    budget = con.execute("SELECT COUNT(*) FROM listings WHERE total_price<=40").fetchone()[0]
    districts = con.execute("""
        SELECT COALESCE(NULLIF(district2,''), NULLIF(district,''), '未标注') d, COUNT(*) c
        FROM listings GROUP BY d ORDER BY c DESC""").fetchall()
    decos = con.execute("""
        SELECT decoration d, COUNT(*) c FROM listings
        WHERE IFNULL(decoration,'')<>'' GROUP BY d ORDER BY c DESC""").fetchall()
    last = con.execute("SELECT MAX(crawl_time) FROM listings").fetchone()[0]
    return jsonify({
        "total": total, "budget": budget, "crawl_time": last,
        "districts": [{"name": r["d"], "count": r["c"]} for r in districts],
        "decorations": [{"name": r["d"], "count": r["c"]} for r in decos][:8],
    })


@app.route("/api/items")
def items():
    con = db()
    where, args = build_filters()
    sort = SORTS.get(request.args.get("sort", "price_asc"), SORTS["price_asc"])

    base = (VIEW_SQL + where)
    rows = con.execute(
        f"SELECT t.*, c.med_unit, c.n, "
        f"CASE WHEN t.unit_price>0 AND c.med_unit>0 THEN t.unit_price*1.0/c.med_unit-1 END AS dev "
        f"FROM ({base}) t LEFT JOIN comm_stats c ON t.community=c.community "
        f"ORDER BY {sort} LIMIT ? OFFSET ?",
        args + [int(request.args.get("size", 30)),
                int(request.args.get("page", 0)) * int(request.args.get("size", 30))]
    ).fetchall()

    count_sql = "SELECT COUNT(*) FROM listings" + where
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
    row = con.execute("SELECT * FROM comm_stats WHERE community=?", (name,)).fetchone()
    items = con.execute(
        "SELECT house_id,total_price,unit_price,area,layout,floor_desc,list_date,url "
        "FROM listings WHERE community=? ORDER BY total_price ASC LIMIT 60", (name,)).fetchall()
    info = dict(row) if row else {}
    info["items"] = [dict(i) for i in items]
    info["district"] = con.execute(
        "SELECT COALESCE(NULLIF(district2,''),NULLIF(district,''),'') FROM listings WHERE community=? LIMIT 1",
        (name,)).fetchone()[0]
    info["block"] = con.execute(
        "SELECT IFNULL(block,'') FROM listings WHERE community=? AND IFNULL(block,'')<>'' LIMIT 1",
        (name,)).fetchone()[0]
    return jsonify(info)


@app.route("/api/stats")
def stats():
    con = db()
    rows = con.execute("""
        SELECT COALESCE(NULLIF(district2,''),NULLIF(district,''),'未标注') d,
               COUNT(*) n, ROUND(AVG(total_price),1) avg_price, ROUND(AVG(unit_price)) avg_unit
        FROM listings GROUP BY d ORDER BY n DESC""").fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    build_comm_stats()
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
