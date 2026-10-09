# 大商所/商品期货（DCE Coking Coal）基线版本与回滚操作手册

## 📌 一、基线版本快照归档（Baseline Snapshots）

为保证系统稳定与变更可追溯，本项目实行严格的基线归档机制：

| 基线版本 | Commit Hash | 归档时间 | 说明与包含特性 |
| :--- | :--- | :--- | :--- |
| **v2.0.0 (当前最新基线)** | **`14ecd0f`**<br/>`14ecd0f076729c2e95344c931a4fed0a39fd55cb` | `2026-09-30 16:50:09` | **双看板全功能封板版**：<br/>• 包含持仓龙虎榜大屏 + 8大品种仓单交割库存全景大屏<br/>• 包含 58 个交易日完整数据（截至 2026-09-30）<br/>• **在执行 SQLite 底层改造前的完备备份** |
| **v1.0.0 (历史基线)** | `52dbbc3`<br/>`52dbbc30216945de98236cf2abc329cbe12bc9d7` | `2026-09-17 15:23:27` | **纯净龙虎榜基线版**：<br/>• 仅包含焦煤期货持仓龙虎榜单看板与对应数据 |

---

## 📁 二、v2.0.0 基线版本文件结构清单

```text
dce_coking_coal/
├── data/
│   ├── daily/                       # 龙虎榜日度快照库 (截至 2026-09-30, 共 35+ 个交易日)
│   ├── receipt_daily/               # 仓单日度快照库 (截至 2026-09-30, 共 58 个交易日)
│   ├── coking_coal_positions.json   # 龙虎榜全量历史聚合数据源 (~28MB)
│   ├── coking_coal_positions.data.js# 龙虎榜离线数据垫片
│   ├── coking_coal_receipts.json    # 仓单全量历史聚合数据源 (~196KB)
│   └── coking_coal_receipts.data.js # 仓单离线数据垫片
├── coking_coal_dragon_tiger.html     # 龙虎榜专业三栏可视化大屏 (纯静态、双击即开、图片导出)
├── coking_coal_receipt.html         # 8大品种交割仓单与仓库明细全景大屏
├── sync_coking_coal_positions.py     # 龙虎榜全自动多线程抓取与聚合调度脚本
├── sync_coking_coal_receipts.py      # 仓单时序全自动抓取与仓库明细聚合调度脚本
├── README.md                         # 项目核心说明文档
└── BASELINE_ROLLBACK_GUIDE.md        # 本回滚与基线手册
```

---

## ⏪ 三、快速回滚操作指南（Rollback Instructions）

如果在后续 SQLite 改造或数据入库过程中遇到任何非预期问题，可通过以下操作秒级回滚到 **v2.0.0 基线版本 (`14ecd0f`)**：

### 方案 1：仅将 `dce_coking_coal` 单独回滚至 v2.0.0 基线（推荐，最安全）
> **优点**：仅恢复本模块，完全不影响同仓库下的其他项目（如 `dw_topology_studio`, `mbb_consulting_deck`, `echarts_universal_studio`）。

在终端中执行：
```bash
# 1. 切换到项目根目录
cd /Users/qushuxian/githubs/ai-project

# 2. 仅检出 v2.0.0 基线版本的 dce_coking_coal 目录内容
git checkout 14ecd0f -- dce_coking_coal/

# 3. 清理后续新增的未跟踪文件（如测试产生的 .db 文件等）
git clean -fd dce_coking_coal/

# 4. 提交回滚记录
git commit -m "revert(dce_coking_coal): 回滚至 v2.0.0 双看板稳定基线版 (14ecd0f)"
```

### 方案 2：全仓库硬重置（彻底回到 v2.0.0 提交）
> **注意**：此命令会将仓库所有内容强制重置为 `14ecd0f`。

```bash
cd /Users/qushuxian/githubs/ai-project
git reset --hard 14ecd0f
git clean -fd
```

### 方案 3：临时对比当前工作区与基线差异
```bash
# 查看当前工作区与 v2.0.0 基线版的全部差异
git diff 14ecd0f -- dce_coking_coal/
```

---

## 🔍 四、基线版本验证方法

回滚后，只需验证以下两项即可确认系统回到 v2.0.0 正常基线状态：
1. **运行同步与体检**：
   ```bash
   python3 dce_coking_coal/sync_coking_coal_receipts.py --verify
   python3 dce_coking_coal/sync_coking_coal_positions.py --verify
   ```
2. **打开看板**：
   - 双击打开 `coking_coal_dragon_tiger.html`，三栏龙虎榜与合约胶囊切换正常；
   - 双击打开 `coking_coal_receipt.html`，8 大品种仓单时序趋势与交割仓库明细展示正常。
