# 大商所/郑商所/上期所 核心期货品种持仓龙虎榜、仓单库存与合约价差全景看板

本项目是一套面向国内主要商品期货（焦煤 JM、焦炭 J、纯碱 SA、玻璃 FG、甲醇 MA、螺纹钢 RB、氧化铝 AO、热轧卷板 HC 等 8 大核心品种）的**席位日成交持仓龙虎榜**、**指定交割仓库仓单日报库存**以及**郑商所纯碱/玻璃 01·05·09 合约季节性价差**的全自动采集、预处理与多看板可视化系统。

---

## 📁 架构与目录文件清单

```text
futures-dragon-tiger/
├── .github/
│   └── workflows/
│       └── daily_sync.yml             # GitHub Actions 自动化定时同步与数据回写流水线
├── data/
│   ├── coking_coal.db                 # 【核心底座】SQLite 统一结构化数据库 (含日历、状态、龙虎榜、仓单与日行情)
│   ├── coking_coal_positions.json     # 龙虎榜历史全量聚合数据源 (供 HTTP 模式加载)
│   ├── coking_coal_positions.data.js  # 龙虎榜离线数据垫片 (window.__COKING_COAL_DATA__)
│   ├── coking_coal_receipts.json      # 仓单全量聚合数据源 (由 SQLite 物化导出)
│   ├── coking_coal_receipts.data.js   # 仓单离线数据垫片 (window.__COKING_COAL_RECEIPT_DATA__)
│   ├── czce_spread_bars.json          # 纯碱/玻璃 01·05·09 近4年日行情聚合数据源 (由 SQLite 物化导出)
│   ├── czce_spread_bars.data.js       # 合约价差离线数据垫片 (window.__CZCE_SPREAD_DATA__)
│   └── receipt_daily/                 # 仓单历史降级目录 (主流程已迁移至 SQLite)
├── modules/                           # 【二级目录】核心数据采集、存储与导出引擎
│   ├── __init__.py                    # 模块导出定义
│   ├── db_manager.py                  # SQLite 数据底座管理引擎 (表架构维护、状态记录、物化导出)
│   ├── sync_coking_coal_positions.py  # 龙虎榜采集与净持仓计算调度脚本
│   ├── sync_coking_coal_receipts.py   # 仓单采集、清洗与物化发布调度脚本
│   └── sync_czce_spread_bars.py       # 郑商所纯碱/玻璃 01·05·09 官方日行情导入与自动增量同步脚本
├── run_daily_sync.py                  # 【根目录总调度】一键按序调度脚本 (龙虎榜 → 仓单 → 郑商所合约日行情)
├── index.html                         # 入口页 (自动跳转至龙虎榜大屏)
├── coking_coal_dragon_tiger.html      # 【看板 1】持仓龙虎榜专业三栏大屏 (席位博弈、多空力量对比)
├── coking_coal_receipt.html           # 【看板 2】仓单与交割库存全景大屏 (8大品种时序趋势、仓库分布)
├── czce_contract_spread.html          # 【看板 3】郑商所纯碱/玻璃 01·05·09 合约季节性价差大屏 (按年叠加对比)
├── requirements.txt                   # Python 运行依赖 (requests, pandas, beautifulsoup4, openpyxl)
├── BASELINE_ROLLBACK_GUIDE.md         # 稳定基线版本归档与秒级回滚操作手册
└── README.md                          # 技术与使用文档
```

---

## 🌟 三大看板核心功能与定位隔离

