# 译研 · Windows 一键安装与应用更新

新版提供简洁工作台、全屏中英对照、各自缩放、彩色高亮与下划线、自由选色盘及带批注 PDF 导出。

**[下载 Windows 一键安装器](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v{VERSION}-windows-update/Yiyan-Windows-OneClick-Setup.exe)** · **[已有译研，下载更新工具](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v{VERSION}-windows-update/Yiyan-Updater.exe)**

1.15.1 在 **翻译设置 → 更多设置** 中新增 **翻译表格文字**，默认关闭。表内原文、数值和排版保留；开启后翻译可识别的表内文字，表题和表注继续翻译。本地与 API、单篇与批量任务使用同一规则，切换时保留已保存的手动表格译文。

修复 Windows 本地模型偶尔使用错误字段返回中文、导致短段落被判定失败的问题；本地推理增加译文格式约束，并继续检查中文、数值与公式。

新电脑选择 **4B 或 14B** 后点击安装，完整环境准备好即可离线使用。已经安装的用户运行新版安装器或更新工具，继续使用原有模型。

软件菜单 **软件 → 检查更新** 可以获取后续版本。普通更新只下载应用包，保留 4B / 14B 模型、论文、译稿、词表和设置，也复用原来的推理与阅读环境。

支持断点续传、文件大小与 SHA256 校验；替换应用时保留临时备份，更新失败自动恢复。下载连接独立于系统代理，并支持备用加速线路。

应用更新包不包含模型权重。模型继续保存在原有安装目录的 `Models` 或个人资料中的模型目录；论文与设置继续使用原工作室。

[Windows 安装和更新教程](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/WINDOWS_INSTALL.md) · [更新发布说明](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/WINDOWS_RELEASE_MAINTENANCE.md)


本版新增可保存的 API 配置、本地 / API 切换、跨页段落与表格语境优化，以及本地模型硬件检查、加载和生成超时提示。


修复含大量图像对象的 PDF 在最后 98% 校验阶段重复渲染的问题，新增逐页校验进度、取消响应和超大页面分块检查。原有图像、图表线条与公式检查标准保持一致。

1.14.2 修复参考文献标题与书目合并时被错误翻译的问题，支持带编号标题、双栏续排和跨页书目。术语与上下文提取排除书目，致谢、附录和图注继续翻译；跨栏页码保持完整。新增 19 个参考文献回归用例，并使用安装后的应用验证正文翻译和参考文献保留。
