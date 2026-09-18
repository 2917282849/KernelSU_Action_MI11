#!/usr/bin/env python3
"""
从「我们编译出的内核 Image」里抽出 IKCONFIG（内嵌 .config），
与「原厂 boot.img 里抽出的原厂 config」逐项对比。

== 为什么必须校验 ==
原厂内核启用了 LTO + THINLTO + CFI_CLANG + SHADOW_CALL_STACK，
且 /vendor/lib/modules 与 ramdisk 里的 *_dlkm.ko 都是配套编译的。
一旦配置不一致，会导致：
  - SHADOW_CALL_STACK 寄存器 ABI 不一致 -> 模块崩溃
  - CFI_CLANG 类型哈希不一致           -> CFI trap -> panic
  - genksyms 符号 CRC 不一致           -> 模块被拒绝加载
三者任一都足以开不了机。所以必须在编译后立即校验。

== 差异的分类（重要）==
  1) 预期内差异   ：我们有意改的项（LOCALVERSION / KSU / 编译器指纹）
  2) 源码树不支持 ：该符号在【当前源码树里根本不存在】。
                    本树是 QCOM/AOSP 基线，不含小米 MIUI 私有补丁
                    （MIHW/MILLET/MIGT/EMERGENCY_MEMORY/BOOTUP_RECLAIM ...），
                    这些项无法通过改配置解决，不属于回归。
  3) 真实差异     ：符号存在但取值不同 —— 【需要关注】。
                    上一次就是这类问题：整份追加原厂 defconfig 导致
                    925 处 "override: reassigning" 静默覆盖，产生 108 项
                    非预期差异（如把原厂 DEBUG_FS=n 改成 y）。

只有 CRITICAL 列表里的项不一致才判失败（那是真正破坏模块 ABI 的项）。

用法：
  python3 verify_config.py <我们编译的 Image> <原厂 config 文本> [内核源码根目录]

退出码：
  0 = 关键项全部一致
  1 = 有关键项不一致（打印 ::error:: 供 GitHub Actions 高亮）
"""

import os
import re
import sys

# 关键项：这些必须与原厂一致，否则模块 ABI 不兼容
CRITICAL = [
    "CONFIG_LTO",
    "CONFIG_LTO_CLANG",
    "CONFIG_LTO_NONE",
    "CONFIG_THINLTO",
    "CONFIG_CFI_CLANG",
    "CONFIG_CFI_CLANG_SHADOW",
    "CONFIG_SHADOW_CALL_STACK",
    "CONFIG_SHADOW_CALL_STACK_VMAP",
    "CONFIG_MODVERSIONS",
    "CONFIG_MODULES",
    "CONFIG_MODULE_SIG",
    "CONFIG_MODULE_SIG_FORCE",
    "CONFIG_MODULE_SIG_ALL",
    "CONFIG_KPROBES",
    "CONFIG_TRIM_UNUSED_KSYMS",
    "CONFIG_RANDSTRUCT",
    "CONFIG_GCC_PLUGIN_RANDSTRUCT",
    "CONFIG_GCC_PLUGINS",
    # 原子指令实现（原厂 ARM64_LSE_ATOMICS=y）。
    # 注意：旧版 Linux 把 prompt 放在 ARM64_LSE_ATOMICS 上，新版放在
    # ARM64_USE_LSE_ATOMICS 上（两者互为 default）。原厂 config 里
    # 只有 ARM64_LSE_ATOMICS，所以比对它；USE 那个比不出结果。
    "CONFIG_ARM64_LSE_ATOMICS",
    "CONFIG_ARM64_USE_LSE_ATOMICS",
]

# 允许的差异（我们有意修改的项）
ALLOWED_DIFF_KEYS = {
    "CONFIG_LOCALVERSION",       # 改成完整后缀以对齐 vermagic
    "CONFIG_LOCALVERSION_AUTO",  # 关掉
    "CONFIG_KSU",                # 加 KernelSU
    "CONFIG_FRAME_WARN",         # 0 以规避 QCOM WiFi 栈帧告警
    "CONFIG_WERROR",             # 5.4 无此项
    "CONFIG_CC_VERSION_TEXT",    # 编译器 build 号（r383902b vs r383902b1）
    "CONFIG_CLANG_VERSION",
    "CONFIG_GCC_VERSION",
    "CONFIG_AS_VERSION",
    "CONFIG_LD_VERSION",
    "CONFIG_LLD_VERSION",
}

# 值对比时忽略的等价写法
def norm(v):
    return v


RE_CONFIG_NAME = re.compile(r"^config\s+([A-Za-z0-9_]+)\s*$")


def kconfig_symbols(root):
    """扫源码树，收集所有 Kconfig 里定义的配置项名（CONFIG_XXX）。

    用于区分「源码树没有这个符号」和「符号存在但值不同」。
    """
    names = set()
    if not os.path.isdir(root):
        return None
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not (fn.startswith("Kconfig") or fn.startswith("Config.in")):
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8",
                          errors="replace") as fh:
                    for ln in fh:
                        m = RE_CONFIG_NAME.match(ln)
                        if m:
                            names.add("CONFIG_" + m.group(1))
            except OSError:
                continue
    return names