| 看板类型 | 文件入口 | 核心定位与业务心智 | 核心交互与特性 |
| :--- | :--- | :--- | :--- |
| **📊 席位龙虎榜** | `coking_coal_dragon_tiger.html` | **截面资金博弈与主力动向**<br/>展示各在交易合约的前 20 强会员席位（成交量、多头、空头）持仓排名与净持仓。 | • 成交量/多单/空单三栏对标专业软件<br/>• 合约秒级切换、多空力量对比条与左下角毛玻璃仓单悬浮卡<br/>• 席位搜索过滤与表头升降序排序 |
| **🏭 仓单与库存** | `coking_coal_receipt.html` | **基本面供需与实物交割承压**<br/>展示 8 大核心品种在各大港口和指定交割仓库的注册仓单、日度累库/注销去库、有效预报。 | • 8 大品种胶囊秒级横向切换<br/>• 仓单走势折线 + 日增减柱图<br/>• 各交割港口份额环形图与明细流水表 |
| **📈 合约价差** | `czce_contract_spread.html` | **跨期结构与季节性规律**<br/>基于郑商所官方近 4 年真实日行情，展示纯碱 (`SA`) / 玻璃 (`FG`) `01·05·09` 合约在同日历日期 (`01-01 ~ 12-31`) 下的跨年价差叠加曲线。 | • 支持 `01-05` / `05-09` / `09-01` 跨期合约组合<br/>• 可选 **开盘价 / 收盘价 / 最高价 / 最低价** 4 种价差口径<br/>• 支持「标准4月跨期」与「全年活跃对比」，换月处自动断线防跳空 |

> 💡 **三看板无缝联动**：三大看板顶栏均内置统一风格的 `[📊 席位龙虎榜] ↔ [🏭 仓单与库存] ↔ [📈 合约价差]` 导航 Tab，点击即可秒级穿梭跳转，完全支持本地双击（`file://`）协议与离线运行（移动端 `≤768px` 自动切换为汉堡侧滑抽屉导航）。

---

## 📡 数据获取、清洗与调度架构

### 1. 监控品种与计量标准（3 大交易所 · 8 大核心品种）

| 交易所 | 品种代码 | 品种名称 | 龙虎榜合约单位 | 仓单计量单位 | 仓单折吨系数 (`ton_per_hand`) |
| :--- | :---: | :--- | :---: | :---: | :--- |
| **大商所 (DCE)** | `JM` | 焦煤 | 手 | 手 | `60` 吨/手 |
| **大商所 (DCE)** | `J` | 焦炭 | 手 | 手 | `100` 吨/手 |
| **郑商所 (CZCE)** | `SA` | 纯碱 | 手 | 手 (张) | `20` 吨/手 |
| **郑商所 (CZCE)** | `FG` | 玻璃 | 手 | 手 (张) | `20` 吨/手 |
| **郑商所 (CZCE)** | `MA` | 甲醇 | 手 | 手 (张) | `10` 吨/手 |
| **上期所 (SHFE)** | `RB` | 螺纹钢 | 手 | 吨 | `1`（原生以吨披露；合约规格 10 吨/手） |
| **上期所 (SHFE)** | `AO` | 氧化铝 | 手 | 吨 | `1`（原生以吨披露；合约规格 20 吨/手） |
| **上期所 (SHFE)** | `HC` | 热轧卷板 | 手 | 吨 | `1`（原生以吨披露；合约规格 10 吨/手） |

---

### 2. 持仓龙虎榜数据获取 (`modules/sync_coking_coal_positions.py`)

- **主数据源（新浪财经期货同步镜像）**：
  - 接口：`http://vip.stock.finance.sina.com.cn/q/view/vFutures_Positions_cjcc.php?t_breed=<合约>&t_date=<YYYY-MM-DD>`
  - 解析逻辑：以 `GBK` 解码响应，使用 `BeautifulSoup` 提取页面内 3 张 `table.listT`（依次为成交量、买单持仓、卖单持仓前 20 强及“合计”行），从 `<input id="calen">` 读取实际披露交易日。
- **自动容灾备通道（东方财富数据中心）**：
  - 接口：`https://datacenter-web.eastmoney.com/api/data/v1/get`（`reportName=RPT_FUTU_DAILYPOSITION`，过滤 `TYPE="0"` 及 `<rank_col><>9999`，取前 20 条）。
  - 触发机制：当新浪接口返回成交量/多头/空头三榜全空时，自动切换至东财接口补齐该合约数据。
