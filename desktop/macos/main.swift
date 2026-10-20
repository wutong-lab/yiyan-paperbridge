import AppKit
import WebKit
import UniformTypeIdentifiers
import Darwin

// The lightweight native app reuses the local engine, models and documents.
final class PaperBridgeApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var splash: NSView!
    var statusLabel: NSTextField!
    var retryButton: NSButton!
    var spinner: NSProgressIndicator!
    var root: URL!
    var backend: Process?
    var logHandle: FileHandle?
    var service: URL?
    var ready = false
    var quitting = false
    var checkingQuit = false
    var startupGeneration = 0
    var pendingFiles: [URL] = []
    var importing = false
    var portable = false
    var bundledModels = false
    var sharedDataDirectory: String?
    var privatePreview = false
    var downloads: [ObjectIdentifier: (temporary: URL, destination: URL)] = [:]
    var activeDownloads = Set<ObjectIdentifier>()
    var downloadNavigation = false
    let network: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        config.connectionProxyDictionary = [:]
        return URLSession(configuration: config)
    }()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let config = Bundle.main.url(forResource: "Project", withExtension: "plist")
            .flatMap { NSDictionary(contentsOf: $0) }
        privatePreview = config?["PrivatePreview"] as? Bool == true
        makeMenus()
        makeWindow()
        portable = config?["Portable"] as? Bool == true
        bundledModels = config?["BundledModels"] as? Bool == true
        sharedDataDirectory = config?["SharedDataDirectory"] as? String
        if portable {
            do { try preparePortableProject(); start() }
            catch { startupError("准备本地工作室失败：\(error.localizedDescription)") }
            return
        }
        let path = UserDefaults.standard.string(forKey: "ProjectDirectory") ?? config?["ProjectDirectory"] as? String ?? ""
        root = URL(fileURLWithPath: path, isDirectory: true)
        if validProject(root) { start() } else { chooseProject() }
    }

    // Read-only application resources and writable user papers live separately.
    // No developer path, existing Python installation or model is required.
    func preparePortableProject() throws {
        let fm = FileManager.default
        guard let resources = Bundle.main.resourceURL else {
            throw NSError(domain: "PaperBridge", code: 1, userInfo: [NSLocalizedDescriptionKey: "应用资源不完整，请重新安装。"])
        }
        let override = ProcessInfo.processInfo.environment["PAPERBRIDGE_DATA_HOME"]
        root = override.map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent(sharedDataDirectory ?? (bundledModels ? "PaperBridge-Offline" : "PaperBridge"), isDirectory: true)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let engine = resources.appendingPathComponent("Engine")
        let revision = try String(contentsOf: engine.appendingPathComponent("BUILD_ID"), encoding: .utf8)
        let marker = root.appendingPathComponent(".engine-build")
        if (try? String(contentsOf: marker, encoding: .utf8)) != revision {
            for name in ["paperbridge", "scripts", "examples", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"] {
                let source = engine.appendingPathComponent(name)
                let destination = root.appendingPathComponent(name)
                let staging = root.appendingPathComponent(".install-" + name)
                if fm.fileExists(atPath: staging.path) { try fm.removeItem(at: staging) }
                try fm.copyItem(at: source, to: staging)
                if fm.fileExists(atPath: destination.path) { try fm.removeItem(at: destination) }
                try fm.moveItem(at: staging, to: destination)
            }
            try revision.write(to: marker, atomically: true, encoding: .utf8)
        }
        let runtime = resources.appendingPathComponent("Python", isDirectory: true)
        let link = root.appendingPathComponent(".venv")
        // Refresh the link when the application is moved, e.g. from Downloads
        // to Applications. Never remove a real user-created environment.
        if let old = try? fm.destinationOfSymbolicLink(atPath: link.path) {
            if old != runtime.path { try fm.removeItem(at: link); try fm.createSymbolicLink(at: link, withDestinationURL: runtime) }
        } else if !fm.fileExists(atPath: link.path) {
            try fm.createSymbolicLink(at: link, withDestinationURL: runtime)
        } else {
            throw NSError(domain: "PaperBridge", code: 2, userInfo: [NSLocalizedDescriptionKey: "工作室中的 .venv 不是应用创建的链接，请另选空的工作室目录。"])
        }
        for name in ["models", "data/jobs", "output/pdf", "tmp"] {
            try fm.createDirectory(at: root.appendingPathComponent(name), withIntermediateDirectories: true)
        }
        if privatePreview {
            let support = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            for name in ["qwen3-14b", "qwen3-4b"] {
                let target = root.appendingPathComponent("models/" + name)
                if fm.fileExists(atPath: target.path) { continue }
                for workspace in ["PaperBridge-Offline", "PaperBridge"] {
                    let existing = support.appendingPathComponent(workspace + "/models/" + name)
                    if fm.fileExists(atPath: existing.appendingPathComponent("installation.json").path) {
                        try fm.createSymbolicLink(at: target, withDestinationURL: existing)
                        break
                    }
                }
            }
        }
        // Full offline edition ships unchanged public weights. Link to the
        // installed app instead of creating a second 10 GB model copy.
        for name in ["qwen3-14b", "qwen3-4b"] {
            let model = resources.appendingPathComponent("Models/" + name, isDirectory: true)
            if fm.fileExists(atPath: model.appendingPathComponent("installation.json").path) {
                let target = root.appendingPathComponent("models/" + name)
                if let old = try? fm.destinationOfSymbolicLink(atPath: target.path) {
                    if old.contains("/Contents/Resources/Models/"), old != model.path {
                        try fm.removeItem(at: target)
                        try fm.createSymbolicLink(at: target, withDestinationURL: model)
                    }
                } else if !fm.fileExists(atPath: target.path) {
                    try fm.createSymbolicLink(at: target, withDestinationURL: model)
                }
            }
        }
        let layout = resources.appendingPathComponent("LayoutAssets")
        let layoutCache = root.appendingPathComponent(".cache/babeldoc")
        if fm.fileExists(atPath: layout.appendingPathComponent("ready.json").path),
           !fm.fileExists(atPath: layoutCache.appendingPathComponent("ready.json").path) {
            statusLabel.stringValue = "正在准备内置排版资源…"
            try fm.createDirectory(at: layoutCache, withIntermediateDirectories: true)
            for name in ["fonts", "cmap", "models", "tiktoken"] {
                let from = layout.appendingPathComponent(name)
                let to = layoutCache.appendingPathComponent(name)
                try fm.createDirectory(at: to, withIntermediateDirectories: true)
                for item in try fm.contentsOfDirectory(at: from, includingPropertiesForKeys: nil) {
                    let destination = to.appendingPathComponent(item.lastPathComponent)
                    if !fm.fileExists(atPath: destination.path) { try fm.copyItem(at: item, to: destination) }
                }
            }
            // Mark ready only once all public resources have been installed.
            try fm.copyItem(at: layout.appendingPathComponent("ready.json"), to: layoutCache.appendingPathComponent("ready.json"))
        }
        guard validProject(root) else {
            throw NSError(domain: "PaperBridge", code: 3, userInfo: [NSLocalizedDescriptionKey: "内置翻译环境不完整，请重新安装。"])
        }
    }

    func validProject(_ path: URL) -> Bool {
        FileManager.default.isExecutableFile(atPath: path.appendingPathComponent(".venv/bin/python").path)
            && FileManager.default.fileExists(atPath: path.appendingPathComponent("paperbridge/desktop_server.py").path)
    }

    func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 880),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = privatePreview ? "译研预览版 · 本地 / API" : "译研 · SCI 论文翻译"
        window.minSize = NSSize(width: 940, height: 650)
        window.setFrameAutosaveName("PaperBridgeMainWindow")
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.backgroundColor = NSColor(calibratedRed: 0.97, green: 0.98, blue: 0.96, alpha: 1)
        window.tabbingMode = .disallowed
        // Keep both new and restored windows inside the current Mac display.
        // A previously used external monitor may have been larger.
        if let visible = window.screen?.visibleFrame ?? NSScreen.main?.visibleFrame {
            var fitted = window.frame
            fitted.size.width = min(fitted.width, visible.width)
            fitted.size.height = min(fitted.height, visible.height)
            window.setFrame(fitted, display: false)
        }
        window.center()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.applicationNameForUserAgent = "PaperBridgeDesktop/" + (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.3.0")
        web = WKWebView(frame: window.contentView!.bounds, configuration: configuration)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        web.uiDelegate = self
        web.isHidden = true
        window.contentView!.addSubview(web)
        splash = NSView(frame: window.contentView!.bounds)
        splash.autoresizingMask = [.width, .height]
        window.contentView!.addSubview(splash)
        let title = NSTextField(labelWithString: "译研")
        title.font = .systemFont(ofSize: 38, weight: .semibold)
        title.textColor = NSColor(calibratedRed: 0.20, green: 0.35, blue: 0.28, alpha: 1)
        let subtitle = NSTextField(labelWithString: "论文留在本机，研究自在阅读。")
        subtitle.textColor = .secondaryLabelColor
        statusLabel = NSTextField(wrappingLabelWithString: "正在启动本地翻译服务…")
        statusLabel.alignment = .center
        statusLabel.preferredMaxLayoutWidth = 580
        statusLabel.textColor = .secondaryLabelColor
        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.startAnimation(nil)
        retryButton = NSButton(title: "重新启动", target: self, action: #selector(retryStart))
        retryButton.bezelStyle = .rounded
        retryButton.isHidden = true
        let stack = NSStackView(views: [title, subtitle, spinner, statusLabel, retryButton])
        stack.orientation = .vertical
        stack.spacing = 22
        stack.translatesAutoresizingMaskIntoConstraints = false
        splash.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: splash.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: splash.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualToConstant: 620)
        ])
        showWindow()
    }

    func makeMenus() {
        let main = NSMenu()
        func menu(_ title: String, _ items: [(String, Selector?, String)]) -> NSMenu {
            let holder = NSMenuItem(); main.addItem(holder)
            let submenu = NSMenu(title: title); holder.submenu = submenu
            for (name, selector, key) in items {
                if name == "-" { submenu.addItem(.separator()); continue }
                let item = NSMenuItem(title: name, action: selector, keyEquivalent: key)
                if selector != nil { item.target = self }
                submenu.addItem(item)
            }
            return submenu
        }
        _ = menu("译研", [("关于译研", #selector(about), ""), ("-", nil, ""),
                          ("检查更新…", #selector(checkForUpdates), ""), ("-", nil, ""), ("隐藏译研", #selector(hideApp), "h"), ("退出译研", #selector(quitApp), "q")])
        _ = menu("文件", [("导入论文 PDF…", #selector(openPDF), "o"), ("打开论文工作室", #selector(home), "1"),
                          ("API 配置…", #selector(apiSettings), ""),
                          ("-", nil, ""), ("在访达中查看导出文件", #selector(showOutput), ""),
                          ("在访达中查看软件目录", #selector(showProject), ""), ("关闭窗口", #selector(closeWindow), "w")])
        let edit = menu("编辑", [])
        for (title, action, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"),
                                     ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            edit.addItem(NSMenuItem(title: title, action: Selector(action), keyEquivalent: key))
        }
        _ = menu("显示", [("刷新页面", #selector(refresh), "r"), ("放大界面", #selector(zoomIn), "+"),
                          ("缩小界面", #selector(zoomOut), "-"), ("恢复界面大小", #selector(zoomReset), "0")])
        let windows = menu("窗口", [("显示译研", #selector(showMain), "")]); NSApp.windowsMenu = windows
        _ = menu("帮助", [("查看使用说明", #selector(help), ""), ("开源许可与来源", #selector(showLicenses), ""), ("查看启动日志", #selector(showLog), "")])
        NSApp.mainMenu = main
    }

    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "译研 PaperBridge", .applicationVersion: (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "") + " · Mac 桌面版",
            .credits: NSAttributedString(string: "SCI 论文英译中 · 本地模型 / 大模型 API · 原译对照\n保存常用 API 配置，选择翻译服务，在原有版面上阅读中文。")])
    }
    @objc func apiSettings() { showWindow(); web.evaluateJavaScript("showAPISettings()", completionHandler: nil) }
    @objc func checkForUpdates() {
        if privatePreview { alert("本地预览版", "此版本通过本机预览包更新。正式更新通道继续保留，模型沿用本机文件。"); return }
        guard let base = service, ready else { alert("正在启动", "请等待工作室准备好后检查更新。"); return }
        network.dataTask(with: base.appendingPathComponent("api/desktop/status")) { data, _, _ in
            let state = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async {
                guard state?["busy"] as? Bool == false else { self.alert("论文正在处理", "请等待当前论文完成后更新，已保存的内容会保留。"); return }
                self.lookupUpdate()
            }
        }.resume()
    }
    func lookupUpdate() {
        guard let resources = Bundle.main.resourceURL else { return }
        DispatchQueue.global(qos:.userInitiated).async {
            let process=Process()
            process.executableURL=resources.appendingPathComponent("Updater.app/Contents/MacOS/YiyanSetup")
            process.arguments=["--check-update"]
            let output=Pipe();process.standardOutput=output;process.standardError=FileHandle.nullDevice
            do {try process.run()} catch {DispatchQueue.main.async {self.alert("暂时无法检查更新","请稍后重试。")};return}
            let data=output.fileHandleForReading.readDataToEndOfFile();process.waitUntilExit()
            guard process.terminationStatus==0,data.count<=2_000_000,
                  let release=try? JSONSerialization.jsonObject(with:data) as? [String:Any],
                  release["schema"] as? Int==1,let version=release["version"] as? String,
                  version.range(of:"^\\d+\\.\\d+\\.\\d+$",options:.regularExpression) != nil else {
                DispatchQueue.main.async {self.alert("暂时无法检查更新","请连接网络后重试。已安装的版本可以继续离线使用。")};return
            }
            DispatchQueue.main.async {
                    let current = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
                    guard version.compare(current,options:.numeric) == .orderedDescending else {self.alert("已是最新版本", "译研 \(current) · 本地模型将继续保留。");return}
                    let prompt = NSAlert();prompt.messageText = "发现译研 \(version)";prompt.informativeText = "更新只下载应用与运行环境，保留已安装模型、论文和设置。";prompt.addButton(withTitle:"下载并更新");prompt.addButton(withTitle:"稍后")
                    guard prompt.runModal() == .alertFirstButtonReturn else {return}
                    do {
                        let cache = FileManager.default.urls(for:.cachesDirectory,in:.userDomainMask)[0].appendingPathComponent("PaperBridgeUpdater/"+UUID().uuidString)
                        try FileManager.default.createDirectory(at:cache,withIntermediateDirectories:true)
                        let helper = cache.appendingPathComponent("安装译研.app")
                        try FileManager.default.copyItem(at:resources.appendingPathComponent("Updater.app"),to:helper)
                        let config = NSWorkspace.OpenConfiguration();config.createsNewApplicationInstance = true;config.arguments = ["--update","--destination",Bundle.main.bundleURL.path,"--data",self.root.path]
                        NSWorkspace.shared.openApplication(at:helper,configuration:config) { _,error in DispatchQueue.main.async {if let error=error {self.alert("更新未能启动",error.localizedDescription)}else{NSApp.terminate(nil)}} }
                    } catch {self.alert("更新未能启动",error.localizedDescription)}
            }
        }
    }
    func captureSmoke() {
        let apiView = CommandLine.arguments.contains("--api-settings-smoke")
        let hardwareView = CommandLine.arguments.contains("--hardware-settings-smoke")
        if apiView { web.evaluateJavaScript("showAPISettings()", completionHandler: nil) }
        // UI fixture only: it does not override the engine's actual checks.
        if hardwareView, let input = ProcessInfo.processInfo.environment["PAPERBRIDGE_SMOKE_HARDWARE"],
           let data = input.data(using:.utf8), let value = try? JSONSerialization.jsonObject(with:data),
           let encoded = try? JSONSerialization.data(withJSONObject:value), let json = String(data:encoded,encoding:.utf8) {
            web.evaluateJavaScript("showHardwareSettings(\(json), {provider:'local',profile:'precision'},()=>{}); true", completionHandler:nil)
        }
        DispatchQueue.main.asyncAfter(deadline:.now()+1) {
            self.web.takeSnapshot(with:nil) { image,error in
                let folder=self.root.appendingPathComponent(".cache/desktop")
                let name = hardwareView ? "macos-hardware-smoke" : apiView ? "macos-api-settings-smoke" : "macos-ui-smoke"
                if let image=image, let tiff=image.tiffRepresentation,let bitmap=NSBitmapImageRep(data:tiff),let data=bitmap.representation(using:.png,properties:[:]) {try? data.write(to:folder.appendingPathComponent(name + ".png"))}
                guard let service=self.service else {exit(1)}
                self.network.dataTask(with:service.appendingPathComponent("api/status")) { data,response,_ in
                    var record:[String:Any] = ["loaded":error==nil,"version":Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "","workspace":self.root.path,"api_settings_view":apiView,"hardware_view_simulated":hardwareView]
                    if let data=data,let status=try? JSONSerialization.jsonObject(with:data) {record["status"]=status}
                    if let encoded=try? JSONSerialization.data(withJSONObject:record,options:[.prettyPrinted,.sortedKeys]) {try? encoded.write(to:folder.appendingPathComponent(name + ".json"))}
                    DispatchQueue.main.async {NSApp.terminate(nil)}
                }.resume()
            }
        }
    }
    @objc func hideApp() { NSApp.hide(nil) }
    @objc func quitApp() { NSApp.terminate(nil) }
    @objc func showMain() { showWindow() }
    @objc func closeWindow() { window.performClose(nil) }
    @objc func home() { if let base = service { web.load(URLRequest(url: base)); showWindow() } }
    @objc func refresh() { if ready { web.reload() } else { start() } }
    @objc func zoomIn() { web.pageZoom = min(1.5, web.pageZoom + 0.1) }
    @objc func zoomOut() { web.pageZoom = max(0.7, web.pageZoom - 0.1) }
    @objc func zoomReset() { web.pageZoom = 1 }
    @objc func showOutput() { if let root = root { NSWorkspace.shared.open(root.appendingPathComponent("output/pdf")) } }
    @objc func showProject() { if let root = root { NSWorkspace.shared.open(root) } }
    @objc func showLog() { if let root = root { NSWorkspace.shared.open(root.appendingPathComponent(".cache/desktop/app.log")) } }
    @objc func showLicenses() { if let root = root { NSWorkspace.shared.open(root.appendingPathComponent("THIRD_PARTY_NOTICES.md")) } }
    @objc func help() { alert("如何使用译研", "点击“选择论文 PDF”或按 ⌘O 导入论文，然后选择领域和翻译模式。完成后可左右对照，并通过“导出”保存 PDF。\n\n关闭窗口后任务继续运行；点击 Dock 图标可重新打开。使用 ⌘Q 退出软件。翻译过程中请保持 Mac 唤醒。\n\n" + (sharedDataDirectory != nil ? "模型已独立保存在本机工作室，更新应用时继续保留。通过“译研 → 检查更新”获取新版本，更新无需重新下载模型。论文、译稿和模型保存在“资源库/Application Support/PaperBridge-Offline”中。" : bundledModels ? "此版本内置 14B、4B 模型与排版资源，可以离线翻译，无需再下载模型。内置模型随应用保存，请勿在运行时移动或删除应用。论文和任务保存在本机“资源库/Application Support/PaperBridge-Offline”中。" : portable ? "此版本内置运行环境。首次使用点击“准备本地翻译资源”下载公开模型；准备完成后可离线翻译。论文与模型保存在本机“资源库/Application Support/PaperBridge”中。" : "此版本复用本机现有 Python 环境和模型；请保留原“翻译软件”目录。")) }
    @objc func retryStart() {
        if portable {
            do { try preparePortableProject(); start() }
            catch { startupError("准备本地工作室失败：\(error.localizedDescription)") }
        } else if validProject(root) { start() } else { chooseProject() }
    }
    func showWindow() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    func windowShouldClose(_ sender: NSWindow) -> Bool { sender.orderOut(nil); return false }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWindow(); return true }

    func chooseProject() {
        let panel = NSOpenPanel()
        panel.title = "选择原来的“翻译软件”文件夹"
        panel.message = "译研需要使用你已安装的本地模型与翻译环境。"
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        if panel.runModal() == .OK, let selected = panel.url, validProject(selected) {
            root = selected
            UserDefaults.standard.set(selected.path, forKey: "ProjectDirectory")
            start()
        } else { startupError("未找到翻译环境。请选择包含 .venv、models 和 paperbridge 的原软件目录。") }
    }

    func start() {
        startupGeneration += 1
        let generation = startupGeneration
        ready = false; service = nil
        splash.isHidden = false; web.isHidden = true; retryButton.isHidden = true
        statusLabel.stringValue = portable ? "正在启动本地论文工作室…" : "正在启动本地翻译服务…\n首次打开时，请在 macOS 提示中允许访问“文稿”，以读取本地模型和论文。"
        spinner.startAnimation(nil)
        if backend?.isRunning == true {
            pollStartup(generation, deadline: Date().addingTimeInterval(30))
            return
        }
        let stateURL = root.appendingPathComponent(".cache/desktop/server.json")
        let state = (try? Data(contentsOf: stateURL)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let port = state?["port"] as? Int ?? 8765
        let ports = [port] + (8765...8775).filter { $0 != port }
        findExisting(ports, generation: generation)
    }

    func findExisting(_ ports: [Int], generation: Int) {
        guard generation == startupGeneration else { return }
        guard let port = ports.first else { launchService(generation); return }
        let candidate = URL(string: "http://127.0.0.1:\(port)")!
        health(candidate) { info in
            guard generation == self.startupGeneration else { return }
            if info != nil { self.loadService(candidate) }
            else { self.findExisting(Array(ports.dropFirst()), generation: generation) }
        }
    }

    func health(_ base: URL, completion: @escaping ([String: Any]?) -> Void) {
        network.dataTask(with: base.appendingPathComponent("api/desktop/status")) { data, response, error in
            let value = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            let valid = (response as? HTTPURLResponse)?.statusCode == 200 && value?["application"] as? String == "PaperBridge"
                && value?["workspace"] as? String == self.canonicalWorkspacePath()
            DispatchQueue.main.async { completion(valid ? value : nil) }
        }.resume()
    }

    func canonicalWorkspacePath() -> String {
        // Foundation may abbreviate /private/tmp to /tmp. The backend uses
        // POSIX realpath; use the same identity on symlinked install paths.
        guard let resolved = realpath(root.path, nil) else { return root.path }
        defer { free(resolved) }
        return String(cString: resolved)
    }

    func launchService(_ generation: Int) {
        do {
            let cache = root.appendingPathComponent(".cache/desktop")
            try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
            let log = cache.appendingPathComponent("app.log")
            if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil) }
            logHandle = try FileHandle(forWritingTo: log)
            logHandle?.seekToEndOfFile()
            logHandle?.write(Data("\n[desktop] launching at \(Date()) from \(root.path)\n".utf8))
            let process = Process()
            process.executableURL = root.appendingPathComponent(".venv/bin/python")
            process.currentDirectoryURL = root
            process.arguments = ["-u", "-m", "paperbridge.desktop_server", "--parent-pid", String(ProcessInfo.processInfo.processIdentifier),
                                 "--state-file", cache.appendingPathComponent("server.json").path]
            var env = ProcessInfo.processInfo.environment
            env["PYTHONUNBUFFERED"] = "1"
            env["PAPERBRIDGE_HOME"] = root.path
            env["PAPERBRIDGE_APP_VERSION"] = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
            if portable, let resources = Bundle.main.resourceURL {
                env["PAPERBRIDGE_BUNDLE_RESOURCES"] = resources.path
                // Keep the signed application read-only, including imported
                // libraries; writable caches belong in the user workspace.
                env["PYTHONDONTWRITEBYTECODE"] = "1"
            }
            env.removeValue(forKey: "PYTHONHOME")
            env.removeValue(forKey: "PYTHONPATH")
            process.environment = env
            process.standardOutput = logHandle; process.standardError = logHandle
            process.terminationHandler = { finished in
                DispatchQueue.main.async {
                    if !self.quitting && self.backend === finished {
                        self.backend = nil
                        self.startupError("本地服务已停止。可以重新启动，已保存的论文仍在原目录中。若反复失败，请查看“帮助 → 查看启动日志”。")
                    }
                }
            }
            backend = process
            try process.run()
            logHandle?.write(Data("[desktop] started python PID \(process.processIdentifier)\n".utf8))
            // First launch may be paused by macOS's Documents permission sheet.
            pollStartup(generation, deadline: Date().addingTimeInterval(300))
        } catch { backend = nil; startupError("启动失败：\(error.localizedDescription)") }
    }

    func pollStartup(_ generation: Int, deadline: Date) {
        guard generation == startupGeneration, backend?.isRunning == true else { return }
        if Date() > deadline {
            backend?.terminationHandler = nil; backend?.terminate(); backend = nil
            startupError("本地服务启动时间较长。请查看启动日志后重试。"); return
        }
        let path = root.appendingPathComponent(".cache/desktop/server.json")
        if let data = try? Data(contentsOf: path), let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let pid = info["pid"] as? Int, pid == Int(backend!.processIdentifier), let port = info["port"] as? Int,
           let base = URL(string: "http://127.0.0.1:\(port)") {
            health(base) { status in
                guard generation == self.startupGeneration else { return }
                if status?["pid"] as? Int == pid && status?["instance_id"] as? String == info["instance_id"] as? String { self.loadService(base) }
                else { DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { self.pollStartup(generation, deadline: deadline) } }
            }
        } else { DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { self.pollStartup(generation, deadline: deadline) } }
    }

    func loadService(_ base: URL) {
        service = base
        statusLabel.stringValue = "正在打开论文工作室…"
        web.load(URLRequest(url: base))
    }
    func startupError(_ text: String) {
        if let root = root {
            let folder = root.appendingPathComponent(".cache/desktop")
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let path = folder.appendingPathComponent("startup-error.log")
            try? ("\(Date()): \(text)\n").write(to: path, atomically: true, encoding: .utf8)
        }
        ready = false; splash.isHidden = false; web.isHidden = true
        spinner.stopAnimation(nil); statusLabel.stringValue = text; retryButton.isHidden = false
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        ready = true; splash.isHidden = true; web.isHidden = false; spinner.stopAnimation(nil)
        importNext()
        if CommandLine.arguments.contains("--smoke-test") { captureSmoke() }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        // WebKit deliberately cancels frame navigation when handing a response
        // to WKDownload. This is a successful export, not a disconnected app.
        let detail = error as NSError
        if detail.domain == "WebKitErrorDomain" && detail.code == 102 && downloadNavigation {
            downloadNavigation = false
            return
        }
        if (error as NSError).code != NSURLErrorCancelled { startupError("页面未能打开：\(error.localizedDescription)") }
    }

    func isLocal(_ url: URL) -> Bool { url.scheme == service?.scheme && url.host == "127.0.0.1" && url.port == service?.port }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if isLocal(url) {
            if navigationAction.shouldPerformDownload { downloadNavigation = true }
            decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow)
        }
        else { decisionHandler(.cancel); if ["https", "http", "mailto"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) } }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        let shouldDownload = disposition.lowercased().contains("attachment") || !navigationResponse.canShowMIMEType
        if shouldDownload { downloadNavigation = true }
        decisionHandler(shouldDownload ? .download : .allow)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if isLocal(url) { webView.load(URLRequest(url: url)) }
            else if ["https", "http", "mailto"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        }
        return nil
    }

    @objc func openPDF() {
        let panel = pdfPanel()
        panel.beginSheetModal(for: window) { result in
            if result == .OK { self.pendingFiles.append(contentsOf: panel.urls); self.importNext() }
        }
    }
    func pdfPanel() -> NSOpenPanel {
        let panel = NSOpenPanel()
        panel.title = "导入论文"; panel.prompt = "导入论文"
        panel.allowedContentTypes = [.pdf]; panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false; panel.canChooseFiles = true
        return panel
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = pdfPanel(); panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }
    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        pendingFiles.append(contentsOf: filenames.map { URL(fileURLWithPath: $0) })
        if window != nil { showWindow(); importNext() }
        sender.reply(toOpenOrPrint: .success)
    }
    func importNext() {
        guard ready, !importing, !pendingFiles.isEmpty, let base = service else { return }
        let file = pendingFiles.removeFirst()
        guard file.pathExtension.lowercased() == "pdf" else { alert("请选择 PDF", "当前支持文字型英文论文 PDF。"); importNext(); return }
        if let size = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize, size > 80 * 1024 * 1024 {
            alert("文件较大", "单篇论文最多支持 80 MB。"); importNext(); return
        }
        importing = true; window.title = "正在导入 \(file.lastPathComponent)…"
        var request = URLRequest(url: base.appendingPathComponent("api/documents"))
        request.httpMethod = "POST"; request.timeoutInterval = 180
        request.setValue("application/pdf", forHTTPHeaderField: "Content-Type")
        request.setValue(file.lastPathComponent.addingPercentEncoding(withAllowedCharacters: .alphanumerics), forHTTPHeaderField: "X-Filename")
        network.uploadTask(with: request, fromFile: file) { data, response, error in
            let info = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async {
                self.window.title = "译研 · SCI 论文翻译"
                if let id = info?["id"] as? String, (response as? HTTPURLResponse)?.statusCode == 200,
                   id.range(of: "^[0-9a-f]{32}$", options: .regularExpression) != nil {
                    // Reuse the web batch queue and reading navigation for native imports.
                    self.web.callAsyncJavaScript("await desktopImported(id)", arguments: ["id": id], in: nil, in: .page) { result in
                        self.importing = false
                        if case .failure = result {
                            self.ready = false
                            self.web.load(URLRequest(url: URL(string: "/?job=\(id)", relativeTo: base)!))
                            return
                        }
                        self.importNext()
                    }
                } else { self.importing = false; self.alert("导入未完成", info?["detail"] as? String ?? error?.localizedDescription ?? "请重新选择论文后重试。"); self.importNext() }
            }
        }.resume()
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { attach(download) }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { attach(download) }
    func attach(_ download: WKDownload) { download.delegate = self; activeDownloads.insert(ObjectIdentifier(download)) }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.title = "保存导出文件"; panel.prompt = "保存"
        panel.nameFieldStringValue = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        panel.canCreateDirectories = true
        if panel.nameFieldStringValue.lowercased().hasSuffix(".pdf") { panel.allowedContentTypes = [.pdf] }
        panel.beginSheetModal(for: window) { result in
            guard result == .OK, let final = panel.url else { self.activeDownloads.remove(ObjectIdentifier(download)); completionHandler(nil); return }
            let temporary = final.deletingLastPathComponent().appendingPathComponent(".paperbridge-\(UUID().uuidString).download")
            self.downloads[ObjectIdentifier(download)] = (temporary, final)
            completionHandler(temporary)
        }
    }
    func downloadDidFinish(_ download: WKDownload) {
        activeDownloads.remove(ObjectIdentifier(download))
        guard let item = downloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
        do {
            if FileManager.default.fileExists(atPath: item.destination.path) {
                _ = try FileManager.default.replaceItemAt(item.destination, withItemAt: item.temporary)
            } else { try FileManager.default.moveItem(at: item.temporary, to: item.destination) }
            NSWorkspace.shared.activateFileViewerSelecting([item.destination])
        } catch { alert("保存未完成", "完整文件暂存在 \(item.temporary.path)\n\(error.localizedDescription)") }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        activeDownloads.remove(ObjectIdentifier(download))
        if let item = downloads.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: item.temporary) }
        if (error as NSError).code != NSURLErrorCancelled { alert("导出未完成", error.localizedDescription) }
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) { alert("译研", message); completionHandler() }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let box = NSAlert(); box.messageText = "译研"; box.informativeText = message
        box.addButton(withTitle: "确定"); box.addButton(withTitle: "取消")
        completionHandler(box.runModal() == .alertFirstButtonReturn)
    }
    func alert(_ title: String, _ message: String) {
        let box = NSAlert(); box.messageText = title; box.informativeText = message; box.addButton(withTitle: "知道了"); box.runModal()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateNow }
        if importing || !pendingFiles.isEmpty || !activeDownloads.isEmpty {
            showWindow(); alert("正在导入或保存文件", "请等文件操作完成后再退出。"); return .terminateCancel
        }
        guard let base = service else { quitting = true; return .terminateNow }
        if checkingQuit { return .terminateCancel }
        checkingQuit = true
        health(base) { info in
            self.checkingQuit = false
            if info?["busy"] as? Bool == true || (info == nil && self.backend?.isRunning == true) {
                self.showWindow()
                self.alert("当前任务还在运行", "请等待翻译或模型准备完成。若想中止翻译，请先在页面中点击“停止翻译”，等保存后再退出。关闭窗口可以让任务继续在后台运行。")
                self.checkingQuit = false
            } else {
                self.web.evaluateJavaScript("typeof desktopBusy === 'function' && desktopBusy()") { value, error in
                    if value as? Bool == true {
                        self.showWindow(); self.alert("正在处理文件", "请等待当前导入、批量提交或清理完成后再退出。")
                        self.checkingQuit = false
                    } else { self.checkingQuit = false; self.quitting = true; NSApp.terminate(nil) }
                }
            }
        }
        // Keep the normal run loop alive while WebKit completes its busy probe.
        return .terminateCancel
    }
    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        backend?.terminationHandler = nil
        if backend?.isRunning == true { backend?.terminate() }
        try? logHandle?.close()
    }
}

let application = NSApplication.shared
let delegate = PaperBridgeApp()
application.delegate = delegate
application.run()
