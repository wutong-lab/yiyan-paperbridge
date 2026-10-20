using System.Diagnostics;
using System.IO.Compression;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using PaperBridgeSetup;

namespace PaperBridgeUpdate;

public sealed record AppRelease(int Schema, string Version, string Platform, Asset Archive, string[] Components,
    [property:JsonPropertyName("expanded_bytes")] long ExpandedBytes,
    [property:JsonPropertyName("minimum_installed_version")] string MinimumInstalledVersion,
    [property:JsonPropertyName("runtime_included")] bool RuntimeIncluded)
{
    public static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true, WriteIndented = true, PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    public static readonly HashSet<string> AllowedComponents = new(StringComparer.OrdinalIgnoreCase) {
        "Engine", "Updater", "Runtime", "Yiyan.exe", "Yiyan.dll", "Yiyan.pdb", "Yiyan.deps.json", "Yiyan.runtimeconfig.json",
        "WebView2Loader.dll", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "Microsoft.Web.WebView2.Wpf.dll",
        "Microsoft.Web.WebView2.Core.xml", "Microsoft.Web.WebView2.WinForms.xml", "Microsoft.Web.WebView2.Wpf.xml",
        "Windows-安装教程.md", "BUILD-REPORT.json"
    };
    public void Validate()
    {
        if (Schema != 1 || Platform != "windows-x64" || !Regex.IsMatch(Version,"^\\d+\\.\\d+\\.\\d+$") || !System.Version.TryParse(MinimumInstalledVersion,out _) ||
            Archive.Name != $"Yiyan-{Version}-Windows-x64-App.zip" || Archive.Bytes <= 0 || !Regex.IsMatch(Archive.Sha256,"^[a-f0-9]{64}$") ||
            (Archive.Url != $"https://github.com/hengyuwu-dot/yiyan-paperbridge/releases/download/v{Version}-windows-update/{Archive.Name}" && Archive.Url != $"https://github.com/wutong-lab/yiyan-paperbridge/releases/download/v{Version}-windows-update/{Archive.Name}") ||
            ExpandedBytes <= 0 || ExpandedBytes > 12_000_000_000 || Components.Length==0 || Components.Distinct(StringComparer.OrdinalIgnoreCase).Count()!=Components.Length ||
            Components.Any(c=>!AllowedComponents.Contains(c)) || !Components.Contains("Engine") || !Components.Contains("Yiyan.exe") || !Components.Contains("Updater") ||
            Components.Contains("Runtime") != RuntimeIncluded)
            throw new InvalidDataException("Windows 应用更新清单无效，请重新检查更新。");
    }
    public bool NewerThan(string current) => System.Version.Parse(Version) > System.Version.Parse(current);
}

public static class UpdateNetwork
{
    public const string Channel = "https://raw.githubusercontent.com/hengyuwu-dot/yiyan-paperbridge/main/release-assets/windows-update.json";
    public static readonly string[] Metadata = [Channel,"https://cdn.jsdelivr.net/gh/hengyuwu-dot/yiyan-paperbridge@main/release-assets/windows-update.json"];
    public static HttpClient Client() => new(new SocketsHttpHandler {UseProxy=false,ConnectTimeout=TimeSpan.FromSeconds(12),MaxConnectionsPerServer=3}) {Timeout=Timeout.InfiniteTimeSpan};
    public static string BinaryUrl(Asset asset,int attempt) => ((attempt%3) switch {1=>"https://ghfast.top/",2=>"https://gh-proxy.com/",_=>""})+asset.Url;
    public static async Task<AppRelease> Latest(HttpClient http,CancellationToken token=default,string? testManifest=null)
    {
        if(testManifest is not null&&!Regex.IsMatch(testManifest,"^https://github\\.com/(?:hengyuwu-dot|wutong-lab)/yiyan-paperbridge/releases/download/v\\d+\\.\\d+\\.\\d+-windows-update/windows-app\\.json$"))throw new InvalidDataException("测试更新源无效。");
        Exception? failure=null;
        foreach (string url in testManifest is null?Metadata:new[]{testManifest})
        {
            try {
                using var timeout=CancellationTokenSource.CreateLinkedTokenSource(token);timeout.CancelAfter(TimeSpan.FromSeconds(15));
                using var response=await http.GetAsync(url,HttpCompletionOption.ResponseHeadersRead,timeout.Token);response.EnsureSuccessStatusCode();
                await using var body=await response.Content.ReadAsStreamAsync(timeout.Token);using var bytes=new MemoryStream();var buffer=new byte[8192];int n;
                while ((n=await body.ReadAsync(buffer,timeout.Token))>0) {if(bytes.Length+n>1_000_000)throw new InvalidDataException("更新清单过大");bytes.Write(buffer,0,n);}
                var release=JsonSerializer.Deserialize<AppRelease>(bytes.ToArray(),AppRelease.Json) ?? throw new InvalidDataException("更新清单为空");release.Validate();return release;
            } catch(Exception e) when(!token.IsCancellationRequested) {failure=e;}
        }
        token.ThrowIfCancellationRequested();throw new IOException("暂时无法检查更新。联网后可重试，已安装的软件仍可离线使用。",failure);
    }
}

