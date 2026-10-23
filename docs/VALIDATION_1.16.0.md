# 1.16.0 验证记录

2026-10-23 对 GitHub 发布的安装包完成安装、离线翻译与阅读批注验证。

| 检查 | Mac Apple Silicon | Windows x64 |
| --- | --- | --- |
| 公开安装器下载 | 通过 | 通过，SHA-256 一致 |
| 实际安装与启动 | 通过，复用已有 4B 模型 | 通过，原生安装与升级流程 |
| 4B 离线翻译，两页合成文档，表格关闭 | 19.3 秒，0 失败段落 | 93.5 秒，0 失败段落 |
| 同一文档，表格开启 | 20.7 秒，0 失败段落 | 113.1 秒，0 失败段落 |
| 图片、参考文献、表格数字及双语导出 | 通过 | 通过 |
| 中英文批注保存、重载、颜色与下划线导出 | 通过 | 通过 |
| 旋转页面坐标、重复删除、过期版本保护 | 通过 | 通过 |

这些时间对应两页合成测试文档；Mac 使用 MLX，Windows 使用 GitHub Actions 的 CPU 环境。Windows 另外完成单页实际 4B 翻译、应用内更新、模型和论文设置保留，以及本地兼容 API 协议和 DPAPI 凭据保存测试。

Mac 原生窗口实测包括：全屏进入与 Esc 退出、左侧 125% / 右侧 150% 独立缩放、双侧新建高亮、重新打开后保留批注、系统保存窗口导出带批注的译文 PDF。Windows 的阅读报告覆盖安装后的接口、资源与 PDF 输出；原生全屏交互不计入该报告的自动化覆盖。

自动回归检查：**442 项 Python 测试、67 项 JavaScript 测试通过**。本轮增加了切换文档时仍在保存批注、重新生成 PDF 时旧批注导出、快速退出全屏和有未保存批注时更新软件等检查。

## 可核验报告

- [Mac 下载包与本机验收](../release-assets/verification-1.16.0-macos.json)
- [Windows 真实翻译报告](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.16.0-windows-update/TABLE-TRANSLATION-REPORT.json)
- [Windows 阅读批注报告](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.16.0-windows-update/READER-INSTALLED-REPORT.json)
- [Windows 更新保留检查](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.16.0-windows-update/WINDOWS-UPDATE-PRESERVATION.json)
- [Windows 构建与实际运行流程](https://github.com/wutong-lab/yiyan-paperbridge/actions/runs/39882860367)

开发者直接使用 Mac 应用包内的 Python 运行验收脚本时，应加 `-B` 或设置 `PYTHONDONTWRITEBYTECODE=1`，避免测试生成的字节码缓存写入已签名应用。原生启动器已设置该选项。验收应使用下载包中的引擎，不传开发源码覆盖参数。
