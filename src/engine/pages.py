# -*- coding: utf-8 -*-
"""页面层：起浏览器、登录、进列表页。

这一层只做"怎么和浏览器打交道"，不含任何业务判断。
"""

from __future__ import annotations

import time

from .logkit import log
from .config import 配置


def 起浏览器(cfg: 配置, 有头: bool = False, 慢动作: int = 0):
    """起浏览器，返回 (管理器, 浏览器, 上下文, 页面)。

    优先用**系统 Chrome**：投产时就是这么定的 —— 复用用户已有的字体/代理环境，
    而且不需要额外下载几百 MB 的 Chromium。没有 Chrome 就退回自带 Chromium，
    这样"别人 clone 下来也能跑"才成立。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("没装 playwright（%s）。先跑：pip install -r requirements.txt" % e)

    管理器 = sync_playwright().start()
    最后错误 = None
    for 参数 in ({"channel": "chrome"}, {}):
        try:
            浏览器 = 管理器.chromium.launch(headless=not 有头, slow_mo=慢动作, **参数)
            上下文 = 浏览器.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
            页面 = 上下文.new_page()
            log("[浏览器] 已启动：%s" % ("系统 Chrome" if 参数 else "自带 Chromium"))
            return 管理器, 浏览器, 上下文, 页面
        except Exception as e:
            最后错误 = e
    管理器.stop()
    raise RuntimeError("起不了浏览器：%s" % 最后错误)


def 关浏览器(管理器, 浏览器, 上下文) -> None:
    for 动作 in (lambda: 上下文.close(), lambda: 浏览器.close(), lambda: 管理器.stop()):
        try:
            动作()
        except Exception:
            pass


def 登录(页面, cfg: 配置, 读验证码=None, 最多试: int = 3) -> bool:
    """登录。``读验证码`` 是一个可选回调：给它页面，返回验证码字符串。

    为什么要做成回调：真实站点那一步是"OCR 自动识别 + 识别不出来就弹窗让真人输"，
    靶站则是把答案直接放在 DOM 里。**换站点只换这个回调，登录流程本身不用动。**
    """
    for 第 in range(1, 最多试 + 1):
        页面.goto(cfg.站点.网址.rstrip("/") + "/login", wait_until="domcontentloaded")
        页面.fill(cfg.选择器.用户名框, cfg.站点.账号)
        页面.fill(cfg.选择器.密码框, cfg.站点.口令)

        码 = ""
        if 读验证码:
            try:
                码 = (读验证码(页面) or "").strip()
            except Exception as e:
                log("[验证码] 读取失败：%s" % e)
        if 码:
            页面.fill(cfg.选择器.验证码框, 码)
        else:
            log("[验证码] 没拿到验证码，直接试一次")

        页面.click(cfg.选择器.登录按钮)
        try:
            页面.wait_for_selector(cfg.选择器.登录后标志, timeout=6000)
            log("[登录] 成功（第 %d 次）" % 第)
            return True
        except Exception:
            log("[登录] 第 %d 次没进去（多半是验证码不对），重来一次" % 第)
    raise RuntimeError("登录连续 %d 次失败" % 最多试)


def 进列表页(页面, cfg: 配置, 最多试: int = 3) -> None:
    """直达列表页并等到表格计数出现。

    ⚠️ 这一步看着啰嗦，但它拦住过一整类事故：
       如果流程是"建完标签、跳过保存"直接去搜索，浏览器**还停在创建页**上，
       而创建页里根本没有【高级查询】按钮 —— 表现就是反复点不开、
       每一轮都白跑。所以进打印流程之前，先把"我在列表页"这件事确认下来。
    """
    from .panel import 高级查询面板
    for 第 in range(1, 最多试 + 1):
        页面.goto(cfg.站点.网址.rstrip("/") + cfg.站点.列表页路径, wait_until="domcontentloaded")
        try:
            页面.wait_for_selector(cfg.选择器.总条数, timeout=8000)
            log("[页面] 已进入列表页（第 %d 次尝试）" % 第)
            return
        except Exception:
            log("[页面] 第 %d 次没等到列表页，重来" % 第)
    raise RuntimeError("进不去列表页（等不到「%s」）" % cfg.选择器.总条数)
