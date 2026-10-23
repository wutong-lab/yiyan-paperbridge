# 对照阅读桌面端验证

网页阅读器使用标准 Fullscreen API：用户点击按钮后调用阅读区域的 `requestFullscreen()`，退出调用 `document.exitFullscreen()`，并由 `fullscreenchange` 更新界面状态。桌面外壳不向网页开放额外的本机操作接口。

- Mac：开启 `WKPreferences.isElementFullscreenEnabled`（macOS 12.3 及以上），由 WebKit 管理全屏窗口和 Escape。WKWebView 使用 autoresizing 布局，允许 WebKit 在进入全屏时转移视图。
- Windows：监听 WebView2 的 `ContainsFullScreenElementChanged`，进入时隐藏菜单和窗口边框、覆盖当前显示器；退出时恢复原窗口位置、边框、菜单以及最大化状态。Escape 由 WebView2 处理，并通过同一事件恢复窗口。
- PDF 导出：两端通用下载处理器均接受服务器返回的 `Content-Disposition: attachment`，包括批注导出。Mac 将保存对话框附着到当前 WKWebView 所在窗口，保证全屏阅读时也能看到保存位置选择。

每次修改全屏逻辑后，在实际桌面程序检查以下操作：

1. 正常窗口、最大化窗口分别进入对照全屏，确认左右 PDF 与工具栏完整可见。
2. 原文放大到 150%、译文缩小到 90%，分别翻页、滚动，再检查批注位置是否随内容缩放。
3. 按 Escape 和点击退出按钮各退出一次，检查窗口位置、大小、菜单和阅读进度恢复。
4. 进入全屏后导出中英文批注 PDF，确认保存对话框可见，导出后仍能阅读和退出全屏。
5. 多显示器环境下将窗口移到副屏再进入全屏，确认覆盖当前屏幕；退出后保留原窗口状态。

平台实现依据：[Apple WKPreferences](https://developer.apple.com/documentation/webkit/wkpreferences/iselementfullscreenenabled)、[Microsoft WebView2 全屏状态事件](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.containsfullscreenelementchanged)。
