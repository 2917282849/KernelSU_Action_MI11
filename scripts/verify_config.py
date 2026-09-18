#!/usr/bin/env python3
"""
从「我们编译出的内核 Image」里抽出 IKCONFIG（内嵌 .config），
与「原厂 boot.img 里抽出的原厂 config」逐项对比。

目的：
  原厂内核启用了 LTO + CFI_CLANG + SHADOW_CALL_STACK，
  且 /vendor/lib/modules 与 ramdisk 里的 *_dlkm.ko 都是配套编译的。
  一旦配置不一致，会导致：
    - SHADOW_CALL_STACK 寄存器 ABI 不一致 -> 模块崩溃
    - CFI_CLANG 类型哈希不一致 -> CFI trap -> panic
    - genksyms 符号 CRC 不一致 -> 模块被拒绝加载
  三者任一都足以开不了机。所以必须在编译后立即校验。

用法：
  python3 verify_config.py <我们编译的 Image> <原厂 config 文本>

退出码：
  0 = 关键项全部一致
  1 = 有关键项不一致（会打印 ::error:: 供 GitHub Actions 高亮）
"""

import gzip
import sys

# 关键项：这些必须与原厂完全一致，否则模块 ABI 不兼容
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
    "CONFIG_KPROBES",
    "CONFIG_TRIM_UNUSED_KSYMS",
    "CONFIG_RANDSTRUCT",
    "CONFIG_GCC_PLUGIN_RANDSTRUCT",
    "CONFIG_GCC_PLUGINS",
    "CONFIG_ARM64_USE_LSE_ATOMICS",
]

# 允许的差异（我们有意修改的项）
ALLOWED_DIFF_KEYS = {
    "CONFIG_LOCALVERSION",      # 改成完整后缀以对齐 vermagic
    "CONFIG_LOCALVERSION_AUTO",  # 关掉
    "CONFIG_KSU",               # 加 KernelSU
    "CONFIG_FRAME_WARN",        # 0 以规避 QCOM WiFi 栈帧告警
    "CONFIG_WERROR",            # 5.4 无此项
    "CONFIG_CC_VERSION_TEXT",   # 编译器 build 号（r383902b vs r383902b1）
    "CONFIG_CLANG_VERSION",
    "CONFIG_GCC_VERSION",
    "CONFIG_CC_VERSION_TEXT",
}


def load_ikconfig(path):
    """从内核 Image 里抽出内嵌 .config"""
    with open(path, "rb") as fh:
        data = fh.read()
    i = data.find(b"IKCFG_ST")
    j = data.find(b"IKCFG_ED")
    if i < 0 or j < 0:
        raise RuntimeError(f"{path}: 找不到 IKCFG_ST/IKCFG_ED（CONFIG_IKCONFIG 未启用？）")
    return gzip.decompress(data[i + 8 : j]).decode("utf-8", errors="replace")


def to_dict(text):
    d = {}
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            # 形如 "# CONFIG_FOO is not set"
            if ln.startswith("# CONFIG_") and ln.endswith(" is not set"):
                d[ln[2 : ln.index(" is not set")]] = "n"
            continue
        if "=" in ln:
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    new = to_dict(load_ikconfig(sys.argv[1]))
    with open(sys.argv[2], encoding="utf-8", errors="replace") as fh:
        stock = to_dict(fh.read())

    keys = sorted(set(new) | set(stock))
    diffs = []
    for k in keys:
        a, b = stock.get(k), new.get(k)
        if a != b:
            diffs.append((k, a, b))

    print("=" * 78)
    print(f"原厂 config 项数: {len(stock)}   本次编译 Image 项数: {len(new)}")
    print(f"差异项数: {len(diffs)}")
    print("=" * 78)

    critical_bad = []
    benign = []
    for k, a, b in diffs:
        line = f"  {k}\n      原厂: {a}\n      本次: {b}"
        if k in ALLOWED_DIFF_KEYS:
            benign.append(line)
        else:
            critical_bad.append(line)

    if benign:
        print(f"\n--- 预期内的差异（{len(benign)} 项，可忽略）---")
        for line in benign:
            print(line)

    if critical_bad:
        print(f"\n--- ⚠️ 非预期差异（{len(critical_bad)} 项）---")
        for line in critical_bad:
            print(line)

    # 关键项单独强校验
    print("\n=== 关键 ABI 项校验 ===")
    failed = False
    for k in CRITICAL:
        a, b = stock.get(k), new.get(k)
        if a != b:
            print(f"  [FAIL] {k}: 原厂={a} 本次={b}")
            print(f"::error::关键配置不一致 {k}: 原厂={a} 本次={b}")
            failed = True
        else:
            print(f"  [ OK ] {k} = {a}")

    print("\n=== 内核版本号（vermagic 基础）===")
    lv_new = new.get("CONFIG_LOCALVERSION")
    lv_stock = stock.get("CONFIG_LOCALVERSION")
    print(f"  原厂 CONFIG_LOCALVERSION = {lv_stock} (+ LOCALVERSION_AUTO={stock.get('CONFIG_LOCALVERSION_AUTO')})")
    print(f"  本次 CONFIG_LOCALVERSION = {lv_new} (+ LOCALVERSION_AUTO={new.get('CONFIG_LOCALVERSION_AUTO')})")

    if failed:
        print("\n::error::配置与原厂不兼容，刷入会导致模块加载失败/CFI trap/内核崩溃。禁止发布。")
        return 1

    print("\n[OK] 所有关键 ABI 项与原厂一致，模块兼容性检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
