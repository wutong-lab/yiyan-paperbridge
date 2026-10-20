# 译研 PaperBridge

在 Apple 芯片 Mac 上，用本地 Qwen3 模型将英文科研 PDF 翻译为中文。
支持专业术语、批量导入、原译文左右对照与同步翻页、独立缩放、中文及双语 PDF 导出、用时统计、内存和硬盘显示、可选清理。

## 安装

1. 下载 Release 中的 `Yiyan-1.4.0-AppleSilicon.dmg`。
2. 打开安装包，将“译研”拖到“应用程序”。
3. 打开应用，点击“准备本地翻译资源”。首次需要联网下载公开模型和排版资源；论文不会上传。
4. 资源准备完成后，选择 PDF，选择领域和翻译模式，开始翻译。

当前发行包：Apple Silicon（M1/M2/M3/M4 等）、macOS 26.2 或更新版本。
精译模式使用 Qwen3-14B 4bit，建议至少 24GB 统一内存；标准模式使用 Qwen3-4B。
下载模型约为 7.85GB（14B）或 2.15GB（4B），全文排版资源约 337MB；建议至少保留 15GB 可用空间。
安装包内置 Python 和运行依赖，无需另外安装 Python、终端环境或模型 API。

发行包使用本地临时签名，尚未通过 Apple Developer ID 签名和公证。
来自互联网的安装包可能被 macOS 拦截。先核对同一 Release 中的 SHA256；确认来源可信后，按 [Apple 官方说明](https://support.apple.com/zh-cn/102445) 在“系统设置 → 隐私与安全性”中允许打开。请勿关闭系统的整体安全检查。

## 使用与文件位置

- `⌘O` 导入；可一次选择多篇论文，也可拖入 PDF。
- 翻译完成后左右对照，点击“导出”选择保存位置。
- 关闭窗口后任务继续；`⌘Q` 退出。执行任务时请保持 Mac 唤醒。
- 模型、论文、译稿、任务记录保存在 `~/Library/Application Support/PaperBridge/`。
- 删除应用不会删除论文和模型；需要迁移时可复制上述数据文件夹。
- 升级前退出旧版，模型和论文会保留。请从安装位置运行，不要在运行期间移动应用。

## 翻译边界

本版主要支持文字型英文 PDF。扫描页尚不支持 OCR；图内文字、参考文献和独立公式通常沿用原稿。
复杂表格、跨栏顺序、公式附近的语义以及极长段落仍可能有误；自动检查不能证明科学语义完全正确。
中文与英文长度不同，换行、字号和局部排版可能变化，不能保证任意 PDF 的所有视觉细节完全一致。
无法安全写回的段落保留英文，并提供可查看的中文或错误提示。

## 从源码运行和构建

本仓库仅包含软件代码、内置术语与合成样例生成器，不含用户论文、译稿、模型权重或任务记录。
源码采用 AGPL-3.0；第三方组件仍采用各自许可，见 `THIRD_PARTY_NOTICES.md` 和 `release-assets/licenses/`。
本软件没有与小绿鲸或其他商业翻译工具的隶属关系，没有复制其代码、图标或界面资源。

开发环境需要 Apple Silicon Mac、Python 3.12 和 Apple Command Line Tools：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python scripts/make_sample.py
./run.command
```

复现当前安装包需要 Python 3.12 的可重定位独立发行版作为基础运行时：

```sh
.venv/bin/python scripts/prepare_release_licenses.py
.venv/bin/python scripts/package_macos.py --python-runtime /path/to/standalone/python
```

打包脚本会排除个人数据、使用公开 README、复制应用源码和第三方许可、内置运行依赖、进行重定位自检，并生成 DMG 和源码 ZIP。
完整分发时同时发布安装包、源码 ZIP、第三方对应源码 ZIP、许可说明及 SHA256SUMS.txt；不得仅分享可执行文件而忽略许可与对应源码要求。
GitHub 普通仓库存放源码，DMG 和对应源码压缩包放 Releases。模型由用户从官方仓库下载。


## 本地模型 / API

在工作室打开“API 配置”，填写自己的服务地址、模型和密钥，保存测试后可在翻译时直接选择。支持 DeepSeek 和 OpenAI 兼容接口；Mac 钥匙串 / Windows DPAPI 加密保存密钥。选择 API 会联网发送待译内容与必要上下文；本地模型支持离线使用。
