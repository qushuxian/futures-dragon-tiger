#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大商所/郑商所/上期所 8 大核心期货品种仓单日报自动化采集与预处理系统
------------------------------------------------------------------
数据源策略（100% 真实披露数据，严禁跨年错配与伪造比例）：
1. 郑商所（SA 纯碱、FG 玻璃、MA 甲醇）：
   - 直连郑商所官方 DFS 每日仓单 Excel 报表 (FutureDataWhsheet.xlsx)，
     基于表头列名动态映射（兼容甲醇“完税+保税”双列与玻璃无“有效预报”列），
     精确提取真实注册仓单、当日增减、有效预报与各仓库/厂库明细。
2. 上期所（RB 螺纹钢、HC 热轧卷板、AO 氧化铝）：
   - 直连上期所官方每日仓单 HTML 页面 (dailystock_{YYYYMMDD}/ZH/shfe/{prod}.html)，
     完整解析带 rowspan/colspan 的仓库与厂库提货地表，按吨汇总仓库仓单与厂库仓单。
3. 大商所（JM 焦煤、J 焦炭）及全品种总量补充：
   - 以东方财富数据中心 RPT_FUTU_STOCKDATA 为首选总量通道，99期货网 stockIn 为历史回溯与备用通道。
   - 因大商所官网启用瑞数动态反爬校验，暂不模拟突破反爬，亦不再使用固定比例伪造仓库明细，
     对大商所品种仅展示全市场真实注册仓单总量及增减。
4. 自愈状态机：
   - 取消 500 假数据兜底；若当日部分交易所尚未出数，标记为 PARTIAL，确保下次定时任务自动增量补齐。
"""

import os
import sys
import io
import re
import json
import time
import datetime
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Tuple

import requests
import pandas as pd
from bs4 import BeautifulSoup

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

RECEIPT_DAILY_DIR = os.path.join(DATA_DIR, "receipt_daily")
JSON_OUTPUT_PATH = os.path.join(DATA_DIR, "coking_coal_receipts.json")
JS_OUTPUT_PATH = os.path.join(DATA_DIR, "coking_coal_receipts.data.js")

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 8 大核心品种元数据定义（仓单口径）
COMMODITY_META: Dict[str, Dict[str, Any]] = {
    "JM": {
        "name": "焦煤",
        "code": "JM",
        "desc": "Coking Coal",
        "exchange": "大连商品交易所 (DCE)",
        "unit": "手",
        "ton_per_hand": 60,
        "99qh_pid": 75,
    },
    "J": {
        "name": "焦炭",
        "code": "J",
        "desc": "Coke",
        "exchange": "大连商品交易所 (DCE)",
        "unit": "手",
        "ton_per_hand": 100,
        "99qh_pid": 74,
    },
    "SA": {
        "name": "纯碱",
        "code": "SA",
        "desc": "Soda Ash",
        "exchange": "郑州商品交易所 (CZCE)",
        "unit": "手",
        "ton_per_hand": 20,
        "99qh_pid": 57,
    },
    "FG": {
        "name": "玻璃",
        "code": "FG",
        "desc": "Glass",
        "exchange": "郑州商品交易所 (CZCE)",
        "unit": "手",
        "ton_per_hand": 20,
        "99qh_pid": 50,
    },
    "MA": {
        "name": "甲醇",
        "code": "MA",
        "desc": "Methanol",
        "exchange": "郑州商品交易所 (CZCE)",
        "unit": "手",
        "ton_per_hand": 10,
        "99qh_pid": 49,
    },
    "RB": {
        "name": "螺纹钢",
        "code": "RB",
        "desc": "Rebar",
        "exchange": "上海期货交易所 (SHFE)",
        "unit": "吨",
        "ton_per_hand": 1,
        "99qh_pid": 11,
        "shfe_prod": "rb",
    },
    "AO": {
        "name": "氧化铝",
        "code": "AO",
        "desc": "Alumina",
        "exchange": "上海期货交易所 (SHFE)",
        "unit": "吨",
        "ton_per_hand": 1,
        "99qh_pid": 8,
        "shfe_prod": "ao",
    },
    "HC": {
        "name": "热轧卷板",
        "code": "HC",
        "desc": "Hot Rolled Coil",
        "exchange": "上海期货交易所 (SHFE)",
        "unit": "吨",
        "ton_per_hand": 1,
        "99qh_pid": 13,
        "shfe_prod": "hc",
    },
}

CITY_TO_PROVINCE = [
    ("青岛", "山东"), ("日照", "山东"), ("董家口", "山东"), ("淄博", "山东"), ("潍坊", "山东"), ("滕州", "山东"), ("黄岛", "山东"), ("胶州", "山东"), ("市北", "山东"),
    ("曹妃甸", "河北"), ("沙河", "河北"), ("唐山", "河北"), ("石家庄", "河北"), ("藁城", "河北"), ("高邑", "河北"), ("衡水", "河北"), ("迁安", "河北"), ("邯郸", "河北"), ("承德", "河北"),
    ("太仓", "江苏"), ("常州", "江苏"), ("南通", "江苏"), ("江阴", "江苏"), ("张家港", "江苏"), ("连云港", "江苏"), ("无锡", "江苏"), ("南京", "江苏"), ("苏州", "江苏"), ("常熟", "江苏"), ("镇江", "江苏"),
    ("宁波", "浙江"), ("湖州", "浙江"), ("嘉兴", "浙江"), ("杭州", "浙江"),
    ("佛山", "广东"), ("东莞", "广东"), ("广州", "广东"), ("南沙", "广东"),
    ("钦州", "广西"), ("防城港", "广西"), ("柳州", "广西"),
    ("乌鲁木齐", "新疆"), ("头屯河", "新疆"), ("中疆", "新疆"), ("炬申新疆", "新疆"),
    ("安阳", "河南"), ("三门峡", "河南"), ("洛阳", "河南"), ("郑州", "河南"), ("濮阳", "河南"),
    ("介休", "山西"), ("交口", "山西"),
    ("荆州", "湖北"), ("武汉", "湖北"), ("洪湖", "湖北"),
    ("西安", "陕西"),
    ("塘沽", "天津"), ("滨海", "天津"),
    ("上港", "上海"), ("张华浜", "上海"),
    ("嘉峪关", "甘肃"), ("兰州", "甘肃"), ("甘肃", "甘肃"),
]

PROVINCES = [
    "山东", "河北", "江苏", "河南", "山西", "安徽", "湖北", "广东",
    "广西", "内蒙古", "新疆", "浙江", "上海", "天津", "辽宁", "四川",
    "陕西", "甘肃", "北京", "重庆", "福建", "江西", "云南", "贵州",
]


def safe_int(val: Any) -> int:
    """安全将数字、浮点或带逗号字符串转换为整数"""
    if val is None:
        return 0
    try:
        if pd.isna(val):
            return 0
    except Exception:
        pass
    if isinstance(val, (int,)):
        return int(val)
    if isinstance(val, float):
        return int(round(val))
    s = str(val).strip().replace(",", "")
    if not s or s in ("-", "--", "nan", "None"):
        return 0
    try:
        return int(round(float(s)))
    except ValueError:
        cleaned = re.sub(r"[^\d\-+.]", "", s)
        if not cleaned or cleaned in ("-", "+"):
            return 0
        try:
            return int(round(float(cleaned)))
        except ValueError:
            return 0


def infer_warehouse_region_and_type(
    wh_name: str,
    explicit_region: Optional[str] = None,
    is_factory: bool = False,
) -> str:
    """
    根据显式地区、物理地名关键词及库型生成标准化区域标签。
    城市/港口关键词优先于企业前缀省份，避免如“浙江汇善（青岛港）”误判为浙江。
    """
    wh_clean = str(wh_name).strip()
    province = (explicit_region or "").strip()

    if province == "厂库提货地":
        return "厂库提货地"

    if not province:
        for city, prov in CITY_TO_PROVINCE:
            if city in wh_clean:
                province = prov
                break

    if not province:
        for prov in PROVINCES:
            if prov in wh_clean:
                province = prov
                break

    if is_factory:
        return f"{province}厂库" if province else "厂库提货地"

    is_port = any(k in wh_clean for k in ["港", "曹妃甸", "董家口", "码头", "海铁", "海运"])
    if is_port:
        return f"{province}港口" if province else "港口指定库"
    if province:
        return f"{province}指定库"
    return "指定交割库"


def ensure_dirs() -> None:
    os.makedirs(RECEIPT_DAILY_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(JSON_OUTPUT_PATH), exist_ok=True)


def http_get_with_retry(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    retries: int = 2,
) -> Optional[requests.Response]:
    """带重试机制的 HTTP GET 封装"""
    hdrs = headers or HTTP_HEADERS
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404:
                return None
        except Exception:
            pass
        if attempt < retries:
            time.sleep(0.4 * (attempt + 1))
    return None


def get_known_trading_dates_from_db() -> List[str]:
    """从 SQLite 数据库读取已知交易日（交易日历、龙虎榜及已归档仓单日期并集）"""
    try:
        from db_manager import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT trade_date FROM trade_calendar WHERE is_trading_day = 1
            UNION
            SELECT DISTINCT trade_date FROM contract_metrics
            UNION
            SELECT DISTINCT trade_date FROM receipt_summaries
            ORDER BY trade_date ASC;
        """)
        dates = [r["trade_date"] for r in cur.fetchall() if r["trade_date"]]
        conn.close()
        return dates
    except Exception:
        return []


