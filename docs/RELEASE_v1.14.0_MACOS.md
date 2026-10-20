# 译研 1.14 · 图像密集论文快速校验

**[Mac 一键安装器](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.14.0-macos/Yiyan-Mac-OneClick-Setup.dmg)** · **[Windows 一键安装器](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.14.0-windows-update/Yiyan-Windows-OneClick-Setup.exe)**

修复含大量细小图像对象的论文在最后校验阶段长时间停留在 98% 的问题。普通页面复用一次完整渲染，超大页面按固定大小分块检查；同一像素区域复用比较结果。继续逐项核对图像摘要和坐标、实际外观、图表线条及公式，保留原有精度标准。

导出阶段现在分别显示 PDF 整理、双语生成和逐页校验进度，校验期间也可以停止任务。用时记录包含最后的校验和导出。

新增含 27,284 个图像对象的合成 PDF 回归测试，覆盖旋转、裁剪、图像被覆盖、超大页面分块与取消操作。Mac、Windows 使用相同的修复和校验流程。

支持保存 DeepSeek 和其他 OpenAI 兼容 API 配置，也可继续选择本地 4B / 14B 离线翻译。更新继续使用已安装模型和个人设置。

[Mac 安装教程](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/MAC_INSTALL.md) · [Windows 安装教程](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/WINDOWS_INSTALL.md) · [API 配置与硬件要求](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/API_SETUP.md)
