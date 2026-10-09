#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大宗商品与期货系统 (DCE/CZCE/SHFE) 全自动一体化同步与调度总控脚本
------------------------------------------------------------------
执行时序：
1. 【龙虎榜】：执行 modules/sync_coking_coal_positions.py (多空席位持仓、净头寸计算及 SQLite 入库)
2. 【仓单日报】：执行 modules/sync_coking_coal_receipts.py (8 大品种指定交割仓库真实仓单流水及全景图表)

使用示例：
  python3 run_daily_sync.py                      # 默认全量增量补全并同步
  python3 run_daily_sync.py --verify             # 龙虎榜与仓单全链路数据质量深度体检
  python3 run_daily_sync.py --force              # 强制重新抓取并覆盖全部日度数据
  python3 run_daily_sync.py --past-days 10       # 同步最近 10 个交易日
  python3 run_daily_sync.py --date 2026-09-30    # 指定同步特定交易日
"""

import os
import sys
import time
import subprocess
import argparse

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(CURRENT_DIR, "modules")
POSITIONS_SCRIPT = os.path.join(MODULES_DIR, "sync_coking_coal_positions.py")
RECEIPTS_SCRIPT = os.path.join(MODULES_DIR, "sync_coking_coal_receipts.py")


def run_step(step_num: int, total_steps: int, title: str, script_path: str, extra_args: list) -> bool:
    """运行流水线中的单个子脚本并实时回传标准输出"""
    print(f"\n{'=' * 72}")
    print(f"[{step_num}/{total_steps}] 🚀 正在启动: {title}")
    print(f"    脚本路径: {os.path.relpath(script_path, CURRENT_DIR)}")
    if extra_args:
        print(f"    附加参数: {' '.join(extra_args)}")
    print(f"{'=' * 72}\n")

    cmd = [sys.executable, script_path] + extra_args
    start_time = time.time()

    # 保持子进程的工作目录为当前项目根目录，以确保输出一致性
    process = subprocess.Popen(
        cmd,
        cwd=CURRENT_DIR,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=dict(os.environ, PYTHONUNBUFFERED="1")
    )
    process.wait()

    elapsed = time.time() - start_time
    if process.returncode != 0:
        print(f"\n❌ [{title}] 执行失败，退出码: {process.returncode} (耗时: {elapsed:.2f}s)")
        return False

    print(f"\n✅ [{title}] 执行成功！(耗时: {elapsed:.2f}s)")
    return True


def run_pipeline(extra_args: list = None) -> int:
    """按序执行 1.龙虎榜 -> 2.仓单 整体同步流程"""
    args = extra_args or []
    total_start = time.time()

    print("\n" + "#" * 72)
    print("  📊 大宗商品与期货系统 (DCE/CZCE/SHFE) 一键全自动同步流水线")
    print("  时序规范：[1] 席位持仓龙虎榜  ──>  [2] 指定交割仓库仓单日报")
    print("#" * 72)

    # 1. 检查模块依赖文件完整性
    for path, name in [(POSITIONS_SCRIPT, "龙虎榜采集脚本"), (RECEIPTS_SCRIPT, "仓单日报采集脚本")]:
        if not os.path.exists(path):
            print(f"❌ 关键脚本不存在: {path} ({name})")
            return 1

    # 2. 步骤一：执行持仓龙虎榜
    success_positions = run_step(
        step_num=1,
        total_steps=2,
        title="持仓龙虎榜与席位博弈全自动采集",
        script_path=POSITIONS_SCRIPT,
        extra_args=args
    )
    if not success_positions:
        print("\n⚠️ 龙虎榜同步异常中断，跳过后续步骤。")
        return 1

    # 3. 步骤二：执行仓单日报
    success_receipts = run_step(
        step_num=2,
        total_steps=2,
        title="指定交割仓库仓单日报全自动采集",
        script_path=RECEIPTS_SCRIPT,
        extra_args=args
    )
    if not success_receipts:
        print("\n⚠️ 仓单日报同步异常中断。")
        return 1

    total_elapsed = time.time() - total_start
    print("\n" + "=" * 72)
    print(f"🎉 全部采集与聚合任务顺利完成！总耗时: {total_elapsed:.2f}s")
    print("📁 核心持久化底座: data/coking_coal.db (SQLite)")
    print("📊 龙虎榜数据源:   data/coking_coal_positions.json / .data.js")
    print("🏭 仓单全景数据源: data/coking_coal_receipts.json / .data.js")
    print("🖥️  前端大屏看板:   coking_coal_dragon_tiger.html / coking_coal_receipt.html")
    print("=" * 72 + "\n")

    return 0


def main():
    # 转发所有传入命令行参数
    forward_args = sys.argv[1:]
    exit_code = run_pipeline(forward_args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