def fetch_eastmoney_real_totals(
    symbols: Optional[List[str]] = None,
    page_size: int = 200,
) -> Tuple[Dict[str, Dict[str, Dict[str, int]]], Dict[str, str]]:
    """
    从东方财富数据中心 RPT_FUTU_STOCKDATA 拉取各品种历史仓单总量与当日增减。
    返回: (date_sym_map, latest_date_by_sym)
    """
    if not symbols:
        symbols = list(COMMODITY_META.keys())

    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    headers = {**HTTP_HEADERS, "Accept": "*/*", "Referer": "https://data.eastmoney.com/"}

    date_sym_map: Dict[str, Dict[str, Dict[str, int]]] = {}
    latest_date_by_sym: Dict[str, str] = {}
    print("🌐 [东财总量通道] 正在拉取 8 大期货品种官方仓单时序基准...")

    for sym in symbols:
        params = {
            "reportName": "RPT_FUTU_STOCKDATA",
            "columns": "SECURITY_CODE,TRADE_DATE,ON_WARRANT_NUM,ADDCHANGE",
            "filter": f'(SECURITY_CODE="{sym}")',
            "pageNumber": "1",
            "pageSize": str(page_size),
            "sortTypes": "-1",
            "sortColumns": "TRADE_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        resp = http_get_with_retry(url, params=params, headers=headers, timeout=10.0, retries=2)
        if not resp:
            print(f"  ⚠️ 东财拉取 {sym} 无响应")
            continue
        try:
            res_json = resp.json()
            rows = (res_json.get("result") or {}).get("data") or []
            for row in rows:
                t_date = str(row.get("TRADE_DATE") or "")[:10]
                if not t_date:
                    continue
                receipt_val = safe_int(row.get("ON_WARRANT_NUM"))
                diff_val = safe_int(row.get("ADDCHANGE"))
                date_sym_map.setdefault(t_date, {})[sym] = {
                    "receipt": receipt_val,
                    "diff": diff_val,
                    "source": "eastmoney",
                }
                if sym not in latest_date_by_sym or t_date > latest_date_by_sym[sym]:
                    latest_date_by_sym[sym] = t_date
            time.sleep(0.03)
        except Exception as e:
            print(f"  ⚠️ 解析东财 {sym} 数据异常: {e}")

    return date_sym_map, latest_date_by_sym


def fetch_99qh_real_totals(
    symbols: Optional[List[str]] = None,
    min_date: str = "2026-06-01",
) -> Tuple[Dict[str, Dict[str, Dict[str, int]]], Dict[str, str]]:
    """
    从 99 期货网 stockIn 页面提取日度仓单总量序列（支持东财 65 天窗口之外的历史回补及备通道）。
    返回: (date_sym_map, latest_date_by_sym)
    """
    if not symbols:
        symbols = list(COMMODITY_META.keys())

    date_sym_map: Dict[str, Dict[str, Dict[str, int]]] = {}
    latest_date_by_sym: Dict[str, str] = {}
    print("🌐 [99期货补充通道] 正在拉取历史仓单总量与回溯序列...")

    for sym in symbols:
        pid = COMMODITY_META.get(sym, {}).get("99qh_pid")
        if not pid:
            continue
        url = f"https://www.99qh.com/data/stockIn?productId={pid}"
        resp = http_get_with_retry(url, timeout=10.0, retries=1)
        if not resp:
            continue
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            nd = soup.find("script", id="__NEXT_DATA__")
            if not nd or not nd.string:
                continue
            pp = json.loads(nd.string).get("props", {}).get("pageProps", {}).get("data", {})

            # 1. 提取近期表格中的精确日增减 stockChange
            change_map: Dict[str, int] = {}
            tbl_list = (pp.get("positionTrendTableListData") or {}).get("list") or []
            for item in tbl_list:
                d_str = str(item.get("date") or "")[:10]
                if d_str and item.get("stockChange") is not None:
                    change_map[d_str] = safe_int(item.get("stockChange"))

            # 2. 提取完整日度仓单序列 [date, close, stock]
            chart_list = (pp.get("positionTrendChartListData") or {}).get("list") or []
            parsed_points: List[Tuple[str, int]] = []
            for pt in chart_list:
                if not isinstance(pt, (list, tuple)) or len(pt) < 3:
                    continue
                d_str = str(pt[0] or "")[:10]
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", d_str):
                    continue
                parsed_points.append((d_str, safe_int(pt[2])))

            # 升序排列以便在缺乏 stockChange 时基于上一已知日计算差分
            parsed_points.sort(key=lambda x: x[0])
            prev_val: Optional[int] = None
            for d_str, qty in parsed_points:
                diff_val = change_map[d_str] if d_str in change_map else (qty - prev_val if prev_val is not None else 0)
                prev_val = qty
                if d_str < min_date:
                    continue
                date_sym_map.setdefault(d_str, {})[sym] = {
                    "receipt": qty,
                    "diff": diff_val,
                    "source": "99qh",
                }
                if sym not in latest_date_by_sym or d_str > latest_date_by_sym[sym]:
                    latest_date_by_sym[sym] = d_str
        except Exception as e:
            print(f"  ⚠️ 解析 99期货 {sym} 异常: {e}")

    return date_sym_map, latest_date_by_sym


def _resolve_czce_columns(header_row: List[Any]) -> Dict[str, Any]:
    """根据郑商所 Excel 区块表头动态定位各列索引（兼容完税/保税双列及无预报列）"""
    col_code = 0
    col_name = 1
    qty_cols: List[int] = []
    col_diff: Optional[int] = None
    col_forecast: Optional[int] = None

    for idx, cell in enumerate(header_row):
        title = str(cell).strip() if pd.notna(cell) else ""
        if not title:
            continue
        if "仓库编号" in title:
            col_code = idx
        elif "仓库简称" in title:
            col_name = idx
        elif "仓单数量" in title:
            qty_cols.append(idx)
        elif "当日增减" in title or "增减" in title:
            col_diff = idx
        elif "有效预报" in title or "预报" in title:
            col_forecast = idx

    return {
        "code": col_code,
        "name": col_name,
        "qty_cols": qty_cols,
        "diff": col_diff,
        "forecast": col_forecast,
    }


def fetch_czce_official_details(trade_date: str) -> Optional[Dict[str, Any]]:
    """
    直连郑商所官方 DFS 服务下载并解析当日仓单 Excel (FutureDataWhsheet.xlsx)。
    严格只请求 trade_date 对应年份与日期，严禁跨年回退。
    """
    date_clean = trade_date.replace("-", "")
    year = date_clean[:4]
    url = f"http://www.czce.com.cn/cn/DFSStaticFiles/Future/{year}/{date_clean}/FutureDataWhsheet.xlsx"

    resp = http_get_with_retry(url, timeout=10.0, retries=2)
    if not resp:
        return None

    try:
        df = pd.read_excel(io.BytesIO(resp.content), engine="openpyxl")
    except ImportError as e:
        print(f"  ❌ 缺少 openpyxl 依赖，无法解析郑商所 Excel: {e}")
        return None
    except Exception:
        return None

    try:
        results: Dict[str, Any] = {}
        target_list = [("纯碱", "SA"), ("玻璃", "FG"), ("甲醇", "MA")]

        for comm_name, code in target_list:
            start_idx = None
            for idx, r_row in df.iterrows():
                f_val = str(r_row.iloc[0]) if pd.notna(r_row.iloc[0]) else ""
                if code in f_val and (comm_name in f_val or "品种" in f_val):
                    start_idx = idx
                    break
            if start_idx is None or start_idx + 1 >= len(df):
                continue

            cols_map = _resolve_czce_columns(list(df.iloc[start_idx + 1].values))
            if not cols_map["qty_cols"]:
                continue

            cur_wh_name: Optional[str] = None
            warehouses: List[Dict[str, Any]] = []
            tot_qty = 0
            tot_diff = 0
            tot_forecast = 0
            has_total = False

            for row_idx in range(start_idx + 2, len(df)):
                r_val = df.iloc[row_idx].values
                first_str = str(r_val[0]).strip() if pd.notna(r_val[0]) else ""

                if "品种" in first_str:
                    break

                if first_str == "总计":
                    tot_qty = sum(safe_int(r_val[c]) for c in cols_map["qty_cols"])
                    tot_diff = safe_int(r_val[cols_map["diff"]]) if cols_map["diff"] is not None else 0
                    tot_forecast = safe_int(r_val[cols_map["forecast"]]) if cols_map["forecast"] is not None else 0
                    has_total = True
                    break

                code_cell = str(r_val[cols_map["code"]]).strip() if pd.notna(r_val[cols_map["code"]]) else ""
                if code_cell.isdigit() and len(code_cell) == 4 and pd.notna(r_val[cols_map["name"]]):
                    cur_wh_name = str(r_val[cols_map["name"]]).strip()

                if first_str == "小计" and cur_wh_name:
                    qty = sum(safe_int(r_val[c]) for c in cols_map["qty_cols"])
                    diff = safe_int(r_val[cols_map["diff"]]) if cols_map["diff"] is not None else 0
                    fc = safe_int(r_val[cols_map["forecast"]]) if cols_map["forecast"] is not None else 0
                    if qty > 0 or diff != 0 or fc > 0:
                        is_fac = "厂库" in cur_wh_name
                        warehouses.append({
                            "name": cur_wh_name,
                            "is_factory": is_fac,
                            "region": infer_warehouse_region_and_type(cur_wh_name, is_factory=is_fac),
                            "today_receipt": qty,
                            "diff": diff,
                            "forecast": fc,
                            "last_receipt": qty - diff,
                        })

            if has_total:
                results[code] = {
                    "total_receipt": tot_qty,
                    "diff": tot_diff,
                    "forecast": tot_forecast,
                    "warehouses": warehouses,
                }
        return results
    except Exception:
        return None


def _expand_html_table(table: Any) -> List[List[str]]:
    """
    展开带 rowspan 与 colspan 的 HTML 表格为规整的二维字符串矩阵。
    确保上期所按地区或厂库合并单元格后的每行都能对齐表头列索引。
    """
    grid: Dict[Tuple[int, int], str] = {}
    max_cols = 0
    rows = table.find_all("tr")

    for r_idx, tr in enumerate(rows):
        c_idx = 0
        cells = tr.find_all(["td", "th"])
        for cell in cells:
            while (r_idx, c_idx) in grid:
                c_idx += 1
            text = cell.get_text(strip=True)
            rowspan = max(1, safe_int(cell.get("rowspan") or 1))
            colspan = max(1, safe_int(cell.get("colspan") or 1))
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r_idx + dr, c_idx + dc)] = text
            c_idx += colspan
            max_cols = max(max_cols, c_idx)

    matrix: List[List[str]] = []
    for r_idx in range(len(rows)):
        row_vals = [grid.get((r_idx, c_idx), "") for c_idx in range(max_cols)]
        if any(v for v in row_vals):
            matrix.append(row_vals)
    return matrix


