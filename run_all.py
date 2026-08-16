#!/usr/env python3
# -*- coding: utf-8 -*-
"""
scheduled-tasks 统一执行入口
============================
本目录即「定时任务集合」。约定：
  - 每个任务是一个独立的 *.py 脚本；用 `python3 脚本.py`（无参数）运行即执行其定时逻辑。
  - 新增任务：往本目录丢一个 .py 即可，无需改动本文件，也无需新增 launchd plist。
  - 本文件（run_all.py）与以下划线 _ 开头的文件会被自动跳过。
  - 单个任务失败 / 超时不影响其他任务。

执行频率由 launchd plist（com.hejinlin.scheduled-tasks.plist）决定：当前每天 08:00 跑一次
（StartCalendarInterval）。本脚本被调用时就执行全部任务，不做时间判断。
改时间 = 改 plist 里的 StartCalendarInterval（或叫我改）。
"""
import glob
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TASK_TIMEOUT = 300  # 单任务超时（秒）


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def main() -> None:
    log("==== 开始执行定时任务集合 ====")
    scripts = sorted(glob.glob(os.path.join(HERE, "*.py")))
    ran = 0
    for path in scripts:
        name = os.path.basename(path)
        if name == os.path.basename(__file__) or name.startswith("_"):
            continue
        ran += 1
        log(f"▶ 运行任务: {name}")
        try:
            r = subprocess.run([PY, path], cwd=HERE, timeout=TASK_TIMEOUT)
            log(f"✔ {name} 完成 (exit={r.returncode})")
        except subprocess.TimeoutExpired:
            log(f"✖ {name} 超时(>{TASK_TIMEOUT}s)，已跳过")
        except Exception as e:  # noqa: BLE001
            log(f"✖ {name} 异常: {e}")
    log(f"==== 本次共运行 {ran} 个任务 ====")


if __name__ == "__main__":
    main()
