# -*- coding: utf-8 -*-
"""发布前闸门 —— 在 push 之前自动证明「仓库里没有真实业务数据」。

它做三件事，任何一件不过就**拒绝发布**（退出码非 0）：

  1. 调 tools/scan_sensitive.py 扫整个仓库（含私有词表），只要还有命中就拦下；
  2. 断言私有目录不在仓库里（防的是"手滑把 _private 挪进来"）；
  3. 断言仓库里没有常见凭证文件（.env / *.pem / *_credential* / 数据库.json …）。

为什么要有它：
    "我记得都脱敏了"不是判据。判据必须独立于我的记忆 ——
    所以脱敏这件事由工具在 push 前自动判一遍，而不是靠人再看一眼。

用法：
    python tools/pre_publish_check.py --词表 ..\\_private\\敏感词表.txt
    （词表由 tools/derive_tokens.py 从原项目文件名派生，只放私有目录）
"""

import argparse
import os
import re
import subprocess
import sys

# —— 控制台编码护栏 ——
# 中文 Windows 的 cmd / PowerShell 默认编码是 GBK，打印 ✓ / ✗ / ⛔ 这类符号会直接
# UnicodeEncodeError 崩掉（不是逻辑错，是编码错）。这里放宽标准输出的错误策略：
# 编不出来的符号退化成 ?，绝不因为一个符号把整个工具打断。
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(errors="replace")
    except Exception:
        pass

仓库根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
扫描器 = os.path.join(仓库根, "tools", "scan_sensitive.py")

禁入文件模式 = (
    ".env", ".pem", ".key", ".pfx", ".p12",
    "credential", "credentials", "密码", "口令", "数据库.json", "凭据",
)


def 读_gitignore(仓库根: str) -> list:
    """把 .gitignore 读成一串 glob，当作扫描器的 --排除。

    为什么要这么做：**"会不会被发布出去"的准确定义就是"git 会不会跟踪它"。**
    运行产物（演示日志、出纸记录）每次都变、也不该入库 ——
    如果闸门去扫它们，报告里永远有几条噪音，久了就没人看了。
    （机器上没装 git，所以自己解一遍；这里只用到最基础的几条语法。）
    """
    路径 = os.path.join(仓库根, ".gitignore")
    if not os.path.isfile(路径):
        return []
    规则 = []
    with open(路径, "r", encoding="utf-8") as f:
        for 行 in f:
            行 = 行.split("#")[0].strip()
            if not 行:
                continue
            行 = 行.lstrip("/").rstrip("/")
            if 行:
                规则.append(行)
    return 规则


def 检查私有目录():
    坏的 = []
    for 当前, 子目录, 文件们 in os.walk(仓库根):
        子目录[:] = [d for d in 子目录 if d != ".git"]
        for d in list(子目录):
            if d in ("_private", "private") or d.startswith("_private"):
                坏的.append(os.path.relpath(os.path.join(当前, d), 仓库根))
    return 坏的


def 检查凭证文件():
    坏的 = []
    for 当前, 子目录, 文件们 in os.walk(仓库根):
        子目录[:] = [d for d in 子目录 if d not in (".git", "__pycache__", ".venv", "venv")]
        for 名 in 文件们:
            小写 = 名.lower()
            if 小写.startswith(".env"):
                坏的.append(os.path.relpath(os.path.join(当前, 名), 仓库根))
                continue
            for 模式 in 禁入文件模式:
                if 模式 in 小写 and not 小写.endswith((".py", ".md")):
                    坏的.append(os.path.relpath(os.path.join(当前, 名), 仓库根))
                    break
    return 坏的