- **合约生成与交易日探测**：
  - 候选合约：基于目标日期按未来 14 个月动态生成 `YYMM` 挂牌序列（`generate_future_contract_months`），自动剔除已到期月份。
  - 交易日探活：`check_trade_day` 从近月主力合约（`01/05/09` 月份优先，取 `RB/SA/MA/JM` 前 6 个探测代码）依次试探新浪与东财接口；默认 `get_past_n_trade_dates(n=30)` 最多回溯 `3n` 个自然日（跳过周末）。
  - 并发采集：单日通过 `ThreadPoolExecutor(max_workers=12)` 并发拉取 8 个品种的全部活跃候选合约。
- **清洗与衍生指标计算 (`build_position_result`)**：
  - 数值清洗：`clean_int()` 正则剔除非数字与正负号字符，异常转 `0`。
  - 席位名称归一化：东财通道自动去除 `（代客）`/`(代客)` 后缀并首尾去空格。
  - 席位净持仓与多空比：交叉匹配多单/空单前 20 席位，计算单席位净头寸（`net_value = 多 - 空`）、净方向（`多`/`空`）及双榜同现标记（`has_both`）；将“前 20 名合计”的增减变动统一重算为榜单 20 行 `change` 之和，并计算合约多空力量占比（`long_percent` / `short_percent`）。

---

### 3. 交割仓单日报数据获取 (`modules/sync_coking_coal_receipts.py`)

仓单模块采用**“权威总量校准 + 分交易所官网直连明细”**采集策略，所有指标均严格来源于交易所及财经镜像真实披露：

| 覆盖范围 | 数据通道 / 接口 | 解析与映射逻辑 |
| :--- | :--- | :--- |
| **全 8 品种总量与日历基准** | 东方财富 `RPT_FUTU_STOCKDATA`（首选）<br/>+ 99期货 `stockIn`（历史回溯与备用） | 东财拉取 `ON_WARRANT_NUM` 与 `ADDCHANGE`；99期货用于补全早于东财窗口（6 月以来）的交易日基准及缺失备位。 |
| **郑商所明细 (`SA/FG/MA`)** | 郑商所官方 DFS 静态 Excel<br/>`http://www.czce.com.cn/.../Future/{YYYY}/{YYYYMMDD}/FutureDataWhsheet.xlsx` | 使用 `openpyxl` 严格按当日日期读取，基于表头列名动态定位（兼容甲醇“完税+保税”双列累加与玻璃无预报列），精准提取各仓库 `小计` 与 `总计`（含 0 仓单）。 |
| **上期所明细 (`RB/HC/AO`)** | 上期所官方日度仓单 HTML<br/>`https://www.shfe.com.cn/data/tradedata/future/stockdata/dailystock_{YYYYMMDD}/ZH/shfe/{prod}.html` | 展开带 `rowspan/colspan` 的结构化表格，分别汇总仓库（`table[1]`）与厂库提货地（`table[2]`）真实吨数，自动消歧跨区同名厂库并计算各库占比。 |
| **大商所 (`JM/J`)** | 东财 `RPT_FUTU_STOCKDATA` + 99期货 `stockIn` | 因大商所官网启用瑞数动态反爬（HTTP 412），不伪造固定比例拆解，仅真实展示全市场注册仓单总量、日增减及连续 0 仓单期间状态。 |

- **清洗与自愈状态机**：
  - 区域/库型推断：`infer_warehouse_region_and_type()` 优先匹配物理城市/港口关键词再匹配省份（避免如“浙江汇善（青岛港）”误归属），并由交易所表结构直接确定 `is_factory`。
  - 增量重抓保护：取消 500 假兜底，若当日盘后某品种尚未披露则记为 `PARTIAL`，配合 `get_existing_receipt_dates()` 自动在下次定时任务中增量刷新补齐，直至 8 品种均达到 `SUCCESS` 或确认 `EMPTY_MARKET`。
  - 全库体检：`--verify` 遍历 SQLite 全量历史，自动审计 500 假值、负值/负预报、仓单与明细加和一致性及吨数一致性。

---

### 4. 郑商所纯碱/玻璃 01·05·09 日行情采集 (`modules/sync_czce_spread_bars.py`)