def _parse_shfe_stock_table(table: Any, is_factory_table: bool) -> Tuple[int, int, List[Dict[str, Any]]]:
    """解析单张上期所仓单表格（仓库表或厂库表）"""
    matrix = _expand_html_table(table)
    if not matrix:
        return 0, 0, []

    header = matrix[0]
    reg_col: Optional[int] = None
    wh_col: Optional[int] = None
    qty_col: Optional[int] = None
    diff_col: Optional[int] = None

    for idx, h in enumerate(header):
        if "地区" in h:
            reg_col = idx
        elif "仓库" in h or "厂库" in h:
            wh_col = idx
        elif "期货" in h:
            qty_col = idx
        elif "增减" in h:
            diff_col = idx

    if wh_col is None or qty_col is None:
        return 0, 0, []

    raw_items: List[Dict[str, Any]] = []
    total_qty: Optional[int] = None
    total_diff: int = 0
    last_subtotal_qty: Optional[int] = None
    last_subtotal_diff: int = 0

    for row in matrix[1:]:
        row_joined = "".join(row)
        if "单位：" in row_joined or "(仓库)" in row_joined or "(厂库)" in row_joined:
            continue

        c0 = row[0].strip() if row else ""
        wh_raw = row[wh_col].strip() if wh_col < len(row) else ""
        reg_raw = row[reg_col].strip() if (reg_col is not None and reg_col < len(row)) else ""
        qty = safe_int(row[qty_col]) if qty_col < len(row) else 0
        diff = safe_int(row[diff_col]) if (diff_col is not None and diff_col < len(row)) else 0

        if c0 == "总计" or wh_raw == "总计" or reg_raw == "总计":
            total_qty = qty
            total_diff = diff
            continue

        if c0 == "合计" or wh_raw == "合计" or reg_raw == "合计":
            last_subtotal_qty = qty
            last_subtotal_diff = diff
            continue

        if not wh_raw:
            continue

        raw_items.append({
            "wh_raw": wh_raw,
            "reg_raw": reg_raw,
            "qty": qty,
            "diff": diff,
        })

    # 检查同一名称是否跨地区重复出现（例如氧化铝厂库“四邦实业”同时存在于甘肃、新疆）
    name_counts: Dict[str, int] = {}
    for it in raw_items:
        name_counts[it["wh_raw"]] = name_counts.get(it["wh_raw"], 0) + 1

    warehouses: List[Dict[str, Any]] = []
    sum_qty = 0
    sum_diff = 0
    for it in raw_items:
        qty = it["qty"]
        diff = it["diff"]
        sum_qty += qty
        sum_diff += diff
        if qty == 0 and diff == 0:
            continue
        wh_name = it["wh_raw"]
        reg_raw = it["reg_raw"]
        if name_counts.get(wh_name, 0) > 1 and reg_raw and reg_raw != "厂库提货地":
            wh_name = f"{wh_name}（{reg_raw}）"
        warehouses.append({
            "name": wh_name,
            "is_factory": is_factory_table,
            "region": infer_warehouse_region_and_type(wh_name, explicit_region=reg_raw, is_factory=is_factory_table),
            "today_receipt": qty,
            "diff": diff,
            "forecast": 0,
            "last_receipt": qty - diff,
            "tonnage": qty,
        })

    if total_qty is None:
        # 螺纹钢/热卷厂库表末尾仅含“合计”行时，以实际累加值或末行合计为准
        total_qty = sum_qty if raw_items else (last_subtotal_qty or 0)
        total_diff = sum_diff if raw_items else last_subtotal_diff

    return total_qty, total_diff, warehouses


