#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
郑州商品交易所 (CZCE) 纯碱(SA) / 玻璃(FG) 01·05·09 合约历史导入与自动增量同步引擎
--------------------------------------------------------------------------------
功能：
1. 【历史冷启动导入】：自动扫描 temp/ 下从郑商所官网下载的官方历史包（支持 ALLFUTURES*.zip、
   单品种/合集 .txt 及 *FUTURES*.xlsx），精准过滤纯碱(SA)与玻璃(FG)的 01/05/09 合约入库 SQLite。
2. 【未来自动增量同步】：自动侦测数据库中缺失 SA 或 FG 数据的交易日，直连郑商所官方 DFS 静态文本
   (http://www.czce.com.cn/cn/DFSStaticFiles/Future/{YYYY}/{YYYYMMDD}/FutureDataDaily.txt)
   低频限速补齐（例如自动补全 2026 年 SA 全年及未来每个新增交易日的 SA/FG 数据），并缓存休市日避免重复请求。
3. 【专业清洗规则】：
   - 将郑商所 3 位合约代码 (如 SA701 / FG405) 结合 trade_date 精确映射为标准 4 位合约码 (SA2701 / FG2405)。
   - 剔除千分位逗号；当当日成交量为 0 且开/高/低/收为 0.00 时置为 NULL，杜绝无成交归零脏点。
   - 校验并落盘 SQLite (data/coking_coal.db -> czce_daily_bars)，同步物化导出前端 JSON 与 .data.js 垫片。
"""

import os
import sys
import re
import time
import zipfile
import datetime
import argparse
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Tuple, Set

import requests
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
TEMP_DIR = os.path.join(PROJECT_ROOT, "temp")
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

JSON_OUTPUT_PATH = os.path.join(DATA_DIR, "czce_spread_bars.json")
JS_OUTPUT_PATH = os.path.join(DATA_DIR, "czce_spread_bars.data.js")

TARGET_VARIETIES = ("SA", "FG")
TARGET_MONTHS = ("01", "05", "09")
CONTRACT_PATTERN = re.compile(r"^(SA|FG)(\d)(01|05|09)$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def parse_float(val: Any) -> Optional[float]:
    """安全解析浮点数值（去除千分位逗号，空值返回 None）"""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except Exception:
        pass
    s = str(val).strip().replace(",", "")
    if not s or s in ("-", "--", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_int(val: Any) -> int:
    """安全解析整数（去除千分位逗号）"""
    f = parse_float(val)
    return int(round(f)) if f is not None else 0


def standardize_czce_contract(contract_raw: str, trade_date: str) -> Optional[Tuple[str, str, str]]:
    """
    将郑商所 3 位合约代码 (如 SA701, FG305) 结合交易日期标准化为 4 位合约码 (SA2701, FG2305)。
    返回: (variety, contract_std, delivery_month) 或 None
    """
    code = str(contract_raw).strip().upper()
    m = CONTRACT_PATTERN.match(code)
    if not m:
        return None

    variety, y_digit_str, d_month_str = m.group(1), m.group(2), m.group(3)
    y_digit = int(y_digit_str)
    d_month = int(d_month_str)

    t_year = int(trade_date[:4])
    t_month = int(trade_date[5:7])
    t_ym = t_year * 12 + t_month

    # 期货合约生命周期在交易日当月至未来约 14 个月内，找出唯一匹配的交割年份
    matched_year = None
    for cand_year in (t_year, t_year + 1, t_year + 2):
        if cand_year % 10 == y_digit:
            cand_ym = cand_year * 12 + d_month
            if t_ym <= cand_ym <= t_ym + 15:
                matched_year = cand_year
                break

    if matched_year is None:
        # 极端兜底：取最近的同年尾数
        for cand_year in (t_year, t_year + 1, t_year - 1):
            if cand_year % 10 == y_digit:
                matched_year = cand_year
                break

    yy = f"{matched_year % 100:02d}"
    contract_std = f"{variety}{yy}{d_month_str}"
    return variety, contract_std, d_month_str


def build_bar_record(
    trade_date: str,
    contract_raw: str,
    fields: List[Any],
    data_source: str,
) -> Optional[Dict[str, Any]]:
    """
    统一构造标准化日行情记录。
    fields 对应顺序 (13 列):
    [0]昨结算, [1]今开盘, [2]最高价, [3]最低价, [4]今收盘, [5]今结算,
    [6]涨跌1, [7]涨跌2, [8]成交量, [9]持仓量, [10]增减量, [11]成交额(万元), [12]交割结算价
    """
    if not DATE_PATTERN.match(trade_date):
        return None
    std = standardize_czce_contract(contract_raw, trade_date)
    if not std:
        return None
    variety, contract_std, delivery_month = std

    def _get(idx: int) -> Any:
        return fields[idx] if idx < len(fields) else None

    prev_settle = parse_float(_get(0))
    open_p = parse_float(_get(1))
    high_p = parse_float(_get(2))
    low_p = parse_float(_get(3))
    close_p = parse_float(_get(4))
    settle_p = parse_float(_get(5))
    chg_close = parse_float(_get(6))
    chg_settle = parse_float(_get(7))
    volume = parse_int(_get(8))
    open_interest = parse_int(_get(9))
    oi_change = parse_int(_get(10))
    turnover = parse_float(_get(11)) or 0.0
    delivery_settle = parse_float(_get(12))

    # 清洗郑商所无成交日或交割配对日开高低收填 0.00 的占位脏价，避免画图与价差计算断崖失真
    if open_p is not None and open_p <= 0.0:
        open_p = None
    if high_p is not None and high_p <= 0.0:
        high_p = None
    if low_p is not None and low_p <= 0.0:
        low_p = None
    if close_p is not None and close_p <= 0.0:
        close_p = None
    if settle_p is not None and settle_p <= 0.0:
        settle_p = None
    if prev_settle is not None and prev_settle <= 0.0:
        prev_settle = None

    return {
        "trade_date": trade_date,
        "variety": variety,
        "contract_raw": str(contract_raw).strip().upper(),
        "contract_std": contract_std,
        "delivery_month": delivery_month,
        "prev_settle": prev_settle,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "settle": settle_p,
        "chg_close": chg_close,
        "chg_settle": chg_settle,
        "volume": volume,
        "open_interest": open_interest,
        "oi_change": oi_change,
        "turnover": turnover,
        "delivery_settle": delivery_settle,
        "data_source": data_source,
    }


def _parse_history_txt_content(text: str, source_tag: str) -> List[Dict[str, Any]]:
    """解析郑商所年度历史文本 (含交易日期首列，15 列 | 分隔)"""
    records: List[Dict[str, Any]] = []
    for raw_line in text.splitlines():
        if "|" not in raw_line:
            continue
        parts = [p.strip() for p in raw_line.split("|")]
        if len(parts) < 13:
            continue
        trade_date = parts[0]
        contract_raw = parts[1]
        if not DATE_PATTERN.match(trade_date):
            continue
        rec = build_bar_record(trade_date, contract_raw, parts[2:], data_source=source_tag)
        if rec:
            records.append(rec)
    return records


def _parse_daily_txt_content(trade_date: str, text: str, source_tag: str = "czce_daily_txt") -> Tuple[List[Dict[str, Any]], bool]:
    """
    解析郑商所单日官方行情文本 FutureDataDaily.txt (无日期首列，14 列 | 分隔)。
    同时做基础格式校验，确认文件确为当日郑商所行情表。
    返回: (SA/FG 01/05/09 记录列表, 是否解析到有效市场行情)
    """
    records: List[Dict[str, Any]] = []
    has_any_contract = False

    for raw_line in text.splitlines():
        if "|" not in raw_line:
            continue
        parts = [p.strip() for p in raw_line.split("|")]
        if len(parts) < 12:
            continue
        first_col = parts[0]
        if first_col in ("合约代码", "小计", "总计", ""):
            continue
        if re.match(r"^[A-Z]{2}\d{3}$", first_col.upper()):
            has_any_contract = True
        rec = build_bar_record(trade_date, first_col, parts[1:], data_source=source_tag)
        if rec:
            records.append(rec)

    return records, has_any_contract


def import_local_temp_files(temp_dir: str = TEMP_DIR) -> List[Dict[str, Any]]:
    """自动扫描 temp/ 目录并解析所有郑商所官方历史包 (.zip / .txt / .xlsx)"""
    if not os.path.isdir(temp_dir):
        return []

    all_records: List[Dict[str, Any]] = []
    entries = sorted(os.listdir(temp_dir))
    for fname in entries:
        fpath = os.path.join(temp_dir, fname)
        if not os.path.isfile(fpath) or fname.startswith("."):
            continue

        lower = fname.lower()
        if lower.endswith(".zip"):
            try:
                with zipfile.ZipFile(fpath, "r") as zf:
                    zip_count = 0
                    for inner in zf.namelist():
                        inner_base = os.path.basename(inner)
                        if not inner_base.lower().endswith(".txt"):
                            continue
                        upper_base = inner_base.upper()
                        # 仅扫描全品种合集或 SA/FG 单品种文件以加速解析
                        if not (upper_base.startswith("ALL") or upper_base.startswith("SA") or upper_base.startswith("FG")):
                            continue
                        raw_bytes = zf.read(inner)
                        try:
                            txt = raw_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            txt = raw_bytes.decode("gb18030", errors="ignore")
                        recs = _parse_history_txt_content(txt, source_tag=f"czce_zip:{fname}")
                        all_records.extend(recs)
                        zip_count += len(recs)
                print(f"📦 [本地历史导入] {fname} -> 提取 SA/FG 01·05·09 记录 {zip_count} 条")
            except Exception as e:
                print(f"  ⚠️ 读取压缩包 {fname} 失败: {e}")

        elif lower.endswith(".txt"):
            try:
                with open(fpath, "rb") as f:
                    raw_bytes = f.read()
                try:
                    txt = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    txt = raw_bytes.decode("gb18030", errors="ignore")
                recs = _parse_history_txt_content(txt, source_tag=f"czce_txt:{fname}")
                all_records.extend(recs)
                print(f"📄 [本地历史导入] {fname} -> 提取 SA/FG 01·05·09 记录 {len(recs)} 条")
            except Exception as e:
                print(f"  ⚠️ 读取文本 {fname} 失败: {e}")

        elif lower.endswith((".xlsx", ".xls")):
            try:
                df = pd.read_excel(fpath, header=None, engine="openpyxl" if lower.endswith(".xlsx") else None)
                xlsx_count = 0
                for idx in range(len(df)):
                    row_vals = list(df.iloc[idx].values)
                    if len(row_vals) < 13:
                        continue
                    t_date = str(row_vals[0]).strip()[:10]
                    if not DATE_PATTERN.match(t_date):
                        continue
                    rec = build_bar_record(t_date, row_vals[1], row_vals[2:], data_source=f"czce_xlsx:{fname}")
                    if rec:
                        all_records.append(rec)
                        xlsx_count += 1
                print(f"📊 [本地历史导入] {fname} -> 提取 SA/FG 01·05·09 记录 {xlsx_count} 条")
            except Exception as e:
                print(f"  ⚠️ 读取 Excel {fname} 失败: {e}")

    return all_records


def fetch_czce_daily_file(trade_date: str, timeout: float = 10.0, retries: int = 2) -> Tuple[str, Optional[str], int]:
    """
    拉取郑商所单日官方行情静态文本 FutureDataDaily.txt。
    返回: (trade_date, 文本内容或 None, HTTP状态码)
    """
    date_clean = trade_date.replace("-", "")
    year = date_clean[:4]
    url = f"http://www.czce.com.cn/cn/DFSStaticFiles/Future/{year}/{date_clean}/FutureDataDaily.txt"

    last_status = 0
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
            last_status = resp.status_code
            if resp.status_code == 200:
                try:
                    txt = resp.content.decode("utf-8")
                except UnicodeDecodeError:
                    txt = resp.content.decode("gb18030", errors="ignore")
                return trade_date, txt, 200
            if resp.status_code == 404:
                return trade_date, None, 404
        except Exception:
            pass
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))

    return trade_date, None, last_status


def get_db_daily_bar_coverage() -> Tuple[Dict[str, Set[str]], Set[str]]:
    """
    查询 SQLite 中已完整入库 SA / FG (各有 3 个月合约) 的日期集合，以及已知非交易日集合。
    返回: (complete_dates_by_var, known_non_trading_dates)
    """
    from db_manager import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT variety, trade_date, COUNT(DISTINCT delivery_month) as m_cnt
        FROM czce_daily_bars
        WHERE variety IN ('SA', 'FG') AND delivery_month IN ('01', '05', '09')
        GROUP BY variety, trade_date
        HAVING m_cnt >= 3;
    """)
    complete_by_var: Dict[str, Set[str]] = {"SA": set(), "FG": set()}
    for r in cur.fetchall():
        complete_by_var[r["variety"]].add(r["trade_date"])

    cur.execute("SELECT trade_date FROM trade_calendar WHERE is_trading_day = 0;")
    non_trading = {r["trade_date"] for r in cur.fetchall()}

    conn.close()
    return complete_by_var, non_trading


