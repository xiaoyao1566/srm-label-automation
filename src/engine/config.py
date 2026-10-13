# -*- coding: utf-8 -*-
"""配置层 —— **所有会变的东西都放这里**。

业务代码里不许出现站点地址、账号、客户名、料号、列号、打印机名。
这一层不是为了"好看"，是为了两件事：

  1. **换一个站点/客户只改这个文件**，代码一行不动；
  2. **仓库里可以做到零业务数据** —— 默认值全是占位符，
     真实值走"本地配置文件 + 环境变量"，而本地配置文件在 .gitignore 里。

    （这条后来变成了硬约束：发布前闸门会扫整个仓库，
      任何一个真实客户名/地址/料号出现在代码里都会让它拒绝发布。）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List


# --------------------------------------------------------------------------
# 一、页面选择器：站点改版时只动这里
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class 选择器:
    """页面元素定位。命名按"它是什么"，不按"它在哪一页"。"""

    # --- 登录 ---
    用户名框: str = "input[placeholder*='账号']"
    密码框: str = "input[type='password']"
    验证码图: str = ".validate-code"
    验证码框: str = "input[placeholder*='验证码']"
    登录按钮: str = "button:has-text('登录')"
    登录后标志: str = ".user-setting-item, header .user"

    # --- 导航 ---
    菜单_销售执行: str = "text=销售执行"
    菜单_送货协同: str = "text=送货协同"
    菜单_标签详情: str = "text=标签详情"

    # --- 列表页 ---
    高级查询按钮: str = "button:has-text('高级查询')"
    搜索按钮: str = "button:has-text('搜索')"
    新增按钮: str = "button:has-text('新增')"
    打印标签按钮: str = "button:has-text('打印标签')"
    确认打印: str = "text=确认打印"
    总条数: str = ".ivu-page-total"

    # ⚠️ 下面这三条是本项目最贵的三个选择器，改之前先读 docs/pitfalls.md
    #    ① 表头全选框在 vxe 表里**有两份 DOM**（固定列一份、滚动列一份），
    #       `.first` 会点到隐藏的那份 —— 必须先 :visible 再取第一份。
    表头全选框: str = ".vxe-header--column .vxe-cell--checkbox:visible"
    #    ② 数据行同样有两个 tab 都挂在 DOM 里，不加 :visible 会把另一张表的行数进来。
    数据行: str = "tr.vxe-body--row:visible"
    #    ③ 弹窗有几份隐藏副本，容器必须限定在"可见的"那一份里。
    订单弹窗容器: str = ".ivu-modal-content:visible"

    # 筛选面板里每个字段的定位。
    # ⚠️ 真实站点的写法是「按标签文字找输入框」：
    #      `label:has-text('物料编码') >> xpath=.. >> input`
    #    这里给的是靶站的写法（data-f 属性）。换站点时**只改这里**，
    #    面板代码一行都不用动 —— 这就是配置层存在的意义。
    筛选项: dict = field(default_factory=lambda: {
        "itemCode": "[data-f='itemCode']",
        "poNum": "[data-f='poNum']",
        "shipFrom": "[data-f='shipFrom']",
        "shipTo": "[data-f='shipTo']",
        "prodFrom": "[data-f='prodFrom']",
        "prodTo": "[data-f='prodTo']",
        "labelType": "[data-f='labelType']",
        "labelStatus": "[data-f='labelStatus']",
    })
    # 日期字段：只能点日历，不能打字（真机上 fill() 会被弹回）
    日历按钮: str = "[data-cal='{字段}']"
    日历某天: str = "[data-d='{值}']"

    # 分页 / 计数
    跨页选择数: str = "#crossPage"
    下一页按钮: str = ".ivu-page-next, #next"
    上一页按钮: str = ".ivu-page-prev, #prev"
    行: str = "tr.vxe-body--row:visible"
    行内复选格: str = ".vxe-cell--checkbox"


# --------------------------------------------------------------------------
# 二、站点与账号
# --------------------------------------------------------------------------
@dataclass
class 站点配置:
    """站点地址与账号。默认值全是占位符 —— 真实值放本地配置，不入库。"""

    网址: str = "http://127.0.0.1:8770"          # 靶站默认地址；真实站点在本地配置里覆盖
    账号: str = ""                                # 空 = 必须由本地配置或环境变量提供
    口令: str = ""                                # ⚠️ 只允许来自本地配置/环境变量，绝不入库
    列表页路径: str = "/list"                     # 靶站路径；真实站点在本地配置里覆盖
    创建页路径: str = "/#/main/LabelManageDetailAdd"


# --------------------------------------------------------------------------
# 三、业务口径（都是"数"和"列"，不是"值"）
# --------------------------------------------------------------------------
@dataclass
class 口径:
    每页条数: int = 20                 # 列表页固定 20 条/页；跨页勾选按这个数翻页

    # 任务表列名 —— 用**列名**找列，不写死列号。
    # 踩过的坑：一个客户一个表布局，写死"第 5 列"会在别的客户表上错位。
    列_料号: str = "物料编码"
    列_订单号: str = "订单号"
    列_排程总量: str = "排程总数量"
    列_本次数量: str = "本次到货数量"
    列_生产日期: str = "生产日期"
    列_每箱内袋: str = "每箱内袋数"
    列_外箱数: str = "外箱数"
    列_状态: str = "状态"

    状态_待打: tuple = ("确认", "执行")      # 只处理这些状态的行
    状态_已打前缀: str = "已打"              # 以它开头的行一律跳过


# --------------------------------------------------------------------------
# 四、时序与重试（数字都是实测调出来的，注释里写了为什么）
# --------------------------------------------------------------------------
@dataclass
class 时序:
    面板失焦等待毫秒: int = 150        # 原来 900ms，实测 150ms 就够
    搜索后轮询最多秒: float = 3.5      # 原来是死等 2500ms
    预览渲染等待毫秒: int = 2500       # ⛔ 别砍：太早走控件树会白转 20 多秒
    队列排空最多秒: int = 240          # 物理时间，砍它就是砍正确性
    出纸重试次数: int = 2
    出纸重试间隔秒: float = 3.0


# --------------------------------------------------------------------------
# 五、加载：默认 → 本地配置 → 环境变量（后者覆盖前者）
# --------------------------------------------------------------------------
默认本地配置路径 = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "标签自动化", "本机配置.json"
)


@dataclass
class 配置:
    站点: 站点配置 = field(default_factory=站点配置)
    选择器: 选择器 = field(default_factory=选择器)
    口径: 口径 = field(default_factory=口径)
    时序: 时序 = field(default_factory=时序)
    打印机: str = ""                  # 空 = 用系统默认打印机
    演练: bool = False                # True = 走完整流程但不真出纸、不写回

    @classmethod
    def 载入(cls, 本地配置路径: str | None = None) -> "配置":
        cfg = cls()
        路径 = 本地配置路径 or 默认本地配置路径
        if os.path.isfile(路径):
            with open(路径, "r", encoding="utf-8") as f:
                本地 = json.load(f)
            for 段名 in ("站点", "口径", "时序"):
                if 段名 in 本地:
                    段 = getattr(cfg, 段名)
                    for k, v in 本地[段名].items():
                        if hasattr(段, k):
                            setattr(段, k, v)

        # 环境变量优先级最高 —— CI / 靶站 / 临时换账号都靠它，不用改文件
        映射 = {
            "SITE_URL": ("站点", "网址"),
            "SITE_ACCOUNT": ("站点", "账号"),
            "SITE_PASSWORD": ("站点", "口令"),
            "PRINTER": ("", "打印机"),
        }
        for 环境名, (段名, 字段) in 映射.items():
            v = os.environ.get(环境名)
            if not v:
                continue
            if 段名:
                setattr(getattr(cfg, 段名), 字段, v)
            else:
                setattr(cfg, 字段, v)

        cfg.演练 = os.environ.get("DRY_RUN") == "1"
        return cfg

    def 摘要(self) -> Dict[str, object]:
        """给日志用的摘要 —— **刻意不含口令**。"""
        return {
            "网址": self.站点.网址,
            "账号": self.站点.账号 or "(未配置)",
            "打印机": self.打印机 or "(系统默认)",
            "演练": self.演练,
            "每页条数": self.口径.每页条数,
        }


def 载入(本地配置路径: str | None = None) -> 配置:
    return 配置.载入(本地配置路径)
