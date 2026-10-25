# -*- coding: utf-8 -*-
"""数据质量快检"""
import sqlite3

con = sqlite3.connect("fang.db", timeout=30)
q = con.execute
print("总数:", q("SELECT COUNT(*) FROM listings").fetchone()[0])
print("有总价:", q("SELECT COUNT(*) FROM listings WHERE total_price>0").fetchone()[0])
print("有面积:", q("SELECT COUNT(*) FROM listings WHERE area>0").fetchone()[0])
print("有区县(列表页):", q("SELECT COUNT(*) FROM listings WHERE district<>''").fetchone()[0])
print("有小区:", q("SELECT COUNT(*) FROM listings WHERE community<>''").fetchone()[0])

print("\n价格分布:")
for r in q("""SELECT CASE WHEN total_price<30 THEN '30万以下'
 WHEN total_price<50 THEN '30-50万' WHEN total_price<80 THEN '50-80万'
 WHEN total_price<110 THEN '80-110万' WHEN total_price<150 THEN '110-150万'
 WHEN total_price<200 THEN '150-200万' ELSE '200万以上' END AS seg, COUNT(*)
 FROM listings WHERE total_price>0 GROUP BY seg ORDER BY MIN(total_price)"""):
    print(f"  {r[0]}: {r[1]}")

print("\n预算内(≤45万):", q("SELECT COUNT(*) FROM listings WHERE total_price<=45").fetchone()[0])
print("区县分布(列表页字段):")
for r in q("SELECT district, COUNT(*) c FROM listings GROUP BY district ORDER BY c DESC LIMIT 12"):
    print(f"  {r[0] or '未标注'}: {r[1]}")

print("\n重复标题TOP5:")
for r in q("SELECT title, COUNT(*) c FROM listings GROUP BY community, area, total_price HAVING c>1 ORDER BY c DESC LIMIT 5"):
    print("  ", str(r[0])[:40], r[1])

print("\n已补全详情:", q("SELECT COUNT(*) FROM listings WHERE detail_done=1").fetchone()[0])
con.close()
