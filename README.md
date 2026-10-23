# 译研 · PaperBridge

### 跨越语言，读懂研究。

译研是一款面向 SCI 论文阅读的桌面应用，将英文科研 PDF 翻译成自然、专业的中文，把全文翻译、专业术语、中英对照和 PDF 导出放进一个简洁的论文工作室。

Mac 与 Windows 都提供 **一键安装向导**：选择 4B 或 14B 后自动准备完整环境，装好即可离线翻译。Mac 与 Windows 后续更新都继续使用已安装模型，论文、译稿与设置也会保留。

**[Mac 一键安装](https://ghfast.top/https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.15.1-macos/Yiyan-Mac-OneClick-Setup.dmg) · [Windows 一键安装](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.15.1-windows-update/Yiyan-Windows-OneClick-Setup.exe)** · **[Mac 安装教程](docs/MAC_INSTALL.md) · [Windows 安装教程](docs/WINDOWS_INSTALL.md)** · **[使用方法](#开始阅读)**

![译研 Mac 论文工作室](docs/images/workspace.jpg)

## 1.16.0：更专注的对照阅读

工作台采用简洁的浅色界面，常用翻译和阅读操作集中显示，详细设置可按需展开。Mac 与 Windows 都支持全屏对照、左右独立缩放，以及原文和译文各自的高亮、下划线。

高亮颜色更醒目，提供预设颜色和自由选色盘；颜色选择自动记住。批注自动保存，切换页码或重新打开软件后可继续阅读，也可分别导出带批注的原文或中文 PDF。

### 表格、模型与 API

在 **翻译设置 → 更多设置** 中选择是否翻译表格文字。默认保留表内原文、数值和排版；开启后翻译可识别的表格文字，表题和表注继续翻译。本地模型与 API、单篇与批量任务共用这一设置。

在工作室的 **API 配置** 中添加 DeepSeek、通义千问、硅基流动或自定义 OpenAI 兼容接口，填写密钥、地址和模型，点击 **保存并测试**。以后翻译时直接选择已保存的服务，也可以继续选择本地 4B / 14B。

Mac 使用系统钥匙串保存密钥，Windows 使用当前用户的 DPAPI 加密保存。API 翻译使用你选择的服务，会发送待译文本和必要的术语、上下文；本地模型继续支持离线翻译。API 模式无需加载本地大模型，不要求独立显卡。

修复图像密集论文最后校验长期停留在 98% 的问题，显示逐页校验进度并支持中途停止。新版加强跨页段落衔接、表格和图注语境、专业译名一致性。开始本地翻译前检查内存与显卡；配置不足时给出切换 4B 或 API 的入口，模型异常或长时间无响应时停止任务并保留已完成内容。详见 [API 配置与使用](docs/API_SETUP.md)。

参考文献按标题、文字行和栏位识别，兼容标题与书目合并、跨栏跨页续排和带编号标题。参考文献保留原文，致谢、附录与图注继续翻译；术语提取排除书目内容。

## 为科研阅读而设计

| 功能 | 使用体验 |
| --- | --- |
| 全文英译中 | 按论文段落处理正文，结合上下文生成中文译文，支持译后复核与润色。 |
| 专业术语 | 提供通用科研、天文与物理、医学与生命科学、材料与化学、工程与计算机词表；支持自定义译名与本篇术语。 |
| 表格翻译 | 默认保留表内原文，可按需开启表格文字翻译；表题、表注继续翻译。 |
| 论文排版 | 在论文版面中呈现中文，保留页面尺寸、图片、表格线和公式组件的阅读结构。 |
| 双栏阅读 | 左侧原文、右侧译文，同步翻页与滚动；两侧可以独立放大，也可以联动缩放。 |
| 批量处理 | 一次导入多篇 PDF，按队列依次翻译，每篇论文单独保存。 |
| 阅读与校订 | 在版面对照和段落译文之间切换，修改中文后保存并更新 PDF。 |
| 多种导出 | 导出中文 PDF、原译页交替的双语 PDF，以及完整译文与核对记录。 |
| 资源管理 | 显示翻译用时、运行内存与固态空间，支持按类别清理和完成后自动清理。 |

## 看看译研

### 原文与译文，一起翻阅

左右对照示例论文，图表、公式与文字处在熟悉的位置。开启专注阅读，把窗口空间留给论文。

![原文与中文译文对照阅读](docs/images/bilingual-reader.jpg)

### 为你的学科，选择准确的词

搜索中英文术语，选择学科词表，加入你认可的专业译名。自定义词表用于后续翻译，本篇术语服务于当前论文的语境。

![专业术语词库与自定义译名](docs/images/terminology.jpg)

### 内存、存储与清理，都在一个地方

查看软件占用和固态空间，选择闲置内存、临时排版文件、旧自动备份、下载缓存或运行日志进行清理。

![固态存储与可选清理内容](docs/images/resources.jpg)

## 安装到 Mac：选择模型，一键准备

**[下载 Mac 一键安装器（约 1.3MB）](https://ghfast.top/https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.15.1-macos/Yiyan-Mac-OneClick-Setup.dmg)** · [GitHub 原始下载](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.15.1-macos/Yiyan-Mac-OneClick-Setup.dmg) · [安装与更新教程](docs/MAC_INSTALL.md)

![Mac 一键安装器：4B / 14B 自选](docs/images/macos-setup.png)

1. 打开 DMG，双击 **安装译研**。
2. 选择 **4B 标准版**或 **14B 精译版**，点击 **下载并安装**。
3. 等待完成，打开译研，导入论文即可离线翻译。

安装器独立直连，自动切换备用加速线路，显示实时速度与剩余时间；无需配置本机代理，也不会修改系统代理设置。支持暂停续传、自动校验，安装成功后默认清理下载缓存。首次安装需要联网，之后的翻译与导出在本机离线运行。

| 配置 | 4B 标准版 | 14B 精译版 |
| --- | --- | --- |
| Mac 芯片 | Apple Silicon（M 系列） | Apple Silicon（M 系列） |
| 系统 | macOS 26.2 或更新 | macOS 26.2 或更新 |
| 建议统一内存 | 16GB 或更多 | 24GB 或更多 |
| 模型大小 | 约 2.15GB | 约 7.86GB |
| 应用与运行环境 | 另约 0.57GB | 另约 0.57GB |
| 安装预留空间 | 15GB | 30GB |

**更新不用重下模型。** Mac 模型独立保存在本机工作室，旧完整离线版的模型会迁入独立目录。通过菜单 **译研 → 检查更新**获取新版，更新只替换应用与运行环境，保留模型、论文、译稿和设置。

后续版本发布方法与更新接口见 [Mac 更新维护说明](docs/MAC_RELEASE_MAINTENANCE.md)。需要为完全离线的电脑准备安装文件，也可使用原有的 [Mac 完整离线包](https://github.com/wutong-lab/yiyan-paperbridge/releases/tag/v1.4.0-offline-14b)。

## 开始阅读

1. **导入论文**：点击“选择论文 PDF”，或将一篇、多篇英文 PDF 拖入工作室。
2. **选择设置**：选择论文领域、翻译页码、本地模型或已保存的 API 服务；在“更多设置”中设置译后复核和自动清理。
3. **开始翻译**：点击“开始翻译”。批量导入后，点击“翻译待处理论文”。
4. **对照阅读**：点击“全屏对照”（快捷键 F），左右可以分别缩放或联动缩放。按 Esc 退出全屏。在“段落译文”中可查看和修改中文。
5. **添加批注**：选择高亮或下划线，选定颜色后在原文或译文上拖过文字；批注自动保存。点击已有批注可删除，批注工具栏可导出带批注的原文或译文。
6. **保存译稿**：从“导出”菜单选择中文 PDF、双语 PDF 或完整译文与核对记录。

专业译名可按下面的格式添加到术语表：

```text
circumstellar medium = 星周介质
progenitor = 前身星
light curve = 光变曲线
```

## Windows 版：选好模型，一键安装

![Windows 一键安装：选择 4B 或 14B](docs/images/windows-setup.png)

**[下载 Windows 一键安装器](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.15.1-windows-update/Yiyan-Windows-OneClick-Setup.exe)** · [安装与使用教程](docs/WINDOWS_INSTALL.md)

1. 双击打开 **Yiyan-Windows-OneClick-Setup.exe**。
2. 选择 **4B 标准版**或 **14B 精译版**，点击 **下载并安装**。
3. 等待向导完成，打开译研，导入论文即可离线翻译。

向导自动下载、校验并安装模型、阅读组件、字体与排版环境；支持暂停、断点续传和网络中断自动重试，默认在安装成功后清理下载缓存。首次下载安装需要联网，之后的翻译在本机离线运行。

| 配置 | 4B 标准 | 14B 精译 |
| --- | --- | --- |
| Windows 系统 | Windows 10 2004+ / 11，x64 | Windows 10 2004+ / 11，x64 |
| 内存最低 / 建议 | **16GB / 16GB+** | **24GB / 32GB+** |
| NVIDIA 显存最低 / 建议 | **6GB / 8GB+** | **12GB / 16GB+** |
| 模型大小 | 约 2.50GB | 约 9.00GB |
| 完整安装文件下载 | **约 4.07GB** | **约 10.49GB** |
| 用户下载安装入口 | **同一个一键安装器，选择 4B** | **同一个一键安装器，选择 14B** |
| 安装使用预留空间 | 15GB | 30GB |

两种模型都支持 CPU，显存要求用于完整 GPU 加速。配置按 Q4_K_M、8192 上下文、单任务预留开销；同时安装两种模型留 40GB 空间。更多选择方法见 Windows 教程。

需要为离线电脑准备安装文件时，也可下载 [4B 完整离线包](https://github.com/wutong-lab/yiyan-paperbridge/releases/tag/v1.6.0-windows-4b-offline)或 [14B 完整离线包](https://github.com/wutong-lab/yiyan-paperbridge/releases/tag/v1.6.0-windows-14b-offline)。

**已有模型，更新无需重新下载。** Windows 菜单 **软件 → 检查更新**获取后续版本；应用更新包保留模型、论文、译稿和设置。旧版首次升级可运行新版一键安装器，选择原安装位置。详见 [Windows 更新发布说明](docs/WINDOWS_RELEASE_MAINTENANCE.md)。

![Windows 应用更新：继续使用已安装模型](docs/images/windows-update.png)

## 开源与许可

应用源码采用 **[AGPL-3.0](LICENSE)**。Qwen 模型使用 Apache-2.0 许可，字体和其他依赖保留各自的许可证。完整发行版随附应用源码与第三方对应源码；详情见 **[第三方声明](THIRD_PARTY_NOTICES.md)**。