- **历史底座导入**：自动扫描本地 `temp/` 目录下的郑商所官方历史归档（支持年度 `.zip`、`.txt` 及 `.xlsx`），精准过滤提取 **纯碱 (`SA`)** 与 **玻璃 (`FG`)** 的 **`01`、`05`、`09`** 三个主力交割月份合约，将郑商所 3 位合约代码（如 `SA605`）按交易日标准化为 4 位（`SA2605`），并清洗零成交/零价 `0.00` 脏值为 `NULL`。
- **官方每日增量自动同步**：
  - 接口：`http://www.czce.com.cn/cn/DFSStaticFiles/Future/{YYYY}/{YYYYMMDD}/FutureDataDaily.txt`
  - 增量与缺口自愈：自动比对 SQLite `czce_daily_bars` 中已完整的日期与 `trade_calendar` 非交易日缓存，仅对缺失日期（含盘后最新交易日）执行礼貌限速抓取；已完成的交易日与已确认的节假日二次运行自动秒级跳过，并已接入 `run_daily_sync.py` 与 GitHub Actions 每日自动同步。

---

### 5. 自动化定时调度 (`.github/workflows/daily_sync.yml`)

- **触发方式**：
  1. `schedule` 定时任务：每个工作日 UTC `07:30–11:00`（北京时间 `15:30–19:00`）每 15 分钟自动执行一次；
  2. `repository_dispatch`（`type: daily_sync`，支持外部 Webhook 远程触发）；
  3. `workflow_dispatch`（支持 GitHub Actions 页面手动触发）。
- **执行链路与回写**：检出代码与 `data/` 资产 → 安装 `requirements.txt` → 运行 `python3 run_daily_sync.py`（龙虎榜 → 仓单 → 郑商所合约日行情）→ 若 `data/` 目录有变更，由 `github-actions[bot]` 自动提交并推送（附 `[skip ci]`）。

---

## 🚀 运行与同步命令

### 1. 【推荐】一键全自动顺序同步（龙虎榜 → 仓单 → 郑商所合约日行情）
```bash
# 1. 默认一键同步（龙虎榜近 30 日 + 仓单近 45 日 + 郑商所 SA/FG 01·05·09 增量补全）
python3 run_daily_sync.py

# 2. 全链路数据质量体检（先后核验龙虎榜、仓单与郑商所合约日行情）
python3 run_daily_sync.py --verify

# 3. 强制重新抓取与覆盖全部目标交易日数据
python3 run_daily_sync.py --force

# 4. 指定回溯天数（支持 --past-days）
python3 run_daily_sync.py --past-days 10
```

> ⚠️ 注意：`run_daily_sync.py` 会按序调度三个子模块并将命令行参数透传；`--date YYYY-MM-DD`、`--past-days`、`--force`、`--verify` 现已覆盖全链路，`--refetch-missing` 仅由 `sync_coking_coal_positions.py` 解析。

### 2. 独立单项同步（调用 modules/ 二级模块）
```bash
# 同步龙虎榜数据（支持 --date 2026-09-30 / --past-days 30 / --verify / --refetch-missing）
python3 modules/sync_coking_coal_positions.py

# 同步仓单数据（8 大品种，支持 --force / --past-days / --verify）
python3 modules/sync_coking_coal_receipts.py

# 同步郑商所纯碱/玻璃 01·05·09 日行情（支持 --force / --skip-temp / --date / --past-days / --verify）
python3 modules/sync_czce_spread_bars.py

# SQLite 数据底座管理与表结构初始化
python3 modules/db_manager.py
```


---

## 🗄️ SQLite 数据底座设计与持久化链路

系统以 `data/coking_coal.db`（开启 `WAL` 日志模式、`synchronous=NORMAL` 与外键约束）作为核心结构化存储，并面向离线双击场景物化导出 JSON 与 JS 全局变量垫片：