def mark_non_trading_dates(dates: List[str]) -> None:
    """将确认休市的历史工作日记入 trade_calendar(is_trading_day=0)，避免后续重复请求"""
    if not dates:
        return
    from db_manager import get_db_connection
    conn = get_db_connection()
    conn.executemany("""
        INSERT INTO trade_calendar (trade_date, is_trading_day, notes)
        VALUES (?, 0, 'CZCE非交易日')
        ON CONFLICT(trade_date) DO NOTHING;
    """, [(d,) for d in dates])
    conn.commit()
    conn.close()


def build_incremental_date_list(
    complete_by_var: Dict[str, Set[str]],
    non_trading: Set[str],
    force: bool = False,
    past_days: Optional[int] = None,
    target_dates: Optional[List[str]] = None,
) -> List[str]:
    """计算需要从郑商所官方按日接口拉取的增量/缺口日期清单"""
    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")
    both_complete = complete_by_var["SA"] & complete_by_var["FG"]

    if target_dates:
        return sorted(set(target_dates))

    if past_days:
        # 向前按工作日回溯，覆盖最近 past_days 个交易日（约 1.6 倍自然日）
        start_dt = today - datetime.timedelta(days=max(past_days * 2, 15))
        cand: List[str] = []
        curr = start_dt
        while curr <= today:
            if curr.weekday() < 5:
                ds = curr.strftime("%Y-%m-%d")
                if force or (ds not in both_complete and (ds not in non_trading or ds == today_str)):
                    cand.append(ds)
            curr += datetime.timedelta(days=1)
        return cand

    # 1. 先检查单品种缺口日（例如 2026 年已有 FG 但缺 SA 的全部 218 个交易日！）
    one_sided_missing = (complete_by_var["SA"] ^ complete_by_var["FG"])

    # 2. 再检查最新完整日期之后直到今天的所有工作日
    all_present = complete_by_var["SA"] | complete_by_var["FG"]
    if all_present:
        latest_present = datetime.datetime.strptime(max(all_present), "%Y-%m-%d").date()
        scan_start = latest_present + datetime.timedelta(days=1)
    else:
        scan_start = datetime.date(today.year, 1, 1)

    forward_dates: Set[str] = set()
    curr = scan_start
    while curr <= today:
        if curr.weekday() < 5:
            ds = curr.strftime("%Y-%m-%d")
            if ds not in non_trading or ds == today_str:
                forward_dates.add(ds)
        curr += datetime.timedelta(days=1)

    # 若今日是工作日且尚未同时具备 SA 和 FG，也纳入尝试
    if today.weekday() < 5 and today_str not in both_complete:
        forward_dates.add(today_str)

    if force:
        # 强制模式：补齐最近 30 天 + 所有单品种缺口
        recent_start = today - datetime.timedelta(days=45)
        curr = recent_start
        while curr <= today:
            if curr.weekday() < 5:
                forward_dates.add(curr.strftime("%Y-%m-%d"))
            curr += datetime.timedelta(days=1)

    return sorted(one_sided_missing | forward_dates)