def fetch_shfe_official_details(trade_date: str) -> Optional[Dict[str, Any]]:
    """
    直连上期所官方日度仓单 HTML 页面 (dailystock_{YYYYMMDD}/ZH/shfe/{prod}.html)，
    严格匹配 trade_date 对应日期，不跨年回退。
    """
    date_clean = trade_date.replace("-", "")
    headers = {**HTTP_HEADERS, "Referer": "https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/"}
    res: Dict[str, Any] = {}

    for code in ["RB", "HC", "AO"]:
        prod = COMMODITY_META[code]["shfe_prod"]
        url = f"https://www.shfe.com.cn/data/tradedata/future/stockdata/dailystock_{date_clean}/ZH/shfe/{prod}.html"
        resp = http_get_with_retry(url, headers=headers, timeout=10.0, retries=2)
        if not resp:
            continue

        try:
            html_txt = resp.content.decode("utf-8", errors="ignore")
            soup = BeautifulSoup(html_txt, "html.parser")
            tables = soup.find_all("table")
            if len(tables) < 2:
                continue

            wh_tot_qty, wh_tot_diff, wh_list = _parse_shfe_stock_table(tables[1], is_factory_table=False)
            fac_tot_qty, fac_tot_diff, fac_list = (0, 0, [])
            if len(tables) >= 3:
                fac_tot_qty, fac_tot_diff, fac_list = _parse_shfe_stock_table(tables[2], is_factory_table=True)

            total_tonnage = wh_tot_qty + fac_tot_qty
            total_diff_tonnage = wh_tot_diff + fac_tot_diff
            warehouses = wh_list + fac_list

            res[code] = {
                "total_receipt": total_tonnage,
                "total_tonnage": total_tonnage,
                "diff": total_diff_tonnage,
                "forecast": 0,
                "warehouse_receipt": wh_tot_qty,
                "factory_receipt": fac_tot_qty,
                "warehouse_tonnage": wh_tot_qty,
                "factory_tonnage": fac_tot_qty,
                "warehouses": warehouses,
            }
        except Exception:
            continue

    return res if res else None


