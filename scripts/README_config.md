# 配置文件来源与生成方式

## 文件说明

| 文件 | 来源 | 用途 |
|---|---|---|
| `renoir_factory_reference.config` | 原厂 boot.img 内嵌 IKCONFIG 抽出（5638 项） | 编译后逐项比对的基准 |
| `renoir_vendor_qgki_defconfig` | 内核源码 `arch/arm64/configs/vendor/renoir-qgki_defconfig`（941 行） | 补齐无 default 项 |
| `renoir_stock_kernelsu_defconfig` | 上面两者合并生成（7986 行） | **实际用于编译** |

## 生成命令

```bash
python3 scripts/config_to_defconfig.py \
    configs/renoir_factory_reference.config \
    configs/renoir_vendor_qgki_defconfig \
    configs/renoir_stock_kernelsu_defconfig
```

## 为什么不能直接用原厂 .config 编译

`.config`（kconfig 输出）与 defconfig（kconfig 输入）格式不同：

- `.config` 里**非 bool 项**也可能被写成 `# CONFIG_X is not set`
- kconfig 读回来时把它解析成**空字符串 ''**
- 若该项在 Kconfig 里**没有 default** 可回退，非交互（CI）模式下会：

```
.config:377: warning: symbol value '' invalid for LITTLE_CPU_MASK
.config:378: warning: symbol value '' invalid for BIG_CPU_MASK
.config:379: warning: symbol value '' invalid for PRIME_CPU_MASK
Error in reading or end of file.        <- 随后无限循环
```

实测导致日志膨胀至 **838 MB**、编译完全卡死（run 35360097302）。

受害的三个项（`arch/arm64/Kconfig` 里均为 `int` 且无 `default`）：

```
CONFIG_LITTLE_CPU_MASK=15      0b0001111  4 个小核  bit0-3
CONFIG_BIG_CPU_MASK=112        0b1110000  3 个大核  bit4-6
CONFIG_PRIME_CPU_MASK=128      0b10000000 1 个超大核 bit7
```

值取自原厂 `vendor/renoir-qgki_defconfig:65-67`，对应骁龙 780G 的 **1+3+4 三丛集**。

## 生成的 defconfig 结构

```
[1] 原厂 .config 全文（保留 LTO/CFI/SCS 等全部编译选项，保证模块 ABI 与 CRC 对齐）
        - 剔除 CONFIG_LOCALVERSION / LOCALVERSION_AUTO（统一指定）
[2] 原厂 vendor/renoir-qgki_defconfig 全文（合法的 kconfig 输入）
        - 补齐无 default 的 int 项
        - 其 83 处 "is not set" 精确覆盖 .config 中可能过时的项
        - 放在后面 => 取值优先
[3] 补丁项
        CONFIG_LOCALVERSION="-qgki-ge0ae3f4f4643"   （对齐原厂 vermagic）
        # CONFIG_LOCALVERSION_AUTO is not set
        CONFIG_KSU=y                                 （KernelSU）
        CONFIG_FRAME_WARN=0                          （规避 QCOM WiFi 栈帧告警）
```

## 相关校验（CI 中自动执行）

1. `Install stock kernel config` 步骤：断言关键 ABI 项 + 三个 CPU_MASK 存在
2. `Verify kernel release (vermagic)`：断言内核版本号 == 5.4.233-qgki-ge0ae3f4f4643
   并断言含原厂 `f2fs-hash:1e2949fb28`
3. `Verify kernel config vs factory`：从编译出的 Image 抽 IKCONFIG 与原厂逐项比对