def load_ikconfig(path):
    """从内核 Image 里抽出内嵌 .config"""
    import gzip
    with open(path, "rb") as fh:
        data = fh.read()
    i = data.find(b"IKCFG_ST")
    j = data.find(b"IKCFG_ED")
    if i < 0 or j < 0:
        raise RuntimeError(
            f"{path}: 找不到 IKCFG_ST/IKCFG_ED（CONFIG_IKCONFIG 未启用？）")
    return gzip.decompress(data[i + 8: j]).decode("utf-8", errors="replace")


def to_dict(text):
    d = {}
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("#"):
            if ln.startswith("# CONFIG_") and ln.endswith(" is not set"):
                d[ln[2: ln.index(" is not set")]] = "n"
            continue
        if "=" in ln:
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    img, stock_cfg = sys.argv[1], sys.argv[2]
    src_root = sys.argv[3] if len(sys.argv) > 3 else "."

    new = to_dict(load_ikconfig(img))
    with open(stock_cfg, encoding="utf-8", errors="replace") as fh:
        stock = to_dict(fh.read())

    tree = kconfig_symbols(src_root)
    if tree is None:
        print(f"[!] 源码目录 {src_root} 不存在，跳过「源码树不支持」分类")
        tree = set()
    else:
        print(f"[i] 源码树 Kconfig 中共有 {len(tree)} 个配置项")

    keys = sorted(set(new) | set(stock))
    diffs = [(k, stock.get(k), new.get(k)) for k in keys
             if stock.get(k) != new.get(k)]

    allowed, unsupported, real = [], [], []
    for k, a, b in diffs:
        line = f"  {k}\n      原厂: {a}\n      本次: {b}"
        if k in ALLOWED_DIFF_KEYS:
            allowed.append(line)
        elif k not in tree:
            unsupported.append(line)
        else:
            real.append(line)

    print("=" * 78)
    print(f"原厂 config 项数: {len(stock)}   本次编译 Image 项数: {len(new)}")
    print(f"差异总数: {len(diffs)}")
    print(f"  ├─ 预期内（我们有意改的）    : {len(allowed)}")
    print(f"  ├─ 源码树不支持（无法避免）  : {len(unsupported)}")
    print(f"  └─ 真实差异（需关注）        : {len(real)}")
    print("=" * 78)

    if allowed:
        print(f"\n--- [1] 预期内差异（{len(allowed)} 项）---")
        for line in allowed:
            print(line)

    if unsupported:
        print(f"\n--- [2] 源码树不支持（{len(unsupported)} 项）---")
        print("本源码树为 QCOM/AOSP 基线，不含小米 MIUI 私有补丁。")
        print("这些符号在树里不存在，无法通过改配置解决，不属于回归。")
        for line in unsupported:
            print(line)

    if real:
        print(f"\n--- [3] 真实差异（{len(real)} 项）---")
        print("⚠️ 这些符号在树里存在但取值不同，可能影响功能，请逐项检查。")
        for line in real:
            print(line)

    # ---- 关键 ABI 项强校验 ----
    print("\n=== 关键 ABI 项校验 ===")
    failed = False
    for k in CRITICAL:
        a, b = stock.get(k), new.get(k)
        if a != b:
            # 新旧命名差异：ARM64_LSE_ATOMICS <-> ARM64_USE_LSE_ATOMICS
            # 两者互为 default，只要其中一个为 y 即功能等价。
            pair = {
                "CONFIG_ARM64_LSE_ATOMICS": "CONFIG_ARM64_USE_LSE_ATOMICS",
                "CONFIG_ARM64_USE_LSE_ATOMICS": "CONFIG_ARM64_LSE_ATOMICS",
            }.get(k)
            if pair and (a == "y" or b == "y") and \
               (stock.get(pair) == "y" or new.get(pair) == "y"):
                print(f"  [ OK ] {k} 命名差异但功能等价 "
                      f"(原厂={a} 本次={b}, 配对 {pair}: "
                      f"原厂={stock.get(pair)} 本次={new.get(pair)})")
                continue
            print(f"  [FAIL] {k}: 原厂={a} 本次={b}")
            print(f"::error::关键配置不一致 {k}: 原厂={a} 本次={b}")
            failed = True
        else:
            print(f"  [ OK ] {k} = {a}")

    print("\n=== 内核版本号（vermagic 基础）===")
    print(f"  原厂 CONFIG_LOCALVERSION = {stock.get('CONFIG_LOCALVERSION')} "
          f"(+ LOCALVERSION_AUTO={stock.get('CONFIG_LOCALVERSION_AUTO')})")
    print(f"  本次 CONFIG_LOCALVERSION = {new.get('CONFIG_LOCALVERSION')} "
          f"(+ LOCALVERSION_AUTO={new.get('CONFIG_LOCALVERSION_AUTO')})")

    if failed:
        print("\n::error::关键 ABI 配置与原厂不一致，刷入会导致模块加载失败/"
              "CFI trap/内核崩溃。禁止发布。")
        return 1

    if real:
        print(f"\n::warning::存在 {len(real)} 项真实配置差异，请人工确认后再刷入。")

    print("\n[OK] 所有关键 ABI 项与原厂一致，模块兼容性检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
