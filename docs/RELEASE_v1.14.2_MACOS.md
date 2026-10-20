# 译研 1.14.2 · 参考文献识别与全文阅读改进

**[Mac 一键安装器](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.14.2-macos/Yiyan-Mac-OneClick-Setup.dmg)** · **[Windows 一键安装器](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.14.2-windows-update/Yiyan-Windows-OneClick-Setup.exe)**

修复参考文献标题与书目合并在同一个 PDF 文本块时被送去翻译的问题。识别改为结合文字行、字体和栏位，支持带阿拉伯数字或罗马数字编号的标题、双栏续排和跨页书目。

参考文献继续保留英文原文、文字位置和链接；同页致谢、后续附录和浮动图注保持可翻译。术语与上下文提取逐行排除书目，保留与参考文献混排的正文。

排除表格中与其他列名并列的 References 表头，避免把后面的正文误当成书目。

同时修复跨栏页码被参考文献恢复过程拆分，以及只有重新导出记录时误显示整篇翻译用时的问题。新增 19 个参考文献回归用例，Mac 与 Windows 构建均执行相同检查；保留图像密集 PDF 的校验优化。

更新继续使用已安装的 4B / 14B 模型、API 配置、论文和词表。

[Mac 安装教程](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/MAC_INSTALL.md) · [Windows 安装教程](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/WINDOWS_INSTALL.md) · [API 配置](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/API_SETUP.md)
