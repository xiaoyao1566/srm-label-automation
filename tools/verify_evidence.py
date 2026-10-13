# -*- coding: utf-8 -*-
"""出纸证据模块的验证 —— **只读**，不打印任何东西。

分两半：

  前半：拿**这台机器上真实的历史打印记录**验证解析器（读事件日志和打印队列，
        都是只读操作，不会出纸、不会动队列里的任务）。
        这一步很重要：解析器最容易写错的地方（7 位小数的时间戳、UTC 与本机时间差 8 小时、
        Param 名字对不上）只有真实数据能暴露。

  后半：用模拟证据源验证**判据本身**，尤其是"证据晚到时不重试"这条安全阀 ——
        它没法靠真实打印机复现（要刚好卡在那个时间窗口），只能用注入的方式验。

用法：
    python tools/verify_evidence.py
退出码：0 = 全过；1 = 有失败。
"""

import io
import json
import os
import sys
import tempfile
import time

# —— 控制台编码护栏 ——
# 中文 Windows 的 cmd / PowerShell 默认编码是 GBK，打印 ⚠ / · 这类符号会直接
# UnicodeEncodeError 崩掉。这里放宽标准输出的错误策略：编不出来的符号退化成 ?，
# 绝不因为一个符号把整个验证打断。
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from engine import evidence          # noqa: E402

通过 = 0
失败 = 0


def 断言(条件, 说明, 细节=""):
    global 通过, 失败
    if 条件:
        通过 += 1
        print("  [PASS] %s" % 说明)
    else:
        失败 += 1
        print("  [FAIL] %s   %s" % (说明, 细节))


def 前半_真实数据():
    print("\n=== 前半：真实历史记录（只读）===")
    try:
        import win32print  # noqa: F401
    except ImportError as e:
        print("  [SKIP] 没装 pywin32（%s）—— 跳过真实数据验证" % e)
        return

    源 = evidence.Windows打印服务()
    try:
        记录 = 源._全部记录(最多=200)
    except Exception as e:
        print("  [SKIP] 读打印服务事件失败（%s）—— 可能是权限或该日志未启用" % e)
        记录 = []
    print("  读到 307 事件 %d 条" % len(记录))
    if 记录:
        最新 = max(记录, key=lambda r: r.记录号)
        print("  最近一条：%s（记录号 %d）" % (最新, 最新.记录号))
        断言(bool(最新.打印机), "打印机名字段解析出来了（Param5）", "空值")
        断言(最新.页数 > 0, "页数字段解析出来了（Param8）", "=0")
        # 判据：解析出来的时刻必须落在合理范围（不是 1970，也不是未来）
        本机 = time.time()
        差 = abs(最新.时刻.timestamp() - 本机)
        断言(差 < 400 * 24 * 3600, "时间戳解析合理（距现在 %.1f 天）" % (差 / 86400))
        断言(最新.时刻.year >= 2020, "年份不是 1970 之类的默认值")
        # UTC/本地时差 8 小时这个坑
        断言(最新.时刻.tzinfo is not None, "时刻带时区信息（已从 UTC 转本地）")
        断言(最新.记录号 > 0, "事件记录号可用于当水位线")
    else:
        print("  [提示] 这台机器没有 307 记录，跳过真实解析验证（不是失败）")

    队列 = evidence.Windows打印队列()
    名字 = 队列.打印机名字列表()
    print("  本机打印机 %d 台：%s" % (len(名字), "、".join(名字) if 名字 else "（无）"))
    断言(isinstance(名字, list), "能枚举本机打印机")
    try:
        q = 队列.队列()
        print("  当前队列任务 %d 个" % len(q))
        断言(True, "能读取打印队列（只读 EnumJobs）")
    except Exception as e:
        断言(False, "读取打印队列失败", str(e))


