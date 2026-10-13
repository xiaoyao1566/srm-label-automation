# -*- coding: utf-8 -*-
"""提交打印 + 判"到底出没出纸"。

这一段是整个项目最反直觉的地方，值钱的也在这里：

  · 点【确认打印】**不会出纸**。它只是把任务提交到页面；
    真正让纸出来的是**另一个窗口**里的【打印】按钮
    （真机上那个按钮在浏览器打印预览 UI 里，Playwright 的 DOM 根本点不到，
      只能靠 Windows UI Automation 遍历控件树去点）。
  · 所以判"成功"的唯一可靠办法，是**去问操作系统**：打印队列 + 打印服务事件。
    脚本自己说"已点打印"不算数 —— 实测过点了空，日志写着成功、一张纸都没出去。
  · 自动重试之前**必须先复查有没有晚到的证据**。纸已经出去了、证据还没落地，
    这时重试 = 整叠标签打两份，不可逆。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .logkit import log, 记账
from .config import 配置
from .table import 标签表格
from .evidence import 等出纸, 等队列排空, 重试前有晚到的证据, 出纸结论


@dataclass
class 打印结果:
    提交: bool
    说明: str
    页数: int = 0
    只用队列证据: bool = False


def _等确认弹窗(页面, cfg: 配置, 最多秒: float = 12.0):
    """等【确认打印】弹窗出现。

    ⚠️ 不要写死 `wait_for_timeout(3000)`。实测这个弹窗 0.04~0.12 秒就出来了，
       死等 3 秒是纯浪费，而且一旦某次慢了 3 秒就会漏判。改成轮询。
    """
    截止 = time.time() + 最多秒
    起 = time.time()
    while time.time() < 截止:
        el = 页面.locator(cfg.选择器.确认打印)
        try:
            if el.count() > 0 and el.first.is_visible():
                log("[确认弹窗] %.2f 秒后出现" % (time.time() - 起))
                return el.first
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("点了【%s】之后没等到【确认打印】弹窗" % cfg.选择器.打印标签按钮)


def 打印并验证(页面, cfg: 配置, 表格: 标签表格, 证据源, 打印机: str,
                账: 记账) -> 打印结果:
    """把当前勾选的内容打出去，并拿到**独立证据**证明它真的出了纸。"""
    水位线 = 证据源.水位线()

    # 先等队列排空。排空成功，才敢用"打印服务事件"当判据 ——
    # 因为事件是**整个打印服务**的，不带料号：上一批的 307 会在这一批之后才落地，
    # 拿它当判据就是"假的成功"（内箱几十张还在打的时候去查外箱，必然误判）。
    排空 = 等队列排空(证据源, 打印机, cfg.时序.队列排空最多秒)
    只认队列 = not 排空
    if 只认队列:
        log("[打印] 队列没能排空（等满 %d 秒）—— 这次只认队列证据，不认事件"
            % cfg.时序.队列排空最多秒)
    else:
        log("[打印] 队列已排空，事件证据可以用")

    页面.click(cfg.选择器.打印标签按钮)
    弹窗 = _等确认弹窗(页面, cfg)

    # 点【确认打印】：它会开一个新窗口（打印预览）。必须接住 popup，
    # 因为**真正出纸的按钮在那个窗口里**，原页面上根本没有。
    try:
        with 页面.expect_popup(timeout=15000) as 弹:
            弹窗.click()
        预览 = 弹.value
        预览.wait_for_load_state("domcontentloaded")
    except Exception as e:
        return 打印结果(False, "点【确认打印】之后没等到打印预览窗口（%s）" % type(e).__name__)

    def 点预览里的打印():
        预览.click("button:has-text('打印')", timeout=8000)

    try:
        点预览里的打印()
    except Exception as e:
        return 打印结果(False, "打印预览里的【打印】点不到：%s: %s" % (type(e).__name__, e))

    log("[打印] 已点打印预览里的【打印】，开始等出纸证据")
    结论 = 等出纸(证据源, 水位线, 打印机, 最多秒=45, 只认队列=只认队列)
    if 结论.提交:
        log("[打印] 出纸证据 OK：%s" % 结论.说明)
        return 打印结果(True, 结论.说明, 结论.页数, 结论.用队列)

    # ---- 零证据：可以重试，但**每次重试前先复查有没有晚到的证据** ----
    for 第 in range(1, cfg.时序.出纸重试次数 + 1):
        time.sleep(cfg.时序.出纸重试间隔秒)
        晚到 = 重试前有晚到的证据(证据源, 水位线, 打印机)
        if 晚到 is not None:
            log("[打印] 复查发现晚到证据：%s —— 判为已出纸，**不重试**" % 晚到.说明)
            return 打印结果(True, 晚到.说明, 晚到.页数, 晚到.用队列)
        log("[打印] 确认没有证据，第 %d/%d 次重试" % (第, cfg.时序.出纸重试次数))
        try:
            点预览里的打印()
        except Exception as e:
            log("[打印] 重试时点不到【打印】：%s" % e)
            break
        结论 = 等出纸(证据源, 水位线, 打印机, 最多秒=30, 只认队列=只认队列)
        if 结论.提交:
            return 打印结果(True, 结论.说明, 结论.页数, 结论.用队列)

    return 打印结果(False, "重试用完，仍然没有任何出纸证据：%s" % 结论.说明)