def main():
    p = argparse.ArgumentParser(description="发布前闸门：脱敏 + 私有目录 + 凭证文件")
    p.add_argument("--词表", help="敏感词表（私有目录里那份）")
    p.add_argument("--豁免", help="豁免表（私有目录里那份；报告会显示豁免了几条）")
    p.add_argument("--跳过扫描", action="store_true", help="只查目录/文件名（调试用）")
    a = p.parse_args()

    失败 = []

    print("=" * 68)
    print("发布前闸门")
    print("=" * 68)

    # --- 1. 私有目录不得在仓库里 ---
    私有 = 检查私有目录()
    if 私有:
        print("[1/3] 私有目录 ✗ 仓库里出现了：%s" % "、".join(私有))
        失败.append("私有目录混进仓库")
    else:
        print("[1/3] 私有目录 ✓ 仓库里没有 _private 之类的目录")

    # --- 2. 凭证文件不得在仓库里 ---
    凭证 = 检查凭证文件()
    if 凭证:
        print("[2/3] 凭证文件 ✗ 发现 %d 个可疑文件：" % len(凭证))
        for x in 凭证[:20]:
            print("        %s" % x)
        失败.append("仓库里有凭证类文件")
    else:
        print("[2/3] 凭证文件 ✓ 没有 .env / 密钥 / 凭据类文件")

    # --- 3. 脱敏扫描 ---
    if a.跳过扫描:
        print("[3/3] 脱敏扫描 —— 已按参数跳过")
    else:
        命令 = [sys.executable, "-X", "utf8", 扫描器, 仓库根, "-o", os.path.join(仓库根, "..", "_private", "脱敏清单_作品集.txt"), "--self-check"]
        # 排除闸门自己和扫描器：这两个文件里写的是"要抓什么"的**模式定义**
        # （`https?://`、`\\\\` 这类正则字面量），不是数据。不排除就会自己命中自己。
        # ⚠️ 这份排除名单是硬编码的：谁要往这里加文件，等于给自己开一道口子，必须过 review。
        命令 += ["--排除", "tools/scan_sensitive.py", "--排除", "tools/pre_publish_check.py"]
        # 工具目录里的中文是"词表与通用词的定义"，不是业务数据；让**敏感词表**这一类
        # 去扫它们必定自己命中自己。所以只对 tools/ 关掉词表这一类，
        # 凭证 / 地址 / 料号 / 公司名在 tools/ 下**照常检查**。
        命令 += ["--词表排除", "tools/*.py"]
        # .gitignore 里列的东西 = 不会发布的东西，不必进报告（否则报告全是运行产物的噪音）
        for 规则 in 读_gitignore(仓库根):
            命令 += ["--排除", 规则]
        if a.词表:
            命令 += ["--词表", a.词表]
        if a.豁免:
            命令 += ["--豁免", a.豁免]
        结果 = subprocess.run(命令, capture_output=True, text=True, encoding="utf-8", errors="replace")
        输出 = (结果.stdout or "") + (结果.stderr or "")
        if 结果.returncode != 0:
            print("[3/3] 脱敏扫描 ✗ 扫描器返回 %d，先修扫描器自己" % 结果.returncode)
            print(输出)
            失败.append("扫描器自身失败")
        else:
            # 从扫描器输出里读命中总数，>0 就拦下。
            #
            # ⚠️ 数字**必须锚定在标签上**，绝对不能"把这一行里的数字全拼起来"。
            #    扫描器那句是：`报告已写入：<完整路径>（N 条命中）` —— 路径就在同一行上。
            #    实测（把仓库 clone 到带数字的目录里跑）：
            #      路径 clone_20261013_184702 → 拼出来的数字是 202610131847020，
            #      于是 **0 命中被判成天文数字命中、拒绝发布**。
            #    仓库恰好放在不带数字的路径下时，这个写法"碰巧"是对的 —— 这种
            #    "静默地依赖巧合"的判据正是要避免的东西。
            #    判据锚定标签后，路径里有什么数字都不影响结果。
            命中数 = None
            for 行 in 输出.splitlines():
                匹配 = re.search(r"命中总数\s*[:：]\s*(\d+)", 行) or re.search(r"(\d+)\s*条命中", 行)
                if 匹配:
                    命中数 = int(匹配.group(1))
            if 命中数 is None:
                print("[3/3] 脱敏扫描 ✗ 读不出命中数，不敢放行")
                失败.append("读不到扫描结果")
            elif 命中数 > 0:
                print("[3/3] 脱敏扫描 ✗ 还有 %d 处命中 —— 报告见 _private\\脱敏清单_作品集.txt" % 命中数)
                失败.append("仍有敏感命中 %d 处" % 命中数)
            else:
                print("[3/3] 脱敏扫描 ✓ 0 处命中")

    print("-" * 68)
    if 失败:
        print("结论：⛔ 不许发布 —— " + "；".join(失败))
        return 1
    print("结论：✅ 可以发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
