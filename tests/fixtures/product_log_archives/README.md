# 产品日志 RAR 测试样本

这些文件使用本机 RAR 6.00 创建，内容全部为人工构造的公开测试日志。测试读取已提交的压缩包，不要求安装 WinRAR，不包含生产日志、账号或设备信息。

| 样本 | 生成参数与用途 |
| --- | --- |
| `compressed-rar4.rar` / `compressed-rar5.rar` | `rar a -ma4/-ma5 -m5 -ep`；每个包含 `diag.log` 的 64 条标准用户态日志，验证真正的压缩数据解码。 |
| `solid-rar4.rar` / `solid-rar5.rar` | 在上述参数中加入 `-s`；包含 `diag.log` 和 `diaglog_01.log`，各 64 条，验证连续固实解码状态。 |
| `blake2-rar5.rar` | `-ma5 -m5 -htb -ep`；验证 RAR5 BLAKE2 校验，而不是假定每个文件都有 CRC32。 |
| `unicode-rar4.rar` | `-ma4 -m5 -ep`；文件 `diaglog_中文.log` 内容为 `Unicode RAR 日志\n`，UTF-8 编码、LF 换行。 |
| `hardlink-rar5.rar` | `-ma5 -m5 -oh -ep`；归档前通过本地硬链接复制 `diag.log` 为 `diaglog_link.log`，验证在数据提取前拒绝链接记录。 |
| `encrypted-file-rar4.rar` / `encrypted-file-rar5.rar` | `-pPublicFixturePassword`；验证明确拒绝加密成员。 |
| `encrypted-header-rar4.rar` / `encrypted-header-rar5.rar` | `-hpPublicFixturePassword`；验证明确拒绝加密头部。 |

`diag.log` 的单行原文如下，重复 64 次；`diaglog_01.log` 把时间中的秒改为 `35`，把内容改为 `RAR historical log`。

```text
2000-01-06 03:51:36.383 C01-A06-sfu2 %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;UB Forward SDK module. (SlotId=62, CpuId=0, ModuleName=udk, Info=RAR log)
```

文件大小和 SHA256 记录在 [manifest.json](manifest.json)。它们包含 RAR 自身的创建时间等元数据；重新生成时二进制哈希可能变化，解压后的日志内容应保持一致。

覆盖样本的入口是 [独立网页压缩包测试](../../test_product_log_standalone_archives.py)。另外，测试在内存中创建 TAR、ZIP/ZIP64、LZMA、gzip/bzip2/xz 嵌套包、损坏包及 RAR4 存储成员，用于验证路径、目录、校验和、输出大小和取消行为。