def fetch_exchange_details_for_date(trade_date: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """拉取单个交易日的郑商所 + 上期所官方明细（供并发调度）"""
    czce_details = fetch_czce_official_details(trade_date)
    shfe_details = fetch_shfe_official_details(trade_date)
    return trade_date, czce_details, shfe_details


def assemble_daily_snapshot(
    trade_date: str,
    em_totals_map: Dict[str, Dict[str, Dict[str, int]]],
    em_latest_by_sym: Dict[str, str],
    qh_totals_map: Dict[str, Dict[str, Dict[str, int]]],
    qh_latest_by_sym: Dict[str, str],
    czce_details: Optional[Dict[str, Any]],
    shfe_details: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    严格基于真实数据装配单日仓单快照，彻底消除 500 兜底与固定比例伪造。
    """
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    snapshot: Dict[str, Any] = {
        "trade_date": trade_date,
        "sync_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "commodities": {},
    }

    j_published = (
        "J" in em_totals_map.get(trade_date, {})
        or "J" in qh_totals_map.get(trade_date, {})
    )

    for comm_code, meta in COMMODITY_META.items():
        em_stat = em_totals_map.get(trade_date, {}).get(comm_code)
        qh_stat = qh_totals_map.get(trade_date, {}).get(comm_code)
        base_stat = em_stat or qh_stat

        max_known_date = max(
            em_latest_by_sym.get(comm_code, ""),
            qh_latest_by_sym.get(comm_code, ""),
        )

        official_today = base_stat["receipt"] if base_stat else 0
        official_diff = base_stat["diff"] if base_stat else 0
        official_forecast = 0
        official_wh_qty = official_today
        official_fac_qty = 0
        warehouses: List[Dict[str, Any]] = []
        sync_status = "PARTIAL"

        # 路径 A: 郑商所 (SA, FG, MA) — 官方 Excel 为权威
        if comm_code in ("SA", "FG", "MA"):
            if czce_details and comm_code in czce_details:
                cz = czce_details[comm_code]
                official_today = cz["total_receipt"]
                official_diff = cz["diff"]
                official_forecast = cz.get("forecast", 0)
                warehouses = [dict(w) for w in cz.get("warehouses", [])]
                official_fac_qty = sum(w["today_receipt"] for w in warehouses if w.get("is_factory"))
                official_wh_qty = max(0, official_today - official_fac_qty)
                sync_status = "SUCCESS" if (official_today > 0 or official_forecast > 0 or len(warehouses) > 0) else "EMPTY_MARKET"
            elif base_stat is not None:
                # 仅拿到总量，缺官网 Excel 仓库明细 -> 标记 PARTIAL 以便后续重抓补全
                sync_status = "PARTIAL"
            else:
                sync_status = "PARTIAL"

        # 路径 B: 上期所 (RB, HC, AO) — 官方 HTML 为权威（含仓库 + 厂库提货地）
        elif comm_code in ("RB", "HC", "AO"):
            if shfe_details and comm_code in shfe_details:
                sh = shfe_details[comm_code]
                official_today = sh["total_receipt"]
                official_diff = sh["diff"]
                official_wh_qty = sh["warehouse_receipt"]
                official_fac_qty = sh["factory_receipt"]
                warehouses = [dict(w) for w in sh.get("warehouses", [])]
                sync_status = "SUCCESS" if (official_today > 0 or len(warehouses) > 0) else "EMPTY_MARKET"
            elif base_stat is not None:
                sync_status = "PARTIAL"
            else:
                sync_status = "PARTIAL"

        # 路径 C: 大商所 (JM, J) — 真实总量与增减（无官网明细时不伪造比例拆分）
        elif comm_code in ("JM", "J"):
            if base_stat is not None:
                sync_status = "SUCCESS" if official_today > 0 else "EMPTY_MARKET"
            elif max_known_date and trade_date < max_known_date:
                # 东财与 99 期货在焦煤连续 0 仓单期间会省略空行，已知后续日期已出数则确认当日真实为 0
                official_today = 0
                official_diff = 0
                official_wh_qty = 0
                sync_status = "EMPTY_MARKET"
            elif comm_code == "JM" and trade_date < today_str and j_published:
                official_today = 0
                official_diff = 0
                official_wh_qty = 0
                sync_status = "EMPTY_MARKET"
            else:
                # 今日尚未轮到该品种披露：记为 PARTIAL，等待下一次定时任务刷新
                official_today = 0
                official_diff = 0
                official_wh_qty = 0
                sync_status = "PARTIAL"

        official_last = official_today - official_diff
        tph = meta["ton_per_hand"]
        official_total_tonnage = float(official_today * tph)
        official_wh_tonnage = float(official_wh_qty * tph)
        official_fac_tonnage = float(official_fac_qty * tph)

        tot_today_actual = sum(w["today_receipt"] for w in warehouses) or official_today
        for w in warehouses:
            w["share_pct"] = round((w["today_receipt"] / tot_today_actual * 100) if tot_today_actual > 0 else 0.0, 2)

        warehouses.sort(key=lambda x: (x["today_receipt"], x.get("forecast", 0), abs(x.get("diff", 0))), reverse=True)
        active_count = len([
            w for w in warehouses
            if w["today_receipt"] > 0 or abs(w.get("diff", 0)) > 0 or w.get("forecast", 0) > 0
        ])

        snapshot["commodities"][comm_code] = {
            "code": comm_code,
            "name": meta["name"],
            "exchange": meta["exchange"],
            "unit": meta["unit"],
            "ton_per_hand": tph,
            "sync_status": sync_status,
            "summary": {
                "total_receipt": official_today,
                "last_total_receipt": official_last,
                "diff": official_diff,
                "forecast": official_forecast,
                "warehouse_receipt": official_wh_qty,
                "factory_receipt": official_fac_qty,
                "active_warehouses": active_count,
                "total_tonnage": official_total_tonnage,
                "warehouse_tonnage": official_wh_tonnage,
                "factory_tonnage": official_fac_tonnage,
            },
            "warehouses": warehouses,
        }

    return snapshot


def get_existing_receipt_dates() -> set:
    """
    从 SQLite 查询已完整且高质量归档的交易日集合：
    - 8 个品种在 sync_task_status 中全部处于 SUCCESS 或 EMPTY_MARKET（排除 PARTIAL / FAILED）
    - 8 个品种在 receipt_summaries 中均存在
    - 排除历史遗留异常数据（500 假兜底、负有效预报、郑商所/上期所有仓单却无仓库明细）
    """
    try:
        from db_manager import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT r.trade_date
            FROM receipt_summaries r
            LEFT JOIN sync_task_status s
              ON r.trade_date = s.trade_date
             AND r.variety = s.variety
             AND s.data_type = 'receipt'
            GROUP BY r.trade_date
            HAVING COUNT(DISTINCT r.variety) >= 8
               AND SUM(CASE WHEN s.status IN ('SUCCESS', 'EMPTY_MARKET') THEN 1 ELSE 0 END) >= 8
               AND SUM(CASE WHEN r.total_receipt = 500 AND r.diff = 0 AND r.active_warehouses = 0 THEN 1 ELSE 0 END) = 0
               AND SUM(CASE WHEN r.forecast < 0 THEN 1 ELSE 0 END) = 0
               AND SUM(CASE WHEN r.variety IN ('SA','FG','MA','RB','AO','HC')
                             AND r.total_receipt > 0 AND r.active_warehouses = 0 THEN 1 ELSE 0 END) = 0;
        """)
        dates = {r["trade_date"] for r in cur.fetchall()}
        conn.close()
        return dates
    except Exception:
        return set()


def sync_all_receipts(
    force: bool = False,
    past_days: Optional[int] = None,
    target_dates: Optional[List[str]] = None,
) -> None:
    """全量/增量同步交易日真实仓单数据并落盘 SQLite"""
    ensure_dirs()
    from db_manager import init_db, save_receipt_day
    init_db()

    db_dates = get_known_trading_dates_from_db()
    em_totals_map, em_latest_by_sym = fetch_eastmoney_real_totals(page_size=max(200, len(db_dates) + 30))

    min_db_date = min(db_dates) if db_dates else "2026-06-15"
    qh_totals_map, qh_latest_by_sym = fetch_99qh_real_totals(min_date=min_db_date)

    # 仅采纳确认为有效交易日的候选日期：
    # 1) 数据库已有龙虎榜/交易日历记录，或 2) 东财接口已返回，或 3) 历史缺失段 (<= 2026-08-03) 中 99qh 与郑/上期所同步存在
    # 避免 99qh 在中秋/国庆等休市日沿用节前数值导致误判为交易日
    try:
        from db_manager import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT trade_date FROM contract_metrics;")
        pos_dates = {r["trade_date"] for r in cur.fetchall()}
        conn.close()
    except Exception:
        pos_dates = set()

    earliest_pos_date = min(pos_dates) if pos_dates else "2026-08-03"
    candidate_dates = set(pos_dates) | set(em_totals_map.keys())
    for d in qh_totals_map.keys():
        if min_db_date <= d < earliest_pos_date:
            candidate_dates.add(d)

    all_dates = sorted([
        d for d in candidate_dates
        if datetime.datetime.strptime(d, "%Y-%m-%d").weekday() < 5
    ])

    if target_dates:
        target_set = set(target_dates)
        all_dates = sorted(list(target_set))
    elif past_days and past_days < len(all_dates):
        all_dates = all_dates[-past_days:]

    existing_dates = set() if force else get_existing_receipt_dates()
    dates_to_sync = [d for d in all_dates if d not in existing_dates]

    if not dates_to_sync:
        latest_date = all_dates[-1] if all_dates else "无"
        print(f"⏩ 所有目标交易日 ({len(all_dates)} 天) 均已在 SQLite 中高质量归档 (最新: {latest_date})，无需重抓 (加 --force 可强制全量刷新)")
        build_aggregate_output()
        return

    print(f"📦 [仓单采集引擎] 开始同步 {len(dates_to_sync)} 个交易日 (已完备跳过: {len(all_dates) - len(dates_to_sync)} 天)...")

    fetched_details: Dict[str, Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]] = {}
    max_workers = min(8, max(1, len(dates_to_sync)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_exchange_details_for_date, d): d for d in dates_to_sync}
        for future in as_completed(futures):
            d_str, cz, sh = future.result()
            fetched_details[d_str] = (cz, sh)

    synced_count = 0
    partial_dates: List[str] = []
    for idx, t_date in enumerate(dates_to_sync):
        cz, sh = fetched_details.get(t_date, (None, None))
        # 若该日既不在龙虎榜与东财记录中，又拿不到郑商所与上期所任何官方文件，则视为休市节假日跳过
        if t_date not in pos_dates and t_date not in em_totals_map and not cz and not sh and not target_dates:
            print(f"  ⏭️ 跳过非交易日/休市日: {t_date}")
            continue
        snapshot = assemble_daily_snapshot(
            trade_date=t_date,
            em_totals_map=em_totals_map,
            em_latest_by_sym=em_latest_by_sym,
            qh_totals_map=qh_totals_map,
            qh_latest_by_sym=qh_latest_by_sym,
            czce_details=cz,
            shfe_details=sh,
        )
        save_receipt_day(t_date, snapshot)
        synced_count += 1

        day_partials = [
            c for c, info in snapshot["commodities"].items()
            if info.get("sync_status") == "PARTIAL"
        ]
        if day_partials:
            partial_dates.append(f"{t_date}({','.join(day_partials)})")

        if (idx + 1) % 15 == 0 or (idx + 1) == len(dates_to_sync):
            print(f"  -> 已入库: {t_date} ({idx + 1}/{len(dates_to_sync)})")

    print(f"✅ [仓单采集引擎] SQLite 入库完成，本次更新: {synced_count} 天。")
    if partial_dates:
        print(f"⏳ 待补齐 PARTIAL 日期（下次调度将自动刷新）: {partial_dates[-5:]}")
    build_aggregate_output()


