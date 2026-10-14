# -*- coding: utf-8 -*-
"""发布闸门自己的回归测试 —— 把两个"静默的错"钉死。

为什么需要它：这两个 bug 都不是"一跑就报错"，而是**环境一变就换个说法**，
只有真跑过才暴露，而且失败方向都很危险：

  ① **GBK 控制台下直接崩**。闸门要打印 ✓ / ✗ 这类符号，中文 Windows 的
     cmd / PowerShell 默认编码是 GBK，编不出来就 `UnicodeEncodeError` ——
     扫描根本没跑完。但退出码非 0 看起来还挺像"拦住了泄漏"，
     所以这种崩**很容易被当成正常拦截**放过去。

  ② **命中数被同一行里的其它数字污染**。原先是"把这一行里的数字全拼起来"，
     而扫描器那行是 `报告已写入：<完整路径>（N 条命中）`，路径就在同一行上。
     把仓库 clone 到**名字里带时间戳**的目录里跑（我们的克隆目录正是这种），
     0 命中会被判成天文数字命中、拒绝发布。

所以这里对①做**差分验证**（把护栏废掉，必须真的崩 —— 证明判据有区分力），
对②直接喂真实形态的字符串。判据不是"我觉得对"，是这两条断言同时成立。

⚠️ 下面用例里的路径**故意不带盘符、也不用 8 位以上连续数字**：
   本文件自己也会被发布闸门扫，带盘符会命中"绝对路径"、8 位数字会命中"长数字串"，
   那就变成了"测试文件把闸门搞挂"。要改这些字符串，先想清楚这一条。

用法：
    python tools/verify_publish_gate.py
退出码：0 = 全过；1 = 有失败。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

# —— 控制台编码护栏（和别的入口一样；这个脚本自己也得能在这个环境下跑完）——
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(errors="replace")
    except Exception:
        pass

本文件目录 = os.path.dirname(os.path.abspath(__file__))
仓库根 = os.path.dirname(本文件目录)
sys.path.insert(0, 本文件目录)

import pre_publish_check as 闸门模块      # noqa: E402

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


# --------------------------------------------------------------------------
# 一、命中数必须锚定在标签上
# --------------------------------------------------------------------------
def 测_读命中数():
    print("\n=== 一、命中数必须锚定在标签上（不能被同一行里的其它数字污染）===")
    用例 = [
        ("真实翻车形态：路径含数字 + 0 命中（旧写法会粘成 70）",
         "报告已写入：/tmp/run7/_private/脱敏清单_作品集.txt（0 条命中）", 0),
        ("路径含数字 + 3 命中（旧写法会粘成 73）",
         "报告已写入：/work/proj7/out.txt（3 条命中）", 3),
        ("报告头那行的文件数不能被粘进来（旧写法会粘成 200）",
         "被扫描文件数：20   命中总数：0", 0),
        ("命中总数：17", "命中总数：17", 17),
        ("读不出来 -> None（调用方必须 fail-closed，不许放行）",
         "扫描完成，一切正常", None),
    ]
    for 名, 输出, 期望 in 用例:
        得 = 闸门模块.从扫描输出读命中数(输出)
        断言(得 == 期望, 名, "得到 %r，期望 %r" % (得, 期望))


# --------------------------------------------------------------------------
# 二、GBK 控制台：护栏必须在，而且必须真的有用（差分验证）
# --------------------------------------------------------------------------
def 子进程编码(env) -> str:
    """探一下子进程在给定环境下的 stdout 编码 —— 判断这条测试在这台机器上有没有意义。"""
    结果 = subprocess.run([sys.executable, "-c", "import sys; print(sys.stdout.encoding)"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    return ((结果.stdout or "") + (结果.stderr or "")).strip().lower()


def 跑闸门(脚本路径: str, env, 参数=()):
    """跑一遍闸门，把子进程输出**按 GBK 解码**。

    ⚠️ 这里不能按 UTF-8 解：子进程环境是 PYTHONIOENCODING=gbk，它写出来的是 GBK 字节。
       按 UTF-8 解会得到乱码，于是"输出里应该有「私有目录」"这类断言会**假失败**
       —— 第一版我就是这么错的：测试自己踩了「编码不匹配」的坑。
    """
    结果 = subprocess.run([sys.executable, 脚本路径, *参数],
                          capture_output=True, env=env, cwd=仓库根)
    原 = (结果.stdout or b"") + (结果.stderr or b"")
    return 原.decode("gbk", errors="replace"), 结果.returncode


def 测_gbk控制台():
    print("\n=== 二、GBK 控制台下不崩（中文 Windows 默认就是 GBK）===")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "gbk"
    env.pop("PYTHONUTF8", None)

    编码 = 子进程编码(env)
    if "gbk" not in 编码:
        print("  [SKIP] 这台机器没法把子进程 stdout 设成 gbk（探到 %r）—— 这条测试在这里没有意义" % 编码)
        return

    闸门 = os.path.join(本文件目录, "pre_publish_check.py")
    输出, 码 = 跑闸门(闸门, env, ("--跳过扫描",))
    断言("UnicodeEncodeError" not in 输出, "对法：GBK 下跑完、不崩",
         "输出尾部：" + 输出.strip()[-200:])
    断言(码 == 0, "对法：GBK 下退出码 0", "returncode=%d" % 码)
    断言("私有目录" in 输出, "对法：确实走到了检查步骤（不是没跑就退出）")

    # ---- 差分：把护栏废掉，必须真的崩。不然这条测试就是在自欺 ----
    源码 = open(闸门, "r", encoding="utf-8").read()
    废掉 = 源码.replace("for _流 in (sys.stdout, sys.stderr):", "for _流 in ():")
    if 废掉 == 源码:
        print("  [SKIP] 找不到护栏那句，跳过差分验证（护栏写法变了就得同步改这里）")
        return
    with tempfile.TemporaryDirectory() as 临时:
        坏脚本 = os.path.join(临时, "pre_publish_check_无护栏.py")
        with open(坏脚本, "w", encoding="utf-8") as f:
            f.write(废掉)
        坏输出, _ = 跑闸门(坏脚本, env, ("--跳过扫描",))
    断言("UnicodeEncodeError" in 坏输出, "错法：把护栏废掉之后必须真的崩（证明判据有区分力）",
         "输出：" + 坏输出.strip()[-200:])


def main() -> int:
    print("=" * 68)
    print("发布闸门回归测试")
    print("=" * 68)
    测_读命中数()
    测_gbk控制台()
    print("\n" + "=" * 68)
    print("通过 %d 项，失败 %d 项" % (通过, 失败))
    print("=" * 68)
    return 1 if 失败 else 0


if __name__ == "__main__":
    sys.exit(main())
