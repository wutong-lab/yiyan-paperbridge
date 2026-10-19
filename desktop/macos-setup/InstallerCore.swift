import Foundation
import AppKit

import CryptoKit
import Darwin

enum SetupFailure: LocalizedError {
    case message(String), paused
    var errorDescription: String? { switch self { case .message(let s): return s; case .paused: return "下载已暂停，进度已保存。" } }
}
struct RemoteAsset: Codable {
    let name: String, url: String, sha256: String
    let bytes: Int64
    func validate() throws {
        guard name == URL(fileURLWithPath: name).lastPathComponent, !name.hasPrefix("."), !name.contains("\\"), bytes > 0,
              sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { throw SetupFailure.message("下载清单无效。") }
        try trustedURL(url)
    }
}
struct AppRelease: Codable {
    let schema: Int, version: String, minimum_macos: String, architecture: String
    let app: RemoteAsset
    let models_url: String
    func validate() throws {
        guard schema == 1, architecture == "arm64", version.range(of: "^\\d+\\.\\d+\\.\\d+$", options: .regularExpression) != nil,
              app.name == "Yiyan-\(version)-AppleSilicon-App.tar.gz" else { throw SetupFailure.message("软件版本清单无效。") }
        try app.validate(); try trustedURL(models_url)
    }
}
struct SnapshotFile: Codable { let file: String, sha256: String; let size: Int64 }
struct ModelSnapshot: Codable { let repo: String, revision: String; let files: [SnapshotFile] }
struct ModelEdition: Codable {
    let directory: String
    let snapshot: ModelSnapshot
    let parts: [RemoteAsset]
    func validate(_ profile: String) throws {
        let expected = profile == "precision" ? "qwen3-14b" : "qwen3-4b"
        let revision = profile == "precision" ? "ba63a5141812f9870287df53341123a71ba41433" : "52a5ab34fa604bc8af6d3ce0cac0cab10b7eb495"
        guard directory == expected, snapshot.revision == revision, snapshot.repo == (profile == "precision" ? "Qwen/Qwen3-14B-MLX-4bit" : "Qwen/Qwen3-4B-MLX-4bit"), !parts.isEmpty, !snapshot.files.isEmpty else { throw SetupFailure.message("模型版本与清单不一致。") }
        guard Set(snapshot.files.map{$0.file}).count == snapshot.files.count, Set(parts.map{$0.name}).count == parts.count else { throw SetupFailure.message("模型清单包含重复文件。") }
        for file in snapshot.files {
            guard file.file == URL(fileURLWithPath:file.file).lastPathComponent, !file.file.hasPrefix("."), !file.file.contains("\\"), file.size > 0, file.sha256.range(of:"^[0-9a-f]{64}$",options:.regularExpression) != nil else { throw SetupFailure.message("模型文件名无效。") }
        }
        for part in parts { try part.validate(); guard part.name.hasPrefix(directory + ".tar.part") else { throw SetupFailure.message("模型分块名称无效。") } }
    }
}
struct ModelCatalog: Codable {
    let schema: Int
    let editions: [String:ModelEdition]
    func validate() throws {
        guard schema == 1, Set(editions.keys) == Set(["standard","precision"]) else { throw SetupFailure.message("模型清单无效。") }
        for (profile,edition) in editions { try edition.validate(profile) }
    }
}
let repositoryPrefix = "https://github.com/hengyuwu-dot/yiyan-paperbridge/releases/download/"
func trustedURL(_ value: String) throws {
    guard let url=URL(string:value), url.scheme == "https", url.user == nil, url.password == nil,
          value.hasPrefix(repositoryPrefix) || value == publishedMacManifest || value == "https://raw.githubusercontent.com/hengyuwu-dot/yiyan-paperbridge/main/release-assets/macos-update.json" else { throw SetupFailure.message("下载地址不属于译研的发布仓库。") }
}
func fetchJSON<T: Decodable>(_ url: String, as: T.Type) throws -> T {
    try trustedURL(url)
    var last:Error=SetupFailure.message("暂时无法取得版本信息，请检查网络后重试。")
    for address in installerMetadataURLs(url) {
        var data:Data?,failure:Error?
        let done=DispatchSemaphore(value:0)
        let config=installerNetworkConfiguration();config.timeoutIntervalForRequest=8;config.timeoutIntervalForResource=12
        let session=URLSession(configuration:config)
        session.dataTask(with:address) { body,response,error in
            if let http=response as? HTTPURLResponse,http.statusCode==200,let body=body,body.count<=2_000_000 {data=body}
            else {failure=error ?? last}
            done.signal()
        }.resume()
        done.wait();session.finishTasksAndInvalidate()
        do {if let failure=failure {throw failure};return try JSONDecoder().decode(T.self,from:data!)} catch {last=error}
    }
    throw last
}
struct UpdateChannel: Decodable { let schema:Int;let manifest_url:String }
func latestRelease(allowBundled:Bool=true) throws -> AppRelease {
    // The standalone DMG carries its release manifest, so first installation does
    // not depend on access to raw.githubusercontent.com or a mutable mirror index.
    if allowBundled,let path=Bundle.main.url(forResource:"BootstrapRelease",withExtension:"json") {
        let release=try JSONDecoder().decode(AppRelease.self,from:Data(contentsOf:path));try release.validate();return release
    }
    if let release:AppRelease=try? fetchJSON(publishedMacManifest,as:AppRelease.self) {try release.validate();return release}
    let channel:UpdateChannel=try fetchJSON("https://raw.githubusercontent.com/hengyuwu-dot/yiyan-paperbridge/main/release-assets/macos-update.json",as:UpdateChannel.self)
    guard channel.schema == 1 else { throw SetupFailure.message("请下载新版安装向导。") }
    let release:AppRelease=try fetchJSON(channel.manifest_url,as:AppRelease.self);try release.validate();return release
}
func modelCatalog(_ url:String) throws -> ModelCatalog {
    if let path=Bundle.main.url(forResource:"BootstrapModels",withExtension:"json") {
        let catalog=try JSONDecoder().decode(ModelCatalog.self,from:Data(contentsOf:path));try catalog.validate();return catalog
    }
    let catalog:ModelCatalog=try fetchJSON(url,as:ModelCatalog.self);try catalog.validate();return catalog
}
func fileSHA(_ url:URL) throws -> String {
    let handle=try FileHandle(forReadingFrom:url);defer{try? handle.close()}
    var hash=SHA256()
    while let data=try handle.read(upToCount:4*1024*1024), !data.isEmpty { hash.update(data:data) }
    return hash.finalize().map{String(format:"%02x",$0)}.joined()
}
func fileBytes(_ path:URL) -> Int64 { (try? FileManager.default.attributesOfItem(atPath:path.path)[.size] as? NSNumber)?.int64Value ?? 0 }
func versionIsNewer(_ lhs:String,_ rhs:String) -> Bool { lhs.compare(rhs,options:.numeric) == .orderedDescending }

