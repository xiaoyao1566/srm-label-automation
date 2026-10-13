# -*- coding: utf-8 -*-
"""出纸证据 —— **判据必须独立于被测对象**。

这个模块回答的问题是：「这一批标签，到底出纸了没有？」

为什么不能听脚本自己说：
    脚本报"打印按钮已点: True"是**它自己的说法**。实测过点了个空 ——
    日志上写着"已点打印"，打印服务事件里一条记录都没有，一张纸都没出去。
    更早还出过反过来的事：控件点击抛了异常、脚本判"失败"，
    但纸其实已经出去了 —— 于是重跑，整叠标签打了两份。

所以这里只认两种**操作系统给的说法**：

  ① 打印队列（``win32print.EnumJobs``）—— 任务**进队**的那一瞬间就能看到，最快（约 0ms）；
  ② 打印服务事件（``Microsoft-Windows-PrintService/Operational`` 的 307 事件）——
     任务**真的打到打印机**上才记一笔，最权威（约 2ms 读一次）。

两者一起用，是因为它们**覆盖的时间点不同**：
  队列能证明"提交成功"，但它**排空之后就没有痕迹了**；
  307 事件能证明"真的出纸了"，但它**比提交晚**（内箱几十张要打好几分钟，
  它的 307 要等到那之后才落地）。

⚠️ 一个必须记住的坑：**307 事件是"整个打印服务"的事件，不带料号**。
    所以"水位线之上有新的 307"只能证明**有东西出纸了**，不能证明是**这一批**。
    内箱还在打的时候去查外箱，必然能查到内箱的 307 —— 判成"外箱也成功了"。
    正确做法：**先用队列排空 + 水位线把范围框住**，排空不了就只认队列、不认事件。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Protocol

日志通道 = "Microsoft-Windows-PrintService/Operational"
事件号_打印 = 307


@dataclass
class 出纸记录:
    记录号: int          # 事件 EventRecordID / 队列 JobId —— 单调递增，用来当水位线
    打印机: str
    页数: int
    时刻: datetime

    def __str__(self) -> str:
        return "%s %s %d页 @%s" % (self.记录号, self.打印机 or "(未知打印机)", self.页数,
                                   self.时刻.strftime("%m-%d %H:%M:%S"))


class 证据源(Protocol):
    """证据源的统一接口。

    ⚠️ 这个 Protocol 是刻意存在的：**真实实现和模拟实现走的是同一条代码路径**。
       没有打印机的地方（CI、别人的电脑）用模拟源跑，
       引擎里"判出纸"那几行代码**一个字都不用改** ——
       否则"能跑"就变成了"只在我这台机器上能跑"。
    """

    def 水位线(self) -> int: ...
    def 新记录(self, 水位线: int) -> List[出纸记录]: ...
    def 队列(self) -> List[出纸记录]: ...


# ==========================================================================
# 一、真实实现
# ==========================================================================
class Windows打印服务:
    """读 307 事件。**只读**，不碰打印机、不碰队列。"""

    def __init__(self, 打印机过滤: Optional[str] = None) -> None:
        self.打印机过滤 = 打印机过滤
        self._缓存: Optional[List[出纸记录]] = None

    # -- 读 ----------------------------------------------------------------
    def _全部记录(self, 最多: int = 400) -> List[出纸记录]:
        """倒序拉最近的 307 事件。倒序是为了"最近的先拿到"，通常只看几条就够了。"""
        if self._缓存 is not None:
            return self._缓存
        import win32evtlog

        查询 = win32evtlog.EvtQuery(
            日志通道,
            win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection,
            "*[System[(EventID=%d)]]" % 事件号_打印,
            None,
        )
        记录: List[出纸记录] = []
        while len(记录) < 最多:
            try:
                批 = win32evtlog.EvtNext(查询, 32)
            except Exception:
                # 读到头时 pywin32 抛的是 "没有更多数据可用"（259），不是返回空列表。
                # 不接住它，一个正常的"读完了"会被当成故障。
                break
            if not 批:
                break
            for 事件 in 批:
                xml = win32evtlog.EvtRender(事件, win32evtlog.EvtRenderEventXml)
                r = _解析307(xml)
                if r:
                    记录.append(r)
        self._缓存 = 记录
        return 记录

    def 刷新(self) -> None:
        self._缓存 = None

    # -- 接口 --------------------------------------------------------------
    def 水位线(self) -> int:
        记录 = self._全部记录()
        return max((r.记录号 for r in 记录), default=0)

    def 新记录(self, 水位线: int) -> List[出纸记录]:
        self.刷新()
        新 = [r for r in self._全部记录() if r.记录号 > 水位线]
        新.sort(key=lambda r: r.记录号)
        if self.打印机过滤:
            新 = [r for r in 新 if r.打印机 == self.打印机过滤]
        return 新

    def 队列(self) -> List[出纸记录]:
        return Windows打印队列(self.打印机过滤).队列()


class Windows打印队列:
    """读打印机队列。**只读**：只 EnumJobs，不删任务、不改打印机状态。"""

    def __init__(self, 打印机过滤: Optional[str] = None) -> None:
        self.打印机过滤 = 打印机过滤

    def 打印机名字列表(self) -> List[str]:
        import win32print
        标记 = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [p[2] for p in win32print.EnumPrinters(标记, None, 1)]

    def 队列(self) -> List[出纸记录]:
        import pywintypes
        import win32print

        出 = []
        for 名 in self.打印机名字列表():
            if self.打印机过滤 and 名 != self.打印机过滤:
                continue
            try:
                句柄 = win32print.OpenPrinter(名)
            except pywintypes.error:
                continue          # 没权限/离线，跳过就好，不能因此判"没出纸"
            try:
                for j in win32print.EnumJobs(句柄, 0, -1, 1):
                    出.append(出纸记录(记录号=j["JobId"], 打印机=名,
                                       页数=j.get("TotalPages") or 0,
                                       时刻=datetime.now()))
            finally:
                win32print.ClosePrinter(句柄)
        return 出

    def 水位线(self) -> int:
        return max((r.记录号 for r in self.队列()), default=0)

    def 新记录(self, 水位线: int) -> List[出纸记录]:
        return [r for r in self.队列() if r.记录号 > 水位线]


# ⚠️ 解析 307 事件时有**四个**真实的坑，全是在真机上踩出来的（当时解析结果恒为 0 条，
#    而事件日志里明明有几千条）。写成注释是因为每一条都会让解析**静默返回 0**，
#    不报错、不提示，看起来就像"这台机器没打过"：
#
#    ① 参数字段是**元素**：`<Param5>值</Param5>`，
#       不是常见的 `<Data Name="Param5">值</Data>` —— 按后者写，一个都匹配不到。
#    ② XML 属性用的是**单引号**：`SystemTime='...'`。
#       正则里写死双引号 `SystemTime="` 会全军覆没。
#    ③ 秒有 **7 位小数**（`.8455019`），而 Python 3.10 的 fromisoformat 最多认 6 位。
#    ④ `SystemTime` 是 **UTC**，本机在 +8 区 —— 不转换会差 8 小时，
#       排查时会得出"时间对不上、肯定不是这次打的"这种错误结论。
#
_参数正则 = re.compile(r"<Param(\d+)>([^<]*)</Param\1>")
_属性时间正则 = re.compile(r"SystemTime=(['\"])([^'\"]+)\1")
_记录号正则 = re.compile(r"<EventRecordID>(\d+)</EventRecordID>")


def _解析307(xml: str) -> Optional[出纸记录]:
    """把一条 307 事件解析成出纸记录。字段口径：Param5=打印机名，Param8=页数。"""
    参 = {k: v for k, v in _参数正则.findall(xml)}
    if not 参:
        return None                      # 坑 ①：匹配不到就说明写法不对，宁可返回 None
    打印机 = 参.get("5", "")
    try:
        页数 = int(参.get("8") or 0)
    except ValueError:
        页数 = 0

    m = _属性时间正则.search(xml)
    时刻 = datetime.now()
    if m:
        原文 = m.group(2)
        原文 = re.sub(r"(\.\d{6})\d+", r"\1", 原文)      # 坑 ③：截断到 6 位
        try:
            时刻 = datetime.fromisoformat(原文.replace("Z", "+00:00")).astimezone()   # 坑 ④：UTC → 本地
        except ValueError:
            pass

    m2 = _记录号正则.search(xml)
    return 出纸记录(记录号=int(m2.group(1)) if m2 else 0, 打印机=打印机, 页数=页数, 时刻=时刻)


# ==========================================================================
# 二、模拟实现（给"没有打印机的地方"用；走完全一样的代码路径）
# ==========================================================================
class 模拟打印服务:
    """证据来自一个 JSON 文件。靶站每"打印"一次就往里追加一条。

    它的存在**不是**为了让演示好看，是为了让 ``等出纸`` 那段逻辑
    在**任何电脑上都能被验证**（包括"证据晚到"这种只在真实世界出现的时序）。

    它刻意把两件事**分开建模**，因为真实世界里它们本来就不是一回事：

      · **队列**：任务一提交就出现，打完就消失 → ``队列保留秒`` 控制它在队列里待多久。
        默认 0 = 立刻消失（"打印机很闲，一提交就打完了"）。
      · **事件日志**：只在真的打到打印机上才记一笔，而且**比提交晚**
        → ``事件延迟秒`` 控制它晚多久才"落到日志里"。
        日志是**永久**的，不会消失。

    一开始我把两个源都从同一份数据里出，结果测出来"队列也会晚到" ——
    那就等于把两个源的意义抹平了。分开建模之后，"队列比事件快"这件事实才被测得住。
    """

    def __init__(self, 文件路径: str, 打印机过滤: Optional[str] = None,
                 事件延迟秒: float = 0.0, 队列保留秒: float = 0.0) -> None:
        self.文件路径 = 文件路径
        self.打印机过滤 = 打印机过滤
        self.事件延迟秒 = 事件延迟秒
        self.队列保留秒 = 队列保留秒
        self._起点 = time.time()

    def _全部(self) -> List[出纸记录]:
        if not os.path.isfile(self.文件路径):
            return []
        出 = []
        with open(self.文件路径, "r", encoding="utf-8") as f:
            for 行 in f:
                行 = 行.strip()
                if not 行:
                    continue
                d = json.loads(行)
                出.append(出纸记录(记录号=int(d["id"]), 打印机=d.get("printer", ""),
                                   页数=int(d.get("pages", 0)),
                                   时刻=datetime.fromtimestamp(d.get("ts", time.time()))))
        return 出

    def _已落地(self) -> List[出纸记录]:
        """"事件已经写进日志"的那些 —— 受事件延迟影响。"""
        if not self.事件延迟秒:
            return self._全部()
        if (time.time() - self._起点) < self.事件延迟秒:
            return []
        return self._全部()

    def 水位线(self) -> int:
        return max((r.记录号 for r in self._已落地()), default=0)

    def 新记录(self, 水位线: int) -> List[出纸记录]:
        新 = [r for r in self._已落地() if r.记录号 > 水位线]
        if self.打印机过滤:
            新 = [r for r in 新 if r.打印机 == self.打印机过滤]
        return sorted(新, key=lambda r: r.记录号)

    def 队列(self) -> List[出纸记录]:
        """队列 = 还没打完的任务。**不受事件延迟影响** —— 它本来就比事件早。"""
        现在 = time.time()
        出 = [r for r in self._全部() if (现在 - r.时刻.timestamp()) < self.队列保留秒]
        if self.打印机过滤:
            出 = [r for r in 出 if r.打印机 == self.打印机过滤]
        return 出


# ==========================================================================
# 三、判出纸：把上面两个源合成一句人话
# ==========================================================================
@dataclass
class 出纸结论:
    提交: bool                # 有没有**硬证据**证明任务出去了
    说明: str
    页数: int = 0
    用队列: bool = False      # True = 这次只拿到队列证据（没排空，事件不可信）


def 等队列排空(源, 打印机: str, 最多秒: float, 每次秒: float = 0.3) -> bool:
    """等队列吐空。**返回布尔值** —— 它决定了下面的判据敢不敢用事件。

    实测教训：**队列空 ≠ 事件已写完**，最后一条 307 可能晚一两秒落地。
    所以这里的返回值只用来回答"能不能用事件当判据"，不用来判断"打完没有"。
    """
    截止 = time.time() + 最多秒
    最后非空 = 0
    while time.time() < 截止:
        队列 = [r for r in 源.队列() if not 打印机 or r.打印机 == 打印机]
        if not 队列:
            if 最后非空 == 0:
                return True
            # 队列曾经非空、现在空了 —— 再确认一次，避免"刚好在两批之间"误判
            time.sleep(0.4)
            if not [r for r in 源.队列() if not 打印机 or r.打印机 == 打印机]:
                return True
        else:
            最后非空 = len(队列)
        time.sleep(每次秒)
    return False


def 等出纸(源, 水位线: int, 打印机: str, 最多秒: float = 45,
           只认队列: bool = False, 每次秒: float = 0.3) -> 出纸结论:
    """点完打印之后调它：把"提交成功了吗"这件事判定下来。

    ``只认队列=True`` 用在**队列没排空**的时候 —— 那时 307 事件里可能混着
    上一批的记录，拿来当判据就是假的成功（这正是曾经误判外箱的原因）。
    """
    截止 = time.time() + 最多秒
    见过的队列 = 0
    while time.time() < 截止:
        队列 = [r for r in 源.队列() if not 打印机 or r.打印机 == 打印机]
        if 队列:
            见过的队列 += len(队列)
            if 只认队列:
                return 出纸结论(True, "队列出现新任务（%d 个，第 %.1f 秒）"
                                % (len(队列), 最多秒 - (截止 - time.time())), 用队列=True)
        if not 只认队列:
            新 = 源.新记录(水位线)
            if 新:
                页数 = sum(r.页数 for r in 新)
                return 出纸结论(True, "打印服务记录了 %d 条新任务，共 %d 页（最早 %s）"
                                % (len(新), 页数, 新[0].时刻.strftime("%H:%M:%S")), 页数=页数)
        time.sleep(每次秒)
    if 只认队列:
        return 出纸结论(False, "队列里没出现新任务（%d 秒内）" % int(最多秒))
    return 出纸结论(False, "队列和打印服务事件都没有新记录（%d 秒内）" % int(最多秒))


def 重试前有晚到的证据(源, 水位线: int, 打印机: str) -> Optional[出纸结论]:
    """自动重试**之前**必须先问一句：上一次是不是其实已经提交了？

    这是防"打两份"的安全阀。晚到的证据有两种样子：
      · 队列里出现了新任务；
      · 打印服务事件在**点完之后**才落地（实测真会这样）。
    只要有一种，就立刻判成功、**绝不重试**。

    宁可偶尔"以为没打出去、其实打了"（人去看一眼就知道），
    也绝不冒"整叠标签打两份"的险 —— 前者可恢复，后者是废纸 + 停机。
    """
    队列 = [r for r in 源.队列() if not 打印机 or r.打印机 == 打印机]
    if 队列:
        return 出纸结论(True, "复查发现队列里有任务（%d 个）—— 上一次其实提交了" % len(队列), 用队列=True)
    新 = 源.新记录(水位线)
    if 新:
        return 出纸结论(True, "复查发现打印服务有晚到的事件 %s —— 上一次其实出纸了" % 新[0], 页数=sum(r.页数 for r in 新))
    return None