def 后半_判据逻辑():
    print("\n=== 后半：判据逻辑（模拟证据源，走同一套代码路径）===")
    临时 = tempfile.mkdtemp(prefix="evidence_")

    def 新文件(名):
        return os.path.join(临时, 名)

    def 追加(文件, 记录号, 页数=1, 打印机="MockPrinter"):
        with io.open(文件, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": 记录号, "pages": 页数, "printer": 打印机,
                                "ts": time.time()}) + "\n")

    # ---- A：点了打印但什么都没有 → 必须判"没交出去" ----
    fa = 新文件("a.jsonl")
    源a = evidence.模拟打印服务(fa, 打印机过滤="MockPrinter", 队列保留秒=0)
    断言(源a.水位线() == 0, "A0 空日志时水位线为 0")
    结论 = evidence.等出纸(源a, 源a.水位线(), "MockPrinter", 最多秒=1.0, 只认队列=True)
    断言(not 结论.提交, "A 零证据 → 判为未提交（不会误报成功）", 结论.说明)

    # ---- B：队列有任务 → 判提交，且标明用的是队列证据 ----
    fb = 新文件("b.jsonl")
    源b = evidence.模拟打印服务(fb, 打印机过滤="MockPrinter", 队列保留秒=9999)
    追加(fb, 1, 页数=3)
    结论 = evidence.等出纸(源b, 0, "MockPrinter", 最多秒=1.0, 只认队列=True)
    断言(结论.提交 and 结论.用队列, "B 队列有新任务 → 判提交（队列证据）", 结论.说明)

    # ---- C：**晚到的证据** —— 防打两份的安全阀 ----
    fc = 新文件("c.jsonl")
    源c = evidence.模拟打印服务(fc, 打印机过滤="MockPrinter", 队列保留秒=0)
    基线 = 源c.水位线()
    断言(evidence.重试前有晚到的证据(源c, 基线, "MockPrinter") is None,
          "C1 复查确实没有新东西 → 允许重试")
    追加(fc, 2, 页数=5)
    晚到 = evidence.重试前有晚到的证据(源c, 基线, "MockPrinter")
    断言(晚到 is not None and 晚到.提交,
          "C2 复查发现晚到的记录 → 拦住重试（绝不打两份）",
          晚到.说明 if 晚到 else "没拦住")

    # ---- D：队列比事件快（两个源同时存在的理由）----
    fd = 新文件("d.jsonl")
    延迟源 = evidence.模拟打印服务(fd, 打印机过滤="MockPrinter",
                                    事件延迟秒=1.5, 队列保留秒=9999)
    追加(fd, 3, 页数=7)
    断言(延迟源.队列() and not 延迟源.新记录(0),
          "D1 同一时刻：队列已可见，事件还没落地（这就是为什么要两个源）",
          "队列=%d 事件=%d" % (len(延迟源.队列()), len(延迟源.新记录(0))))
    结论 = evidence.等出纸(延迟源, 0, "MockPrinter", 最多秒=0.8, 只认队列=True)
    断言(结论.提交 and 结论.用队列, "D2 事件晚到时，队列先顶上", 结论.说明)

    # ---- E：打印机名过滤（别把别台机器的记录算进来）----
    fe = 新文件("e.jsonl")
    源e = evidence.模拟打印服务(fe, 打印机过滤="MockPrinter", 队列保留秒=9999)
    追加(fe, 4, 页数=9, 打印机="OtherPrinter")
    断言(源e.新记录(0) == [] and 源e.队列() == [],
          "E 按打印机名过滤生效（别的机器的记录不算）")

    # ---- F：排空判据（它决定上面那段敢不敢用事件）----
    ff1 = 新文件("f1.jsonl")
    追加(ff1, 5, 页数=1)
    断言(evidence.等队列排空(evidence.模拟打印服务(ff1, "MockPrinter", 队列保留秒=9999),
                              "MockPrinter", 最多秒=0.8) is False,
          "F1 队列非空 → 排空返回 False（此时不许拿事件当判据）")
    ff2 = 新文件("f2.jsonl")
    追加(ff2, 6, 页数=1)
    断言(evidence.等队列排空(evidence.模拟打印服务(ff2, "MockPrinter", 队列保留秒=0),
                              "MockPrinter", 最多秒=0.8) is True,
          "F2 队列已空 → 排空返回 True（可以放心用事件）")

    # ---- G：水位线语义（只认比它大的）----
    fg = 新文件("g.jsonl")
    源g = evidence.模拟打印服务(fg, 打印机过滤="MockPrinter", 队列保留秒=0)
    追加(fg, 7, 页数=2)
    断言(源g.新记录(源g.水位线()) == [], "G 水位线之后没有新记录时返回空")

    import shutil
    shutil.rmtree(临时, ignore_errors=True)


if __name__ == "__main__":
    print("出纸证据模块验证（全程只读，不出纸）")
    前半_真实数据()
    后半_判据逻辑()
    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (通过, 失败))
    sys.exit(1 if 失败 else 0)