final class ResumableTransfer: NSObject, URLSessionDataDelegate {
    private let lock=NSLock()
    private var current: URLSessionDataTask?
    private var cancelled=false
    private var output:FileHandle?, result:Error?, done:DispatchSemaphore?
    private var expected:Int64=0, received:Int64=0, offset:Int64=0
    private var attemptBytes:Int64=0
    private var progress:((Int64)->Void)?
    var routeChanged:((String)->Void)?
    var networkObserved:((Bool)->Void)?
    private var preferredRoute=0
    private let routes:[DownloadRoute]
    private let slowInterval:Double
    init(routes:[DownloadRoute]=installerDownloadRoutes(),slowInterval:Double=20) {self.routes=routes;self.slowInterval=slowInterval;super.init()}
    func pause() { lock.lock();cancelled=true;let task=current;lock.unlock();task?.cancel() }
    func check() throws { lock.lock();let paused=cancelled;lock.unlock();if paused {throw SetupFailure.paused} }
    func download(_ asset:RemoteAsset, into folder:URL, progress:@escaping(Int64)->Void) throws -> URL {
        try asset.validate();try check()
        let final=folder.appendingPathComponent(asset.name), partial=folder.appendingPathComponent(asset.name+".partial")
        if fileBytes(final) == asset.bytes, try fileSHA(final) == asset.sha256 { progress(asset.bytes);return final }
        try? FileManager.default.removeItem(at:final)
        for attempt in 0..<max(4,routes.count*2) {
            try check()
            do {
                if fileBytes(partial)>asset.bytes {try FileManager.default.removeItem(at:partial)}
                if fileBytes(partial)==asset.bytes {
                    if try fileSHA(partial)==asset.sha256 {try FileManager.default.moveItem(at:partial,to:final);progress(asset.bytes);return final}
                    try FileManager.default.removeItem(at:partial)
                }
                if !FileManager.default.fileExists(atPath:partial.path) {FileManager.default.createFile(atPath:partial.path,contents:nil)}
                offset=fileBytes(partial);received=offset;expected=asset.bytes;attemptBytes=0;self.progress=progress;result=nil
                output=try FileHandle(forWritingTo:partial);try output!.seekToEnd();done=DispatchSemaphore(value:0)
                let route=routes[(preferredRoute+attempt)%routes.count]
                routeChanged?(route.name+" · 独立直连，不使用系统代理")
                var request=URLRequest(url:route.url(asset.url));request.timeoutInterval=15
                if offset>0 {request.setValue("bytes=\(offset)-",forHTTPHeaderField:"Range")}
                let config=installerNetworkConfiguration()
                let queue=OperationQueue();queue.maxConcurrentOperationCount=1
                let session=URLSession(configuration:config,delegate:self,delegateQueue:queue)
                let task=session.dataTask(with:request)
                lock.lock();current=task;let paused=cancelled;lock.unlock()
                if paused {try? output?.close();session.invalidateAndCancel();throw SetupFailure.paused}
                let windowStart=Date()
                let monitor=DispatchSource.makeTimerSource(queue:DispatchQueue.global())
                monitor.schedule(deadline:.now()+slowInterval,repeating:slowInterval)
                monitor.setEventHandler { [weak self] in queue.addOperation {
                    guard let self=self else{return}
                    self.lock.lock();let active=self.current === task;self.lock.unlock()
                    guard active,attempt<self.routes.count else{return}
                    let elapsed=Date().timeIntervalSince(windowStart)
                    if Double(self.attemptBytes)/max(0.01,elapsed)<256*1024 {
                        self.result=SetupFailure.message("当前线路较慢，正在切换线路并续传…");task.cancel()
                    }
                }}
                progress(offset);task.resume();monitor.resume();done!.wait();monitor.cancel();session.finishTasksAndInvalidate()
                try? output?.close();output=nil
                try check();if let error=result {throw error}
                guard received==asset.bytes else {throw SetupFailure.message("网络中断，正在继续下载。")}
                guard try fileSHA(partial)==asset.sha256 else {try? FileManager.default.removeItem(at:partial);throw SetupFailure.message("文件校验未通过，正在重新下载。")}
                try FileManager.default.moveItem(at:partial,to:final);preferredRoute=(preferredRoute+attempt)%routes.count;progress(asset.bytes);return final
            } catch {
                try? output?.close();output=nil
                try check()
                if attempt==max(4,routes.count*2)-1 {throw error}
                routeChanged?("线路暂时较慢或不可用，正在自动切换并续传…")
                for _ in 0..<10 {try check();Thread.sleep(forTimeInterval:0.1)}
            }
        }
        throw SetupFailure.message("下载未完成，请重试。")
    }
    func urlSession(_ session:URLSession,dataTask:URLSessionDataTask,didReceive response:URLResponse,completionHandler:@escaping(URLSession.ResponseDisposition)->Void) {
        do {
            guard let http=response as? HTTPURLResponse else {throw SetupFailure.message("下载响应无效。")}
            if http.statusCode==206 {
                guard http.value(forHTTPHeaderField:"Content-Range")=="bytes \(offset)-\(expected-1)/\(expected)" else {throw SetupFailure.message("续传响应无效，请重试。")}
            } else if http.statusCode==200 {
                if offset>0 {try output?.truncate(atOffset:0);try output?.seek(toOffset:0);received=0}
            } else {throw SetupFailure.message("下载服务暂时不可用（\(http.statusCode)），请重试。")}
            completionHandler(.allow)
        } catch {result=error;completionHandler(.cancel)}
    }
    func urlSession(_ session:URLSession,dataTask:URLSessionDataTask,didReceive data:Data) {
        do {
            guard received+Int64(data.count)<=expected else {throw SetupFailure.message("下载文件大小与清单不符。")}
            try output?.write(contentsOf:data);received+=Int64(data.count);attemptBytes+=Int64(data.count);progress?(received)
        } catch {result=error;dataTask.cancel()}
    }
    func urlSession(_ session:URLSession,task:URLSessionTask,didCompleteWithError error:Error?) {if result==nil {result=error};lock.lock();current=nil;lock.unlock();done?.signal()}
    func urlSession(_ session:URLSession,task:URLSessionTask,didFinishCollecting metrics:URLSessionTaskMetrics) {
        for transaction in metrics.transactionMetrics {networkObserved?(transaction.isProxyConnection)}
    }
}

