# -*- coding: utf-8 -*-
"""端到端演示：在靶站上把"筛选 → 跨页勾选 → 提交打印 → 验证真的出纸"跑一遍。

它不是"跑个 hello world"，它要证明三件事：
  1. 靶站复刻的那些坑是真的 —— 用**错的写法**会失败，用**对的写法**能过；
  2. 打印这件事**不能听脚本自己说**，必须拿到独立证据（这里读靶站写的出纸记录）；
  3. 整条链路在**任何一台装了 Python 的电脑上**都能跑（不依赖真门户、不依赖打印机）。

用法：
    python demo/run_demo.py                # 无头模式
    python demo/run_demo.py --有头          # 看着它跑（录屏用）
    python demo/run_demo.py --截图          # 顺手把关键界面存成 PNG（README 里的图就是这么来的）
    python demo/run_demo.py --网址 http://127.0.0.1:8770
退出码：0 = 全流程通过；1 = 有步骤没通过。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# —— 控制台编码护栏 ——
# 中文 Windows 的 cmd / PowerShell 默认编码是 GBK，打印 ✓ / ✔ / ⛔ 这类符号会直接
# UnicodeEncodeError 崩掉（不是逻辑错，是编码错）。这里放宽标准输出的错误策略：
# 编不出来的符号退化成 ?，绝不因为一个符号把整个演示打断。
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(errors="replace")
    except Exception:
        pass

本文件目录 = os.path.dirname(os.path.abspath(__file__))
仓库根 = os.path.dirname(本文件目录)
sys.path.insert(0, os.path.join(仓库根, "src"))

from engine import evidence, pages, printing                       # noqa: E402
from engine.config import 载入                                      # noqa: E402
from engine.logkit import 记账, log, 开日志, 关日志                  # noqa: E402
from engine.panel import 高级查询面板                                # noqa: E402
from engine.table import 标签表格                                    # noqa: E402

演示料号 = "DM-1001"
演示订单 = "PO-DEMO-01"
演示日期 = "2026-10-13"
靶站打印机 = "MockPrinter"


# --------------------------------------------------------------------------
# 靶站：没起就自己起（子进程的输出丢到 DEVNULL，不占管道）
# --------------------------------------------------------------------------
def 靶站活着(网址: str) -> bool:
    try:
        with urllib.request.urlopen(网址.rstrip("/") + "/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def 起靶站(网址: str, 码流文件: str):
    端口 = 网址.rsplit(":", 1)[-1].strip("/")
    进程 = subprocess.Popen(
        [sys.executable, "-X", "utf8", os.path.join(仓库根, "mock_srm", "server.py"),
         "--端口", 端口, "--码流文件", 码流文件],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=仓库根)
    for _ in range(40):
        if 靶站活着(网址):
            log("[靶站] 已自动启动（PID %d）" % 进程.pid)
            return 进程
        time.sleep(0.25)
    进程.terminate()
    raise RuntimeError("靶站起不来")


# --------------------------------------------------------------------------
# 靶站专用：验证码答案就在 DOM 里
# --------------------------------------------------------------------------
def 读靶站验证码(页面) -> str:
    """真实站点这里必须是「OCR 识别 + 识别不出来就弹窗让真人输」。

    靶站把它简化成读一个属性 —— 目的是让"clone 下来就能跑"成立。
    换站点时**只换这个回调**，登录流程一个字都不用改。
    """
    try:
        return 页面.locator(".validate-code").first.get_attribute("data-answer") or ""
    except Exception:
        return ""


# --------------------------------------------------------------------------
def 提示(文本: str) -> None:
    log("")
    log("─" * 60)
    log(文本)
    log("─" * 60)


def 截图器(开启: bool, 目录: str):
    """返回一个 ``(页面, 名字) -> None`` 的截图器；没开启就返回 None。

    为什么做成"可选回调"：截图只服务于 README 出图，跟演示逻辑无关。
    没开 ``--截图`` 时这条路径整个不存在 —— 演示行为与不传参数时完全一致。
    """
    if not 开启:
        return None
    os.makedirs(目录, exist_ok=True)

    def 拍(页面, 名字: str) -> None:
        try:
            页面.screenshot(path=os.path.join(目录, 名字 + ".png"))
            log("[截图] 已存 %s.png" % 名字)
        except Exception as e:
            log("[截图] %s 失败（不影响演示）：%s" % (名字, e))

    return 拍


def main() -> int:
    p = argparse.ArgumentParser(description="靶站端到端演示")
    p.add_argument("--网址", default="http://127.0.0.1:8770")
    p.add_argument("--有头", action="store_true", help="显示浏览器窗口（录屏用）")
    p.add_argument("--慢动作", type=int, default=0, help="每步放慢多少毫秒")
    p.add_argument("--码流文件", default=os.path.join(本文件目录, "spool.jsonl"))
    p.add_argument("--不复用靶站", action="store_true", help="不在本脚本里自动起靶站")
    p.add_argument("--截图", action="store_true", help="把关键界面存成 PNG（README 出图用）")
    p.add_argument("--图片目录", default=os.path.join(仓库根, "docs", "images"))
    a = p.parse_args()
    截图 = 截图器(a.截图, a.图片目录)

    os.environ["SITE_URL"] = a.网址
    cfg = 载入()
    cfg.站点.账号 = os.environ.get("SITE_ACCOUNT", "演示账号")
    cfg.站点.口令 = os.environ.get("SITE_PASSWORD", "演示口令")
    # 每次演示从干净状态开始，这样出纸证据是**本次**产生的，不会混进上次的
    if os.path.isfile(a.码流文件):
        os.remove(a.码流文件)

    开日志(os.path.join(本文件目录, "演示运行日志.txt"))
    log("靶站端到端演示开始；配置摘要：%s" % json.dumps(cfg.摘要(), ensure_ascii=False))

    账 = 记账()
    靶站进程 = None
    管理器 = 浏览器 = 上下文 = None
    通过 = []
    计时 = {}

    try:
        if not 靶站活着(a.网址):
            if a.不复用靶站:
                log("[靶站] 没在跑，而且指定了 --不复用靶站")
                return 1
            靶站进程 = 起靶站(a.网址, a.码流文件)

        证据源 = evidence.模拟打印服务(a.码流文件, 打印机过滤=靶站打印机)

        # ---------------- 起浏览器 + 登录 ----------------
        t0 = time.time()
        管理器, 浏览器, 上下文, 页面 = pages.起浏览器(cfg, 有头=a.有头, 慢动作=a.慢动作)
        if 截图:
            页面.goto(a.网址.rstrip("/") + "/login", wait_until="domcontentloaded")
            截图(页面, "01-login")
        pages.登录(页面, cfg, 读验证码=读靶站验证码)
        pages.进列表页(页面, cfg)
        计时["打开系统+登录+进列表页"] = time.time() - t0
        通过.append("登录并进入列表页")

        面板 = 高级查询面板(页面, cfg, 账)
        表格 = 标签表格(页面, cfg, 账)
        if 截图:
            截图(页面, "02-list")

        # ---------------- 第一轮：单个料号（顺带验"值记忆"）----------------
        提示("第一轮：按料号+订单+生产日期筛选（日期必须点日历，不能打字）")
        t0 = time.time()
        面板.打开()
        面板.填("itemCode", 演示料号)
        面板.填("poNum", 演示订单)
        面板.填日期("prodFrom", 演示日期)
        面板.填日期("prodTo", 演示日期)
        面板.填下拉("labelType", "内箱标签")
        面板.搜索()
        计时["第一轮填筛选（冷启动，6 个字段）"] = time.time() - t0

        行 = 表格.本页行()
        log("[演示] 筛出 %d 行：%s" % (len(行), [r["labelCode"] for r in 行]))
        assert len(行) >= 1, "筛出来一行都没有，筛条件没生效"
        通过.append("筛选条件生效（%d 行）" % len(行))

        # 值记忆：第二个料号只差一个字段，其余不该重填
        t0 = time.time()
        面板.填("itemCode", "DM-1002")
        面板.搜索()
        计时["第二轮只改料号（值记忆生效）"] = time.time() - t0
        if 计时["第二轮只改料号（值记忆生效）"] < 计时["第一轮填筛选（冷启动，6 个字段）"]:
            通过.append("值记忆生效（改一个字段比冷启动快）")

        # ---------------- 第二轮：全量跨页勾选 ----------------
        提示("第二轮：清掉条件，跨 3 页全选（只勾当前页会少打）")
        t0 = time.time()
        页面.goto(a.网址.rstrip("/") + cfg.站点.列表页路径, wait_until="domcontentloaded")
        页面.wait_for_selector(cfg.选择器.总条数, timeout=8000)
        面板 = 高级查询面板(页面, cfg, 账)      # 导航之后面板是全新的
        表格 = 标签表格(页面, cfg, 账)
        应勾 = 表格.总条数()
        实勾 = 表格.跨页全选()
        计时["跨页全选（%d 行 / %d 页）" % (应勾, (应勾 + 19) // 20)] = time.time() - t0
        assert 实勾 == 应勾, "勾选数 %d 与共 %d 条不一致" % (实勾, 应勾)
        if 截图:
            截图(页面, "03-cross-page-42")
        通过.append("跨页全选对账通过（%d = %d）" % (实勾, 应勾))

        # ---------------- 提交打印 + 验证出纸 ----------------
        提示("第三轮：提交打印 —— 注意【确认打印】本身不出纸，真按钮在另一个窗口里")
        t0 = time.time()
        结果 = printing.打印并验证(页面, cfg, 表格, 证据源, 靶站打印机, 账, 拍照=截图)
        计时["提交打印并等到出纸证据"] = time.time() - t0
        log("[演示] 打印结果：提交=%s，%s" % (结果.提交, 结果.说明))
        assert 结果.提交, "没有拿到出纸证据：%s" % 结果.说明
        通过.append("拿到独立出纸证据")

        # 证据不是脚本说的，是**文件里真的多了一条记录**
        记录 = 证据源.新记录(0)
        log("[演示] 出纸记录 %d 条：%s" % (len(记录), [str(r) for r in 记录]))
        assert 记录, "出纸记录文件是空的 —— 说明'证据'不是真证据"
        通过.append("出纸记录文件里有 %d 条真实记录" % len(记录))

        # ---------------- 汇总 ----------------
        提示("耗时")
        for k, v in 计时.items():
            log("  %-42s %6.1f 秒" % (k, v))

        提示("通过的检查点")
        for x in 通过:
            log("  ✔ %s" % x)

        log(账.汇总())
        return 0 if not 账.出错 else 1

    except Exception as e:
        import traceback
        log("[演示] 失败：%s: %s" % (type(e).__name__, e))
        log(traceback.format_exc())
        log(账.汇总())
        return 1
    finally:
        if 管理器:
            pages.关浏览器(管理器, 浏览器, 上下文)
        if 靶站进程:
            靶站进程.terminate()
            log("[靶站] 已停止")
        关日志()


if __name__ == "__main__":
    sys.exit(main())
