# -*- coding: utf-8 -*-
"""校验导出文件"""
import os
import pandas as pd

f = "嘉兴二手房房源数据.xlsx"
print("文件大小 MB:", round(os.path.getsize(f) / 1048576, 2))
xl = pd.ExcelFile(f)
print("工作表:", xl.sheet_names)

for sh in xl.sheet_names[:3]:
    d = pd.read_excel(f, sheet_name=sh)
    print(f"\n[{sh}] {d.shape[0]} 行 x {d.shape[1]} 列")
    print(" 列:", list(d.columns))
    if "区县" in d.columns:
        print(" 区县分布:\n", d["区县"].value_counts().head(10).to_string())
    print(d.head(3).to_string()[:1200])
