# -*- coding: utf-8 -*-
"""靶站 —— 一个"故意长得像真实供应商门户"的本地网站，纯标准库、零依赖。

它存在的理由：
    真实门户是老板的系统，别人的电脑打不开，而且里面全是真数据。
    作品集要让人**clone 下来就能自己跑通**，就得有一个能公开的替身。

它复刻的不是"页面长相"，是**当时真正难的四件事**（每条都能被脚本踩到）：

  ① **表头全选框有两份 DOM**：固定列那份在 DOM 里排在前面，但它是隐藏的。
     用 `.first` 会点到隐藏的那份 —— 点了没反应，而日志上完全看不出来。
  ② **筛选面板里按回车会毁掉面板**：输入框回车 = 触发查询 + **清空整个面板**。
     所以只能靠失焦（Tab）提交。这个坑当年让脚本"筛出来的料全是错的"。
  ③ **每页固定 20 条，勾选要跨页**：勾完第一页翻页，勾选状态还在（像真实 vxe 那样）；
     但只勾当前页就打印，只会出 20 张。
  ④ **点【确认打印】不会出纸**：真正的打印按钮在**另一个窗口**里
     （真实站点里那个按钮在浏览器的打印预览 UI 里，DOM 根本点不到，
      只能靠 Windows UI Automation 去点 —— 这里用"另一个窗口"复刻
      "它不在当前页面里"这个本质）。

另外还有一条也一并复刻了：
  ⑤ **日期框打不进字**：只能点开日历挑。当年用 fill() 填日期会被弹回。

登录：靶站**不校验具体口令**（只要非空就放行）。
    这样仓库里就不需要出现任何口令字面量 —— 脱敏和可运行在这里不冲突。

启动：
    python mock_srm/server.py --端口 8770 [--码流文件 <路径>]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import sys
import time
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# —— 控制台编码护栏 ——
# 中文 Windows 的 cmd / PowerShell 默认编码是 GBK，打印 ⚠ 这类符号会直接
# UnicodeEncodeError 崩掉。这里放宽标准输出的错误策略：编不出来的符号退化成 ?，
# 绝不因为一个符号把靶站启动打断。
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(errors="replace")
    except Exception:
        pass

本目录 = os.path.dirname(os.path.abspath(__file__))
静态目录 = os.path.join(本目录, "static")

会话: dict = {}          # sid -> {"account": str, "created": float}
启动参数 = {"码流文件": os.path.join(本目录, "spool.jsonl")}


# ==========================================================================
# 假数据：形态像真数据，但**全部是编的**
# ==========================================================================
# ⚠️ 刻意不用"8 位以上的纯数字"当料号 —— 那种号码看起来就像真的业务编码，
#    容易被误当成真数据。这里用带前缀的短码，一眼就能看出是演示数据。
料号池 = ["DM-1001", "DM-1002", "DM-1003", "DM-1004", "DM-1005", "DM-2001", "DM-2002"]
订单池 = ["PO-DEMO-01", "PO-DEMO-02", "PO-DEMO-03"]


def 造假数据() -> list:
    """固定随机种子 —— 每次启动的数据一模一样，这样测试结果可复现。"""
    rnd = random.Random("demo-seed-v1")
    行 = []
    rid = 0
    基准 = date(2026, 10, 13)
    for 订单 in 订单池:
        for 料号 in 料号池:
            出货 = 基准
            for 类型, 前缀 in (("内箱标签", "BAG"), ("外箱标签", "BOX")):
                rid += 1
                内袋数 = rnd.randint(1, 6) if 类型 == "内箱标签" else 1
                行.append({
                    "id": rid,
                    "itemCode": 料号,
                    "poNum": 订单,
                    "labelCode": "%s%s%04d" % (前缀, 出货.strftime("%Y%m%d"), rid),
                    "labelTypeName": 类型,
                    "labelStatusName": "生效",
                    "productionDate": 出货.isoformat(),
                    "shipmentDate": 出货.isoformat(),
                    "labelAllocationQty": 内袋数 * 100,
                    "deliveryQty": 100,
                    "creationDate": "%s %02d:%02d:%02d" % (出货.isoformat(),
                                                        8 + rid % 10, rid % 60, (rid * 7) % 60),
                })
    return 行


全部标签 = 造假数据()
每页条数 = 20


def 筛选(参数: dict) -> list:
    出 = []
    for r in 全部标签:
        if 参数.get("itemCode") and 参数["itemCode"] not in r["itemCode"]:
            continue
        if 参数.get("poNum") and 参数["poNum"] not in r["poNum"]:
            continue
        if 参数.get("labelType") and 参数["labelType"] != r["labelTypeName"]:
            continue
        if 参数.get("labelStatus") and 参数["labelStatus"] != r["labelStatusName"]:
            continue
        if 参数.get("prodFrom") and r["productionDate"] < 参数["prodFrom"]:
            continue
        if 参数.get("prodTo") and r["productionDate"] > 参数["prodTo"]:
            continue
        if 参数.get("shipFrom") and r["shipmentDate"] < 参数["shipFrom"]:
            continue
        if 参数.get("shipTo") and r["shipmentDate"] > 参数["shipTo"]:
            continue
        出.append(r)
    return 出


# ==========================================================================
# HTTP
# ==========================================================================
class 处理器(BaseHTTPRequestHandler):
    server_version = "MockPortal/1.0"

    # ---- 小工具 ----
    def _发(self, 状态: int, 内容: bytes, 类型: str = "text/html; charset=utf-8",
            额外头: dict | None = None):
        self.send_response(状态)
        self.send_header("Content-Type", 类型)
        self.send_header("Content-Length", str(len(内容)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (额外头 or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(内容)

    def _JSON(self, 对象, 状态: int = 200, 额外头: dict | None = None):
        self._发(状态, json.dumps(对象, ensure_ascii=False).encode("utf-8"),
                 "application/json; charset=utf-8", 额外头)

    def _读体(self) -> dict:
        长度 = int(self.headers.get("Content-Length") or 0)
        if not 长度:
            return {}
        try:
            return json.loads(self.rfile.read(长度).decode("utf-8"))
        except Exception:
            return {}

    def _当前会话(self):
        from http.cookies import SimpleCookie
        原始 = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        try:
            c.load(原始)
        except Exception:
            return None
        sid = c["sid"].value if "sid" in c else None
        return 会话.get(sid) if sid else None

    def _静态(self, 名: str):
        路径 = os.path.join(静态目录, 名)
        if not os.path.isfile(路径):
            self._发(404, b"not found")
            return
        with open(路径, "rb") as f:
            内容 = f.read()
        类型 = "text/html; charset=utf-8"
        if 名.endswith(".js"):
            类型 = "application/javascript; charset=utf-8"
        elif 名.endswith(".css"):
            类型 = "text/css; charset=utf-8"
        self._发(200, 内容, 类型)

    def log_message(self, fmt, *args):      # 静音默认日志，免得刷屏
        pass

    # ---- 路由 ----
    def do_GET(self):
        解析 = urllib.parse.urlparse(self.path)
        路径 = 解析.path
        参数 = {k: v[0] for k, v in urllib.parse.parse_qs(解析.query).items()}

        if 路径 in ("/", "/index.html"):
            if self._当前会话():
                self.send_response(302)
                self.send_header("Location", "/list")
                self.end_headers()
            else:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
            return

        if 路径 == "/login":
            self._静态("login.html")
            return

        if 路径 == "/list":
            if not self._当前会话():
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            self._静态("list.html")
            return

        if 路径 == "/printpreview":
            # ⚠️ 这个页面**故意不要求登录**：真实场景里它是浏览器弹出来的打印预览，
            #    是另一个窗口、另一套上下文。脚本必须能处理"不在当前页面里的按钮"。
            self._静态("print_preview.html")
            return

        if 路径 == "/api/labels":
            行 = 筛选(参数)
            页 = int(参数.get("page") or 1)
            起 = (页 - 1) * 每页条数
            self._JSON({
                "total": len(行),
                "page": 页,
                "pageSize": 每页条数,
                "rows": 行[起:起 + 每页条数],
            })
            return

        if 路径 == "/api/captcha":
            # 靶站专用：验证码的答案**就放在返回里**（真实站点当然不会这么干）。
            # 目的是让靶站零依赖就能跑通；真实站点那一步必须靠 OCR 或人工。
            a, b = random.Random(time.time()).randint(1000, 9999), None
            self._JSON({"captcha": str(a)})
            return

        if 路径 == "/api/health":
            self._JSON({"ok": True, "labels": len(全部标签), "pageSize": 每页条数})
            return

        self._发(404, b"not found")

    def do_POST(self):
        解析 = urllib.parse.urlparse(self.path)
        路径 = 解析.path

        if 路径 == "/api/login":
            体 = self._读体()
            if not (体.get("account") or "").strip() or not (体.get("password") or "").strip():
                self._JSON({"ok": False, "msg": "账号和口令都要填"}, 400)
                return
            # 口令正确性由**真实验证码**决定（靶站不校验口令内容 —— 仓库里因此没有口令字面量）
            期望 = 体.get("captchaExpect")
            if 期望 is not None and str(体.get("captcha") or "") != str(期望):
                self._JSON({"ok": False, "msg": "验证码不对"}, 400)
                return
            sid = secrets.token_hex(8)
            会话[sid] = {"account": 体["account"], "created": time.time()}
            self._JSON({"ok": True, "user": 体["account"]},
                       额外头={"Set-Cookie": "sid=%s; Path=/" % sid})
            return

        if 路径 == "/api/print":
            # 【确认打印】点完之后：真实世界这里**什么都不会发生**（不出纸），
            # 必须再去打印预览里点那个【打印】。靶站照这个语义来：
            # 这一步只登记一个"待打印任务"，不写码流（码流 = 出纸证据）。
            体 = self._读体()
            ids = 体.get("ids") or []
            if not ids:
                self._JSON({"ok": False, "msg": "没有勾选任何标签"}, 400)
                return
            选中 = [r for r in 全部标签 if r["id"] in ids]
            任务号 = int(time.time() * 1000) % 100000
            self._JSON({"ok": True, "jobId": 任务号, "count": len(选中),
                        "pages": sum(max(1, r["labelAllocationQty"] // 100) for r in 选中)})
            return

        if 路径 == "/api/print/commit":
            # 【打印】按钮真正被点到 → 写一行"出纸记录"。
            # ⚠️ 这是**证据**，不是"脚本说成功"。引擎会去读这个文件，而不是听返回值。
            体 = self._读体()
            页数 = int(体.get("pages") or 0)
            打印机 = 体.get("printer") or "MockPrinter"
            目录 = os.path.dirname(os.path.abspath(启动参数["码流文件"]))
            if 目录:
                os.makedirs(目录, exist_ok=True)
            记录号 = 0
            if os.path.isfile(启动参数["码流文件"]):
                with open(启动参数["码流文件"], "r", encoding="utf-8") as f:
                    for 行 in f:
                        if 行.strip():
                            记录号 = max(记录号, int(json.loads(行).get("id", 0)))
            记录号 += 1
            with open(启动参数["码流文件"], "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": 记录号, "pages": 页数,
                                    "printer": 打印机, "ts": time.time()}) + "\n")
            self._JSON({"ok": True, "spoolId": 记录号, "pages": 页数, "printer": 打印机})
            return

        self._发(404, b"not found")


def main():
    p = argparse.ArgumentParser(description="靶站（本地模拟门户）")
    p.add_argument("--端口", type=int, default=8770)
    p.add_argument("--码流文件", default=启动参数["码流文件"],
                   help="出纸记录写到这里；引擎把它当**出纸证据**读")
    a = p.parse_args()
    启动参数["码流文件"] = a.码流文件

    服务 = ThreadingHTTPServer(("127.0.0.1", a.端口), 处理器)
    print("靶站已启动：http://127.0.0.1:%d" % a.端口)
    print("  假数据 %d 条标签，每页 %d 条" % (len(全部标签), 每页条数))
    print("  出纸记录文件：%s" % a.码流文件)
    try:
        服务.serve_forever()
    except KeyboardInterrupt:
        print("\n靶站已停止")


if __name__ == "__main__":
    main()
