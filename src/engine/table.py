# -*- coding: utf-8 -*-
"""表格操作 —— 这里藏着两条"看着能跑、其实全错"的坑。

坑一：**表头全选框有两份 DOM**。
    vxe 这类表格会把固定列单独渲染一份，于是 `.vxe-header--column .vxe-cell--checkbox`
    能匹配到**两个**元素：一份在隐藏的固定列容器里，一份是真正的表头。
    隐藏的那份在 DOM 里排在**前面**，所以 `.first` 会取到它 ——
    点击要么超时，要么"点成功了但什么都没发生"。
    解法是把 `:visible` 写进选择器（配置里已经带了），而不是事后补救。

坑二：**每页固定 20 条，全选只作用于当前页**。
    "勾完就打印"只会出 20 张。必须逐页勾，而且要和页面上的「共 N 条」对账 ——
    翻到某页新勾到 0 个就以为勾完了，是最容易漏打的原因。
"""

from __future__ import annotations

import re
import time

from .logkit import log, 记账
from .config import 配置


def _取数(文本: str) -> int:
    m = re.search(r"(\d+)", 文本 or "")
    return int(m.group(1)) if m else 0


class 标签表格:
    def __init__(self, 页面, cfg: 配置, 账: 记账):
        self.页面 = 页面
        self.cfg = cfg
        self.账 = 账

    # ------------------------------------------------------------ 读
    def 本页行(self) -> list:
        """一次读回本页所有行（用 evaluate_all 而不是逐格 inner_text，
        43 行逐格读要几百次往返，一次读只要几十毫秒）。"""
        return self.页面.locator(self.cfg.选择器.行).evaluate_all(
            """els => els.map(tr => {
                 const t = i => tr.children[i] ? tr.children[i].innerText.trim() : "";
                 return { id: Number(tr.dataset.id), itemCode: t(1), poNum: t(2),
                          labelCode: t(3), labelType: t(4), productionDate: t(5),
                          shipmentDate: t(6), qty: t(7), creationDate: t(8) };
               })""")

    def 总条数(self) -> int:
        return _取数(self.页面.locator(self.cfg.选择器.总条数).first.inner_text())

    def 选中数(self) -> int:
        """页面上"跨页选择总行数"—— 这是脚本之外的独立计数，用来对账。"""
        return _取数(self.页面.locator(self.cfg.选择器.跨页选择数).first.inner_text())

    # ------------------------------------------------------------ 勾选
    def 全选本页(self) -> None:
        格 = self.页面.locator(self.cfg.选择器.表头全选框)
        if 格.count() == 0:
            raise RuntimeError("找不到表头全选框（选择器：%s）" % self.cfg.选择器.表头全选框)
        格.first.click()

    def 下一页(self) -> bool:
        前 = self.本页行()
        self.页面.click(self.cfg.选择器.下一页按钮)
        # 等到行真的换了（按 data-id 判断，而不是"等 1 秒"）
        截止 = time.time() + 4
        while time.time() < 截止:
            后 = self.本页行()
            if 后 and (not 前 or 后[0]["id"] != 前[0]["id"]):
                return True
            time.sleep(0.15)
        return False

    def 跨页全选(self) -> int:
        """逐页勾选，直到勾满「共 N 条」。

        停止条件刻意写得保守：**连续 2 页没有新增**才停。
        只按"这一页新勾到 0 个"就停，会在页面还在渲染时提前收手 → 少打标签。
        最后一定要和「共 N 条」对账，对不上就报警。
        """
        应勾 = self.总条数()
        if 应勾 == 0:
            return 0
        页 = 1
        连续无新增 = 0
        while True:
            if 页 > 1 and not self.下一页():
                log("[表格] 翻页在第 %d 页停住了" % 页)
                break
            self.全选本页()
            现 = self.选中数()
            if 现 <= (页 - 1) * self.cfg.口径.每页条数 and 页 > 1:
                连续无新增 += 1
            else:
                连续无新增 = 0
            log("[表格] 第 %d 页勾完，累计选中 %d" % (页, 现))
            if 现 >= 应勾 or 连续无新增 >= 2:
                break
            页 += 1
            if 页 > 200:                      # 防死循环的兜底
                self.账.记错("表格", "翻页超过 200 页，放弃", "")
                break

        实勾 = self.选中数()
        if 实勾 != 应勾:
            self.账.记错("表格", "勾选数 %d 与页面「共 %d 条」对不上" % (实勾, 应勾), "")
        else:
            log("[表格] 勾选数对账通过：%d = 页面共 %d 条" % (实勾, 应勾))
        return 实勾
