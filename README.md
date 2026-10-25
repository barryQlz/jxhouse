# 嘉兴二手房数据采集与选房助手

从房天下（搜房网）嘉兴二手房频道采集结构化房源数据，落库 SQLite，导出 Excel，并提供一个局域网可访问、移动端优先的查询网页。

采集于 2026-10-25，共 **19,010** 条房源记录、34 个字段。

## 功能

- **采集**：页面内嵌 `schema.org` JSON-LD 解析，比 DOM 选择器稳定；requests 复用 Cookie 高并发，Cookie 失效时由 Playwright 自动续期
- **扩量**：价格段 × 面积段 × 户型 × 楼层 × 装修 × 朝向 × 楼龄共 552 个切片交叉，突破单一切片 60 条/页上限
- **补全**：详情页多线程补全区县、板块、装修、电梯、梯户比、抵押、挂牌时间等字段
- **导出**：Excel 分 5 个 sheet（全部房源 / 去重后 / 预算内 / 小区汇总 / 数据说明），附小区中位单价、单价偏离度、疑似重复挂牌数
- **查询站**：Flask + 原生 JS 单页，支持按区县/价格/面积/户型/电梯/装修筛选，按单价、总价、面积、偏离小区均价排序，收藏存 localStorage

## 目录结构

```
.
├── crawl_fang.py        # 主采集器（Cookie 复用 + 切片枚举）
├── expand.py            # 多维切片扩采（支持断点续跑）
├── enrich.py            # 详情页补全（ThreadPoolExecutor）
├── refill.py            # 回填建成年代 / 卫数 / 户型
├── export.py            # 导出 Excel
├── scrape.py            # 贝壳采集器（备用，未采用）
├── stats.py             # 数据质量快检
├── check.py             # 导出文件校验
├── fang.db              # SQLite 数据库（19010 条）
├── 嘉兴二手房房源数据.xlsx # 导出的 Excel
└── web/
    ├── app.py           # Flask 后端
    └── templates/
        └── index.html   # 移动端单页
```

## 快速开始

```bash
pip install requests playwright flask pandas openpyxl
```

启动查询站：

```powershell
cd web
$env:PORT="8000"; python app.py
```

- 电脑访问 `http://127.0.0.1:8000`
- 手机（同一 WiFi）访问 `http://<本机局域网IP>:8000`

重新采集（可选）：

```bash
python crawl_fang.py --refresh      # 仅刷新 Cookie
python crawl_fang.py --maxpages 100 # 采集
python expand.py                    # 切片扩采，支持断点续跑
python enrich.py                    # 详情补全
python export.py                    # 导出 Excel
```

## 数据说明

| 项目 | 说明 |
| --- | --- |
| 数据来源 | 房天下 jx.esf.fang.com 二手房频道 |
| 记录数 | 19,010 条（含同一房源多中介重复挂牌） |
| 去重口径 | 同小区 + 同面积 + 同总价 视为同一套 |
| 预算内（≤45 万） | 1,301 条 |
| 主要字段 | 区县、板块、小区、户型、面积、朝向、楼层、总价、单价、装修、电梯、梯户比、抵押、挂牌时间、房源链接 |

## 已知局限

- 平台声明 45,232 套，实采 19,010 套：差额主要来自多中介重复挂牌与历史失效房源
- 建成年代仅约 3.5% 有值（详情页 JSON-LD 与 HTML 标签均缺失该字段，抽样 12 个全部为空）
- 贝壳、链家小区页无「建筑年代」字段，房天下小区页返回 521，无法补齐

## 免责声明

数据仅用于个人购房决策参考，采集内容版权归原平台及发布方所有，请勿用于商业用途或大规模再分发。价格与在售状态可能已变化，请以平台实时信息为准。