def sync_czce_spread_bars(
    force: bool = False,
    past_days: Optional[int] = None,
    target_dates: Optional[List[str]] = None,
    skip_temp: bool = False,
) -> None:
    """全流程：本地 temp 历史包导入 -> 郑商所官方按日增量补齐 -> 落库 SQLite -> 物化前端 JSON/JS"""
    from db_manager import init_db, save_czce_daily_bars, export_czce_spreads_to_frontend
    init_db()

    complete_by_var, non_trading = get_db_daily_bar_coverage()
    existing_days_count = len(complete_by_var["SA"] & complete_by_var["FG"])

    # 1. 本地 temp 历史包导入（首次冷启动、存在年份缺口或指定 --force 时执行）
    if not skip_temp and os.path.isdir(TEMP_DIR):
        need_temp_import = force or existing_days_count < 700
        if need_temp_import:
            print("📂 [历史底座] 检测到 temp/ 目录，正在扫描并导入郑商所官方历史数据...")
            temp_records = import_local_temp_files(TEMP_DIR)
            if temp_records:
                saved = save_czce_daily_bars(temp_records)
                print(f"✅ [历史底座] 已将 temp/ 中 {saved} 条 SA/FG 01·05·09 日行情写入 SQLite。")
                complete_by_var, non_trading = get_db_daily_bar_coverage()

    # 2. 计算缺口与增量日期，低频礼貌拉取郑商所官方 FutureDataDaily.txt
    dates_to_fetch = build_incremental_date_list(
        complete_by_var=complete_by_var,
        non_trading=non_trading,
        force=force,
        past_days=past_days,
        target_dates=target_dates,
    )

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    if not dates_to_fetch:
        latest_d = max(complete_by_var["SA"] & complete_by_var["FG"]) if (complete_by_var["SA"] & complete_by_var["FG"]) else "无"
        print(f"⏩ 纯碱(SA)/玻璃(FG) 01·05·09 合约日行情均已完整同步 (共 {len(complete_by_var['SA'] & complete_by_var['FG'])} 个交易日，最新: {latest_d})。")
    else:
        print(f"🌐 [官方增量同步] 待从郑商所拉取 {len(dates_to_fetch)} 个交易日 ({dates_to_fetch[0]} ~ {dates_to_fetch[-1]})...")
        fetched_records: List[Dict[str, Any]] = []
        confirmed_holidays: List[str] = []
        failed_dates: List[str] = []

        def _fetch_worker(d_str: str) -> Tuple[str, Optional[str], int]:
            time.sleep(0.12)  # 低频礼貌限速，降低对官网静态服务的瞬时请求压力
            return fetch_czce_daily_file(d_str)

        max_workers = min(3, max(1, len(dates_to_fetch)))
        done_cnt = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_worker, d): d for d in dates_to_fetch}
            for fut in as_completed(futures):
                d_str, txt, status = fut.result()
                done_cnt += 1
                if status == 200 and txt:
                    recs, has_market = _parse_daily_txt_content(d_str, txt)
                    if recs:
                        fetched_records.extend(recs)
                    elif not has_market and d_str < today_str:
                        confirmed_holidays.append(d_str)
                elif status == 404:
                    if d_str < today_str:
                        confirmed_holidays.append(d_str)
                else:
                    failed_dates.append(f"{d_str}(HTTP {status})")

                if done_cnt % 40 == 0 or done_cnt == len(dates_to_fetch):
                    print(f"  -> 抓取进度: {done_cnt}/{len(dates_to_fetch)} (累计提取 {len(fetched_records)} 条记录)")

        if fetched_records:
            saved_inc = save_czce_daily_bars(fetched_records)
            print(f"✅ [官方增量同步] 成功写入 {saved_inc} 条 SA/FG 日行情记录至 SQLite。")
        if confirmed_holidays:
            mark_non_trading_dates(confirmed_holidays)
            print(f"🗓️ 已记录 {len(confirmed_holidays)} 个历史非交易日跳过后续重复请求。")
        if failed_dates:
            print(f"⚠️ 以下日期拉取未成功（下次调度将自动重试）: {failed_dates[:8]}")

    # 3. 从 SQLite 物化导出前端消费文件
    export_czce_spreads_to_frontend(JSON_OUTPUT_PATH, JS_OUTPUT_PATH)
    print(f"📊 [SQLite 聚合发布] 已生成: {JSON_OUTPUT_PATH} ({os.path.getsize(JSON_OUTPUT_PATH) // 1024} KB)")
    print(f"🚀 [SQLite 离线垫片] 已生成: {JS_OUTPUT_PATH} ({os.path.getsize(JS_OUTPUT_PATH) // 1024} KB)")