func runArchive(_ files:[URL], arguments:[String]) throws -> Data {
    let process=Process();process.executableURL=URL(fileURLWithPath:"/usr/bin/tar");process.arguments=arguments
    // Finder-launched apps may inherit the C locale. BSD tar then octal-escapes
    // Chinese names, which must never be mistaken for actual archive paths.
    var environment=ProcessInfo.processInfo.environment
    environment["LC_ALL"]="en_US.UTF-8";environment["LANG"]="en_US.UTF-8"
    process.environment=environment
    let input=Pipe(),output=Pipe(),errors=Pipe();process.standardInput=input;process.standardOutput=output;process.standardError=errors
    try process.run();let written=DispatchGroup();written.enter()
    var writeFailure:Error?,diagnostics=Data()
    written.enter()
    DispatchQueue.global().async {diagnostics=errors.fileHandleForReading.readDataToEndOfFile();written.leave()}
    DispatchQueue.global().async {
        defer{try? input.fileHandleForWriting.close();written.leave()}
        do {for file in files {let h=try FileHandle(forReadingFrom:file);defer{try? h.close()};while let d=try h.read(upToCount:4*1024*1024), !d.isEmpty {try input.fileHandleForWriting.write(contentsOf:d)}}}
        catch {writeFailure=error}
    }
    let data=output.fileHandleForReading.readDataToEndOfFile();process.waitUntilExit();written.wait()
    if process.terminationStatus != 0 {throw SetupFailure.message("解压未完成："+String((String(data:diagnostics,encoding:.utf8) ?? "未知错误").prefix(500)))}
    if let error=writeFailure {throw error};return data
}
func extractVerified(_ files:[URL], prefix:String, destination:URL) throws {
    let list=try runArchive(files,arguments:["-tf","-"])
    guard let text=String(data:list,encoding:.utf8), !text.isEmpty else {throw SetupFailure.message("安装归档无效。")}
    for line in text.split(separator:"\n") {
        let path=String(line)
        guard (path==prefix || path.hasPrefix(prefix+"/")), !path.split(separator:"/").contains(".."), !path.hasPrefix("/") else {throw SetupFailure.message("安装归档包含无效路径："+String(path.prefix(160)))}
    }
    try FileManager.default.createDirectory(at:destination,withIntermediateDirectories:true)
    _=try runArchive(files,arguments:["-xf","-","--no-same-owner","-C",destination.path])
}
func modelReady(_ directory:URL,_ edition:ModelEdition, deep:Bool=false) -> Bool {
    guard let data=try? Data(contentsOf:directory.appendingPathComponent("installation.json")), let meta=try? JSONDecoder().decode(ModelSnapshot.self,from:data), meta.repo==edition.snapshot.repo, meta.revision==edition.snapshot.revision else{return false}
    for f in edition.snapshot.files {
        let path=directory.appendingPathComponent(f.file)
        guard fileBytes(path)==f.size, !deep || (try? fileSHA(path))==f.sha256 else{return false}
    }
    return true
}
func verifyApplication(_ app:URL) throws {
    let p=Process();p.executableURL=URL(fileURLWithPath:"/usr/bin/codesign");p.arguments=["--verify","--deep","--strict",app.path]
    let output=Pipe();p.standardOutput=output;p.standardError=output;try p.run();let message=output.fileHandleForReading.readDataToEndOfFile();p.waitUntilExit()
    guard p.terminationStatus==0, let b=Bundle(url:app), ["org.paperbridge.yiyan","org.paperbridge.yiyan.offline"].contains(b.bundleIdentifier ?? ""),FileManager.default.isExecutableFile(atPath:app.appendingPathComponent("Contents/MacOS/PaperBridge").path) else {throw SetupFailure.message("应用校验未通过："+(String(data:message,encoding:.utf8) ?? "请重新下载。"))}
}

