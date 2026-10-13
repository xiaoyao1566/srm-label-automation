# -*- coding: utf-8 -*-
"""靶站的差分验证：**错的写法必须失败，对的写法必须通过**。

为什么非要这样验：
    一个"复刻了技术难点"的靶站，如果错的写法也能过，那它什么都没复刻，
    只是长得像而已。所以每一个坑都要两边都跑一遍：
      · 错法：必须给出**可观察的失败**（抛异常、或勾选数/筛选结果明显不对）；
      · 对法：必须成功。
    判据不是"我觉得像"，是这两条断言同时成立。

用法：
    python tools/verify_pitfalls.py [--有头]
退出码：0 = 五个坑全部达标；1 = 有坑没复刻到位。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# —— 控制台编码护栏 ——
# 中文 Windows 的 cmd / PowerShell 默认编码是 GBK，打印 ✗ / · 这类符号会直接
# UnicodeEncodeError 崩掉（不是逻辑错，是编码错）。这里放宽标准输出的错误策略：
# 编不出来的符号退化成 ?，绝不因为一个符号把整个验证打断。
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(errors="replace")
    except Exception:
        pass

本文件目录 = os.path.dirname(os.path.abspath(__file__))
仓库根 = os.path.dirname(本文件目录)
sys.path.insert(0, os.path.join(仓库根, "src"))
sys.path.insert(0, os.path.join(仓库根, "demo"))

from engine import pages                                    # noqa: E402
from engine.config import 载入                               # noqa: E402
from engine.logkit import 记账                              # noqa: E402
from engine.panel import 高级查询面板                        # noqa: E402
from engine.table import 标签表格                            # noqa: E402
from run_demo import 靶站活着, 起靶站, 读靶站验证码           # noqa: E402

网址 = "http://127.0.0.1:8771"
结果 = []


def 记(名, 通过, 说明):
    结果.append((名, 通过, 说明))
    print("  [%s] %s\n         %s" % ("PASS" if 通过 else "FAIL", 名, 说明))


def 回列表(页面, cfg):
    页面.goto(网址 + cfg.站点.列表页路径, wait_until="domcontentloaded")
    页面.wait_for_selector(cfg.选择器.总条数, timeout=8000)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--有头", action="store_true")
    a = p.parse_args()

    os.environ["SITE_URL"] = 网址
    cfg = 载入()
    # 靶站不校验口令内容，所以这里给的是**演示占位**，不是任何真实凭证。
    # 用 os.environ 取：既让换账号不用改代码，也避免仓库里出现"口令 = 字面量"这种形状
    # —— 那形状正是脱敏闸门要抓的东西，不该为了图省事给它开先例。
    cfg.站点.账号 = os.environ.get("SITE_ACCOUNT", "演示账号")
    cfg.站点.口令 = os.environ.get("SITE_PASSWORD", "演示口令")

    码流 = os.path.join(本文件目录, "..", "demo", "verify_spool.jsonl")
    if os.path.isfile(码流):
        os.remove(码流)

    靶站 = None
    管理器 = 浏览器 = 上下文 = None
    if not 靶站活着(网址):
        靶站 = 起靶站(网址, 码流)

    try:
        from engine import evidence
        源 = evidence.模拟打印服务(码流, 打印机过滤="MockPrinter")
        管理器, 浏览器, 上下文, 页面 = pages.起浏览器(cfg, 有头=a.有头)
        pages.登录(页面, cfg, 读验证码=读靶站验证码)
        pages.进列表页(页面, cfg)
        账 = 记账()
        面板 = 高级查询面板(页面, cfg, 账)
        表格 = 标签表格(页面, cfg, 账)

        print("\n=== 坑①　表头全选框有两份 DOM ===")
        回列表(页面, cfg)
        错法结果 = "没抛异常"
        try:
            # 错的写法：不带 :visible，直接取第一个 —— 取到的是隐藏的那一份
            页面.locator(".vxe-header--column .vxe-cell--checkbox").first.click(timeout=3000)
        except Exception as e:
            错法结果 = type(e).__name__
        勾 = 表格.选中数()
        记("坑① 错法（不带 :visible 取 .first）必须失败",
           错法结果 != "没抛异常" or 勾 == 0,
           "错法结果：%s，勾选数 %d（应为 0）" % (错法结果, 勾))

        回列表(页面, cfg)
        表格 = 标签表格(页面, cfg, 账)
        表格.全选本页()
        勾 = 表格.选中数()
        记("坑① 对法（带 :visible）必须成功", 勾 == 20, "勾选数 %d（应为 20）" % 勾)

        print("\n=== 坑②　筛选面板里按回车会毁掉面板 ===")
        回列表(页面, cfg)
        面板 = 高级查询面板(页面, cfg, 账)
        面板.打开()
        el = 页面.locator(cfg.选择器.筛选项["itemCode"])
        el.click(); el.fill("DM-1001"); el.press("Enter")
        页面.click(cfg.选择器.搜索按钮)
        time.sleep(0.6)
        错后 = 表格.总条数()
        按钮值 = (页面.locator(cfg.选择器.筛选项["itemCode"]).input_value() or "")
        记("坑② 错法（回车提交）必须毁掉条件",
           错后 == 42 and 按钮值 == "",
           "回车后：筛选框值=%r，结果 %d 条（应为空值 + 全部 42 条）" % (按钮值, 错后))

        回列表(页面, cfg)
        面板 = 高级查询面板(页面, cfg, 账)
        表格 = 标签表格(页面, cfg, 账)
        面板.打开()
        面板.填("itemCode", "DM-1001")
        面板.搜索()
        time.sleep(0.4)
        对后 = 表格.总条数()
        记("坑② 对法（Tab 失焦提交）必须筛对", 对后 == 6,
           "Tab 提交后 %d 条（DM-1001 有 3 个订单 × 内/外箱 = 6 条）" % 对后)

        print("\n=== 坑③　每页 20 条，勾选必须跨页 ===")
        回列表(页面, cfg)
        表格 = 标签表格(页面, cfg, 账)
        应勾 = 表格.总条数()
        表格.全选本页()
        只勾本页 = 表格.选中数()
        记("坑③ 错法（只勾当前页）必须少勾",
           只勾本页 == 20 and 只勾本页 < 应勾,
           "只勾当前页得 %d，而页面共 %d 条 —— 直接提交会少打 %d 行"
           % (只勾本页, 应勾, 应勾 - 只勾本页))

        回列表(页面, cfg)
        表格 = 标签表格(页面, cfg, 账)
        实勾 = 表格.跨页全选()
        记("坑③ 对法（逐页勾 + 对账）必须勾满", 实勾 == 应勾,
           "勾到 %d = 共 %d 条" % (实勾, 应勾))

        print("\n=== 坑④　点【确认打印】不会出纸，真按钮在另一个窗口 ===")
        # 先随便勾一行，好让【打印标签】有东西可提交
        表格.全选本页()
        水位线 = 源.水位线()
        页面.click(cfg.选择器.打印标签按钮)
        页面.wait_for_selector(cfg.选择器.确认打印, timeout=8000)
        with 页面.expect_popup(timeout=15000) as 弹:
            页面.locator(cfg.选择器.确认打印).first.click()
        预览 = 弹.value
        预览.wait_for_load_state("domcontentloaded")

        过了 = 0
        for _ in range(20):                     # 最多等 2 秒
            if 源.新记录(水位线):
                过了 = len(源.新记录(水位线))
                break
            time.sleep(0.1)
        记("坑④ 错法（只点【确认打印】）必须不出纸", 过了 == 0,
           "点完【确认打印】等了 2 秒，出纸记录 %d 条（应为 0）" % 过了)

        预览.click("button:has-text('打印')")
        过了 = 0
        for _ in range(30):
            if 源.新记录(水位线):
                过了 = len(源.新记录(水位线))
                break
            time.sleep(0.1)
        记("坑④ 对法（再去预览窗口点【打印】）必须出纸", 过了 > 0,
           "点完预览里的【打印】出纸记录 %d 条" % 过了)
        预览.close()

        print("\n=== 坑⑤　日期框打不进字，只能点日历 ===")
        回列表(页面, cfg)
        面板 = 高级查询面板(页面, cfg, 账)
        面板.打开()
        错法 = "没抛异常"
        try:
            页面.locator(cfg.选择器.筛选项["prodFrom"]).fill("2026-10-13", timeout=3000)
        except Exception as e:
            错法 = type(e).__name__
        值1 = 页面.locator(cfg.选择器.筛选项["prodFrom"]).input_value() or ""
        记("坑⑤ 错法（往日期框打字）必须失败", 错法 != "没抛异常" or 值1 == "",
           "错法结果：%s，框里值=%r" % (错法, 值1))

        面板.填日期("prodFrom", "2026-10-13")
        值2 = 页面.locator(cfg.选择器.筛选项["prodFrom"]).input_value() or ""
        记("坑⑤ 对法（点日历挑日期）必须成功", 值2 == "2026-10-13",
           "点日历后框里值=%r" % 值2)

    finally:
        if 管理器:
            pages.关浏览器(管理器, 浏览器, 上下文)
        if 靶站:
            靶站.terminate()

    失败 = [x for x in 结果 if not x[1]]
    print("\n" + "=" * 64)
    print("差分验证：%d 项通过，%d 项失败" % (len(结果) - len(失败), len(失败)))
    for 名, _, 说明 in 失败:
        print("  ✗ %s" % 名)
    print("=" * 64)
    return 1 if 失败 else 0


if __name__ == "__main__":
    sys.exit(main())