| 表名 | 定位与核心字段 | 业务价值 |
| :--- | :--- | :--- |
| `trade_calendar` | `trade_date, is_trading_day, notes` | 标准交易日历基准（含节假日缓存） |
| `sync_task_status` | `trade_date, variety, data_type, status, record_count, error_msg` | 品种 × 日期粒度的任务状态流水记录 |
| `position_rankings` | `trade_date, variety, contract_code, rank_type, rank, member_name, qty, diff, net_value, net_dir, has_both` | 前 20 强期货席位多空/成交明细 |
| `contract_metrics` | `trade_date, contract_code, variety, top20_long_sum, top20_short_sum, net_spread, long_percent` | 各合约多空力量比、净头寸差额、成交合计 |
| `receipt_summaries` | `trade_date, variety, total_receipt, diff, forecast, warehouse_receipt, factory_receipt, total_tonnage` | 8 大品种日度总仓单时序趋势与去库/累库总计 |
| `warehouse_details` | `trade_date, variety, wh_name, is_factory, region, last_receipt, today_receipt, diff, forecast, share_pct` | 指定交割库/港口持仓、区域分布及有效预报明细 |
| `czce_daily_bars` | `trade_date, variety, contract_raw, contract_std, delivery_month, prev_settle, open, high, low, close, settle, volume, open_interest, data_source` | 郑商所纯碱/玻璃 01·05·09 官方近4年日行情（支撑跨期价差季节性大屏） |

### 数据写入与前端消费链路对照
- **仓单看板 (`coking_coal_receipt.html`)**：**完整采用 SQLite 读写闭环**。`sync_coking_coal_receipts.py` 通过 `save_receipt_day()` 写入 SQLite 后，调用 `db_manager.export_receipts_to_frontend()` 全量物化导出 `data/coking_coal_receipts.json` 与 `data/coking_coal_receipts.data.js`（`window.__COKING_COAL_RECEIPT_DATA__`）。
- **合约价差看板 (`czce_contract_spread.html`)**：**完整采用 SQLite 读写闭环**。`sync_czce_spread_bars.py` 将 `temp/` 官方归档与每日官方 `FutureDataDaily.txt` 统一写入 `czce_daily_bars` 后，调用 `db_manager.export_czce_spreads_to_frontend()` 物化导出 `data/czce_spread_bars.json` 与 `data/czce_spread_bars.data.js`（`window.__CZCE_SPREAD_DATA__`）。
- **龙虎榜看板 (`coking_coal_dragon_tiger.html`)**：**SQLite 仅作增量写入归档，导出层仍由脚本本地维护**。`sync_coking_coal_positions.py` 在抓包后调用 `save_position_day()` 写入 SQLite，但增量去重读取（`load_all_daily_data`）与前端输出（`save_and_sync_html`）仍基于 `data/coking_coal_positions.json`、`.data.js` 及 HTML 内嵌 `EMBEDDED_DATA`（`db_manager.py` 中预留的 `export_positions_to_frontend()` 与 `get_missing_sync_tasks()` 尚未接入主调度）。

---

## 🖥️ 快速打开与使用
- **零环境依赖，双击即开**：直接在文件管理器中双击打开 `coking_coal_dragon_tiger.html`、`coking_coal_receipt.html` 或 `czce_contract_spread.html`，无需配置任何本地服务器；
- **支持主题切换**：支持深色极客风（🌙）与浅色商务风（☀️）一键切换；
- **一键导出高清图**：点击顶栏“📸 导出图片”即可一键打印或导出高质量研报长图。

---

## 📌 版本基线与回滚指引 (Version Baseline & Rollback)

- **当前封板基线**：`v2.0.0`（双看板全功能稳定版，含截至 2026-09-30 的 58 个交易日完整数据）
- **Commit Hash**：`14ecd0f076729c2e95344c931a4fed0a39fd55cb`（短码：`14ecd0f`）
- **一键回滚本模块命令**：
  ```bash
  git checkout 14ecd0f -- dce_coking_coal/ && git clean -fd dce_coking_coal/
  ```
  详细回滚操作与多套备用策略请查阅：[BASELINE_ROLLBACK_GUIDE.md](./BASELINE_ROLLBACK_GUIDE.md)。

