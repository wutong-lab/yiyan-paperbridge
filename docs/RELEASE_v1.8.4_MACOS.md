# 译研 · Mac 1.8.4

**[下载 Mac 一键安装器](https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.8.4-macos/Yiyan-Mac-OneClick-Setup.dmg)** · [备用加速下载](https://ghfast.top/https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v1.8.4-macos/Yiyan-Mac-OneClick-Setup.dmg)

修复部分 Mac 从图形界面启动安装器时提示“安装归档包含无效路径”的问题。解压程序现在独立使用 UTF-8 读取中文文件名；归档诊断信息与文件列表分开处理，继续校验归档目录范围。

如果遇到旧版报错，请关闭旧安装器，打开本版安装器，选择原来的模型和安装位置后继续。完整下载的模型缓存会先校验再复用，已经安装的模型、论文与设置继续保留。

本版增加 C、POSIX、未设置语言环境、中文与空格目录、分卷归档及异常路径回归检查；发布前对实际应用归档执行 C 语言环境解压与应用签名校验。

支持 M 系列 Mac，4B / 14B 模型自选。首次联网下载安装，准备好后离线翻译；应用更新继续复用本地模型。

[Mac 安装教程](https://github.com/wutong-lab/yiyan-paperbridge/blob/main/docs/MAC_INSTALL.md) · [软件首页](https://github.com/wutong-lab/yiyan-paperbridge)
