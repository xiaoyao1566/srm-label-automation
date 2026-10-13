# -*- coding: utf-8 -*-
"""筛选面板 —— 这个模块里藏着一条最贵的教训：**绝不按回车**。

真实站点上的行为：
    在高级查询面板的输入框里按回车 = 触发查询 + **把整个面板重置掉**。
    于是"填了 7 个条件、每填一个按回车"的结果是：条件一个都没生效，
    筛出来的料**全是错的** —— 而且页面看起来一切正常，日志也不会报错。

这就是为什么 :meth:`高级查询面板.填` 用 `Tab`（失焦）提交，而不是 `Enter`，
而且填完还要**回读核对**：读回来的值和填进去的不一样就当场报错，
绝不带着"我以为筛上了"继续往下走。
"""

from __future__ import annotations

import time

from .logkit import log, 记账
from .config import 配置


class 高级查询面板:
    def __init__(self, 页面, cfg: 配置, 账: 记账):
        self.页面 = 页面
        self.cfg = cfg
        self.账 = 账
        self.值记忆: dict = {}
        # 面板一旦被重置（回车、导航回来），下一颗料必须**整套重填**，
        # 不能只改一个字段就搜索 —— 否则会把全天所有标签都筛出来。
        self.需整套重填 = False

    # ---------------------------------------------------------------- 打开
    def _面板可见(self) -> bool:
        定位 = self.cfg.选择器.筛选项["itemCode"]
        el = self.页面.locator(定位)
        try:
            return el.count() > 0 and el.first.is_visible()
        except Exception:
            return False

    def 打开(self) -> None:
        if self._面板可见():
            return
        起 = time.time()
        try:
            self.页面.click(self.cfg.选择器.高级查询按钮, timeout=8000)
        except Exception as e:
            # 恢复阶梯：先原地点第二次，再按 Esc，最后才重新进列表页。
            # 重新进列表页会把面板清空 —— 所以必须置"整套重填"旗子，
            # 否则下一颗料只会设一个标签类型就去搜索，那会筛出全天数据。
            log("[面板] 第一次点不开【高级查询】（%s），换招" % type(e).__name__)
            if not self._重新进列表页开面板():
                raise RuntimeError("【高级查询】面板打不开")
        for _ in range(20):
            if self._面板可见():
                log("[面板] 已展开（%.1f 秒）" % (time.time() - 起))
                return
            time.sleep(0.2)
        raise RuntimeError("【高级查询】点开了但面板没出现")

    def _重新进列表页开面板(self) -> bool:
        log("[面板] 重新进列表页（预览残留之类的情况，原地怎么点都点不开）")
        try:
            self.页面.goto(self.cfg.站点.网址.rstrip("/") + self.cfg.站点.列表页路径,
                          wait_until="domcontentloaded")
            self.页面.wait_for_selector(self.cfg.选择器.总条数, timeout=8000)
            self.页面.click(self.cfg.选择器.高级查询按钮, timeout=8000)
            for _ in range(20):
                if self._面板可见():
                    self.值记忆.clear()
                    self.需整套重填 = True
                    log("[面板] 重新导航之后点开了；面板是空的，下一颗料会整套重填")
                    return True
                time.sleep(0.2)
        except Exception as e:
            log("[面板] 重新导航也没成功：%s" % e)
        return False

    # ---------------------------------------------------------------- 填值
    def 填(self, 字段: str, 值: str) -> None:
        """填一个文本框。**用 Tab 失焦提交，绝不按 Enter。**"""
        定位 = self.cfg.选择器.筛选项.get(字段)
        if not 定位:
            raise KeyError("配置里没有筛选字段「%s」" % 字段)
        if self.值记忆.get(字段) == str(值):
            # 值没变就一项都不重填 —— 同一批里第二颗料常常只差一个料号，
            # 全量重填一次 7 个字段要二十多秒，而这里 0.5 秒就够。
            return

        el = self.页面.locator(定位)
        el.click()
        el.fill(str(值))
        el.press("Tab")                      # ⛔ 这一行是 Enter 的话，面板就没了
        time.sleep(self.cfg.时序.面板失焦等待毫秒 / 1000.0)

        回读 = (el.input_value() or "").strip()
        if 回读 != str(值).strip():
            # 回读对不上：多半是面板被重置了（回车/导航）。
            # **必须当场报错**，因为带着错值继续 = 筛出一批错的料去打印。
            self.需整套重填 = True
            self.值记忆.clear()
            raise RuntimeError("填「%s」之后回读是「%s」，不是「%s」——面板可能被重置了"
                               % (字段, 回读, 值))
        self.值记忆[字段] = str(值)

    def 填下拉(self, 字段: str, 值: str) -> None:
        """下拉框（标签类型 / 标签状态）。下拉不需要 Tab —— 选完就生效。"""
        定位 = self.cfg.选择器.筛选项.get(字段)
        if not 定位:
            raise KeyError("配置里没有筛选字段「%s」" % 字段)
        if self.值记忆.get(字段) == str(值):
            return
        el = self.页面.locator(定位)
        el.select_option(value=str(值))
        回读 = (el.input_value() or "").strip()
        if 回读 != str(值):
            self.需整套重填 = True
            self.值记忆.clear()
            raise RuntimeError("选了「%s」，回读是「%s」" % (值, 回读))
        self.值记忆[字段] = str(值)

    def 填日期(self, 字段: str, 值: str) -> None:
        """日期只能点日历挑。

        真实站点上那些日期框是组件化的 DatePicker：往里 `fill()` 打字会被**弹回**原值，
        看起来"填成功了"，实际筛选条件里根本没有它。
        """
        if self.值记忆.get(字段) == str(值):
            return
        self.页面.click(self.cfg.选择器.日历按钮.format(字段=字段))
        某天 = self.页面.locator(self.cfg.选择器.日历某天.format(值=值))
        某天.first.wait_for(state="visible", timeout=5000)
        某天.first.click()
        输入框 = self.页面.locator(self.cfg.选择器.筛选项[字段])
        回读 = (输入框.input_value() or "").strip()
        if 回读 != str(值):
            self.需整套重填 = True
            self.值记忆.clear()
            raise RuntimeError("点日历选了「%s」，但框里是「%s」" % (值, 回读))
        self.值记忆[字段] = str(值)

    # ---------------------------------------------------------------- 搜索
    def 搜索(self) -> None:
        """所有条件填完，**只点一次**搜索，然后轮询等结果，不写死等待秒数。"""
        self.页面.click(self.cfg.选择器.搜索按钮)
        截止 = time.time() + self.cfg.时序.搜索后轮询最多秒
        while time.time() < 截止:
            try:
                txt = self.页面.locator(self.cfg.选择器.总条数).first.inner_text()
                if "共" in txt:
                    return
            except Exception:
                pass
            time.sleep(0.2)

    def 视为干净(self) -> None:
        """一颗料打完，把"需整套重填"的旗子拔掉。"""
        self.需整套重填 = False
