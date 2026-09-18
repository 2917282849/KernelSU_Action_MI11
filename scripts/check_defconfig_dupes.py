#!/usr/bin/env python3
"""
检测 defconfig 里的【重复赋值】。

== 为什么需要 ==
同一个符号在 defconfig 里写两次，kconfig 不会报错，只会：
    warning: override: reassigning to symbol DEBUG_FS
然后【后写的那条胜出】，静默覆盖前面的值。

实测教训（run 35363363147）：
  把原厂 vendor/renoir-qgki_defconfig 整份追加到 .config 后面，
  两个文件有 925 个符号重叠 -> 925 处静默覆盖
  -> 与原厂产生 108 项非预期差异
     （最典型：原厂 DEBUG_FS=n 是量产设置，被覆盖成 y）
  -> 若没被校验器拦住就会刷进手机

所以：任何重复赋值都必须视为错误。

用法：
  python3 check_defconfig_dupes.py <defconfig>

退出码：
  0 = 无重复
  1 = 有重复（打印 ::error::）
"""

import collections
import re
import sys

RE_ASSIGN = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=")
RE_NOTSET = re.compile(r"^#\s*(CONFIG_[A-Za-z0-9_]+)\s+is\s+not\s+set\s*$")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    path = sys.argv[1]
    hits = collections.defaultdict(list)

    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, ln in enumerate(fh, 1):
            s = ln.strip()
            m = RE_ASSIGN.match(s) or RE_NOTSET.match(s)
            if m:
                hits[m.group(1)].append((lineno, s))

    dupes = {k: v for k, v in hits.items() if len(v) > 1}
    print(f"[i] {path}: {len(hits)} 个符号，{sum(len(v) for v in hits.values())} 条赋值")

    if not dupes:
        print("[OK] 无重复赋值，不会发生静默覆盖")
        return 0

    print(f"\n::error::{len(dupes)} 个符号被重复赋值 —— "
          f"kconfig 会静默地用后一条覆盖前一条！")
    for k in sorted(dupes):
        print(f"  {k}:")
        for lineno, s in dupes[k]:
            print(f"      第 {lineno} 行: {s}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
