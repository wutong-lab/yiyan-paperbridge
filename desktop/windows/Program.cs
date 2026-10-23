using System.Diagnostics;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using PaperBridgeUpdate;
using System.Reflection;

internal static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new PaperWindow(args.Contains("--smoke-test"),args.Contains("--update-smoke")));
    }
}

internal sealed class PaperWindow : Form
{
    readonly WebView2 view = new() { Dock = DockStyle.Fill };
    readonly Label loading = new() { Text = "正在准备论文工作室…", Dock = DockStyle.Fill,
        TextAlign = ContentAlignment.MiddleCenter, Font = new Font("Microsoft YaHei UI", 16) };
    static bool PrivatePreview => File.Exists(Path.Combine(AppContext.BaseDirectory, "PRIVATE-PREVIEW.json"));
    readonly string data = Environment.GetEnvironmentVariable("PAPERBRIDGE_HOME") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), PrivatePreview ? "PaperBridge-DeepSeek-Preview" : "PaperBridge");
    readonly HttpClient http = new(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(3) };
    readonly StringBuilder log = new();
    readonly bool smoke;
    readonly bool updateSmoke;
    readonly ToolStripMenuItem updateMenu=new("检查更新…"){Enabled=false};
    bool checkingUpdate,updateStarted;
    Process? server;
    string stateFile = "";
    string origin = "";
    bool closing;
    bool readerFullscreen;
    Rectangle windowedBounds;
    FormWindowState windowedState;
    FormBorderStyle windowedBorder;
    bool windowedMenuVisible;

    public PaperWindow(bool smokeTest,bool updateTest=false)
    {
        smoke = smokeTest;
        updateSmoke=updateTest;
        Text = PrivatePreview ? "译研预览版 · 本地 / API" : "译研 · SCI 论文翻译";
        Size = new Size(1380, 920);
        MinimumSize = new Size(960, 680);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(248, 249, 245);
        var icon = System.Drawing.Icon.ExtractAssociatedIcon(Environment.ProcessPath!);
        if (icon != null) Icon = icon;
        Controls.Add(view); Controls.Add(loading);
        var menu=new MenuStrip();var software=new ToolStripMenuItem("软件");software.DropDownItems.Add(updateMenu);menu.Items.Add(software);
        var apiMenu = new ToolStripMenuItem("API 配置…");
        apiMenu.Click += async (_, _) => { if(view.CoreWebView2 != null) await view.CoreWebView2.ExecuteScriptAsync("showAPISettings()"); };
        software.DropDownItems.Add(apiMenu);
        updateMenu.Click+=async(_,_)=>await CheckUpdate();Controls.Add(menu);MainMenuStrip=menu;menu.BringToFront();
        Shown += async (_, _) => await StartStudio();
        FormClosing += async (_, e) => {
            if (closing) return;
            e.Cancel = true;
            bool busy = false;
            try { var s = JsonDocument.Parse(await http.GetStringAsync(origin + "/api/desktop/status")); busy = s.RootElement.GetProperty("busy").GetBoolean(); }
            catch { }
            try { if (view.CoreWebView2 != null) busy |= await view.CoreWebView2.ExecuteScriptAsync("typeof desktopBusy === 'function' && desktopBusy()") == "true"; }
            catch { }
            if (busy && MessageBox.Show("论文或批注正在处理中，退出可能中断当前保存。建议等待完成后退出。仍要退出吗？", "退出译研", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) != DialogResult.Yes) return;
            closing = true; StopServer(); Close();
        };
        FormClosed += (_, _) => { StopServer(); http.Dispose(); };
    }

    async Task StartStudio()
    {
        try
        {
            string root = AppContext.BaseDirectory;
            string runtimeRoot = root;
            if(PrivatePreview && !File.Exists(Path.Combine(runtimeRoot,"Runtime","Python","python.exe"))) {
                string existing = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","Yiyan");
                if(!File.Exists(Path.Combine(existing,"Runtime","Python","python.exe"))) {
                    using var picker = new FolderBrowserDialog { Description="选择已经安装的译研目录，以复用运行环境与模型", UseDescriptionForTitle=true, SelectedPath=existing };
                    if(picker.ShowDialog(this) != DialogResult.OK) throw new IOException("请选择已安装的译研目录后打开预览版。");
                    existing=picker.SelectedPath;
                }
                if(!File.Exists(Path.Combine(existing,"Runtime","Python","python.exe"))) throw new IOException("该目录中没有译研运行环境，请先安装正式版。");
                runtimeRoot=existing;
            }
            string? browserFolder = File.Exists(Path.Combine(runtimeRoot, "Runtime", "WebView2", "msedgewebview2.exe")) ? Path.Combine(runtimeRoot, "Runtime", "WebView2") : null;
            _ = CoreWebView2Environment.GetAvailableBrowserVersionString(browserFolder);
            Directory.CreateDirectory(data);
            stateFile = Path.Combine(data, "tmp", "desktop-" + Guid.NewGuid().ToString("N") + ".json");
            Directory.CreateDirectory(Path.GetDirectoryName(stateFile)!);
            var start = new ProcessStartInfo(Path.Combine(runtimeRoot, "Runtime", "Python", "python.exe")) {
                WorkingDirectory = Path.Combine(root, "Engine"), UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
            foreach (string arg in new[] { "-X", "utf8", "-c", "import sys; sys.path.insert(0,sys.argv.pop(1)); from paperbridge.windows_bootstrap import main; sys.exit(main())", Path.Combine(root,"Engine"), "--parent-pid", Environment.ProcessId.ToString(), "--state-file", stateFile }) start.ArgumentList.Add(arg);
            start.Environment["PAPERBRIDGE_HOME"] = data;
            start.Environment["PAPERBRIDGE_APP_VERSION"] = AppVersion;
            start.Environment["PAPERBRIDGE_LLAMA_DIR"] = Path.Combine(runtimeRoot, "Runtime", "llama");
            start.Environment["PAPERBRIDGE_WINDOWS_BUNDLE"] = runtimeRoot;
            if(PrivatePreview) start.Environment["PAPERBRIDGE_INSTALLED_MODELS_ROOT"] = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"PaperBridge","models");
            if(PrivatePreview) start.Environment["PAPERBRIDGE_LAYOUT_SEED"] = Path.Combine(runtimeRoot,"Engine",".cache","babeldoc");
            start.Environment["PYTHONUTF8"] = "1";
            server = new Process { StartInfo = start };
            server.OutputDataReceived += (_, e) => { if (e.Data != null) lock (log) log.AppendLine(e.Data); };
            server.ErrorDataReceived += (_, e) => { if (e.Data != null) lock (log) log.AppendLine(e.Data); };
            server.Start(); server.BeginOutputReadLine(); server.BeginErrorReadLine();
            DateTime deadline = DateTime.UtcNow.AddMinutes(4);
            while (DateTime.UtcNow < deadline)
            {
                if (server.HasExited) throw new Exception("本地服务启动失败。" + log.ToString()[..Math.Min(log.Length, 1500)]);
                try
                {
                    if (File.Exists(stateFile)) {
                        var state = JsonDocument.Parse(await File.ReadAllTextAsync(stateFile));
                        int port = state.RootElement.GetProperty("port").GetInt32();
                        if (port < 8765 || port > 8775) throw new Exception("本机服务端口无效");
                        string candidate = "http://127.0.0.1:" + port;
                        var probe = JsonDocument.Parse(await http.GetStringAsync(candidate + "/api/desktop/status"));
                        if (probe.RootElement.GetProperty("pid").GetInt32() == server.Id &&
                            probe.RootElement.GetProperty("instance_id").GetString() == state.RootElement.GetProperty("instance_id").GetString()) { origin = candidate; break; }
                    }
                }
                catch (HttpRequestException) { }
                catch (JsonException) { }
                catch (IOException) { }
                await Task.Delay(200);
            }
            if (origin.Length == 0) throw new Exception("启动等待超时，请关闭后重新打开译研。");
            var environment = await CoreWebView2Environment.CreateAsync(browserExecutableFolder: browserFolder, userDataFolder: Path.Combine(data, "WebView2"));
            await view.EnsureCoreWebView2Async(environment);
            view.CoreWebView2.Settings.UserAgent += " PaperBridgeDesktop/"+AppVersion+" Windows";
            view.CoreWebView2.Settings.AreDevToolsEnabled = false;
            // The HTML Fullscreen API fills the WebView; the host must also
            // cover its display. WebView2 handles Escape and reports exit here.
            view.CoreWebView2.ContainsFullScreenElementChanged += (_, _) =>
                SetReaderFullscreen(view.CoreWebView2.ContainsFullScreenElement);
            view.CoreWebView2.NavigationStarting += (_, e) => {
                if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri) && uri.GetLeftPart(UriPartial.Authority) == origin) return;
                e.Cancel = true;
            };
            view.CoreWebView2.NewWindowRequested += (_, e) => {
                e.Handled = true;
                if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri) && uri.GetLeftPart(UriPartial.Authority) == origin) view.CoreWebView2.Navigate(e.Uri);
            };
            view.CoreWebView2.DownloadStarting += (_, e) => {
                using var picker = new SaveFileDialog { FileName = Path.GetFileName(e.ResultFilePath), Filter = "所有文件|*.*", OverwritePrompt = true };
                if (picker.ShowDialog(this) == DialogResult.OK) e.ResultFilePath = picker.FileName;
                else e.Cancel = true;
                e.Handled = true;
            };
            view.CoreWebView2.NavigationCompleted += async (_, e) => {
                if (!e.IsSuccess) return;
                loading.Visible = false;
                updateMenu.Enabled=true;
                if(updateSmoke&&!updateStarted){updateStarted=true;updateMenu.PerformClick();return;}
                if (smoke) {
                    await Task.Delay(1500);
                    using var capture = File.Create(Path.Combine(data, "windows-ui-smoke.png"));
                    await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, capture);
                    await view.CoreWebView2.ExecuteScriptAsync("document.getElementById('modelBadge').click()");
                    await Task.Delay(800);
                    using var modelCapture = File.Create(Path.Combine(data, "windows-models-smoke.png"));
                    await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, modelCapture);
                    await view.CoreWebView2.ExecuteScriptAsync("showAPISettings();");
                    await Task.Delay(500);
                    string apiReady = await view.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('#apiModal:not([hidden])')) && document.getElementById('apiKey').value === ''");
                    // Capture only a fresh test workspace, with no configured user credentials.
                    using var apiCapture = File.Create(Path.Combine(data, "windows-api-settings-smoke.png"));
                    await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, apiCapture);
                    var status = JsonDocument.Parse(await http.GetStringAsync(origin + "/api/status"));
                    if(apiReady != "true") throw new IOException("API settings view did not open in the installed application");
                    await File.WriteAllTextAsync(Path.Combine(data, "windows-ui-smoke.json"), JsonSerializer.Serialize(new { loaded = true, api_settings_loaded = true, engine = "WebView2", bundled_browser = browserFolder != null, origin, status = status.RootElement.Clone() }));
                    closing = true; StopServer(); Close();
                }
            };
            view.CoreWebView2.Navigate(origin);
        }
        catch (WebView2RuntimeNotFoundException)
        {
            MessageBox.Show("请先安装 Microsoft Edge WebView2 Runtime，再打开译研。下载地址已写在 Windows 安装教程中。", "准备 Windows 阅读组件", MessageBoxButtons.OK, MessageBoxIcon.Information);
            StopServer(); Environment.ExitCode = 1; closing = true; Close();
        }
        catch (Exception e)
        {
            Directory.CreateDirectory(data);
            await File.WriteAllTextAsync(Path.Combine(data, "desktop-error.log"), e + "\n" + log);
            MessageBox.Show(e.Message, "译研启动提示", MessageBoxButtons.OK, MessageBoxIcon.Error);
            StopServer(); Environment.ExitCode = 1; closing = true; Close();
        }
    }

    void SetReaderFullscreen(bool enabled)
    {
        if (readerFullscreen == enabled || IsDisposed) return;
        readerFullscreen = enabled;
        SuspendLayout();
        try
        {
            if (enabled)
            {
                var display = Screen.FromControl(this).Bounds;
                windowedState = WindowState;
                windowedBounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                windowedBorder = FormBorderStyle;
                windowedMenuVisible = MainMenuStrip?.Visible ?? false;
                WindowState = FormWindowState.Normal;
                if (MainMenuStrip != null) MainMenuStrip.Visible = false;
                FormBorderStyle = FormBorderStyle.None;
                Bounds = display;
            }
            else
            {
                WindowState = FormWindowState.Normal;
                FormBorderStyle = windowedBorder;
                Bounds = windowedBounds;
                if (MainMenuStrip != null) MainMenuStrip.Visible = windowedMenuVisible;
                WindowState = windowedState;
            }
        }
        finally { ResumeLayout(true); }
    }

    void StopServer()
    {
        try { if (server is { HasExited: false }) { server.Kill(entireProcessTree: true); server.WaitForExit(5000); } }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
        try { if (stateFile.Length > 0) File.Delete(stateFile); } catch (IOException) { }
    }
    static string AppVersion {get{var v=Assembly.GetExecutingAssembly().GetName().Version!;return $"{v.Major}.{v.Minor}.{v.Build}";}}
    async Task CheckUpdate()
    {
        if(PrivatePreview) { MessageBox.Show("此版本通过本机预览包更新。正式更新通道继续保留，模型沿用本机文件。","本地预览版"); return; }
        if(checkingUpdate||origin.Length==0)return;checkingUpdate=true;updateMenu.Enabled=false;
        try {
            using var state=JsonDocument.Parse(await http.GetStringAsync(origin+"/api/desktop/status"));
            if(state.RootElement.GetProperty("busy").GetBoolean()){MessageBox.Show("请等待论文处理完成后再更新，已经保存的内容会保留。","论文正在处理");return;}
            using var network=UpdateNetwork.Client();var release=await UpdateNetwork.Latest(network,testManifest:updateSmoke?Environment.GetEnvironmentVariable("PAPERBRIDGE_WINDOWS_TEST_MANIFEST"):null);
            if(!release.NewerThan(AppVersion)){if(!updateSmoke)MessageBox.Show("译研 "+AppVersion+" 已是最新版本。","检查更新");else throw new IOException("Update smoke needs a newer published version");return;}
            if(!updateSmoke&&MessageBox.Show("发现译研 "+release.Version+"。\n\n本次只下载应用更新，保留已有 4B / 14B 模型、论文、译稿与设置。","软件更新",MessageBoxButtons.OKCancel,MessageBoxIcon.Information)!=DialogResult.OK)return;
            // Version lookup and the confirmation dialog can outlive a new
            // translation or annotation save. Recheck both sides immediately
            // before launching the helper, since update shutdown skips FormClosing.
            using var latestState=JsonDocument.Parse(await http.GetStringAsync(origin+"/api/desktop/status"));
            bool pendingFrontend=view.CoreWebView2 != null && await view.CoreWebView2.ExecuteScriptAsync("typeof desktopBusy === 'function' && desktopBusy()") == "true";
            if(latestState.RootElement.GetProperty("busy").GetBoolean() || pendingFrontend){MessageBox.Show("请等待论文处理或批注保存完成；未保存的批注请先重试保存或删除，然后再更新。","当前内容尚未处理完成");return;}
            string helper=Path.Combine(data,"Updater",Guid.NewGuid().ToString("N"),"Yiyan-Updater.exe");Directory.CreateDirectory(Path.GetDirectoryName(helper)!);
            File.Copy(Path.Combine(AppContext.BaseDirectory,"Updater","Yiyan-Updater.exe"),helper);
            var start=new ProcessStartInfo(helper){UseShellExecute=false};
            foreach(string arg in new[]{"--destination",AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar),"--data",data,"--parent-pid",Environment.ProcessId.ToString()})start.ArgumentList.Add(arg);
            if(updateSmoke){foreach(string arg in new[]{"--update-smoke","--no-launch","--evidence",Path.Combine(data,"UpdateEvidence")})start.ArgumentList.Add(arg);}
            using var child=Process.Start(start)??throw new IOException("更新向导未能启动，请稍后重试。");
            if(updateSmoke)await File.WriteAllTextAsync(Path.Combine(data,"windows-update-launch.json"),JsonSerializer.Serialize(new{source="native_menu",helper_pid=child.Id,version=release.Version}));
            closing=true;StopServer();Close();
        }catch(Exception e){if(updateSmoke){Environment.ExitCode=1;await File.WriteAllTextAsync(Path.Combine(data,"windows-update-error.log"),e.ToString());closing=true;StopServer();Close();}else MessageBox.Show(e.Message,"暂时无法检查更新",MessageBoxButtons.OK,MessageBoxIcon.Information);}
        finally{checkingUpdate=false;if(!IsDisposed)updateMenu.Enabled=true;}
    }
}
