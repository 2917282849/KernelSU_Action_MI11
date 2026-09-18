# 配置来源与生成方式

## 文件说明

| 文件 | 来源 | 用途 |
|---|---|---|
| `renoir_factory_reference.config` | 原厂 boot.img 内嵌 IKCONFIG 抽出（5638 项） | **权威基准**：真正编译出原厂内核的配置 |
| `renoir_vendor_qgki_defconfig` | 内核源码 `arch/arm64/configs/vendor/renoir-qgki_defconfig` | **仅用于提取 3 个 int 项取值** |
| `renoir_stock_kernelsu_defconfig` | 上面两者按下方规则生成（5643 符号） | **实际用于编译** |

## 生成命令

```bash
python3 scripts/config_to_defconfig.py \
    configs/renoir_factory_reference.config \
    configs/renoir_vendor_qgki_defconfig \
    configs/renoir_stock_kernelsu_defconfig
```

## 生成规则（三层，最小改动）

```
[1] 原厂 .config 全文
      - 保留 LTO / THINLTO / CFI_CLANG / SHADOW_CALL_STACK 等全部编译选项
        （保证模块 ABI 与符号 CRC 对齐）
      - 剔除 EXTRA 块将要设置的项：LOCALVERSION / LOCALVERSION_AUTO /
        FRAME_WARN / KSU / WERROR      ← 避免重复赋值被静默覆盖
[2] 只从原厂 defconfig 提取 3 个 int 取值（其余一概不拿）
      CONFIG_LITTLE_CPU_MASK=15       0b0001111   4 小核    bit0-3
      CONFIG_BIG_CPU_MASK=112         0b1110000   3 大核    bit4-6
      CONFIG_PRIME_CPU_MASK=128       0b10000000  1 超大核  bit7
      （对应骁龙 780G 的 1+3+4 三丛集）
[3] 补丁项
      CONFIG_LOCALVERSION="-qgki-ge0ae3f4f4643"   对齐原厂 vermagic
      # CONFIG_LOCALVERSION_AUTO is not set
      CONFIG_KSU=y                                KernelSU
      CONFIG_FRAME_WARN=0                         规避 QCOM WiFi 栈帧告警
      # CONFIG_WERROR is not set
```

---

## ⚠️ 踩过的两个坑（均已加 CI 拦截）

### 坑 1：把 `.config` 直接当 defconfig → kconfig 死循环

`.config`（kconfig 输出）与 defconfig（kconfig 输入）是两种格式：

- `.config` 里**非 bool 项**也会被写成 `# CONFIG_X is not set`
- kconfig 读回来把它解析成**空字符串 `''`**
- 若该项**没有 default** 可回退，非交互（CI）模式下：

```
warning: symbol value '' invalid for LITTLE_CPU_MASK
Error in reading or end of file.        ← 随后无限循环
```

实测日志膨胀到 **838 MB**、编译完全卡死（run 35360097302）。

受害的只有 3 个项 —— 全树 `int/hex + 有 prompt + 无 default` 的项**恰好只有这 3 个**
（另有 11 个 string 同类项，但 string 空值合法，无害）。

**拦截**：`scripts/check_no_default_int.py`（静态）+ 带 `timeout` 的 kconfig 干跑（动态）

### 坑 2：整份追加原厂 defconfig → 925 处静默覆盖

原厂 vendor defconfig 与 `.config` 有 **925 个符号重叠**。整份追加会被 kconfig
以"后写胜出"静默覆盖，只发一行 warning：

```
warning: override: reassigning to symbol DEBUG_FS
```

实测造成 **108 项非预期差异**，最典型的是把原厂量产设置 `DEBUG_FS=n`
改成了 `y`（run 35363363147）。

**拦截**：`scripts/check_defconfig_dupes.py` —— 任何重复赋值都直接 fail

---

## 编译后的配置校验

`scripts/verify_config.py` 从编译出的 `Image` 抽 IKCONFIG，与原厂逐项比对，
差异分三类：

| 类别 | 含义 | 处理 |
|---|---|---|
| 预期内 | 我们有意改的项（LOCALVERSION/KSU/编译器指纹） | 列出，不报错 |
| 源码树不支持 | 符号在本源码树里**不存在**（小米 MIUI 私有补丁） | 列出，不报错 |
| **真实差异** | 符号存在但取值不同 | 列出 + `::warning::` |

只有 **关键 ABI 项**不一致才判失败：

```
LTO / LTO_CLANG / LTO_NONE / THINLTO
CFI_CLANG / CFI_CLANG_SHADOW
SHADOW_CALL_STACK / SHADOW_CALL_STACK_VMAP
MODVERSIONS / MODULES / MODULE_SIG(_FORCE/_ALL)
KPROBES / TRIM_UNUSED_KSYMS
RANDSTRUCT / GCC_PLUGIN_RANDSTRUCT / GCC_PLUGINS
ARM64_LSE_ATOMICS
```

> `ARM64_USE_LSE_ATOMICS` 与 `ARM64_LSE_ATOMICS` 互为 default，新旧内核
> 把 prompt 放在不同的一侧。校验器识别这种命名差异并判为功能等价。

## 已知的源码树局限

本源码树（`Official-Ayrton990/android_kernel_xiaomi_renoir` A13）是
**QCOM/AOSP 基线，不含小米 MIUI 私有补丁**：

```
MIHW / MILLET / MIGT / PERF_HUMANTASK / MSM_IDLE_STATS_*      → 调度与性能特性
EMERGENCY_MEMORY / BOOTUP_RECLAIM / CAM_RECLAIM / MI_RECLAIM  → MIUI 内存管理
QTI_BATTERY_CHARGER_K8 / HAPTIC_NV_L / INPUT_FINGERPRINT      → 硬件相关
```

这些符号在树里**根本不存在**，改配置无法解决。缺失影响的是功能特性
（性能/省电/部分 MIUI 设置项），不是模块 ABI，**不影响模块加载**。

（树里带有 `QTI_BATTERY_CHARGER`、`FINGERPRINT_GOODIX_*`、
`TOUCHSCREEN_GOODIX_*`、`synaptics_s3908p` 等 renoir 专属驱动，属于设备适配层。）
