#!/usr/bin/env python3
"""
把「从内核镜像里抽出的 .config」转换成「合法的 defconfig」。

== 背景：为什么需要转换 ==
.config 与 defconfig 是两种格式：

  .config（kconfig 的输出）              defconfig（kconfig 的输入）
    CONFIG_FOO=y                          CONFIG_FOO=y
    # CONFIG_FOO is not set               省略（或写 # CONFIG_FOO is not set）
    CONFIG_NUM=32                         CONFIG_NUM=32
    非 bool 且无值也写成 "# ... is not set"  非 bool 项【从不】写 "is not set"

把 .config 直接当 defconfig 喂给 make 会出致命问题：
  int/hex/string 类型的项若带 "# ... is not set"，kconfig 把它的值
  解析为空字符串 ''；如果该 Kconfig 项又没有 default 可回退，
  非交互模式（CI）下就会：
      .config:377: warning: symbol value '' invalid for LITTLE_CPU_MASK
      Error in reading or end of file.        <- 无限循环
  实测导致日志膨胀到 838MB、编译完全卡死。

== 本脚本的做法（保守、最小改动）==
1. 原样保留 .config 的每一行（只丢弃 4 个会引发问题的 int 项形式）
2. 在文件【末尾追加】以下内容：
     a) 从原厂 defconfig 逐行继承（它才是 kconfig 的合法输入，
        含 LITTLE_CPU_MASK=15 这类无 default 项的必需取值）
        —— 放在末尾，让原厂取值覆盖 .config 中的可疑值
     b) KernelSU 与构建健壮性补丁项
3. 不解析 Kconfig，不做类型推断 —— 避免自作聪明引入新 bug

用法:
  python3 config_to_defconfig.py <输入.config> <原厂defconfig> <输出defconfig>
"""

import sys

# 必须由原厂 defconfig 提供、且不能以 "is not set" 形式出现的 int 项
# （它们在 arch/arm64/Kconfig 里没有 default，缺失或空值会导致 syncconfig 死循环）
NO_DEFAULT_INTS = (
    "CONFIG_LITTLE_CPU_MASK",
    "CONFIG_BIG_CPU_MASK",
    "CONFIG_PRIME_CPU_MASK",
)

EXTRA = """
# ======== 内核版本号（对齐原厂 vermagic）========
# 原厂：CONFIG_LOCALVERSION="-qgki" + LOCALVERSION_AUTO=y + git 后缀 ge0ae3f4f4643
# 这里直接写死完整后缀，并关掉 AUTO，不再依赖 git（编译目录无 .git/.scmversion）
CONFIG_LOCALVERSION="-qgki-ge0ae3f4f4643"
# CONFIG_LOCALVERSION_AUTO is not set

# ======== KernelSU 集成 + 构建健壮性补丁（非原厂项）========
CONFIG_KSU=y
CONFIG_FRAME_WARN=0
# CONFIG_WERROR is not set
"""


def read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().split("\n")


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2

    config_path, ref_defconfig_path, out_path = sys.argv[1:4]

    src = read_lines(config_path)
    ref = read_lines(ref_defconfig_path)

    # ---- 1) 原样保留 .config，剔除无 default int 项的各种形式 ----
    # 这些项不能出现 "# ... is not set"（空值 -> 死循环）
    # 也不能出现 "=0" 之类被 .config 固化但不正确的值 —— 一律剔除，
    # 统一改由原厂 defconfig 提供（见下方追加）
    out = []
    dropped = []
    for ln in src:
        s = ln.strip()
        drop = False
        for k in NO_DEFAULT_INTS:
            if s == f"# {k} is not set" or s.startswith(f"{k}="):
                drop = True
                dropped.append(s)
                break
        if not drop:
            out.append(ln)

    # LOCALVERSION / LOCALVERSION_AUTO 交给原厂 defconfig + EXTRA 处理，
    # 避免两处不一致
    out = [
        ln
        for ln in out
        if not ln.startswith("CONFIG_LOCALVERSION=")
        and not ln.startswith("CONFIG_LOCALVERSION_AUTO=")
        and ln.strip() != "# CONFIG_LOCALVERSION_AUTO is not set"
    ]

    # ---- 2) 追加原厂 defconfig 全文（合法输入，含无 default 项的必需取值）----
    # 放最后 => 其取值优先，覆盖 .config 里可能过时的项
    ref_lines = [ln for ln in ref if ln.strip()]
    # 原厂 defconfig 自身也写 "is not set"，这些是合法输入（bool 项），保留
    # 但剔除它可能带的 LOCALVERSION（由我们统一指定）
    ref_lines = [
        ln for ln in ref_lines if not ln.startswith("CONFIG_LOCALVERSION=")
    ]

    # ---- 3) 组装 ----
    text = (
        "\n".join(out)
        + "\n\n# ======== 以下继承自原厂 vendor/renoir-qgki_defconfig ========\n"
        + "\n".join(ref_lines)
        + "\n"
        + EXTRA
    )

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    # ---- 4) 自检 ----
    lines = text.split("\n")
    print(f"[i] 输入 .config      : {len(src)} 行")
    print(f"[i] 剔除无 default int: {len(dropped)} 处 -> {dropped}")
    print(f"[i] 继承原厂 defconfig: {len(ref_lines)} 行")
    print(f"[i] 输出              : {len(lines)} 行, {len(text)} 字节 -> {out_path}")

    for k in NO_DEFAULT_INTS:
        hits = [l for l in lines if l.startswith(k + "=")]
        print(f"    {k}: {hits if hits else '❌ 缺失！会导致 syncconfig 死循环'}")

    need = ["CONFIG_LTO=y", "CONFIG_THINLTO=y", "CONFIG_CFI_CLANG=y",
            "CONFIG_SHADOW_CALL_STACK=y", "CONFIG_MODVERSIONS=y",
            "CONFIG_KPROBES=y", "CONFIG_KSU=y",
            'CONFIG_LOCALVERSION="-qgki-ge0ae3f4f4643"']
    missing = [n for n in need if n not in lines]
    if missing:
        print(f"[!] 缺少关键项: {missing}")
        return 1
    print("[OK] 关键项齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
