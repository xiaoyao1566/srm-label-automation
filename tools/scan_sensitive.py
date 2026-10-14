# -*- coding: utf-8 -*-
"""脱敏面扫描器 —— 只报「位置 + 类别 + 长度」，绝不回报任何命中原文。

为什么这样设计（这是踩过坑的结论，不是洁癖）：

    一个"帮你找敏感信息"的工具，本身就是最大的泄漏面。
    只要报告里带任何形式的原文片段（"预览"、"开头 6 位"、"上下文"），
    这份报告就变成了第二份泄漏源 —— 找敏感信息的工具反而制造了敏感文件。

    所以本工具的硬约束：
      1. 报告里**没有**任何命中内容的字符，只有 文件 / 行 / 类别 / 长度。
         连敏感词命中也只写「词表第 N 条」—— 词本身留在私有的词表文件里。
      2. 文件路径一律相对化 —— 绝对路径里常常带着公司名和共享盘名。
      3. `--self-check` 会拿扫描结果回头逐条断言「报告正文（去掉路径部分后）
         不含任何命中原文」—— 验证的是工具自己的**输出**，而不是它自己的逻辑。

    ⚠️ 一条必须知道的边界：**报告里的文件名本身就可能带客户简称**
       （`<客户>打标签.py`）。这不是泄漏，是报告要有用就避不开的；
       代价是**这份报告只能放私有目录，不能入库、不能外发**。
       self-check 因此只保证"内容"那一层（去掉路径后再断言）。

用法：
    python tools/scan_sensitive.py <要扫的目录> [-o 报告.txt] [--词表 私有/敏感词表.txt] [--self-check]
    python tools/scan_sensitive.py --selftest        # 用内置假样本自测，不碰真实代码

退出码：0 = 扫描完成；2 = 自我检验失败（报告里混进了原文，必须当成 bug 修）。
"""

import argparse
import fnmatch
import os
import re
import sys

