# 大商所/郑商所/上期所 核心期货品种持仓龙虎榜与仓单库存全景看板

本项目是一套面向国内主要商品期货（焦煤 JM、焦炭 J、纯碱 SA、玻璃 FG、甲醇 MA、螺纹钢 RB、氧化铝 AO、热轧卷板 HC 等 8 大核心品种）的**席位日成交持仓龙虎榜**与**指定交割仓库仓单日报库存**的全自动采集、预处理与独立双大屏可视化系统。

---

## 📁 架构与目录文件清单

```text
dce_coking_coal/
├── data/
│   ├── coking_coal.db               # 【核心底座】SQLite 统一结构化数据库 (含交易日历、自愈状态机、龙虎榜与仓单流水)
│   ├── coking_coal_positions.json   # 龙虎榜前端物化聚合数据源 (由 SQLite 引擎按窗口智能导出)
│   ├── coking_coal_positions.data.js# 龙虎榜离线数据垫片 (window.__COKING_COAL_DATA__)
│   ├── coking_coal_receipts.json    # 仓单全量聚合数据源 (~196KB，毫秒级轻量载入)
│   └── coking_coal_receipts.data.js # 仓单离线数据垫片 (window.__COKING_COAL_RECEIPT_DATA__)
├── modules/                         # 【二级目录】核心数据与采集计算引擎收拢
│   ├── __init__.py                  # 模块导出定义
│   ├── db_manager.py                # SQLite 统一底座管理引擎 (表架构维护、缺口自愈侦测、物化视图导出)
│   ├── sync_coking_coal_positions.py # 龙虎榜采集与计算调度脚本 (直连 SQLite 入库与导出)
│   └── sync_coking_coal_receipts.py  # 仓单采集与聚合调度脚本 (直连 SQLite 入库与物化发布)
├── run_daily_sync.py                # 【根目录总调度】一键按序调度脚本 (先龙虎榜再仓单)
├── coking_coal_dragon_tiger.html     # 【看板 1】持仓龙虎榜专业三栏大屏 (席位博弈、多空力量对比)
├── coking_coal_receipt.html         # 【看板 2】仓单与交割库存全景大屏 (8大品种时序趋势、仓库分布)
├── BASELINE_ROLLBACK_GUIDE.md        # 稳定基线版本归档与秒级回滚操作手册
└── README.md                         # 技术与使用文档
```

---

## 🌟 双看板核心功能与定位隔离

| 看板类型 | 文件入口 | 核心定位与业务心智 | 核心交互与特性 |
| :--- | :--- | :--- | :--- |
| **📊 席位龙虎榜** | `coking_coal_dragon_tiger.html` | **截面资金博弈与主力动向**<br/>展示各在交易合约的前 20 强会员席位（成交量、多头、空头）持仓排名与净持仓。 | • 成交量/多单/空单三栏对标专业软件<br/>• 合约秒级切换、多空力量对比条<br/>• 席位搜索过滤与表头升降序排序 |
| **🏭 仓单与库存** | `coking_coal_receipt.html` | **基本面供需与实物交割承压**<br/>展示 8 大核心品种在各大港口和指定交割仓库的注册仓单、日度累库/注销去库、有效预报。 | • 8 大品种胶囊秒级横向切换<br/>• 33 天仓单走势折线 + 日增减柱图<br/>• 各交割港口份额环形图与明细流水表 |

> 💡 **双向无缝联动**：两个看板顶栏均内置统一风格的 `[📊 席位龙虎榜] ↔ [🏭 仓单与库存]` 导航 Tab，点击即可在两个大屏之间秒级无缝穿梭跳转，完全支持本地双击（`file://`）协议与离线运行。

---

## 🚀 运行与同步命令

### 1. 【推荐】一键全自动顺序同步（先执行龙虎榜，再执行仓单）
```bash
# 1. 默认一键同步（按序补齐最新交易日龙虎榜与仓单并落入 SQLite）
python3 run_daily_sync.py

# 2. 全链路数据质量体检（先后核验龙虎榜与仓单）
python3 run_daily_sync.py --verify

# 3. 强制重新抓取与覆盖所有日度数据
python3 run_daily_sync.py --force
```

### 2. 独立单项同步（调用 modules/ 二级模块）
```bash
# 同步龙虎榜数据
python3 modules/sync_coking_coal_positions.py

# 同步仓单数据（8 大品种）
python3 modules/sync_coking_coal_receipts.py

# SQLite 数据底座管理与初始化
python3 modules/db_manager.py
```


---

## 🗄️ SQLite 数据底座设计与核心资产

系统已完成底层数据架构升级，以 `data/coking_coal.db` 作为单一事实数据底座（SSOT），具备**强类型约束、毫秒级多维查询、细粒度自愈状态机**：

| 表名 | 定位与核心字段 | 业务价值 |
| :--- | :--- | :--- |
| `trade_calendar` | `trade_date, is_trading_day, notes` | 标准交易日历基准，防漏判 |
| `sync_task_status` | `trade_date, variety, data_type, status, record_count, error_msg` | **细粒度自愈状态机**（按品种×日期侦测漏抓并靶向补全） |
| `position_rankings` | `trade_date, variety, contract_code, rank_type, rank, member_name, qty, diff, net_value, net_dir` | 10万+ 行前 20 强期货席位多空明细，秒级按席位/合约聚合 |
| `contract_metrics` | `trade_date, contract_code, variety, top20_long_sum, top20_short_sum, net_spread, long_percent` | 各合约多空力量比、净头寸差额、成交合计 |
| `receipt_summaries` | `trade_date, variety, total_receipt, diff, forecast, warehouse_receipt, factory_receipt` | 8 大品种日度总仓单时序趋势与去库/累库总计 |
| `warehouse_details` | `trade_date, variety, wh_name, is_factory, region, today_receipt, diff, forecast, share_pct` | 指定交割库/港口持仓、区域分布及有效预报明细 |

---

## 🖥️ 快速打开与使用
- **零环境依赖，双击即开**：直接在文件管理器中双击打开 `coking_coal_receipt.html` 或 `coking_coal_dragon_tiger.html`，无需配置任何本地服务器；
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

