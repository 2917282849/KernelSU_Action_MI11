#!/usr/bin/env python3
"""
安全检查：找出所有【有 prompt、无 default、且类型为 int/hex/string】的配置项。

这些项就是 syncconfig 死循环的元凶：
  - 有 prompt         -> kconfig 认为需要用户提供值
  - 无 default        -> 没有回退值
  - 类型是 int/hex/string（非 bool）-> 不能用 "is not set" 表示
  => kconfig 解析出空值 '' -> "symbol value '' invalid" -> 无限循环

用法:
  python3 check_no_default_int.py <Kconfig目录> [我们的defconfig]
"""

import os
import re
import sys

RE_CONFIG = re.compile(r"^config\s+([A-Za-z0-9_]+)\s*$")
RE_KIND = re.compile(r"^\s+(bool|tristate|int|hex|string)\b(.*)$")


def scan(roots):
    files = []
    for root in roots:
        for dirpath, _d, filenames in os.walk(root):
            for fn in filenames:
                if fn.startswith("Kconfig") or fn.startswith("Config.in"):
                    files.append(os.path.join(dirpath, fn))

    results = {}
    for path in files:
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
        except OSError:
            continue
        i = 0
        while i < len(lines):
            m = RE_CONFIG.match(lines[i])
            if not m:
                i += 1
                continue
            name = "CONFIG_" + m.group(1)
            if not lines[i].startswith("config"):
                # 只接受顶格的 config（避免 "menuconfig" 之类）
                i += 1
                continue
            i += 1
            kind = None
            prompt = False
            has_default = False
            while i < len(lines):
                ln = lines[i]
                if ln and not ln.startswith((" ", "\t")):
                    break
                s = ln.strip()
                km = RE_KIND.match(ln)
                if km and kind is None:
                    kind = km.group(1)
                    rest = km.group(2)
                    if '"' in rest:
                        prompt = True
                elif s.startswith("prompt ") and '"' in s:
                    prompt = True
                elif s.startswith("default "):
                    has_default = True
                i += 1
            results[name] = (kind, prompt, has_default)
    return results, len(files)


def main():
    roots = sys.argv[1:2] or ["."]
    our_defconfig = sys.argv[2] if len(sys.argv) > 2 else None

    res, nfiles = scan(roots)
    print(f"[i] 扫描 {nfiles} 个 Kconfig，共 {len(res)} 个配置项")

    # 只有 int/hex 才会出事：string 类型的空值（"")是合法的，
    # 而 int/hex 解析出 '' 时会报 "symbol value '' invalid" 并死循环。
    dangerous = {
        k: v for k, v in res.items()
        if v[0] in ("int", "hex") and v[1] and not v[2]
    }
    strings_only = {
        k for k, v in res.items()
        if v[0] == "string" and v[1] and not v[2]
    }
    print(f"[i] 危险项（int/hex + 有 prompt + 无 default）: {len(dangerous)}")
    print(f"[i] 无害项（string + 有 prompt + 无 default，空值合法）: {len(strings_only)}，已忽略")

    have = {}
    if our_defconfig and os.path.exists(our_defconfig):
        for ln in open(our_defconfig, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if ln.startswith("CONFIG_") and "=" in ln:
                k, v = ln.split("=", 1)
                have[k.strip()] = v
            elif ln.startswith("# CONFIG_") and ln.endswith(" is not set"):
                have[ln[2: ln.index(" is not set")]] = None

    bad = []
    for k in sorted(dangerous):
        v = have.get(k, "【defconfig 中不存在】")
        ok = v not in ("【defconfig 中不存在】", None, "")
        mark = "OK " if ok else "❌ "
        if not ok:
            bad.append(k)
        print(f"  {mark}{k:34s} 我们 defconfig 里的值 = {v}")

    if bad:
        print(f"\n::error::以下 {len(bad)} 项无有效值，会导致 syncconfig 死循环: {bad}")
        return 1
    print("\n[OK] 所有危险项都有有效值 -> 不会再死循环")
    return 0


if __name__ == "__main__":
    sys.exit(main())