# —— 控制台编码护栏 ——
# 中文 Windows 的 cmd / PowerShell 默认编码是 GBK，打印 ⚠ / ✔ / ✘ 这类符号会直接
# UnicodeEncodeError 崩掉。这里放宽标准输出的错误策略：编不出来的符号退化成 ?，
# 绝不因为一个符号把整个扫描打断。
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------- 类别定义
# 每一类都是「宁可多报、不可漏报」：产出是"待人工确认清单"，
# 真正判定要不要脱敏由人来做，工具只负责把范围框出来、别漏。
#
# 但"宁可多报"有个限度：**假命中比漏报更能坏事** ——
# 一份总在报警的报告会训练人直接忽略它。所以下面每条判据都刻意写窄：
类别 = [
    # 网址：忽略三类**不是数据**的 http 串：
    #   ① 回环地址 —— 靶站/演示本来就跑在 127.0.0.1；
    #   ② XML/SVG 命名空间 —— `xmlns="http://www.w3.org/2000/svg"` 是标签规范的一部分，
    #      每个 SVG 里都有一份，认它等于每次都在报告里刷一条假命中。
    #   ③ 仓库自己的**公开**链接 —— README 顶上的 CI badge / license badge 指向
    #      github.com 与 shields.io。那是公开站点，不是客户门户地址；
    #      不放过它们，每次发布都得为它们写一条假豁免，反而把豁免表用坏了。
    #      白名单只加"公开站点"，客户门户那种专有域名照旧一律命中。
    #      ⚠️ 负向断言是锚在 `://` 之后的 —— 所以**带子域的也要单独列**
    #         （`shields\.io` 挡不住 `img.shields.io`，实测漏了一条）。
    ("URL网址",      re.compile(r"https?://(?!127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]|www\.w3\.org|schemas\.|github\.com|githubusercontent\.com|shields\.io|img\.shields\.io)[^\s\"'<>)\]}]+")),
    ("UNC网络路径",  re.compile(r"\\\\[^\s\"'<>)\]}]+")),
    ("绝对路径",     re.compile(r"[A-Za-z]:\\[^\s\"'<>)\]}]+")),
    ("公司名",       re.compile(r"[\u4e00-\u9fa5]{2,12}(?:有限公司|股份有限公司|集团)")),
    # 凭证只认「赋值形态」且**值必须像字面量**（引号里的东西，或纯数字）：
    #   ✔ `密码 = "xxx"`   ✔ `"password": "xxx"`   ✔ `口令: 1`
    #   ✘ `口令: str = ""`（类型标注）  ✘ 文档里写「口令不落盘」（散文）
    # 一开始写成"出现这几个字眼就算"，报了 228 处、九成是散文，
    # 收紧到"赋值 + 字面量"之后剩个位数 —— 那才是真正要处理的。
    ("凭证赋值",     re.compile(r"(?i)(密码|口令|passwd|password|pwd|secret|token|api[_-]?key|连接串|凭据)\s*[:=]\s*(['\"][^'\"]{1,200}['\"]|\d{1,20})")),
    # 账号单独一类：账号常常只有 7 位，够不着"长数字串"那条线。
    ("账号赋值",     re.compile(r"(?i)(账号|帐号|用户名|account|username)\s*[:=]\s*['\"][^'\"]{1,60}['\"]")),
    # 长数字串（料号/订单号/单据号）。
    ("长数字串",     re.compile(r"(?<![.\d])\d{8,}(?![.\d])")),
    # IP：加负向断言，否则**版本号会被当成 IP**（`opencv-python==4.11.0.86` 实测中招）。
    ("IP地址",       re.compile(r"(?<![=.\d])(?!127\.0\.0\.1|0\.0\.0\.0)(?:\d{1,3}\.){3}\d{1,3}(?![.\d])")),
    ("邮箱",         re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
]

词表类别名 = "敏感词表"

# 自我检验时忽略过短的命中：像 "1" 这种单字符必然会在报告的别处出现
# （行号、列宽…），拿它做断言只会制造假失败，糊掉真问题。
自我检验最短长度 = 6


def 读词表(路径):
    """词表格式：`序号<TAB>词`，# 开头是注释。返回 [(序号, 词)]。"""
    词 = []
    if not 路径:
        return 词
    with open(路径, "r", encoding="utf-8") as f:
        for 行 in f:
            行 = 行.rstrip("\n")
            if not 行 or 行.startswith("#"):
                continue
            段 = 行.split("\t")
            if len(段) == 2 and 段[0].isdigit():
                词.append((int(段[0]), 段[1]))
    return 词


def 词指纹(词: str) -> str:
    """词的短指纹。豁免表按它索引，而不是按"词表第 N 条"。

    为什么不能用序号：词表是从文件名**自动派生**的，
    原项目加一个文件、通用词表调一下，所有序号就会漂 ——
    实测踩到过：加完几个通用词后，原来豁免第 71 条的规则落到了**另一个词**头上，
    于是该豁免的没豁免、不该豁免的被放过。指纹只跟词本身有关，不受顺序影响。
    """
    import hashlib
    return hashlib.sha256(词.encode("utf-8")).hexdigest()[:8]


def 读豁免(路径):
    """豁免表格式：`词指纹<TAB>理由`。

    ⚠️ 为什么会需要这个东西：词表是从**文件名**自动派生的，里面难免混进
       "通用汉语短语"（实测撞到过「记一笔」这种）—— 它出现在正文里不是泄漏。
       但**必须带理由、必须放在私有目录、而且报告里要显示豁免了几条** ——
       悄悄地少查几条，就等于给自己开了个没人看得见的后门。

       豁免是**全局**的（豁免一个词，它在所有文件里都不再报），
       所以只允许用来豁免"确认是通用词、不是专名"的条目。
       如果某个词的豁免理由是"它只在我这个文件里" —— 那说明该改的是文案，不是加豁免。
    """
    豁免 = set()
    if not 路径:
        return 豁免
    with open(路径, "r", encoding="utf-8") as f:
        for 行 in f:
            行 = 行.rstrip("\n")
            if not 行 or 行.startswith("#"):
                continue
            段 = 行.split("\t")
            if 段 and 段[0].strip():
                豁免.add(段[0].strip())
    return 豁免


def 逐行扫描(路径, 相对根, 词表, 查词表=True, 豁免=None):
    """产出 6 元组：相对路径 / 行号 / 类别 / 命中长度 / 命中原文 / 报告里显示的标签。

    命中原文仅在本函数与自我检验之间流转，**永不进入报告**。
    """
    try:
        with open(路径, "r", encoding="utf-8", errors="replace") as f:
            内容 = f.read()
    except Exception:
        return
    相对 = os.path.relpath(路径, 相对根)
    for 行号, 行 in enumerate(内容.splitlines(), 1):
        for 名, 正则 in 类别:
            for m in 正则.finditer(行):
                yield 相对, 行号, 名, len(m.group(0)), m.group(0), 名
        if not 查词表:
            continue
        for 序号, 词 in 词表:
            指纹 = 词指纹(词)
            if 豁免 and 指纹 in 豁免:
                continue
            if 词 in 行:
                yield (相对, 行号, 词表类别名, len(词), 词,
                       "%s 第 %d 条(指纹 %s)" % (词表类别名, 序号, 指纹))


def 收集(根目录, 排除=()):
    后缀 = (".py", ".js", ".ts", ".json", ".md", ".txt", ".bat", ".vbs", ".html", ".css", ".jsonl", ".yml", ".yaml", ".cfg", ".ini")
    for 当前, 子目录, 文件们 in os.walk(根目录):
        子目录[:] = [d for d in 子目录 if d not in ("__pycache__", ".git", "node_modules", ".venv")]
        for 名 in sorted(文件们):
            if os.path.splitext(名)[1].lower() not in 后缀:
                continue
            相对 = os.path.relpath(os.path.join(当前, 名), 根目录).replace(os.sep, "/")
            if any(fnmatch.fnmatch(相对, pat) or fnmatch.fnmatch(名, pat) for pat in 排除):
                continue
            yield os.path.join(当前, 名), 相对


def 生成报告(根目录, 词表, 排除=(), 词表排除=(), 豁免=None):
    条目 = []
    文件集合 = set()
    for 路径, 相对 in 收集(根目录, 排除):
        文件集合.add(路径)
        # 词表类别可以按路径单独关掉（见 --词表排除 的说明）：
        # 工具文件里的中文是"词表与模式的**定义**"，不是业务数据，
        # 让词表类别去扫它们只会自己命中自己，制造假告警。
        # 注意只关词表这一类 —— 凭证/地址/料号在同样这些文件里**照常检查**。
        查词表 = not any(fnmatch.fnmatch(相对, pat) or fnmatch.fnmatch(os.path.basename(相对), pat) for pat in 词表排除)
        条目.extend(逐行扫描(路径, 根目录, 词表, 查词表, 豁免))
    条目.sort(key=lambda x: (x[2], x[0], x[1]))

    行 = []
    行.append("脱敏面扫描报告")
    行.append("=" * 72)
    行.append("说明：本报告刻意不含任何命中原文 —— 只有 位置 / 类别 / 长度。")
    行.append("      敏感词命中也只标「词表第 N 条」，词本身在私有词表文件里。")
    行.append("      这里是「待人工确认清单」，不是「泄漏清单」。")
    行.append("")
    行.append("被扫描文件数：%d   命中总数：%d" % (len(文件集合), len(条目)))
    行.append("")
    行.append("按类别汇总：")

    统计 = {}
    for 相对, 行号, 类, 长度, 原文, 标签 in 条目:
        统计.setdefault(类, []).append((相对, 行号, 标签))

    类别顺序 = [名 for 名, _ in 类别] + [词表类别名]
    for 名 in 类别顺序:
        if 名 in 统计:
            文件数 = len(set(p for p, _, _ in 统计[名]))
            行.append("  %-12s 命中 %5d 处，散落在 %d 个文件" % (名, len(统计[名]), 文件数))
    行.append("")
    for 名 in 类别顺序:
        if 名 not in 统计:
            continue
        行.append("-" * 72)
        行.append("【%s】共 %d 处" % (名, len(统计[名])))
        for 相对, 行号, 标签 in 统计[名]:
            尾巴 = "" if 标签 == 名 else "   ← %s" % 标签
            行.append("    %s : 第 %d 行%s" % (相对, 行号, 尾巴))
        行.append("")
    return "\n".join(行), 条目


def 自我检验(报告文本, 条目):
    """核心：验证**输出**。断言报告正文里不含任何一条命中原文。

    ⚠️ 一个必须说清楚的边界：报告里**有文件名**，而文件名本身就常常带客户简称
    （`<客户>打标签.py`）。这不是"泄漏"，是这份报告的用途决定的 ——
    没有文件名就没法照着改。代价是：**报告只能放私有目录，不能入库、不能外发。**

    所以自我检验只保证「内容」这一层：把每行里 ` : 第 N 行` 之前的路径部分去掉之后再断言。
    这样它拦得住"报告里混进了凭证/地址/料号原文"，同时不会因为路径里有客户名而误报。
    """
    去路径文本 = []
    for 行 in 报告文本.splitlines():
        去路径文本.append(行.split(" : 第 ")[-1])
    文本 = "\n".join(去路径文本)

    漏 = []
    for 相对, 行号, 名, 长度, 原文, 标签 in 条目:
        if len(原文) < 自我检验最短长度:
            continue
        if 原文 in 文本:
            漏.append((相对, 行号, 名, 长度))
    return 漏


def 内置自测():
    """不碰真实代码：造一段假样本，验证「报告不含原文」这条约束真的成立。"""
    import tempfile
    假样本 = (
        '网址 = "https://secret.example.invalid/portal"\n'
        '本地 = "http://127.0.0.1:8770"          # 回环地址不算业务数据\n'
        '版本 = "opencv-python==4.11.0.86"        # 版本号不该被当成 IP\n'
        '路径 = r"\\\\FILESERVER\\share\\客户资料"\n'
        '账号 = "1000035"\n'
        '口令 = "Passw0rd-ShouldNeverAppear"\n'
        '标注: str = ""                            # 类型标注不是凭证\n'
        '客户简称 = "假客户甲"  # 应被词表命中\n'
    )
    with tempfile.TemporaryDirectory() as 临时:
        with open(os.path.join(临时, "sample.py"), "w", encoding="utf-8") as f:
            f.write(假样本)
        词表文件 = os.path.join(临时, "tokens.txt")
        with open(词表文件, "w", encoding="utf-8") as f:
            f.write("# 测试词表\n1\t假客户甲\n")
        词表 = 读词表(词表文件)
        报告, 条目 = 生成报告(临时, 词表)

    assert 条目, "自测失败：假样本一条都没扫到，说明类别正则全废了"
    不该出现 = ["secret.example.invalid", "FILESERVER", "1000035",
                "Passw0rd-ShouldNeverAppear", "假客户甲"]
    混进来的 = [s for s in 不该出现 if s in 报告]
    assert not 混进来的, "自测失败：报告里混进了原文，共 %d 条" % len(混进来的)
    assert any(类 == 词表类别名 for _, _, 类, _, _, _ in 条目), "自测失败：词表类别没生效"

    # 假命中必须被挡住 —— 这几条正是"报告总在报警就会被忽略"的来源
    类别计数 = {}
    for _, _, 类, _, _, _ in 条目:
        类别计数[类] = 类别计数.get(类, 0) + 1
    assert 类别计数.get("URL网址", 0) == 1, "自测失败：回环地址被当成网址了（应只识别 1 条真实网址）"
    assert 类别计数.get("IP地址", 0) == 0, "自测失败：版本号被当成 IP 了"
    assert 类别计数.get("账号赋值", 0) == 1, "自测失败：账号赋值没识别出来"
    assert 类别计数.get("凭证赋值", 0) == 1, "自测失败：凭证赋值应只识别 1 条（类型标注不算）"

    漏 = 自我检验(报告, 条目)
    assert not 漏, "自测失败：自我检验报了 %d 条泄漏" % len(漏)
    print("[自测] 通过：%d 条命中（含词表）全部只报位置，报告正文零原文" % len(条目))
    print("[自测] 覆盖类别：%s" % "、".join(sorted(set(n for _, _, n, _, _, _ in 条目))))
    print("[自测] 假命中拦截：回环地址 / 版本号 / 类型标注 均已排除")
    return 0


def main():
    p = argparse.ArgumentParser(description="脱敏面扫描：只报位置，不报原文")
    p.add_argument("目录", nargs="?", help="要扫描的目录")
    p.add_argument("-o", "--输出", help="报告写到哪个文件（默认打到屏幕）")
    p.add_argument("--词表", help="敏感词表文件（由 derive_tokens.py 生成，放私有目录）")
    p.add_argument("--排除", action="append", default=[], help="要跳过的文件（glob，可重复）。用于排除'模式定义本身'这类非数据文件")
    p.add_argument("--词表排除", action="append", default=[], help="这些文件只跳过「敏感词表」这一类，其他类别照常查（用于工具目录里「词表定义」的自指）")
    p.add_argument("--豁免", help="豁免表（`词表序号<TAB>理由`，放私有目录）。用于豁免确认是通用词的条目；报告会显示豁免了几条")
    p.add_argument("--self-check", action="store_true", help="扫完回头断言报告不含原文")
    p.add_argument("--selftest", action="store_true", help="用内置假样本自测，不碰真实代码")
    a = p.parse_args()

    if a.selftest:
        return 内置自测()
    if not a.目录:
        p.error("要么给一个目录，要么用 --selftest")

    词表 = 读词表(a.词表)
    豁免 = 读豁免(a.豁免)
    报告, 条目 = 生成报告(a.目录, 词表, a.排除, a.词表排除, 豁免)

    if a.排除:
        print("[排除] 已跳过 %d 条 glob：%s" % (len(a.排除), "、".join(a.排除)))
    if a.词表排除:
        print("[词表排除] 这些文件不查敏感词表（其他类别照查）：%s" % "、".join(a.词表排除))
    if 豁免:
        # 豁免必须"看得见" —— 报告里也要写一行，不能悄悄少查
        print("[豁免] 已豁免 %d 条词表条目（理由见豁免表）" % len(豁免))
        报告 = 报告.replace("被扫描文件数", "已豁免词表条目 %d 条（详见豁免表）\n被扫描文件数" % len(豁免), 1)

    if a.self_check:
        漏 = 自我检验(报告, 条目)
        if 漏:
            print("[自我检验] 失败：报告里混进了 %d 条原文 —— 这是工具的 bug，必须先修" % len(漏))
            for 相对, 行号, 名, 长度 in 漏[:10]:
                print("    %s : 第 %d 行 [%s] 长度 %d" % (相对, 行号, 名, 长度))
            return 2
        print("[自我检验] 通过：报告正文不含任何命中原文")

    if a.输出:
        os.makedirs(os.path.dirname(os.path.abspath(a.输出)), exist_ok=True)
        with open(a.输出, "w", encoding="utf-8") as f:
            f.write(报告)
        print("报告已写入：%s（%d 条命中）" % (a.输出, len(条目)))
    else:
        print(报告)
    return 0


if __name__ == "__main__":
    sys.exit(main())
