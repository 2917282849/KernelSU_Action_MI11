#!/usr/bin/env python3
"""
模块 ABI 预检：把我们内核导出的符号 CRC 与「小米原厂模块要求的 CRC」逐一对齐。

== 为什么必须这步 ==
原厂内核 vermagic 里带 modversions，模块加载时内核会逐个校验 CRC。
CRC 是 genksyms 根据「类型签名 + 相关结构体布局」算出来的，任何影响布局的
配置项不同都会变。CRC 不符 -> 模块被拒载 -> init 卡死 -> 卡在开机 logo。
所以「编译成功」不等于「能开机」，这一关才是真正的验收。

== 数据来源 ==
A = configs/vendor_module_crc.txt  (原厂 .ko 的 __versions 节，是模块的"要求")
B = out/Module.symvers             (我们内核的"实际导出")

用法: python3 crc_check.py <Module.symvers> <vendor_module_crc.txt>
退出码: 0=全通过  1=有缺失或不符
"""
import sys, re

def load_expected(p):
    d = {}
    for ln in open(p, encoding='utf-8', errors='ignore'):
        ln = ln.strip()
        if not ln or ln.startswith('#'): continue
        parts = ln.split()
        if len(parts) >= 2:
            d[parts[0]] = int(parts[1], 16)
    return d

def load_symvers(p):
    d = {}
    for ln in open(p, encoding='utf-8', errors='ignore'):
        f = ln.rstrip('\n').split('\t')
        if len(f) >= 2 and f[0].startswith('0x'):
            d[f[1]] = int(f[0], 16)
    return d

def main():
    if len(sys.argv) < 3:
        print('用法: crc_check.py <Module.symvers> <vendor_module_crc.txt>'); return 2
    want = load_expected(sys.argv[2])
    have = load_symvers(sys.argv[1])
    print('  原厂模块要求符号数 : %d' % len(want))
    print('  我们内核导出符号数 : %d' % len(have))
    missing, mismatch, ok = [], [], 0
    for s, crc in sorted(want.items()):
        if s not in have:
            missing.append(s)
        elif have[s] != crc:
            mismatch.append((s, crc, have[s]))
        else:
            ok += 1
    print()
    print('  ✅ CRC 一致 : %d / %d' % (ok, len(want)))
    if missing:
        print('  ❌ 内核未导出（模块会拒载）: %d' % len(missing))
        for s in missing[:20]: print('       %s' % s)
    if mismatch:
        print('  ❌ CRC 不一致（模块会拒载）: %d' % len(mismatch))
        for s, w, h in mismatch[:20]:
            print('       %-42s 原厂要求=%08x  我们=%08x' % (s, w, h))
    if missing or mismatch:
        print()
        print('::error::模块 ABI 预检未通过 —— 此内核刷入会卡 logo，不要交付')
        return 1
    print()
    print('  🎉 全部通过 —— 原厂模块可以加载，这个内核才值得刷')
    return 0

if __name__ == '__main__':
    sys.exit(main())
