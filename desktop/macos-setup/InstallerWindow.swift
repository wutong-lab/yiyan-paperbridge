import AppKit

final class InstallerBackground: NSView {
    override var isOpaque:Bool {true}
    override func draw(_ dirtyRect:NSRect) {NSColor(calibratedRed:0.97,green:0.98,blue:0.96,alpha:1).setFill();dirtyRect.fill()}
}

final class MacInstallerWindow: NSObject, NSWindowDelegate {
    var window:NSWindow!
    let standard=NSButton(radioButtonWithTitle:"4B 标准版",target:nil,action:nil)
    let precision=NSButton(radioButtonWithTitle:"14B 精译版",target:nil,action:nil)
    let location=NSTextField(labelWithString:"")
    let status=NSTextField(wrappingLabelWithString:"准备就绪")
    let detail=NSTextField(wrappingLabelWithString:"首次安装需要联网，装好后直接离线用。")
    let route=NSTextField(wrappingLabelWithString:"独立直连 · 自动切换备用线路 · 保持系统代理设置不变")
    let progress=NSProgressIndicator()
    let start=NSButton(title:"下载并安装 4B",target:nil,action:nil)
    let pause=NSButton(title:"暂停下载",target:nil,action:nil)
    let browse=NSButton(title:"更改位置",target:nil,action:nil)
    let clean=NSButton(checkboxWithTitle:"装好后清理下载缓存",target:nil,action:nil)
    let launch=NSButton(checkboxWithTitle:"安装完成后打开译研",target:nil,action:nil)
    var job:MacInstallation?,working=false,committing=false,finished=false,closeAfterPause=false
    var profile:String {precision.state == .on ? "precision":"standard"}
    var destination:URL
    var dataHome:URL
    var cache:URL
    let updateOnly:Bool
    let smoke:Bool
    let evidence:URL?
    let preview:URL?
    var started=Date(),lastProgress=Date.distantPast
    var rateDate:Date?,rateBytes:Int64=0,smoothedRate:Double=0
    init(arguments:[String]=CommandLine.arguments) {
        func option(_ key:String)->String? {guard let i=arguments.firstIndex(of:key),i+1<arguments.count else{return nil};return arguments[i+1]}
        updateOnly=arguments.contains("--update")
        smoke=arguments.contains("--install-smoke")
        evidence=option("--evidence").map{URL(fileURLWithPath:$0)}
        preview=option("--preview-smoke").map{URL(fileURLWithPath:$0)}
        let fm=FileManager.default
        let apps=fm.isWritableFile(atPath:"/Applications") ? URL(fileURLWithPath:"/Applications") : fm.homeDirectoryForCurrentUser.appendingPathComponent("Applications")
        destination=option("--destination").map{URL(fileURLWithPath:$0)} ?? apps.appendingPathComponent("译研.app")
        dataHome=option("--data").map{URL(fileURLWithPath:$0)} ?? fm.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("PaperBridge-Offline")
        cache=option("--cache").map{URL(fileURLWithPath:$0)} ?? fm.urls(for:.cachesDirectory,in:.userDomainMask)[0].appendingPathComponent("PaperBridgeInstaller/1.8")
        super.init()
        if smoke && (evidence==nil || option("--destination")==nil || option("--data")==nil || option("--cache")==nil) {fputs("Smoke mode needs explicit paths\n",stderr);exit(2)}
        if option("--install-smoke")=="precision" {precision.state = .on}else{standard.state = .on}
        if updateOnly, let data=try? Data(contentsOf:dataHome.appendingPathComponent("macos-installation.json")),let record=try? JSONSerialization.jsonObject(with:data) as? [String:Any],record["default_profile"] as? String == "precision" {precision.state = .on;standard.state = .off}
        makeWindow()
        if let preview=preview {DispatchQueue.main.asyncAfter(deadline:.now()+0.4){self.capture(preview,"macos-setup-4b.png");self.precision.state = .on;self.standard.state = .off;self.selectModel();self.capture(preview,"macos-setup-14b.png");NSApp.terminate(nil)}}
        else if smoke {DispatchQueue.main.asyncAfter(deadline:.now()+0.4){self.capture(self.evidence!,"setup-selected.png");self.start.performClick(nil)}}
    }
    func makeWindow() {
        let green=NSColor(calibratedRed:0.16,green:0.40,blue:0.31,alpha:1)
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:760,height:740),styleMask:[.titled,.closable,.miniaturizable],backing:.buffered,defer:false)
        window.appearance=NSAppearance(named:.aqua)
        window.contentView=InstallerBackground(frame:window.contentView!.bounds)
        window.title=updateOnly ? "译研 · 更新软件":"译研 · 一键下载安装";window.delegate=self;window.isReleasedWhenClosed=false
        window.backgroundColor=NSColor(calibratedRed:0.97,green:0.98,blue:0.96,alpha:1)
        let title=NSTextField(labelWithString:updateOnly ? "更新译研，保留本地模型。":"选择模型，接下来交给译研。")
        title.font = .systemFont(ofSize:27,weight:.semibold);title.textColor=green
        let subtitle=NSTextField(labelWithString:updateOnly ? "只更新应用与运行环境，模型、论文和设置继续保留。":"应用、模型与排版环境自动装好，论文留在你的 Mac。")
        subtitle.textColor = .secondaryLabelColor
        func card(_ choice:NSButton,_ text:String)->NSView {
            choice.target=self;choice.action=#selector(chooseModel(_:));choice.font = .systemFont(ofSize:17,weight:.semibold);choice.contentTintColor=green
            let label=NSTextField(wrappingLabelWithString:text);label.textColor = .secondaryLabelColor
            let stack=NSStackView(views:[choice,label]);stack.orientation = .vertical;stack.alignment = .leading;stack.spacing=12;stack.edgeInsets=NSEdgeInsets(top:18,left:18,bottom:18,right:18)
            stack.wantsLayer=true;stack.layer?.backgroundColor=NSColor(calibratedRed:0.91,green:0.94,blue:0.90,alpha:1).cgColor;stack.layer?.cornerRadius=12
            stack.widthAnchor.constraint(equalToConstant:336).isActive=true;return stack
        }
        let choices=NSStackView(views:[card(standard,"日常科研阅读\n模型约 2.15 GB · 建议 16 GB 内存"),card(precision,"复杂长句与专业表达\n模型约 7.86 GB · 建议 24 GB 内存")]);choices.spacing=16;choices.distribution = .fillEqually
        let hint=NSTextField(labelWithString:"Apple Silicon（M 系列）· 模型独立保存，后续更新应用无需重下模型")
        hint.font = .systemFont(ofSize:12);hint.textColor = .secondaryLabelColor
        let pathTitle=NSTextField(labelWithString:"安装位置")
        location.lineBreakMode = .byTruncatingMiddle;location.stringValue=destination.deletingLastPathComponent().path;location.textColor = .secondaryLabelColor
        browse.target=self;browse.action=#selector(chooseLocation)
        let pathRow=NSStackView(views:[location,browse]);pathRow.distribution = .fill;location.setContentHuggingPriority(.defaultLow,for:.horizontal)
        clean.state = .on;launch.state = smoke ? .off:.on
        let options=NSStackView(views:[clean,launch]);options.spacing=24
        status.font = .systemFont(ofSize:15,weight:.medium);status.textColor=green
        detail.textColor = .secondaryLabelColor;detail.font = .systemFont(ofSize:12)
        route.textColor = .secondaryLabelColor;route.font = .systemFont(ofSize:11)
        progress.isIndeterminate=false;progress.minValue=0;progress.maxValue=1;progress.style = .bar
        progress.widthAnchor.constraint(equalToConstant:688).isActive=true
        pause.target=self;pause.action=#selector(pauseDownload);pause.isEnabled=false
        start.target=self;start.action=#selector(beginInstall);start.bezelStyle = .rounded;start.keyEquivalent="\r";start.contentTintColor=green
        let spacer=NSView();let actions=NSStackView(views:[spacer,pause,start]);spacer.setContentHuggingPriority(.defaultLow,for:.horizontal);actions.spacing=12
        let installerVersion=Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "1.8.4"
        let footer=NSTextField(labelWithString:"译研 PaperBridge · 安装向导 \(installerVersion) · 首次安装联网，之后离线使用")
        footer.font = .systemFont(ofSize:11);footer.textColor = .secondaryLabelColor
        let stack=NSStackView(views:[title,subtitle,choices,hint,pathTitle,pathRow,options,status,detail,route,progress,actions,footer]);stack.orientation = .vertical;stack.alignment = .leading;stack.spacing=16;stack.translatesAutoresizingMaskIntoConstraints=false
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.topAnchor.constraint(equalTo:window.contentView!.topAnchor,constant:30),stack.leadingAnchor.constraint(equalTo:window.contentView!.leadingAnchor,constant:36),stack.trailingAnchor.constraint(equalTo:window.contentView!.trailingAnchor,constant:-36),pathRow.widthAnchor.constraint(equalTo:stack.widthAnchor),actions.widthAnchor.constraint(equalTo:stack.widthAnchor)])
        if updateOnly {standard.isEnabled=false;precision.isEnabled=false;start.title="检查并更新应用";detail.stringValue="模型独立保存，本次只下载应用。"}
        else {selectModel()}
        window.center();window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
    }
    @objc func chooseModel(_ sender:NSButton) {standard.state = sender === standard ? .on:.off;precision.state = sender === precision ? .on:.off;selectModel()}
    func selectModel() {if !working && !updateOnly {finished=false;start.title="下载并安装 "+(profile=="precision" ? "14B":"4B")}}
    @objc func chooseLocation() {let panel=NSOpenPanel();panel.canChooseDirectories=true;panel.canChooseFiles=false;panel.prompt="安装到此处";panel.directoryURL=destination.deletingLastPathComponent();if panel.runModal() == .OK, let url=panel.url {destination=url.appendingPathComponent("译研.app");location.stringValue=url.path}}
    @objc func pauseDownload() {job?.transfer.pause()}
    func enable(_ enabled:Bool) {standard.isEnabled=enabled && !updateOnly;precision.isEnabled=enabled && !updateOnly;browse.isEnabled=enabled;clean.isEnabled=enabled;launch.isEnabled=enabled}
    @objc func beginInstall() {
        if finished {NSWorkspace.shared.open(destination);return}
        guard !working else{return};working=true;committing=false;enable(false);start.isEnabled=false;pause.isEnabled=true;started=Date()
        let runner=MacInstallation();job=runner;rateDate=nil;smoothedRate=0
        runner.transfer.routeChanged={message in DispatchQueue.main.async{self.route.stringValue=message;self.rateDate=nil;self.smoothedRate=0}}
        runner.stageChanged={text in DispatchQueue.main.async{self.status.stringValue=text;self.writeState(["status":"working","stage":text])}}
        runner.progress={bytes,total in DispatchQueue.main.async {
            self.progress.doubleValue=Double(bytes)/Double(total)
            let now=Date()
            if self.rateDate==nil || bytes<self.rateBytes {self.rateDate=now;self.rateBytes=bytes;self.smoothedRate=0}
            if now.timeIntervalSince(self.lastProgress)>0.25 {
                let interval=now.timeIntervalSince(self.rateDate!)
                if interval>=1 {
                    let sample=Double(max(0,bytes-self.rateBytes))/interval
                    self.smoothedRate=self.smoothedRate==0 ? sample:0.3*sample+0.7*self.smoothedRate
                    self.rateDate=now;self.rateBytes=bytes
                }
                self.detail.stringValue=downloadProgressText(bytes:bytes,total:total,speed:self.smoothedRate)
                self.lastProgress=now;self.writeState(["status":"downloading","bytes":bytes,"total_bytes":total,"bytes_per_second":self.smoothedRate,"route":self.route.stringValue])
            }
        }}
        runner.applicationCommitStarted={DispatchQueue.main.async{self.committing=true;self.pause.isEnabled=false;self.progress.isIndeterminate=true;self.progress.startAnimation(nil);self.detail.stringValue="正在安装与校验，请保持向导打开。"}}
        let selected=profile, target=destination, data=dataHome, cacheFolder=cache, shouldClean=clean.state == .on, shouldOpen=launch.state == .on
        DispatchQueue.global(qos:.userInitiated).async {
            do {
                let result=try runner.install(profile:selected,destination:target,dataHome:data,cache:cacheFolder,updateOnly:self.updateOnly,cleanCache:shouldClean)
                DispatchQueue.main.async {
                    self.finished=true;self.progress.stopAnimation(nil);self.progress.isIndeterminate=false;self.progress.doubleValue=1
                    self.status.stringValue=self.updateOnly ? "更新完成，模型与论文已保留。":"安装完成，可以直接离线翻译。"
                    self.detail.stringValue="模型保存在本机独立目录，今后更新应用无需重新下载。";self.start.title="打开译研"
                    self.writeState(result.json);if let evidence=self.evidence {self.writeJSON(result.json,to:evidence.appendingPathComponent("MACOS-SETUP-REPORT.json"));self.capture(evidence,"setup-complete.png")}
                    self.finish();if shouldOpen {NSWorkspace.shared.open(target)}
                }
            } catch {
                DispatchQueue.main.async {
                    self.progress.stopAnimation(nil);self.progress.isIndeterminate=false
                    self.status.stringValue=(error as? SetupFailure).map{switch $0 {case .paused:return "下载已暂停";default:return "暂时未能完成安装"}} ?? "暂时未能完成安装"
                    self.detail.stringValue=error.localizedDescription;self.start.title="继续下载并安装"
                    self.writeState(["status":"error","message":error.localizedDescription])
                    if let evidence=self.evidence {try? error.localizedDescription.write(to:evidence.appendingPathComponent("setup-error.log"),atomically:true,encoding:.utf8);self.capture(evidence,"setup-error.png")}
                    self.finish(failed:true)
                }
            }
        }
    }
    func finish(failed:Bool=false) {working=false;committing=false;enable(true);start.isEnabled=true;pause.isEnabled=false;job=nil;if smoke {exit(failed ? 1:0)};if closeAfterPause {window.close()}}
    func writeJSON(_ object:[String:Any],to url:URL) {try? FileManager.default.createDirectory(at:url.deletingLastPathComponent(),withIntermediateDirectories:true);if let data=try? JSONSerialization.data(withJSONObject:object,options:[.prettyPrinted,.sortedKeys]) {try? data.write(to:url,options:.atomic)}}
    func writeState(_ object:[String:Any]) {if let evidence=evidence {writeJSON(object,to:evidence.appendingPathComponent("setup-progress.json"))}}
    func capture(_ folder:URL,_ name:String) {try? FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true);guard let view=window.contentView,let image=view.bitmapImageRepForCachingDisplay(in:view.bounds) else{return};view.cacheDisplay(in:view.bounds,to:image);try? image.representation(using:.png,properties:[:])?.write(to:folder.appendingPathComponent(name))}
    func windowShouldClose(_ sender:NSWindow)->Bool {if working {if !committing {closeAfterPause=true;job?.transfer.pause()}else{detail.stringValue="正在安装，请等待完成后关闭向导。"};return false};NSApp.terminate(nil);return true}
}