public static class UpdateTransaction
{
    public static string Destination(string target)
    {
        target=InstallPaths.ValidateDestination(target);
        if(!File.Exists(Path.Combine(target,"Yiyan.exe")) || !File.Exists(Path.Combine(target,"Runtime","Python","python.exe")))
            throw new IOException("请选择现有的译研安装目录；新电脑请使用一键安装器。");
        return target;
    }
    static void NoReparse(string path) {if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new IOException("更新目录包含链接，请使用原安装目录。");}
    static void CopyTree(string from,string to)
    {
        NoReparse(from);Directory.CreateDirectory(to);
        foreach(string f in Directory.GetFiles(from)){NoReparse(f);File.Copy(f,Path.Combine(to,Path.GetFileName(f)),false);}
        foreach(string d in Directory.GetDirectories(from))CopyTree(d,Path.Combine(to,Path.GetFileName(d)));
    }
    static void Move(string from,string to) {if(Directory.Exists(from))Directory.Move(from,to);else File.Move(from,to);}
    static bool Exists(string p)=>Directory.Exists(p)||File.Exists(p);
    public static async Task Apply(AppRelease release,string archive,string target,CancellationToken token=default,Action<int>? beforeCommit=null)
    {
        release.Validate();target=Destination(target);NoReparse(target);
        using(var installed=JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(target,"BUILD-REPORT.json"),token))) {
            if(!installed.RootElement.TryGetProperty("version",out var v)||!System.Version.TryParse(v.GetString(),out var current)||current<System.Version.Parse(release.MinimumInstalledVersion))
                throw new IOException("这个安装版本需要先使用新版一键安装器升级。");
            if(current>System.Version.Parse(release.Version))throw new IOException("已安装的版本较新，不能使用较旧更新包覆盖。");
        }
        if(new FileInfo(archive).Length!=release.Archive.Bytes || await Downloader.HashAsync(archive,token)!=release.Archive.Sha256)throw new InvalidDataException("更新包校验失败，原应用保持不变。");
        string stage=Path.Combine(target,".yiyan-stage-"+Guid.NewGuid().ToString("N"));
        string backup=Path.Combine(target,".yiyan-backup-"+Guid.NewGuid().ToString("N"));
        using var updateLock=new FileStream(Path.Combine(target,".yiyan-update.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
        Directory.CreateDirectory(stage);Directory.CreateDirectory(backup);
        var moved=new List<(string live,string saved,bool existed)>();bool committed=false,rollbackFailed=false;
        try {
            using(var zip=ZipFile.OpenRead(archive)) {
                long total=0;var names=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach(var entry in zip.Entries) {
                    string name=entry.FullName.Replace('\\','/');string[] parts=name.Split('/',StringSplitOptions.RemoveEmptyEntries);
                    if(!name.StartsWith("Yiyan/",StringComparison.Ordinal) || parts.Length<2 || !release.Components.Contains(parts[1],StringComparer.OrdinalIgnoreCase) ||
                        parts.Any(p=>p is "." or ".." || p.Contains(':') || p.EndsWith('.') || p.EndsWith(' ') || Regex.IsMatch(p,"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\\.|$)",RegexOptions.IgnoreCase)) ||
                        ((entry.ExternalAttributes>>16)&0xf000)==0xa000 || !names.Add(name.TrimEnd('/')) || name.EndsWith(".gguf",StringComparison.OrdinalIgnoreCase) || name.EndsWith(".safetensors",StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("更新包包含无效路径或模型文件，已停止更新。");
                    total=checked(total+entry.Length);if(total>release.ExpandedBytes)throw new InvalidDataException("更新包大小与清单不一致。");
                    string path=Path.GetFullPath(Path.Combine(stage,Path.Combine(parts.Skip(1).ToArray())));
                    if(!path.StartsWith(stage+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("更新包路径越界。");
                    if(name.EndsWith('/')){Directory.CreateDirectory(path);continue;}
                    Directory.CreateDirectory(Path.GetDirectoryName(path)!);await using var input=entry.Open();await using var output=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.None);
                    await input.CopyToAsync(output,token);
                }
                if(total!=release.ExpandedBytes)throw new InvalidDataException("更新包大小与清单不一致。");
            }
            foreach(string component in release.Components)if(!Exists(Path.Combine(stage,component)))throw new InvalidDataException("更新组件不完整："+component);
            using(var built=JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(stage,"BUILD-REPORT.json"),token)))
                if(built.RootElement.GetProperty("version").GetString()!=release.Version)throw new InvalidDataException("应用版本与更新清单不一致。");
            // Layout/vendor files belong to the installed, unchanged runtime. Keep
            // their matching fonts and corresponding sources across code updates.
            foreach(string relative in new[]{"Engine/.cache","Engine/release-assets/vendor-sources","Engine/release-assets/windows-licenses"}) {
                string src=Path.Combine(target,relative),dst=Path.Combine(stage,relative);
                if(Directory.Exists(src)&&!Directory.Exists(dst))CopyTree(src,dst);
            }
            string oldReport=Path.Combine(target,"BUILD-REPORT.json"),newReport=Path.Combine(stage,"BUILD-REPORT.json");
            if(File.Exists(oldReport)&&File.Exists(newReport)) {
                using var old=JsonDocument.Parse(await File.ReadAllTextAsync(oldReport,token));
                if(old.RootElement.TryGetProperty("edition",out var choice)&&choice.GetString() is "standard" or "precision") {
                    var updated=JsonSerializer.Deserialize<Dictionary<string,JsonElement>>(await File.ReadAllTextAsync(newReport,token))!;updated["edition"]=choice.Clone();await File.WriteAllTextAsync(newReport,JsonSerializer.Serialize(updated),token);
                }
            }
            token.ThrowIfCancellationRequested();
            for(int i=0;i<release.Components.Length;i++) {
                beforeCommit?.Invoke(i);string component=release.Components[i],live=Path.Combine(target,component),saved=Path.Combine(backup,component);bool exists=Exists(live);
                if(exists){NoReparse(live);Move(live,saved);}moved.Add((live,saved,exists));Move(Path.Combine(stage,component),live);
            }
            committed=true;
        } catch {
            foreach(var change in moved.AsEnumerable().Reverse()) {
                try {if(Directory.Exists(change.live))Directory.Delete(change.live,true);else if(File.Exists(change.live))File.Delete(change.live);if(change.existed)Move(change.saved,change.live);}
                catch {rollbackFailed=true;}
            }
            if(rollbackFailed)throw new IOException("原应用备份已保留在 "+backup+"，请保留该目录后重试。");
            throw;
        } finally {
            try{if(Directory.Exists(stage))Directory.Delete(stage,true);}catch{}
            if(committed||!rollbackFailed)try{Directory.Delete(backup,true);}catch{}
        }
    }
}