def verify_czce_spread_data() -> bool:
    """全面质量审计：核验 SA/FG 各年份交易日覆盖、01/05/09 完整性、无异常 0 价与最新价差"""
    print("=" * 72)
    print("🩺 正在对 SQLite 郑商所纯碱(SA)/玻璃(FG) 01·05·09 日行情进行质量体检...")
    print("=" * 72)

    from db_manager import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT variety, SUBSTR(trade_date, 1, 4) as yr,
               COUNT(DISTINCT trade_date) as days,
               COUNT(*) as rows,
               MIN(trade_date) as min_d,
               MAX(trade_date) as max_d
        FROM czce_daily_bars
        WHERE variety IN ('SA', 'FG')
        GROUP BY variety, yr
        ORDER BY variety ASC, yr ASC;
    """)
    stats = cur.fetchall()
    if not stats:
        print("❌ czce_daily_bars 表中暂无数据！")
        conn.close()
        return False

    for s in stats:
        print(f"  📌 {s['variety']} | {s['yr']}年: {s['days']} 交易日, {s['rows']} 条记录 ({s['min_d']} ~ {s['max_d']})")

    issues: List[str] = []

    # 1. 检查是否每日每品种均完整含 01/05/09 三个合约
    cur.execute("""
        SELECT variety, trade_date, COUNT(DISTINCT delivery_month) as m_cnt
        FROM czce_daily_bars
        WHERE variety IN ('SA', 'FG')
        GROUP BY variety, trade_date
        HAVING m_cnt != 3;
    """)
    incomplete = cur.fetchall()
    if incomplete:
        issues.append(f"存在非 3 合约完整日 {len(incomplete)} 条: {[dict(r) for r in incomplete[:5]]}")

    # 2. 检查是否残留 0.00 脏价格
    cur.execute("""
        SELECT COUNT(*) as cnt
        FROM czce_daily_bars
        WHERE open = 0 OR high = 0 OR low = 0 OR close = 0;
    """)
    zero_p = cur.fetchone()["cnt"]
    if zero_p > 0:
        issues.append(f"存在 {zero_p} 条未清洗的 0.00 脏价格记录")

    # 3. 检查 SA 与 FG 交易日期是否对齐
    cur.execute("""
        SELECT trade_date, COUNT(DISTINCT variety) as v_cnt
        FROM czce_daily_bars
        WHERE variety IN ('SA', 'FG')
        GROUP BY trade_date
        HAVING v_cnt < 2;
    """)
    unaligned = cur.fetchall()
    if unaligned:
        issues.append(f"SA 与 FG 日期未对齐 {len(unaligned)} 天: {[r['trade_date'] for r in unaligned[:5]]}")

    # 4. 打印最新交易日 SA / FG 的 01/05/09 收盘价及价差
    cur.execute("SELECT MAX(trade_date) as max_d FROM czce_daily_bars WHERE variety IN ('SA', 'FG');")
    latest_d = cur.fetchone()["max_d"]
    if latest_d:
        print(f"\n🎯 最新交易日 [{latest_d}] 01·05·09 收盘价与月间价差:")
        for var in TARGET_VARIETIES:
            cur.execute("""
                SELECT delivery_month, contract_std, open, high, low, close, settle
                FROM czce_daily_bars
                WHERE trade_date = ? AND variety = ?
                ORDER BY delivery_month ASC;
            """, (latest_d, var))
            m_map = {r["delivery_month"]: dict(r) for r in cur.fetchall()}
            if len(m_map) == 3:
                c01, c05, c09 = m_map["01"], m_map["05"], m_map["09"]
                s15 = (c01["close"] - c05["close"]) if (c01["close"] is not None and c05["close"] is not None) else None
                s59 = (c05["close"] - c09["close"]) if (c05["close"] is not None and c09["close"] is not None) else None
                s91 = (c09["close"] - c01["close"]) if (c09["close"] is not None and c01["close"] is not None) else None
                print(
                    f"  - {var}: {c01['contract_std']}={c01['close']}, {c05['contract_std']}={c05['close']}, {c09['contract_std']}={c09['close']} "
                    f"| 收盘价差: 01-05={s15}, 05-09={s59}, 09-01={s91}"
                )

    conn.close()

    if issues:
        print("\n⚠️ 体检发现以下异常:")
        for iss in issues:
            print(f"  - {iss}")
        print("=" * 72)
        return False

    print("\n✅ 全部纯碱(SA)/玻璃(FG) 01·05·09 日行情数据校验通过，完整且无脏价！")
    print("=" * 72)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="郑商所纯碱(SA)/玻璃(FG) 01·05·09 合约历史导入与自动增量同步引擎")
    parser.add_argument("--force", action="store_true", default=False, help="强制重新导入 temp 并刷新近期数据")
    parser.add_argument("--past-days", type=int, default=None, help="增量同步最近 N 个交易日")
    parser.add_argument("--date", "--dates", dest="dates", nargs="*", default=None, help="指定同步的交易日 (YYYY-MM-DD)")
    parser.add_argument("--skip-temp", action="store_true", default=False, help="跳过扫描 temp/ 历史包")
    parser.add_argument("--verify", action="store_true", help="执行数据质量全面体检")
    args = parser.parse_args()

    parsed_dates: List[str] = []
    if args.dates:
        for item in args.dates:
            parsed_dates.extend(re.findall(r"\d{4}-\d{2}-\d{2}", item))

    if args.verify:
        ok = verify_czce_spread_data()
        sys.exit(0 if ok else 1)
    else:
        sync_czce_spread_bars(
            force=args.force,
            past_days=args.past_days,
            target_dates=parsed_dates or None,
            skip_temp=args.skip_temp,
        )
        verify_czce_spread_data()