def build_aggregate_output() -> None:
    """
    通过 SQLite 物化导出引擎生成前端所需消费数据：
    1. data/coking_coal_receipts.json
    2. data/coking_coal_receipts.data.js
    """
    from db_manager import export_receipts_to_frontend
    export_receipts_to_frontend(JSON_OUTPUT_PATH, JS_OUTPUT_PATH)
    print(f"📊 [SQLite 聚合发布] 已生成: {JSON_OUTPUT_PATH} ({os.path.getsize(JSON_OUTPUT_PATH) // 1024} KB)")
    print(f"🚀 [SQLite 离线垫片] 已生成: {JS_OUTPUT_PATH} ({os.path.getsize(JS_OUTPUT_PATH) // 1024} KB)")


def verify_data_quality() -> bool:
    """全量仓单数据质量审计：检查 500 假值、负值异常、仓库明细加和一致性、吨数一致性和 PARTIAL 状态"""
    print("=" * 72)
    print("🩺 正在对 SQLite 全量仓单数据进行质量体检...")
    print("=" * 72)

    from db_manager import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(DISTINCT trade_date) as d_cnt, MIN(trade_date) as min_d, MAX(trade_date) as max_d FROM receipt_summaries;")
    meta_row = cur.fetchone()
    total_days = meta_row["d_cnt"] if meta_row else 0
    print(f"📅 已归档交易日: {total_days} 天 ({meta_row['min_d']} ~ {meta_row['max_d']})")

    issues: List[str] = []

    # 1. 检查疑似 500 假值（不含真正恰好为 500 且有仓库明细/有变动的日期）
    cur.execute("""
        SELECT trade_date, variety, total_receipt, diff, active_warehouses
        FROM receipt_summaries
        WHERE total_receipt = 500 AND diff = 0 AND active_warehouses = 0
        ORDER BY trade_date DESC;
    """)
    fake_500_rows = cur.fetchall()
    if fake_500_rows:
        issues.append(f"发现疑似 500 无明细假值 {len(fake_500_rows)} 条，例如: {[(r['trade_date'], r['variety']) for r in fake_500_rows[:5]]}")

    # 2. 检查负值与负预报
    cur.execute("""
        SELECT trade_date, variety, total_receipt, forecast
        FROM receipt_summaries
        WHERE total_receipt < 0 OR forecast < 0;
    """)
    neg_rows = cur.fetchall()
    if neg_rows:
        issues.append(f"发现负仓单或负有效预报 {len(neg_rows)} 条，例如: {[dict(r) for r in neg_rows[:5]]}")

    # 3. 检查郑商所与上期所总仓单 vs 仓库明细累加和一致性
    cur.execute("""
        SELECT r.trade_date, r.variety, r.total_receipt,
               COALESCE(SUM(w.today_receipt), 0) as wh_sum,
               COUNT(w.id) as wh_cnt
        FROM receipt_summaries r
        LEFT JOIN warehouse_details w
          ON r.trade_date = w.trade_date AND r.variety = w.variety
        WHERE r.variety IN ('SA', 'FG', 'MA', 'RB', 'AO', 'HC')
        GROUP BY r.trade_date, r.variety
        HAVING r.total_receipt > 0 AND (wh_cnt = 0 OR ABS(r.total_receipt - wh_sum) > 0);
    """)
    mismatch_rows = cur.fetchall()
    if mismatch_rows:
        issues.append(f"发现郑/上期所仓单总量与仓库明细加和不一致 {len(mismatch_rows)} 条，例如: {[dict(r) for r in mismatch_rows[:5]]}")

    # 4. 检查吨数拆分一致性
    cur.execute("""
        SELECT trade_date, variety, total_tonnage, warehouse_tonnage, factory_tonnage
        FROM receipt_summaries
        WHERE ABS((warehouse_tonnage + factory_tonnage) - total_tonnage) > 0.01;
    """)
    ton_rows = cur.fetchall()
    if ton_rows:
        issues.append(f"发现仓库+厂库吨数之和不等于总吨数 {len(ton_rows)} 条，例如: {[dict(r) for r in ton_rows[:5]]}")

    # 5. 检查 PARTIAL/FAILED 状态
    cur.execute("""
        SELECT status, COUNT(*) as cnt
        FROM sync_task_status
        WHERE data_type = 'receipt'
        GROUP BY status;
    """)
    status_summary = {r["status"]: r["cnt"] for r in cur.fetchall()}
    print(f"📌 任务状态分布: {status_summary}")

    # 6. 展示最新交易日全品种快照
    if meta_row and meta_row["max_d"]:
        latest_d = meta_row["max_d"]
        print(f"\n🎯 最新交易日 [{latest_d}] 8 大品种仓单校验:")
        cur.execute("""
            SELECT r.variety, r.variety_name, r.unit, r.total_receipt, r.diff, r.forecast,
                   r.warehouse_receipt, r.factory_receipt, r.active_warehouses, s.status
            FROM receipt_summaries r
            LEFT JOIN sync_task_status s
              ON r.trade_date = s.trade_date AND r.variety = s.variety AND s.data_type = 'receipt'
            WHERE r.trade_date = ?
            ORDER BY r.variety;
        """, (latest_d,))
        for r in cur.fetchall():
            print(
                f"  - {r['variety']} ({r['variety_name']}): 总量={r['total_receipt']:,}{r['unit']} "
                f"(仓:{r['warehouse_receipt']:,} / 厂:{r['factory_receipt']:,}), "
                f"增减={r['diff']:+,}, 预报={r['forecast']:,}, 活跃库={r['active_warehouses']}家 [{r['status']}]"
            )

    conn.close()

    if issues:
        print("\n⚠️ 体检发现以下异常:")
        for iss in issues:
            print(f"  - {iss}")
        print("=" * 72)
        return False

    print("\n✅ 全部历史数据质量校验通过，无 500 假值、无负预报、无加和失配！")
    print("=" * 72)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="8 大期货品种仓单日报自动化采集与聚合系统")
    parser.add_argument("--force", action="store_true", default=False, help="强制覆盖并刷新目标交易日快照")
    parser.add_argument("--past-days", type=int, default=None, help="指定同步最近 N 个交易日")
    parser.add_argument("--date", "--dates", dest="dates", nargs="*", default=None, help="指定同步的交易日 (YYYY-MM-DD)")
    parser.add_argument("--verify", action="store_true", help="全面体检数据质量")
    args = parser.parse_args()

    parsed_dates: List[str] = []
    if args.dates:
        for item in args.dates:
            parsed_dates.extend(re.findall(r"\d{4}-\d{2}-\d{2}", item))

    if args.verify:
        verify_data_quality()
    else:
        sync_all_receipts(
            force=args.force,
            past_days=args.past_days,
            target_dates=parsed_dates or None,
        )
        verify_data_quality()