struct InstallationResult {
    let version:String, profile:String, destination:String
    let downloadedBytes:Int64, reusedModel:Bool, updateOnly:Bool, cacheCleaned:Bool
    let elapsedSeconds:Double
    var json:[String:Any] { ["status":"complete","version":version,"profile":profile,"destination":destination,"downloaded_bytes":downloadedBytes,"model_reused":reusedModel,"application_update_only":updateOnly,"cache_cleaned":cacheCleaned,"elapsed_seconds":elapsedSeconds,"models_outside_application":true] }
}
final class MacInstallation {
    let transfer=ResumableTransfer()
    var stageChanged:((String)->Void)?
    var progress:((Int64,Int64)->Void)?
    var applicationCommitStarted:(()->Void)?
    let fm=FileManager.default
    func install(profile:String,destination:URL,dataHome:URL,cache:URL,updateOnly:Bool,cleanCache:Bool) throws -> InstallationResult {
        let started=Date()
        guard ["standard","precision"].contains(profile),destination.lastPathComponent=="译研.app",destination.isFileURL, dataHome.isFileURL, cache.isFileURL,
              !destination.path.hasPrefix(dataHome.path+"/"), !destination.path.hasPrefix(cache.path+"/") else {throw SetupFailure.message("安装位置无效。")}
        #if arch(arm64)
        #else
        throw SetupFailure.message("此版本适用于 Apple Silicon（M 系列）Mac。")
        #endif
        if fm.fileExists(atPath:destination.path) {try verifyApplication(destination)}
        if NSRunningApplication.runningApplications(withBundleIdentifier:"org.paperbridge.yiyan.offline").isEmpty == false || NSRunningApplication.runningApplications(withBundleIdentifier:"org.paperbridge.yiyan").isEmpty == false {throw SetupFailure.message("请先退出正在运行的译研，再点击继续安装。")}
        try fm.createDirectory(at:cache,withIntermediateDirectories:true)
        let lockPath=cache.deletingLastPathComponent().appendingPathComponent(".paperbridge-installer.lock")
        let descriptor=open(lockPath.path,O_CREAT|O_RDWR,0o600)
        guard descriptor>=0 else {throw SetupFailure.message("无法创建安装锁，请检查存储位置。")}
        defer{flock(descriptor,LOCK_UN);close(descriptor)}
        guard flock(descriptor,LOCK_EX|LOCK_NB)==0 else {throw SetupFailure.message("另一个安装向导正在工作，请稍后重试。")}
        stageChanged?("正在获取软件和模型版本…")
        let release=try latestRelease(allowBundled:!updateOnly)
        let os=ProcessInfo.processInfo.operatingSystemVersion
        guard !versionIsNewer(release.minimum_macos,"\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)") else {throw SetupFailure.message("此版本需要 macOS \(release.minimum_macos) 或更新系统。")}
        let catalog=try modelCatalog(release.models_url)
        let edition=catalog.editions[profile]!
        try fm.createDirectory(at:dataHome.appendingPathComponent("models"),withIntermediateDirectories:true)
        stageChanged?("正在检查本地模型，已安装的内容会保留…")
        // Materialize old full-edition links before replacing the old app bundle.
        for (_,entry) in catalog.editions {
            let target=dataHome.appendingPathComponent("models/"+entry.directory)
            if let link=try? fm.destinationOfSymbolicLink(atPath:target.path), link.contains(".app/Contents/Resources/Models/") {
                guard modelReady(target,entry,deep:true) else {throw SetupFailure.message("原有模型需要重新校验，请保留原应用后重试。")}
                let staging=dataHome.appendingPathComponent("models/.migrate-"+UUID().uuidString)
                do {
                    try fm.copyItem(at:target.resolvingSymlinksInPath(),to:staging)
                    guard modelReady(staging,entry,deep:true) else {throw SetupFailure.message("原模型迁移校验未通过。")}
                    try fm.removeItem(at:target);try fm.moveItem(at:staging,to:target)
                } catch {try? fm.removeItem(at:staging);throw error}
            }
        }
        let targetModel=dataHome.appendingPathComponent("models/"+edition.directory)
        let reused=modelReady(targetModel,edition)
        let needsModel = !updateOnly && !reused
        let assets=[release.app]+(needsModel ? edition.parts : [])
        let total=assets.reduce(Int64(0)){$0+$1.bytes}
        let modelSize=edition.snapshot.files.reduce(Int64(0)){$0+$1.size}
        let cached=assets.reduce(Int64(0)) { $0 + min($1.bytes, max(fileBytes(cache.appendingPathComponent($1.name)),fileBytes(cache.appendingPathComponent($1.name+".partial")))) }
        let required=Int64(4_000_000_000)+max(0,total-cached)+(needsModel ? modelSize : 0)
        let available=(try fm.attributesOfFileSystem(forPath:dataHome.path)[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
        guard available>required else {throw SetupFailure.message("安装需预留约 \(Int(ceil(Double(required)/1e9))) GB 可用空间，请清理后重试。")}
        try fm.createDirectory(at:destination.deletingLastPathComponent(),withIntermediateDirectories:true)
        let test=destination.deletingLastPathComponent().appendingPathComponent(".yiyan-write-"+UUID().uuidString)
        try Data().write(to:test);try fm.removeItem(at:test)
        var completed:Int64=0
        var paths:[URL]=[]
        for asset in assets {
            stageChanged?("正在下载与校验"+(asset.name==release.app.name ? "应用和运行环境…" : "模型…"))
            let base=completed
            paths.append(try transfer.download(asset,into:cache){bytes in self.progress?(base+bytes,total)})
            completed+=asset.bytes
        }
        try transfer.check();stageChanged?("全部文件已校验，正在安装…");applicationCommitStarted?()
        let appStage=destination.deletingLastPathComponent().appendingPathComponent(".yiyan-stage-"+UUID().uuidString)
        defer{try? fm.removeItem(at:appStage)}
        try extractVerified([paths[0]],prefix:"译研.app",destination:appStage)
        let freshApp=appStage.appendingPathComponent("译研.app");try verifyApplication(freshApp)
        guard Bundle(url:freshApp)?.infoDictionary?["CFBundleShortVersionString"] as? String == release.version else {throw SetupFailure.message("应用版本与下载清单不一致。")}
        if needsModel {
            stageChanged?("正在安装并核对完整模型…")
            let modelStage=dataHome.appendingPathComponent("models/.extract-"+UUID().uuidString)
            defer{try? fm.removeItem(at:modelStage)}
            try extractVerified(Array(paths.dropFirst()),prefix:edition.directory,destination:modelStage)
            let freshModel=modelStage.appendingPathComponent(edition.directory)
            guard modelReady(freshModel,edition,deep:true) else {throw SetupFailure.message("模型内容校验未通过，请重试。")}
            if fm.fileExists(atPath:targetModel.path) {throw SetupFailure.message("模型目录存在不完整内容，请在软件中检查模型后重试；原文件已保留。")}
            try fm.moveItem(at:freshModel,to:targetModel)
        }
        let previous=destination.deletingLastPathComponent().appendingPathComponent(".yiyan-previous-"+UUID().uuidString)
        let hadApp=fm.fileExists(atPath:destination.path)
        if hadApp {try fm.moveItem(at:destination,to:previous)}
        do {try fm.moveItem(at:freshApp,to:destination);try verifyApplication(destination)}
        catch {try? fm.removeItem(at:destination);if hadApp {try? fm.moveItem(at:previous,to:destination)};throw error}
        let marker=dataHome.appendingPathComponent("macos-installation.json")
        var record=(try? Data(contentsOf:marker)).flatMap{try? JSONSerialization.jsonObject(with:$0) as? [String:Any]} ?? [:]
        record["schema"]=1;record["app_version"]=release.version;record["application_path"]=destination.path
        if !updateOnly {record["default_profile"]=profile}
        record["models_managed_separately"]=true
        try JSONSerialization.data(withJSONObject:record,options:[.prettyPrinted,.sortedKeys]).write(to:marker,options:.atomic)
        if hadApp {try? fm.removeItem(at:previous)}
        if cleanCache {for path in paths {try? fm.removeItem(at:path)}}
        let cleaned=paths.allSatisfy{!fm.fileExists(atPath:$0.path)}
        return InstallationResult(version:release.version,profile:profile,destination:destination.path,downloadedBytes:total,reusedModel:reused,updateOnly:updateOnly,cacheCleaned:cleaned,elapsedSeconds:Date().timeIntervalSince(started))
    }
}
