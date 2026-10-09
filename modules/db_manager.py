#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大宗商品与期货系统 (DCE/CZCE/SHFE) SQLite 统一数据底座引擎
提供：
1. 交易日历 (trade_calendar) 与精细化同步自愈状态机 (sync_task_status)
2. 龙虎榜明细 (position_rankings) 与合约聚合指标 (contract_metrics)
3. 仓单总量汇总 (receipt_summaries) 与交割仓库分布 (warehouse_details)
4. 前端轻量物化视图与离线垫片导出器 (Exporter)
"""

import os
import json
import sqlite3
import datetime
from typing import Dict, List, Any, Optional, Tuple

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "coking_coal.db")

# 监控的核心品种字典
ALL_VARIETIES = ["JM", "J", "SA", "FG", "MA", "RB", "AO", "HC"]
VARIETY_META = {
    "JM": {"name": "焦煤", "desc": "Coking Coal", "exchange": "大连商品交易所 (DCE)", "unit": "手", "ton_per_hand": 60},
    "J":  {"name": "焦炭", "desc": "Coke", "exchange": "大连商品交易所 (DCE)", "unit": "手", "ton_per_hand": 100},
    "SA": {"name": "纯碱", "desc": "Soda Ash", "exchange": "郑州商品交易所 (CZCE)", "unit": "手", "ton_per_hand": 20},
    "FG": {"name": "玻璃", "desc": "Glass", "exchange": "郑州商品交易所 (CZCE)", "unit": "手", "ton_per_hand": 20},
    "MA": {"name": "甲醇", "desc": "Methanol", "exchange": "郑州商品交易所 (CZCE)", "unit": "手", "ton_per_hand": 10},
    "RB": {"name": "螺纹钢", "desc": "Rebar", "exchange": "上海期货交易所 (SHFE)", "unit": "手", "ton_per_hand": 10},
    "AO": {"name": "氧化铝", "desc": "Alumina", "exchange": "上海期货交易所 (SHFE)", "unit": "手", "ton_per_hand": 20},
    "HC": {"name": "热轧卷板", "desc": "Hot Rolled Coil", "exchange": "上海期货交易所 (SHFE)", "unit": "手", "ton_per_hand": 10}
}


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """获取启用了 WAL 模式和行字典返回的 SQLite 连接"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """初始化数据库架构，创建所有表和核心索引"""
    conn = get_db_connection(db_path)
    cur = conn.cursor()

    # 1. 交易日历表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_calendar (
        trade_date TEXT PRIMARY KEY,
        is_trading_day INTEGER DEFAULT 1,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. 细粒度同步任务状态流水表 (自愈机制核心)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sync_task_status (
        trade_date TEXT NOT NULL,
        variety TEXT NOT NULL,
        data_type TEXT NOT NULL,         -- 'position' (龙虎榜) / 'receipt' (仓单)
        status TEXT NOT NULL,            -- 'SUCCESS', 'FAILED', 'PARTIAL', 'EMPTY_MARKET'
        record_count INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        error_msg TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, variety, data_type)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_task_query ON sync_task_status (status, trade_date);")

    # 3. 期货席位龙虎榜明细表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS position_rankings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        variety TEXT NOT NULL,
        contract_code TEXT NOT NULL,
        rank_type TEXT NOT NULL,        -- 'volume' (成交量), 'long' (多头持仓), 'short' (空头持仓)
        rank INTEGER NOT NULL,          -- 1 - 20
        member_name TEXT NOT NULL,
        qty INTEGER NOT NULL,
        diff INTEGER NOT NULL,
        net_value INTEGER,
        net_dir TEXT,                   -- '多' / '空' / '平'
        has_both INTEGER DEFAULT 0,
        UNIQUE (trade_date, contract_code, rank_type, rank) ON CONFLICT REPLACE
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pos_query ON position_rankings (trade_date, contract_code, rank_type);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pos_member ON position_rankings (member_name, trade_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pos_variety ON position_rankings (variety, trade_date);")

    # 4. 合约指标总计表 (存储多空总和、力量比、合计项变动)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contract_metrics (
        trade_date TEXT NOT NULL,
        contract_code TEXT NOT NULL,
        variety TEXT NOT NULL,
        contract_name TEXT,
        top20_long_sum INTEGER,
        top20_short_sum INTEGER,
        net_spread INTEGER,
        long_percent REAL,
        short_percent REAL,
        volume_total INTEGER,
        volume_total_change INTEGER DEFAULT 0,
        long_total_change INTEGER DEFAULT 0,
        short_total_change INTEGER DEFAULT 0,
        PRIMARY KEY (trade_date, contract_code) ON CONFLICT REPLACE
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_metrics_variety ON contract_metrics (variety, trade_date);")

    # 5. 仓单总量汇总表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS receipt_summaries (
        trade_date TEXT NOT NULL,
        variety TEXT NOT NULL,
        variety_name TEXT,
        exchange TEXT,
        unit TEXT,
        ton_per_hand INTEGER,
        total_receipt INTEGER NOT NULL,
        last_total_receipt INTEGER DEFAULT 0,
        diff INTEGER DEFAULT 0,
        forecast INTEGER DEFAULT 0,
        warehouse_receipt INTEGER DEFAULT 0,
        factory_receipt INTEGER DEFAULT 0,
        active_warehouses INTEGER DEFAULT 0,
        total_tonnage REAL DEFAULT 0.0,
        warehouse_tonnage REAL DEFAULT 0.0,
        factory_tonnage REAL DEFAULT 0.0,
        PRIMARY KEY (trade_date, variety) ON CONFLICT REPLACE
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_receipt_var ON receipt_summaries (variety, trade_date);")

    # 6. 交割仓库明细分布表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS warehouse_details (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        variety TEXT NOT NULL,
        wh_name TEXT NOT NULL,
        is_factory INTEGER DEFAULT 0,
        region TEXT,
        last_receipt INTEGER DEFAULT 0,
        today_receipt INTEGER DEFAULT 0,
        diff INTEGER DEFAULT 0,
        forecast INTEGER DEFAULT 0,
        share_pct REAL DEFAULT 0.0,
        UNIQUE (trade_date, variety, wh_name) ON CONFLICT REPLACE
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wh_query ON warehouse_details (trade_date, variety);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wh_name ON warehouse_details (wh_name, trade_date);")

    conn.commit()
    conn.close()


# =========================================================================
# 任务自愈与侦测
# =========================================================================

def get_missing_sync_tasks(
    data_type: str,
    target_varieties: List[str] = ALL_VARIETIES,
    start_date: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> List[Tuple[str, str]]:
    """
    侦测缺失的 (trade_date, variety) 任务矩阵
    返回: [('2026-09-30', 'JM'), ('2026-09-30', 'SA'), ...]
    """
    conn = get_db_connection(db_path)
    cur = conn.cursor()

    date_filter = ""
    params = [data_type]
    if start_date:
        date_filter = "AND c.trade_date >= ?"
        params.insert(0, start_date)

    variety_cases = " UNION ALL ".join([f"SELECT '{v}' AS variety" for v in target_varieties])

    sql = f"""
    WITH expected_matrix AS (
        SELECT c.trade_date, v.variety
        FROM trade_calendar c
        CROSS JOIN ({variety_cases}) v
        WHERE c.is_trading_day = 1 {date_filter}
    )
    SELECT e.trade_date, e.variety
    FROM expected_matrix e
    LEFT JOIN sync_task_status s
      ON e.trade_date = s.trade_date
     AND e.variety = s.variety
     AND s.data_type = ?
    WHERE s.status IS NULL OR s.status IN ('FAILED', 'PARTIAL')
    ORDER BY e.trade_date ASC, e.variety ASC;
    """

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [(r["trade_date"], r["variety"]) for r in rows]


def update_task_status(
    trade_date: str,
    variety: str,
    data_type: str,
    status: str,
    record_count: int = 0,
    error_msg: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """更新任务流水状态"""
    should_close = False
    if conn is None:
        conn = get_db_connection(db_path)
        should_close = True

    conn.execute("""
    INSERT INTO sync_task_status (trade_date, variety, data_type, status, record_count, retry_count, error_msg, updated_at)
    VALUES (?, ?, ?, ?, ?, 0, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(trade_date, variety, data_type) DO UPDATE SET
        status = excluded.status,
        record_count = excluded.record_count,
        retry_count = sync_task_status.retry_count + CASE WHEN excluded.status = 'FAILED' THEN 1 ELSE 0 END,
        error_msg = excluded.error_msg,
        updated_at = CURRENT_TIMESTAMP;
    """, (trade_date, variety, data_type, status, record_count, error_msg))

    if should_close:
        conn.commit()
        conn.close()


# =========================================================================
# 数据保存服务 (Position & Receipt)
# =========================================================================

def save_receipt_day(
    trade_date: str,
    receipt_day_data: Dict[str, Any],
    conn: Optional[sqlite3.Connection] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """持久化单一交易日的仓单数据及所有品种明细"""
    should_close = False
    if conn is None:
        conn = get_db_connection(db_path)
        should_close = True

    cur = conn.cursor()

    cur.execute("""
    INSERT INTO trade_calendar (trade_date, is_trading_day)
    VALUES (?, 1)
    ON CONFLICT(trade_date) DO UPDATE SET is_trading_day = 1;
    """, (trade_date,))

    commodities = receipt_day_data.get("commodities", {})
    for code, info in commodities.items():
        summary = info.get("summary", {})
        warehouses = info.get("warehouses", [])

        cur.execute("""
        INSERT INTO receipt_summaries (
            trade_date, variety, variety_name, exchange, unit, ton_per_hand,
            total_receipt, last_total_receipt, diff, forecast,
            warehouse_receipt, factory_receipt, active_warehouses,
            total_tonnage, warehouse_tonnage, factory_tonnage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_date, variety) DO UPDATE SET
            variety_name = excluded.variety_name,
            exchange = excluded.exchange,
            unit = excluded.unit,
            ton_per_hand = excluded.ton_per_hand,
            total_receipt = excluded.total_receipt,
            last_total_receipt = excluded.last_total_receipt,
            diff = excluded.diff,
            forecast = excluded.forecast,
            warehouse_receipt = excluded.warehouse_receipt,
            factory_receipt = excluded.factory_receipt,
            active_warehouses = excluded.active_warehouses,
            total_tonnage = excluded.total_tonnage,
            warehouse_tonnage = excluded.warehouse_tonnage,
            factory_tonnage = excluded.factory_tonnage;
        """, (
            trade_date, code, info.get("name", code), info.get("exchange"),
            info.get("unit", "手"), info.get("ton_per_hand", 0),
            summary.get("total_receipt", 0), summary.get("last_total_receipt", 0),
            summary.get("diff", 0), summary.get("forecast", 0),
            summary.get("warehouse_receipt", 0), summary.get("factory_receipt", 0),
            summary.get("active_warehouses", 0),
            summary.get("total_tonnage", 0.0), summary.get("warehouse_tonnage", 0.0),
            summary.get("factory_tonnage", 0.0)
        ))

        wh_records = []
        for w in warehouses:
            wh_records.append((
                trade_date, code, w.get("name", ""),
                1 if w.get("is_factory") else 0,
                w.get("region", ""),
                w.get("last_receipt", 0),
                w.get("today_receipt", 0),
                w.get("diff", 0),
                w.get("forecast", 0),
                w.get("share_pct", 0.0)
            ))
        if wh_records:
            cur.executemany("""
            INSERT INTO warehouse_details (
                trade_date, variety, wh_name, is_factory, region,
                last_receipt, today_receipt, diff, forecast, share_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, variety, wh_name) DO UPDATE SET
                is_factory = excluded.is_factory,
                region = excluded.region,
                last_receipt = excluded.last_receipt,
                today_receipt = excluded.today_receipt,
                diff = excluded.diff,
                forecast = excluded.forecast,
                share_pct = excluded.share_pct;
            """, wh_records)

        status = "SUCCESS" if (summary.get("total_receipt", 0) > 0 or len(warehouses) > 0) else "EMPTY_MARKET"
        update_task_status(trade_date, code, "receipt", status, record_count=len(warehouses), conn=conn)

    if should_close:
        conn.commit()
        conn.close()


def save_position_day(
    trade_date: str,
    pos_day_data: Dict[str, Any],
    conn: Optional[sqlite3.Connection] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """持久化单一交易日的龙虎榜数据及明细"""
    should_close = False
    if conn is None:
        conn = get_db_connection(db_path)
        should_close = True

    cur = conn.cursor()

    cur.execute("""
    INSERT INTO trade_calendar (trade_date, is_trading_day)
    VALUES (?, 1)
    ON CONFLICT(trade_date) DO UPDATE SET is_trading_day = 1;
    """, (trade_date,))

    details = pos_day_data.get("details", {})
    contracts_by_commodity = pos_day_data.get("contracts_by_commodity", {})

    variety_counts: Dict[str, int] = {v: 0 for v in ALL_VARIETIES}

    rankings_to_insert = []
    metrics_to_insert = []

    for contract_code, c_detail in details.items():
        variety = "JM"
        for v in ALL_VARIETIES:
            if contract_code.upper().startswith(v):
                variety = v
                break

        metrics = c_detail.get("metrics", {})

        # 安全提取 volume_total (兼容 int 与 dict)
        vol_raw = c_detail.get("volume_total")
        vol_val = vol_raw.get("value", 0) if isinstance(vol_raw, dict) else (vol_raw or 0)
        vol_chg = vol_raw.get("change", 0) if isinstance(vol_raw, dict) else 0

        # 安全提取 long_total & short_total
        long_raw = c_detail.get("long_total")
        long_val = long_raw.get("value", 0) if isinstance(long_raw, dict) else (long_raw or metrics.get("top20_long_sum", 0))
        long_chg = long_raw.get("change", 0) if isinstance(long_raw, dict) else 0

        short_raw = c_detail.get("short_total")
        short_val = short_raw.get("value", 0) if isinstance(short_raw, dict) else (short_raw or metrics.get("top20_short_sum", 0))
        short_chg = short_raw.get("change", 0) if isinstance(short_raw, dict) else 0

        metrics_to_insert.append((
            trade_date, contract_code, variety,
            c_detail.get("contract", contract_code),
            long_val,
            short_val,
            metrics.get("net_spread", 0),
            metrics.get("long_percent", 50.0),
            metrics.get("short_percent", 50.0),
            vol_val,
            vol_chg,
            long_chg,
            short_chg
        ))

        # 成交量排名
        for item in c_detail.get("volume_ranking", []):
            rankings_to_insert.append((
                trade_date, variety, contract_code, "volume",
                item.get("rank", 0), item.get("member", ""),
                item.get("value", 0), item.get("change", 0),
                None, None, 0
            ))
            variety_counts[variety] = variety_counts.get(variety, 0) + 1

        # 多头排名
        for item in c_detail.get("long_ranking", []):
            rankings_to_insert.append((
                trade_date, variety, contract_code, "long",
                item.get("rank", 0), item.get("member", ""),
                item.get("value", 0), item.get("change", 0),
                item.get("net_value"), item.get("net_dir"),
                1 if item.get("has_both") else 0
            ))
            variety_counts[variety] = variety_counts.get(variety, 0) + 1

        # 空头排名
        for item in c_detail.get("short_ranking", []):
            rankings_to_insert.append((
                trade_date, variety, contract_code, "short",
                item.get("rank", 0), item.get("member", ""),
                item.get("value", 0), item.get("change", 0),
                item.get("net_value"), item.get("net_dir"),
                1 if item.get("has_both") else 0
            ))
            variety_counts[variety] = variety_counts.get(variety, 0) + 1

    if metrics_to_insert:
        cur.executemany("""
        INSERT INTO contract_metrics (
            trade_date, contract_code, variety, contract_name,
            top20_long_sum, top20_short_sum, net_spread, long_percent, short_percent,
            volume_total, volume_total_change, long_total_change, short_total_change
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_date, contract_code) DO UPDATE SET
            top20_long_sum = excluded.top20_long_sum,
            top20_short_sum = excluded.top20_short_sum,
            net_spread = excluded.net_spread,
            long_percent = excluded.long_percent,
            short_percent = excluded.short_percent,
            volume_total = excluded.volume_total,
            volume_total_change = excluded.volume_total_change,
            long_total_change = excluded.long_total_change,
            short_total_change = excluded.short_total_change;
        """, metrics_to_insert)

    if rankings_to_insert:
        cur.executemany("""
        INSERT INTO position_rankings (
            trade_date, variety, contract_code, rank_type, rank,
            member_name, qty, diff, net_value, net_dir, has_both
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_date, contract_code, rank_type, rank) DO UPDATE SET
            member_name = excluded.member_name,
            qty = excluded.qty,
            diff = excluded.diff,
            net_value = excluded.net_value,
            net_dir = excluded.net_dir,
            has_both = excluded.has_both;
        """, rankings_to_insert)

    for v in ALL_VARIETIES:
        cnt = variety_counts.get(v, 0)
        c_list = contracts_by_commodity.get(v, [])
        status = "SUCCESS" if (cnt > 0 or len(c_list) > 0) else "EMPTY_MARKET"
        update_task_status(trade_date, v, "position", status, record_count=cnt, conn=conn)

    if should_close:
        conn.commit()
        conn.close()


# =========================================================================
# 前端物化发布器 (Exporters)
# =========================================================================

def export_receipts_to_frontend(
    output_json_path: Optional[str] = None,
    output_js_path: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> Dict[str, Any]:
    """从 SQLite 查询并导出全量仓单看板消费数据 (与 coking_coal_receipt.html 100% 兼容)"""
    if not output_json_path:
        output_json_path = os.path.join(DATA_DIR, "coking_coal_receipts.json")
    if not output_js_path:
        output_js_path = os.path.join(DATA_DIR, "coking_coal_receipts.data.js")

    conn = get_db_connection(db_path)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT trade_date FROM receipt_summaries ORDER BY trade_date ASC;")
    all_trade_dates = [r["trade_date"] for r in cur.fetchall()]
    latest_trade_date = all_trade_dates[-1] if all_trade_dates else ""

    result: Dict[str, Any] = {
        "sync_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_trade_date": latest_trade_date,
        "all_trade_dates": all_trade_dates,
        "commodities": {}
    }

    for code in ALL_VARIETIES:
        meta = VARIETY_META.get(code, {})

        cur.execute("""
        SELECT trade_date as date, total_receipt, diff, forecast, warehouse_receipt,
               factory_receipt, total_tonnage as tonnage, warehouse_tonnage, factory_tonnage
        FROM receipt_summaries
        WHERE variety = ?
        ORDER BY trade_date ASC;
        """, (code,))
        history_trend = [dict(r) for r in cur.fetchall()]

        cur.execute("""
        SELECT total_receipt, last_total_receipt, diff, forecast,
               warehouse_receipt, factory_receipt, active_warehouses,
               total_tonnage, warehouse_tonnage, factory_tonnage
        FROM receipt_summaries
        WHERE variety = ? AND trade_date = ?;
        """, (code, latest_trade_date))
        latest_summary_row = cur.fetchone()
        latest_summary = dict(latest_summary_row) if latest_summary_row else {
            "total_receipt": 0, "last_total_receipt": 0, "diff": 0, "forecast": 0,
            "warehouse_receipt": 0, "factory_receipt": 0, "active_warehouses": 0,
            "total_tonnage": 0, "warehouse_tonnage": 0, "factory_tonnage": 0
        }

        cur.execute("""
        SELECT wh_name as name, is_factory, region, last_receipt, today_receipt, diff, forecast, share_pct
        FROM warehouse_details
        WHERE variety = ? AND trade_date = ?
        ORDER BY today_receipt DESC, diff DESC;
        """, (code, latest_trade_date))
        latest_warehouses = []
        for r in cur.fetchall():
            d = dict(r)
            d["is_factory"] = bool(d["is_factory"])
            latest_warehouses.append(d)

        result["commodities"][code] = {
            "code": code,
            "name": meta.get("name", code),
            "desc": meta.get("desc", ""),
            "exchange": meta.get("exchange", ""),
            "unit": meta.get("unit", "手"),
            "ton_per_hand": meta.get("ton_per_hand", 0),
            "latest_summary": latest_summary,
            "latest_warehouses": latest_warehouses,
            "history_trend": history_trend
        }

    conn.close()

    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    js_content = f"// 8 大品种官方真实仓单离线数据垫片 (SQLite 物化导出引擎)\n// 同步生成时间: {result['sync_time']}\nwindow.__COKING_COAL_RECEIPT_DATA__ = " + json.dumps(result, ensure_ascii=False) + ";\n"
    with open(output_js_path, "w", encoding="utf-8") as f:
        f.write(js_content)

    return result


def export_positions_to_frontend(
    output_json_path: Optional[str] = None,
    output_js_path: Optional[str] = None,
    max_history_days: int = 45,
    db_path: str = DEFAULT_DB_PATH
) -> Dict[str, Any]:
    """
    从 SQLite 物化导出龙虎榜大屏消费数据 (与 coking_coal_dragon_tiger.html 100% 兼容)
    """
    if not output_json_path:
        output_json_path = os.path.join(DATA_DIR, "coking_coal_positions.json")
    if not output_js_path:
        output_js_path = os.path.join(DATA_DIR, "coking_coal_positions.data.js")

    conn = get_db_connection(db_path)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT trade_date FROM contract_metrics ORDER BY trade_date ASC;")
    all_trade_dates = [r["trade_date"] for r in cur.fetchall()]
    if not all_trade_dates:
        conn.close()
        return {}

    history_dates = all_trade_dates[-max_history_days:]
    latest_date = history_dates[-1]

    commodities_meta_list = []
    for code, m in VARIETY_META.items():
        commodities_meta_list.append({
            "code": code,
            "name": f"{m['name']} ({code})",
            "desc": m["desc"],
            "exchange": m["exchange"]
        })

    def build_single_day_snapshot(t_date: str) -> Dict[str, Any]:
        cur.execute("""
        SELECT contract_code, variety, contract_name, top20_long_sum, top20_short_sum,
               net_spread, long_percent, short_percent, volume_total,
               volume_total_change, long_total_change, short_total_change
        FROM contract_metrics
        WHERE trade_date = ?
        ORDER BY contract_code ASC;
        """, (t_date,))
        metrics_rows = cur.fetchall()

        contracts_list = []
        contracts_by_commodity: Dict[str, list] = {v: [] for v in ALL_VARIETIES}
        details: Dict[str, Any] = {}

        for m_row in metrics_rows:
            c_code = m_row["contract_code"]
            var = m_row["variety"]
            c_item = {"code": c_code, "name": m_row["contract_name"] or c_code, "commodity": var}
            contracts_list.append(c_item)
            contracts_by_commodity[var].append(c_item)

            cur.execute("""
            SELECT rank_type, rank, member_name as member, qty as value, diff as change,
                   net_value, net_dir, has_both
            FROM position_rankings
            WHERE trade_date = ? AND contract_code = ?
            ORDER BY rank ASC;
            """, (t_date, c_code))
            rank_rows = cur.fetchall()

            vol_list = []
            long_list = []
            short_list = []
            for rr in rank_rows:
                rd = dict(rr)
                rd["has_both"] = bool(rd["has_both"])
                rtype = rd.pop("rank_type")
                if rtype == "volume":
                    vol_list.append(rd)
                elif rtype == "long":
                    if rd["net_dir"] and rd["net_value"] is not None:
                        rd["net_display"] = f"{rd['net_dir']} {abs(rd['net_value']):,}"
                    long_list.append(rd)
                elif rtype == "short":
                    if rd["net_dir"] and rd["net_value"] is not None:
                        rd["net_display"] = f"{rd['net_dir']} {abs(rd['net_value']):,}"
                    short_list.append(rd)

            spread = m_row["net_spread"] or 0
            net_disp = f"{'多' if spread > 0 else ('空' if spread < 0 else '平')} {abs(spread):,}"

            details[c_code] = {
                "contract": m_row["contract_name"] or c_code,
                "date": t_date,
                "data_source": "official_dce_czce_shfe",
                "volume_ranking": vol_list,
                "volume_total": {
                    "rank": "合计",
                    "member": "前20名合计",
                    "value": m_row["volume_total"] or 0,
                    "change": m_row["volume_total_change"] or 0
                },
                "long_ranking": long_list,
                "long_total": {
                    "rank": "合计",
                    "member": "前20名合计",
                    "value": m_row["top20_long_sum"] or 0,
                    "change": m_row["long_total_change"] or 0,
                    "net_display": net_disp
                },
                "short_ranking": short_list,
                "short_total": {
                    "rank": "合计",
                    "member": "前20名合计",
                    "value": m_row["top20_short_sum"] or 0,
                    "change": m_row["short_total_change"] or 0,
                    "net_display": net_disp
                },
                "metrics": {
                    "top20_long_sum": m_row["top20_long_sum"] or 0,
                    "top20_short_sum": m_row["top20_short_sum"] or 0,
                    "net_spread": spread,
                    "long_percent": m_row["long_percent"] or 50.0,
                    "short_percent": m_row["short_percent"] or 50.0
                }
            }

        return {
            "query_date": t_date,
            "sync_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "exchange": "大连商品交易所 (DCE) / 郑州商品交易所 (CZCE) / 上海期货交易所 (SHFE)",
            "commodities": commodities_meta_list,
            "contracts_by_commodity": contracts_by_commodity,
            "contracts": contracts_list,
            "details": details
        }

    latest_snapshot = build_single_day_snapshot(latest_date)
    history_dict: Dict[str, Any] = {}
    for d in history_dates:
        history_dict[d] = build_single_day_snapshot(d)

    full_output = {
        "sync_time": latest_snapshot["sync_time"],
        "query_date": latest_date,
        "exchange": latest_snapshot["exchange"],
        "commodities": latest_snapshot["commodities"],
        "contracts_by_commodity": latest_snapshot["contracts_by_commodity"],
        "contracts": latest_snapshot["contracts"],
        "details": latest_snapshot["details"],
        "history": history_dict
    }

    conn.close()

    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(full_output, f, ensure_ascii=False, indent=2)

    js_content = f"// 期货全品种持仓龙虎榜离线数据垫片 (SQLite 物化导出引擎)\n// 同步生成时间: {full_output['sync_time']}\nwindow.__COKING_COAL_DATA__ = " + json.dumps(full_output, ensure_ascii=False) + ";\n"
    with open(output_js_path, "w", encoding="utf-8") as f:
        f.write(js_content)

    return full_output


if __name__ == "__main__":
    print("🚀 [SQLite 引擎] 正在初始化数据库模型...")
    init_db()
    print(f"✅ 数据库架构就绪: {DEFAULT_DB_PATH}")
