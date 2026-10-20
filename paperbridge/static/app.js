let profileInitialized = false;
const $ = id => document.getElementById(id);
if (navigator.userAgent.includes('PaperBridgeDesktop')) {
  const nativeVersion = navigator.userAgent.match(/PaperBridgeDesktop\/(\d+\.\d+)/)?.[1];
  document.querySelector('.version').textContent = `${navigator.userAgent.includes('Windows') ? 'Windows' : 'Mac'} 桌面版 ${nativeVersion && nativeVersion !== '1.0' ? nativeVersion : '1.3'}`;
}
const icons = {
 file:'<path d="M14 2H5a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9Z"/><path d="M14 2v7h7M7 13h10M7 17h7"/>',
 book:'<path d="M3 3h6a3 3 0 0 1 3 3v16a4 4 0 0 0-4-3H3Zm18 0h-6a3 3 0 0 0-3 3v16a4 4 0 0 1 4-3h5Z"/>',
 upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
 download:'<path d="M12 3v13m-5-5 5 5 5-5M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',
 spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4"/>',
 shield:'<path d="M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6Zm-4 9 3 3 5-5"/>',
 layout:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 8h18M12 8v13"/>',
 info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.01"/>',
 chevron:'<path d="m6 9 6 6 6-6"/>',
 arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
 left:'<path d="m15 6-6 6 6 6"/>', right:'<path d="m9 6 6 6-6 6"/>',
 close:'<path d="m6 6 12 12M6 18 18 6"/>',
 refresh:'<path d="M20 7v5h-5M4 17v-5h5m11 0a8 8 0 0 0-14-5M4 12a8 8 0 0 0 14 5"/>',
 menu:'<path d="M4 6h16M4 12h16M4 18h16"/>'
};
function icon(name) { const el = document.createElement('span'); el.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.file}</svg>`; return el; }
document.querySelectorAll('[data-icon]').forEach(el => el.append(icon(el.dataset.icon).firstChild));
let job = null, page = 1, activeTab = 'pdf', currentStatus = {}, timer = null, toastTimer;
let viewRevision = 0, openingRequest = 0, segmentRequest = 0;
let hardwareRequest = null, startingTranslation = false;
let recentDocuments = [], pageSegments = [], targetedSegment = null;
let terms = localStorage.getItem('paperbridge.terms') || '';
let resourceCleaning = false;
let storageInfo = null, storageLoading = false;
let cleanupSelected;
try { cleanupSelected = PaperUI.cleanupSelection(JSON.parse(localStorage.getItem('paperbridge.cleanup') || 'null')); }
catch { cleanupSelected = PaperUI.cleanupSelection(null); }
document.querySelectorAll('[data-cleanup]').forEach(input => {
  input.checked = cleanupSelected[input.dataset.cleanup];
  input.addEventListener('change', () => {
    cleanupSelected[input.dataset.cleanup] = input.checked;
    $('manualCleanupResult').hidden = true;
    try { localStorage.setItem('paperbridge.cleanup',JSON.stringify(cleanupSelected)); } catch { /* Choices remain available without browser storage. */ }
    drawResources();
  });
});
const reader = new PaperReader.SynchronizedReader(
  [$('originalScroll'), $('translatedScroll')],
  [$('originalPaper'), $('translatedContainer')], $('syncScroll'), $('readerZoom'),
  [$('originalZoom'), $('translatedZoom')], $('linkZoom'));
function refreshPreview() {
  if (!job) return;
  const stamp = Date.now();
  const width = index => Math.max(1100, Math.min(3200, Math.ceil(reader.panes[index].clientWidth * reader.scales[index] / 100 * (window.devicePixelRatio || 1))));
  const setPreview = (image, url) => {
    image.classList.add('preview-loading'); image.setAttribute('aria-busy','true');
    const status = image.parentElement.querySelector('.preview-status');
    status.hidden = false; status.classList.remove('error'); status.querySelector('p').textContent = '正在准备这一页…'; status.querySelector('button').hidden = true;
    image.src = url;
  };
  setPreview($('originalPreview'), `/api/documents/${job.id}/page/${page}?version=original&width=${width(0)}`);
  if (job.has_output) setPreview($('translatedPreview'), `/api/documents/${job.id}/page/${page}?version=translated&width=${width(1)}&t=${stamp}`);
}
['originalPreview','translatedPreview'].forEach(id => {
  const status = document.createElement('div'); status.className = 'preview-status'; status.hidden = true; status.setAttribute('role','status');
  const ring = document.createElement('span'); ring.className = 'loading-ring';
  const message = document.createElement('p'); message.textContent = '正在准备这一页…';
  const retry = document.createElement('button'); retry.textContent = '重试页面预览'; retry.hidden = true; retry.addEventListener('click', refreshPreview);
  status.append(ring,message,retry); $(id).parentElement.append(status);
  $(id).addEventListener('load', () => { $(id).classList.remove('preview-loading'); $(id).setAttribute('aria-busy','false'); status.hidden = true; });
  $(id).addEventListener('error', () => { $(id).classList.remove('preview-loading'); $(id).setAttribute('aria-busy','false'); status.hidden = false; status.classList.add('error'); message.textContent = '这一页暂时未能加载'; retry.hidden = false; });
});
reader.onZoom = refreshPreview;
function focusReader(enabled) {
  document.body.classList.toggle('reader-focus', enabled);
  $('readerFocus').textContent = enabled ? '退出专注' : '专注阅读';
  $('readerFocus').setAttribute('aria-pressed', String(enabled));
  if (enabled) { selectTab('pdf'); window.scrollTo(0, 0); }
  reader.resize(); refreshPreview();
}
$('readerFocus').addEventListener('click', () => focusReader(!document.body.classList.contains('reader-focus')));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !document.querySelector('.modal-backdrop:not([hidden])')) focusReader(false);
  if (!job || activeTab !== 'pdf' || document.querySelector('.modal-backdrop:not([hidden])')) return;
  if (event.altKey || event.ctrlKey || event.metaKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || event.target.isContentEditable) return;
  if (event.key === 'ArrowLeft' && page > 1) { event.preventDefault(); page--; updatePage(); }
  if (event.key === 'ArrowRight' && page < job.page_count) { event.preventDefault(); page++; updatePage(); }
});
$('customTerms').value = terms;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6500); }
async function api(path, options = {}) {
  const {timeoutMs = 180000, ...requestOptions} = options;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    try { response = await fetch(path, {...requestOptions,signal:controller.signal}); }
    catch(error) { throw new Error(error.name === 'AbortError' ? '读取本机服务超时；后台任务会继续，请检查软件提示或文件访问权限' : '无法连接本机服务，请检查软件是否正在运行'); }
    if (!response.ok) { let detail; try { detail = (await response.json()).detail; } catch { detail = '操作未完成，请重试'; } const error = new Error(typeof detail === 'string' ? detail : detail?.message || '输入格式有误，请检查'); if(detail?.kind === 'local_runtime') error.failure = detail; throw error; }
    return await response.json();
  } finally { clearTimeout(timeout); }
}
// Called by the native Mac app after a Finder / Cmd-O import. Use the same
// persisted queue as file-input and drag-and-drop imports.
async function desktopImported(id) {
  const document = await api(`/api/documents/${id}`);
  batch.add(document);
  renderBatch();
  await openJob(document, true);
  await recent();
}
function desktopBusy() { return importing || submittingBatch || resourceCleaning || cleanupSaving; }
function busy() { return job && ['queued', 'running', 'exporting'].includes(job.status); }
const modalFocus = new Map();
function showModal(id) {
  document.querySelectorAll('.modal-backdrop:not([hidden])').forEach(el => closeModal(el.id));
  if(document.body.classList.contains('sidebar-open')) setSidebar(false);
  modalFocus.set(id, document.activeElement); $(id).hidden = false;
  const first = (id === 'resourceModal' && $(id).querySelector('[role="tab"][aria-selected="true"]')) || $(id).querySelector('input:not([disabled]),textarea:not([disabled])') || $(id).querySelector('button:not([disabled])'); first?.focus();
}
function closeModal(id) {
  const wasOpen = !$(id).hidden; $(id).hidden = true;
  if(id === 'documentTermsModal') { documentTermOwner = null; documentTermRequest++; }
  if(id === 'apiModal') $('apiKey').value = '';
  if(id === 'hardwareModal') hardwareRequest = null;
  if(wasOpen) modalFocus.get(id)?.focus(); modalFocus.delete(id);
}
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => closeModal(button.dataset.close)));
document.querySelectorAll('.modal-backdrop').forEach(el => el.addEventListener('click', event => { if(event.target === el) closeModal(el.id); }));
document.addEventListener('keydown', event => {
  const modal = document.querySelector('.modal-backdrop:not([hidden])');
  if(event.key === 'Escape') { if(modal) closeModal(modal.id); if(document.body.classList.contains('sidebar-open')) setSidebar(false); $('downloadMenu').hidden = true; }
  if(event.key !== 'Tab' || !modal) return;
  const controls = [...modal.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled])')].filter(el => el.getClientRects().length);
  if(!controls.length) return;
  const first = controls[0], last = controls[controls.length - 1];
  if(event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if(!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
function setSidebar(open) {
  const narrow = window.matchMedia('(max-width:800px)').matches;
  document.body.classList.toggle('sidebar-open',open); $('sidebarBackdrop').hidden = !open;
  $('sidebar').inert = narrow && !open; document.querySelector('main').inert = narrow && open;
  $('toggleSidebar').setAttribute('aria-expanded',String(open)); $('toggleSidebar').setAttribute('aria-label',open ? '关闭论文列表' : '打开论文列表');
  if(open) $('recentSearch').focus();
  else if(narrow && $('sidebar').contains(document.activeElement)) $('toggleSidebar').focus();
}
$('sidebar').addEventListener('keydown', event => { if(event.key !== 'Tab' || !document.body.classList.contains('sidebar-open')) return; const controls = [...$('sidebar').querySelectorAll('a,button,input')].filter(el => el.getClientRects().length); if(event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1).focus(); } else if(!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0].focus(); } });
window.matchMedia('(max-width:800px)').addEventListener('change',() => setSidebar(false));
setSidebar(false);
$('toggleSidebar').addEventListener('click', () => setSidebar(!document.body.classList.contains('sidebar-open')));
$('sidebarBackdrop').addEventListener('click', () => setSidebar(false));
$('helpButton').addEventListener('click', () => showModal('helpModal'));
let apiProfiles = [], apiPresets = [], editingAPIProfile = null, apiSettingsBusy = false;
let preferredService = 'local';
try { preferredService = localStorage.getItem('paperbridge.service') || 'local'; } catch {}
function selectedAPIProfile() { return apiProfiles.find(p => `api:${p.id}` === $('provider').value); }
function rememberService() { preferredService = $('provider').value; try { localStorage.setItem('paperbridge.service',preferredService); } catch {} }
function refreshServiceOptions(preferred = $('provider').value || preferredService) {
  const choices = [{id:'local',name:'本地离线 · 4B / 14B'}, ...apiProfiles.map(p => ({id:`api:${p.id}`,name:`${p.name} · ${p.model}`}))];
  if(preferred.startsWith('api:') && !choices.some(p => p.id === preferred)) choices.push({id:preferred,name:'原任务的 API 配置已删除 · 请重新选择'});
  choices.push({id:'configure',name:'＋ 添加 API 配置…'});
  $('provider').replaceChildren(...choices.map(row => new Option(row.name,row.id)));
  $('provider').value = choices.some(p => p.id === preferred) ? preferred : 'local';
}
function updateProfileState(state) {
  const before = JSON.stringify(apiProfiles);
  apiProfiles = state?.profiles || []; apiPresets = state?.presets || apiPresets;
  if(before !== JSON.stringify(apiProfiles) || !$('provider').dataset.initialized) {
    refreshServiceOptions($('provider').dataset.initialized ? $('provider').value : preferredService);
    $('provider').dataset.initialized = 'true';
    if(!$('apiModal').hidden) drawAPIProfiles();
  }
}
function settingsSummary() {
  const remote = $('provider').value.startsWith('api:');
  const saved = busy() && job?.settings.provider === 'api' ? job.settings.api_config : selectedAPIProfile();
  const model = remote ? saved?.name || '请选择 API 配置' : $('profile').value === 'precision' ? '14B 精译' : '4B 标准';
  $('localModelSetting').hidden = remote; $('apiSelectionInfo').hidden = !remote;
  $('cpuModeSetting').hidden = remote || currentStatus.platform !== 'windows';
  $('apiSelectionInfo').textContent = saved ? `${saved.model} · ${new URL(saved.base_url).host}` : '请添加或选择可用的 API 配置';
  $('engine').querySelector('[value="conservative"]').disabled = remote;
  if(remote) $('engine').value = 'full';
  $('settingsSummary').textContent = `${model} · ${$('polish').checked ? '译后复核' : '不进行润色'}`;
  $('settingsHint').textContent = remote ? `API 翻译：段落与必要语境发送至 ${saved ? new URL(saved.base_url).host : '所选服务'}，使用你的账户用量` : $('pages').value.trim() ? `翻译第 ${$('pages').value.trim()} 页，其余页保留原文` : '翻译全部页面，原稿单独保存';
  $('presetSummary').textContent = `${$('domain').selectedOptions[0].textContent} · ${$('pages').value.trim() ? `第 ${$('pages').value.trim()} 页` : '全部页'} · ${model}`;
  if(remote) $('serviceLabel').textContent = saved ? saved.name : '配置 API';
  else $('serviceLabel').textContent = currentStatus.model_installed || currentStatus.precision_installed ? '本地模型已就绪' : '准备本地模型';
  $('privacyLabel').textContent = remote ? `${saved?.name || 'API'} · 段落联网翻译` : '本地离线 · 文件保存在本机';
}
['domain','profile','polish','engine','pages','forceCPU'].forEach(id => $(id).addEventListener('change',settingsSummary));
$('provider').addEventListener('change',() => {
  if($('provider').value === 'configure') { refreshServiceOptions(preferredService); showAPISettings(); return; }
  rememberService(); settingsSummary();
});
function drawAPIProfiles() {
  $('apiProfileList').replaceChildren();
  for(const row of apiProfiles) {
    const button = document.createElement('button'); button.className = 'api-profile-item';
    button.classList.toggle('active',row.id === editingAPIProfile); button.setAttribute('aria-pressed',String(row.id === editingAPIProfile));
    const title = document.createElement('b'), detail = document.createElement('small');
    title.textContent = row.name; detail.textContent = row.model;
    button.append(title,detail); button.addEventListener('click',() => { if(!apiSettingsBusy) editAPIProfile(row); });
    $('apiProfileList').append(button);
  }
  if(!apiProfiles.length) { const p = document.createElement('p'); p.textContent = '还没有保存的配置'; $('apiProfileList').append(p); }
}
function presetHelp() {
  const preset = apiPresets.find(p => p.id === $('apiPreset').value);
  $('apiPresetHint').textContent = preset?.hint || '';
  $('apiModelSuggestions').replaceChildren(...(preset?.models || []).map(id => new Option(id,id)));
}
function editAPIProfile(row = null) {
  editingAPIProfile = row?.id || null;
  $('apiPreset').replaceChildren(...apiPresets.map(p => new Option(p.name,p.id)));
  const value = row || apiPresets[0] || {};
  $('apiPreset').value = row?.kind || value.id || 'deepseek';
  $('apiName').value = row?.name || '我的 DeepSeek';
  $('apiBase').value = value.base_url || ''; $('apiModelId').value = value.model || '';
  $('apiJsonMode').checked = !!value.json_mode; $('apiStreamUsage').checked = !!value.stream_usage;
  $('apiKey').value = ''; $('apiKey').placeholder = row?.key_configured ? '已安全保存 · 留空沿用，填写新密钥可替换' : '粘贴你的 API Key';
  $('deleteAPIProfile').hidden = !row;
  $('apiStatus').textContent = row ? '此配置已保存。重启软件后可直接选择；密钥留空则继续使用原密钥。' : '填写配置并保存，下次可直接选择。';
  presetHelp(); drawAPIProfiles();
}
async function showAPISettings() {
  closeModal('modelModal'); setSidebar(false);
  try { updateProfileState(await api('/api/providers/profiles',{timeoutMs:8000})); editAPIProfile(selectedAPIProfile() || apiProfiles[0]); showModal('apiModal'); }
  catch(error) { toast(error.message); }
}
['configureAPI','openAPIFromModel','openAPISettings'].forEach(id => $(id).addEventListener('click',showAPISettings));
$('newAPIProfile').addEventListener('click',() => { if(!apiSettingsBusy) { editAPIProfile(); $('apiName').focus(); } });
$('apiPreset').addEventListener('change',() => {
  const preset = apiPresets.find(p => p.id === $('apiPreset').value); if(!preset) return;
  $('apiName').value = `我的 ${preset.name.split(' · ')[0]}`; $('apiBase').value = preset.base_url;
  $('apiModelId').value = preset.model; $('apiJsonMode').checked = preset.json_mode; $('apiStreamUsage').checked = preset.stream_usage;
  $('apiKey').value = ''; presetHelp();
  $('apiStatus').textContent = editingAPIProfile ? '服务地址改变后，请填写对应的新密钥并保存。' : '填写密钥后保存即可使用。';
});
async function saveAPIProfile(action) {
  if(apiSettingsBusy) return;
  apiSettingsBusy = true;
  $('apiModal').querySelectorAll('input,select,button').forEach(el => el.disabled = true);
  try {
    if(action === 'delete') {
      if(!editingAPIProfile) return;
      const removed = editingAPIProfile;
      await api(`/api/providers/profiles/${removed}`,{method:'DELETE'});
      updateProfileState(await api('/api/providers/profiles'));
      if($('provider').value === `api:${removed}` && !busy()) { refreshServiceOptions('local'); rememberService(); }
      editAPIProfile(apiProfiles[0]); $('apiStatus').textContent = '配置与保存的密钥已删除。已提交的任务继续使用提交时的设置。';
    } else {
      const body = {name:$('apiName').value,kind:$('apiPreset').value,base_url:$('apiBase').value,model:$('apiModelId').value,
        api_key:$('apiKey').value.trim(),json_mode:$('apiJsonMode').checked,stream_usage:$('apiStreamUsage').checked};
      const row = await api(`/api/providers/profiles${editingAPIProfile ? '/' + editingAPIProfile : ''}`,{method:editingAPIProfile ? 'PUT' : 'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      $('apiKey').value = ''; body.api_key = '';
      updateProfileState(await api('/api/providers/profiles'));
      if(!busy()) { refreshServiceOptions(`api:${row.id}`); rememberService(); }
      editAPIProfile(row);
      $('apiStatus').textContent = busy() ? `“${row.name}”已保存。本篇继续使用提交时的设置，处理结束后可切换。` : `“${row.name}”已保存并选用。重启软件后可直接翻译。`;
      if(action === 'test') {
        $('apiStatus').textContent = '配置已保存，正在查询服务的模型列表…';
        const result = await api(`/api/providers/profiles/${row.id}/test`,{method:'POST'});
        $('apiStatus').textContent = result.message;
      }
    }
  } catch(error) { $('apiStatus').textContent = error.message; }
  finally { $('apiKey').value = ''; apiSettingsBusy = false; $('apiModal').querySelectorAll('input,select,button').forEach(el => el.disabled = false); settingsSummary(); }
}
$('saveAPIProfile').addEventListener('click',() => saveAPIProfile('save'));
$('testAPI').addEventListener('click',() => saveAPIProfile('test'));
$('deleteAPIProfile').addEventListener('click',() => saveAPIProfile('delete'));
$('useLocalProvider').addEventListener('click',() => { $('provider').value = 'local'; rememberService(); settingsSummary(); closeModal('apiModal'); });

async function withLocalHardware(settings, proceed) {
  if(settings.provider !== 'local') return proceed(settings);
  const owner = job?.id;
  const assessment = await api(`/api/local/preflight?profile=${encodeURIComponent(settings.profile)}&force_cpu=${!!settings.force_cpu}`,{timeoutMs:15000});
  if(job?.id !== owner) return;
  if(assessment.status === 'blocked' || assessment.status === 'confirm_cpu' && !settings.allow_slow_cpu) {
    showHardwareSettings(assessment, settings, proceed); return;
  }
  return proceed(settings);
}
function showHardwareSettings(assessment, settings, proceed) {
  const request = {settings:{...settings},proceed,owner:job?.id};
  $('hardwareHeading').textContent = assessment.status === 'blocked' ? '当前配置无法启动这个模型' : '14B 将使用 CPU，翻译可能较慢';
  $('hardwareMessage').textContent = assessment.message;
  $('hardwareFacts').textContent = `内存 ${assessment.ram_gb ?? '—'} GB · 当前可用 ${assessment.available_ram_gb ?? '—'} GB · 当前模式预留 ${assessment.minimum_ram_gb ?? '—'} GB 内存`;
  $('hardwareContinueCPU').hidden = assessment.status !== 'confirm_cpu';
  $('hardwareUse4B').hidden = settings.profile === 'standard' || assessment.can_use_4b === false;
  $('hardwareAPIChoice').replaceChildren(...apiProfiles.map(p => new Option(`${p.name} · ${p.model}`,p.id)));
  $('hardwareAPIChoice').hidden = !apiProfiles.length;
  $('hardwareUseAPI').textContent = apiProfiles.length ? '改用所选 API 并翻译' : '配置 API';
  showModal('hardwareModal');
  hardwareRequest = request;
}
async function continueHardware(changes) {
  const request = hardwareRequest;
  if(!request || request.owner !== job?.id || busy()) return;
  const settings = {...request.settings,...changes};
  closeModal('hardwareModal');
  if(settings.provider === 'local') {
    $('provider').value = 'local'; $('profile').value = settings.profile; $('forceCPU').checked = !!settings.force_cpu;
    if(settings.profile === 'standard' && !currentStatus.model_installed) { showModal('modelModal'); toast('已选择 4B，请先准备本地模型'); return; }
  } else refreshServiceOptions(`api:${settings.api_profile_id}`);
  rememberService(); settingsSummary();
  try { await withLocalHardware(settings,request.proceed); } catch(error) { toast(error.message); }
}
$('hardwareContinueCPU').addEventListener('click',() => continueHardware({allow_slow_cpu:true,force_cpu:true}));
$('hardwareUse4B').addEventListener('click',() => continueHardware({provider:'local',profile:'standard',force_cpu:false,allow_slow_cpu:false,api_profile_id:''}));
$('hardwareUseAPI').addEventListener('click',() => {
  if(!apiProfiles.length) { closeModal('hardwareModal'); showAPISettings(); return; }
  continueHardware({provider:'api',api_profile_id:$('hardwareAPIChoice').value,engine:'full',force_cpu:false,allow_slow_cpu:false});
});
function handleHardwareFailure(error, settings, proceed) {
  if(error.failure?.assessment) showHardwareSettings(error.failure.assessment,settings,proceed);
  else toast(error.message);
}
$('runtimeRecovery').addEventListener('click', async () => {
  if(!job || busy()) return;
  const id = job.id, revision = viewRevision;
  const settings = {...translationSettings(),provider:'local',profile:job.settings.profile || 'precision',force_cpu:!!job.settings.force_cpu};
  try {
    let assessment = await api(`/api/local/preflight?profile=${settings.profile}&force_cpu=true`);
    if(job?.id !== id || viewRevision !== revision) return;
    if(currentStatus.platform !== 'windows' || assessment.status !== 'confirm_cpu') assessment = {...assessment,status:'blocked',message:job.failure?.message || job.message};
    showHardwareSettings(assessment,settings,chosen => submitTranslation(id,revision,chosen));
  } catch(error) { toast(error.message); }
});
let cleanupSaving = false;
$('autoCleanup').addEventListener('change', async () => {
  if(!job || busy() || cleanupSaving) return;
  const id = job.id, revision = viewRevision, value = $('autoCleanup').checked;
  cleanupSaving = true; $('autoCleanup').disabled = true;
  try {
    await api(`/api/documents/${id}/storage-settings`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({auto_cleanup:value})});
    if(job?.id === id && viewRevision === revision) job.settings.auto_cleanup = value;
  } catch(error) {
    if(job?.id === id && viewRevision === revision) $('autoCleanup').checked = job.settings.auto_cleanup !== false;
    toast(error.message);
  } finally {
    cleanupSaving = false; $('autoCleanup').disabled = busy();
  }
});
async function modelStatus() {
  try {
    currentStatus = await api('/api/status',{timeoutMs:8000});
    updateProfileState(currentStatus.api_profiles);
    if(currentStatus.app_version && currentStatus.app_version !== 'development') document.querySelector('.version').textContent = `${currentStatus.platform === 'windows' ? 'Windows' : 'Mac'} · ${currentStatus.app_version}`;
    drawResources();
    const windows = currentStatus.platform === 'windows';
    $('windowsRequirements').hidden = !windows;
    if (windows) {
      $('modelIntro').textContent = currentStatus.bundled_models?.length ? '完整离线版已内置模型、字体与排版资源，导入论文即可翻译。软件优先使用显存充足的 NVIDIA 显卡，也可以使用 CPU。' : '选择适合电脑的本地模型：4B 标准约 2.50GB，14B 精译约 9.00GB。首次下载模型后即可离线翻译。软件优先使用显存充足的 NVIDIA 显卡，也可以使用 CPU。';
      const h = currentStatus.hardware;
      const gpu = h?.nvidia_gpus?.map(g => `${g.name} · ${(g.vram_bytes / 1024**3).toFixed(1)}GB 显存`).join('；');
      $('hardwareSummary').textContent = h ? `这台电脑：${(h.ram_bytes / 1024**3).toFixed(0)}GB 内存 · ${gpu || 'CPU 运行'}` : '正在读取电脑配置';
      $('prepareFull').textContent = '下载 14B 精译模型（约 9.00GB）';
      settingsSummary();
    }
    if (!job && !profileInitialized && currentStatus.default_profile) { $('profile').value = currentStatus.default_profile; profileInitialized = true; settingsSummary(); }
    if (!windows && currentStatus.managed_models) $('modelIntro').textContent = '模型保存在本机，更新软件时继续保留。已准备好的模型和全文排版资源可以直接离线使用。';
    const ready = currentStatus.model_installed || currentStatus.precision_installed;
    $('modelTitle').textContent = ready ? '本地模型已就绪' : currentStatus.installation.status === 'running' ? '正在下载本地模型' : '尚未安装本地模型';
    $('modelMessage').textContent = ready ? `标准模型${currentStatus.model_installed ? '已就绪' : '尚未准备'} · 精译模型${currentStatus.precision_installed ? '已就绪' : '尚未准备'} · 全文排版${currentStatus.layout_installed ? '已就绪' : '尚未准备'} · 离线运行` : currentStatus.installation.message || '首次下载后，翻译不需要联网';
    $('serviceLabel').textContent = ready ? '本地模型已就绪' : '准备本地模型'; $('modelBadge').classList.remove('offline');
    $('installModel').hidden = currentStatus.model_installed;
    $('installModel').disabled = currentStatus.installation.status === 'running';
    $('installModel').textContent = currentStatus.installation.status === 'running' ? '模型下载中…' : (windows ? '下载 4B 标准模型（约 2.50GB）' : '下载本地模型（约 2.3 GB）');
    $('prepareFull').hidden = currentStatus.precision_installed && currentStatus.layout_installed;
    $('prepareFull').disabled = currentStatus.installation.status === 'running';
    settingsSummary();
  } catch { $('modelTitle').textContent = '本地服务已断开'; $('modelMessage').textContent = navigator.userAgent.includes('PaperBridgeDesktop') ? '请退出并重新打开“译研”应用' : '请重新打开“译研”应用或 run.command'; $('serviceLabel').textContent = '本机服务未连接'; $('modelBadge').classList.add('offline'); currentStatus.can_cleanup = false; currentStatus.memory = null; drawResources(); }
}
function drawResources() {
  const memory = currentStatus.memory;
  const available = memory?.available;
  $('memoryValue').textContent = $('resourceMemory').textContent = available ? PaperUI.formatBytes(memory.rss_bytes) : '—';
  $('backendMemory').textContent = available ? PaperUI.formatBytes(memory.backend_bytes) : '—';
  $('workerMemory').textContent = available ? PaperUI.formatBytes(memory.worker_bytes) : '—';
  const disk = currentStatus.disk || storageInfo?.disk;
  ['Total','Used','Free'].forEach(key => $('disk' + key).textContent = disk?.available ? PaperUI.formatBytes(disk[key.toLowerCase() + '_bytes']) : '—');
  const used = disk?.available && disk.total_bytes ? Math.max(0,Math.min(100,100 * disk.used_bytes / disk.total_bytes)) : 0;
  $('diskUsedBar').style.width = `${used}%`;
  $('diskUsedBar').parentElement.setAttribute('aria-label',disk?.available ? `软件所在磁盘已使用 ${used.toFixed(1)}%，剩余 ${PaperUI.formatBytes(disk.free_bytes)}` : '磁盘空间暂不可读取');
  const anySelected = Object.values(cleanupSelected).some(Boolean);
  const needsStorage = Object.entries(cleanupSelected).some(([id,value]) => id !== 'memory' && value);
  $('cleanupEstimate').textContent = storageInfo ? PaperUI.formatBytes(PaperUI.cleanupEstimate(storageInfo.choices,cleanupSelected)) : needsStorage ? '统计中…' : '0 B';
  $('cleanResources').disabled = resourceCleaning || !currentStatus.can_cleanup || !anySelected || storageLoading || (needsStorage && !storageInfo);
  $('cleanResources').textContent = resourceCleaning ? '正在清理…' : '清理所选内容';
  document.querySelectorAll('[data-cleanup]').forEach(input => input.disabled = resourceCleaning || !currentStatus.can_cleanup);
  $('cleanupAvailability').textContent = !memory ? '本机服务未连接' : !currentStatus.can_cleanup ? '翻译、导出或下载任务结束后可清理' : !anySelected ? '请至少勾选一项清理内容' : storageLoading ? '正在统计预计可释放的空间' : needsStorage && !storageInfo ? '请刷新存储统计，或仅选择闲置内存' : '只清理勾选项目；已安装模型与论文结果保留';
}
async function loadStorage(refresh = false) {
  if(storageLoading) return;
  storageLoading = true; $('refreshStorage').disabled = true; drawResources();
  try {
    storageInfo = await api(`/api/storage${refresh ? '?refresh=true' : ''}`);
    $('storageValue').textContent = $('softwareStorage').textContent = PaperUI.formatBytes(storageInfo.software_bytes);
    $('storageMeasured').textContent = `${storageInfo.partial ? '部分目录无法统计 · ' : ''}${new Date(storageInfo.measured_at * 1000).toLocaleTimeString('zh-CN')} 统计 · 空间为估算，实际可用容量以清理后显示为准`;
    $('storageBreakdown').replaceChildren();
    storageInfo.groups.forEach(group => { const row = document.createElement('div'), name = document.createElement('span'), value = document.createElement('b'); name.textContent = group.name; value.textContent = PaperUI.formatBytes(group.bytes); row.append(name,value); $('storageBreakdown').append(row); });
    storageInfo.choices.forEach(choice => { const size = document.querySelector(`[data-cleanup-size="${choice.id}"]`); if(size && choice.id !== 'memory') { size.textContent = PaperUI.formatBytes(choice.bytes); size.title = `${choice.files} 个文件 · 预计占用空间`; } });
  } catch(error) { $('storageMeasured').textContent = '存储统计未完成，请点击刷新重试'; }
  finally { storageLoading = false; $('refreshStorage').disabled = false; drawResources(); }
}
function selectResourceTab(type) {
  ['storage','memory'].forEach(name => { const selected = name === type; $(name + 'Resources').hidden = !selected; $(name + 'ResourceTab').classList.toggle('selected',selected); $(name + 'ResourceTab').setAttribute('aria-selected',String(selected)); });
}
['storage','memory'].forEach(type => $(type + 'ResourceTab').addEventListener('click', () => selectResourceTab(type)));
$('refreshStorage').addEventListener('click', () => loadStorage(true));
['resourceBadge','openResources'].forEach(id => $(id).addEventListener('click', () => { modelStatus(); loadStorage(); showModal('resourceModal'); }));
$('cleanResources').addEventListener('click', async () => {
  if(resourceCleaning) return;
  resourceCleaning = true; drawResources(); $('manualCleanupResult').hidden = true;
  try {
    const result = await api('/api/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cleanupSelected)});
    const removed = result.removed_files ? `已移除 ${result.removed_files} 个文件，文件大小共 ${PaperUI.formatBytes(result.removed_bytes)}。` : '所选项目当前没有可移除的文件。';
    const memoryNote = result.memory_cleaned ? `闲置内存已清理，当前 ${PaperUI.formatBytes(result.memory_after.rss_bytes)}。` : '';
    const diskNote = result.disk_after?.available ? `磁盘剩余 ${PaperUI.formatBytes(result.disk_after.free_bytes)}。` : '';
    $('manualCleanupResult').textContent = `${removed}${result.status === 'partial' ? '部分文件未能清理。' : ''}${memoryNote}${diskNote}原稿、最新 PDF 和校订记录已保留。`;
    $('manualCleanupResult').hidden = false;
    if(job) { const id = job.id, revision = viewRevision, updated = await api(`/api/documents/${id}`); if(job?.id === id && revision === viewRevision) { job = updated; drawJob(); } }
  } catch(error) { toast(error.message); }
  finally { resourceCleaning = false; await modelStatus(); await loadStorage(true); }
});
$('modelBadge').addEventListener('click', () => { modelStatus(); if($('provider').value.startsWith('api:')) showAPISettings(); else showModal('modelModal'); });
$('installModel').addEventListener('click', async () => { try { await api('/api/model/install', {method:'POST'}); await modelStatus(); toast('正在下载模型，论文不会被上传。完成后可离线翻译。'); } catch(error) { toast(error.message); } });
$('prepareFull').addEventListener('click', async () => { try { await api('/api/resources/install', {method:'POST'}); await modelStatus(); toast('正在准备全文精译，下载只获取公开模型与字体。'); } catch(error) { toast(error.message); } });
$('engine').addEventListener('change', () => { if ($('engine').value === 'conservative') $('profile').value = 'standard'; settingsSummary(); });
function selectFile() { $('fileInput').click(); }
$('chooseFile').addEventListener('click', event => { event.stopPropagation(); selectFile(); });
$('dropzone').addEventListener('click', selectFile);
$('dropzone').addEventListener('keydown', event => { if(event.target === $('dropzone') && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); selectFile(); } });
$('replacePaper').addEventListener('click', selectFile);
$('fileInput').addEventListener('change', event => { if(event.target.files.length) uploadFiles(Array.from(event.target.files)); event.target.value = ''; });
['dragenter','dragover'].forEach(type => $('dropzone').addEventListener(type, event => { event.preventDefault(); $('dropzone').classList.add('dragover'); }));
['dragleave','drop'].forEach(type => $('dropzone').addEventListener(type, event => { event.preventDefault(); $('dropzone').classList.remove('dragover'); }));
let fileDragDepth = 0;
document.addEventListener('dragenter', event => {
  if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return;
  event.preventDefault(); fileDragDepth++;
  document.body.classList.add('file-dragging');
});
document.addEventListener('dragover', event => {
  if (Array.from(event.dataTransfer?.types || []).includes('Files')) event.preventDefault();
});
document.addEventListener('dragleave', () => {
  fileDragDepth = Math.max(0, fileDragDepth - 1);
  if (!fileDragDepth) document.body.classList.remove('file-dragging');
});
document.addEventListener('drop', event => {
  fileDragDepth = 0; document.body.classList.remove('file-dragging');
  if (event.dataTransfer?.files.length) {
    event.preventDefault(); uploadFiles(Array.from(event.dataTransfer.files));
  }
});
const batch = new PaperBatch.BatchQueue(localStorage);
let importing = false, submittingBatch = false;
function translationSettings() {
  return {domain:$('domain').value,glossary:terms,polish:$('polish').checked,
    pages:$('pages').value,engine:$('engine').value,profile:$('profile').value,auto_cleanup:$('autoCleanup').checked,
    provider:$('provider').value.startsWith('api:') ? 'api' : 'local',api_profile_id:selectedAPIProfile()?.id || '',force_cpu:$('forceCPU').checked,allow_slow_cpu:false};
}
function renderBatch() {
  $('batchPanel').hidden = !batch.items.length;
  $('batchSummary').textContent = `${batch.items.length} 篇 · 已生成 ${batch.items.filter(i => i.status === 'complete').length} 篇 · 等待或处理中 ${batch.items.filter(i => PaperBatch.processing(i.status)).length} 篇`;
  $('startBatch').disabled = !job || importing || submittingBatch || !batch.items.some(i => PaperBatch.retryable(i.status));
  $('startBatch').title = job ? '使用当前论文设置，依次处理待翻译论文' : '先打开一篇论文，选择翻译设置';
  $('clearBatch').disabled = importing || submittingBatch;
  $('batchList').replaceChildren();
  batch.items.forEach(item => {
    const row = document.createElement('div'); row.className = 'batch-row';
    const open = document.createElement('button'); open.className = 'batch-open'; open.textContent = item.name;
    open.addEventListener('click', () => openDocument(item.id));
    const state = document.createElement('small'); state.className = 'batch-state'; state.textContent = item.error || item.fetchError || `${statuses[item.status] || item.status} · ${item.progress || 0}%${item.pages ? ` · 页 ${item.pages}` : ''}`;
    row.append(open,state);
    if (PaperBatch.processing(item.status)) {
      const stop = document.createElement('button'); stop.className = 'text-button'; stop.textContent = '停止';
      stop.addEventListener('click', async () => { try { await api(`/api/documents/${item.id}/cancel`,{method:'POST'}); await refreshBatch(); } catch(error) { toast(error.message); } });
      row.append(stop);
    } else {
      const remove = document.createElement('button'); remove.className = 'text-button'; remove.textContent = '移出列表'; remove.setAttribute('aria-label',`从队列移出 ${item.name}`); remove.title = '论文仍保留在最近列表中'; remove.disabled = importing || submittingBatch;
      remove.addEventListener('click', () => { if(batch.remove(item.id)) { renderBatch(); toast('已移出队列列表，论文仍保留在最近的论文中'); } }); row.append(remove);
    }
    $('batchList').append(row);
  });
}
async function refreshBatch() {
  await batch.refresh(id => api(`/api/documents/${id}`));
  batch.save(); renderBatch();
}
async function uploadFiles(files) {
  if(importing) return toast('正在读取上一组论文，请稍候');
  importing = true; $('chooseFile').disabled = true; $('replacePaper').disabled = true;
  const failures = []; let added = 0;
  try {
    for(const file of files) {
      $('chooseFile').textContent = `正在读取 ${added + failures.length + 1} / ${files.length}…`; $('replacePaper').textContent = `导入 ${added + failures.length + 1}/${files.length}…`;
      try {
        if (!/\.pdf$/i.test(file.name)) throw new Error('请选择 PDF 文件');
        if (file.size > 80 * 1024 * 1024) throw new Error('文件不能超过 80 MB');
        const document = await api('/api/documents', {method:'POST',headers:{'Content-Type':'application/pdf','X-Filename':encodeURIComponent(file.name)},body:file});
        batch.add(document); added++; await openJob(document, true); renderBatch();
      } catch(error) { failures.push(`${file.name}：${error.message}`); }
    }
    await recent();
    toast(`已导入 ${added} 篇论文${failures.length ? `；${failures.join('；')}` : '，可开始单篇或批量翻译'}`);
  } finally {
    importing = false; $('chooseFile').disabled = false; $('replacePaper').disabled = false;
    $('chooseFile').replaceChildren(icon('plus'), document.createTextNode('选择论文 PDF')); $('replacePaper').replaceChildren(icon('upload'), document.createTextNode('添加论文')); renderBatch();
  }
}
async function submitBatch(settings) {
  if(submittingBatch || importing) return;
  submittingBatch = true; renderBatch();
  try {
    await batch.start(settings, (id, settings) => api(`/api/documents/${id}/translate`, {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)}), renderBatch);
    if(job) { const id = job.id, revision = viewRevision; const updated = await api(`/api/documents/${id}`); if(job?.id === id && viewRevision === revision) { job = updated; drawJob(); } }
    toast('批量任务已提交，依次翻译；可点击列表查看每篇进度');
  } finally { submittingBatch = false; renderBatch(); }
}
$('startBatch').addEventListener('click',() => withLocalHardware(translationSettings(),submitBatch).catch(error => toast(error.message)));
$('clearBatch').addEventListener('click', () => { batch.clear(); renderBatch(); toast('列表已收起，论文与翻译任务仍保留在最近的论文中'); });
$('tryDemo').addEventListener('click', async () => { $('tryDemo').disabled = true; try { await openJob(await api('/api/demo', {method:'POST'})); await recent(); } catch(error) { toast(error.message); } finally { $('tryDemo').disabled = false; } });
$('newPaper').addEventListener('click', () => { viewRevision++; openingRequest++; closeModal('documentTermsModal'); focusReader(false); setSidebar(false); job = null; drawRecent(); renderBatch(); $('welcome').hidden = false; $('workspace').hidden = true; history.replaceState(null, '', '/'); });
async function recent() {
  try { recentDocuments = await api('/api/documents'); drawRecent(); return true; } catch(error) { toast(error.message); return false; }
}
function drawRecent() {
  const docs = recentDocuments.filter(doc => PaperUI.normalize(doc.name).includes(PaperUI.normalize($('recentSearch').value)));
  $('recentList').replaceChildren();
  if(!docs.length) { const p = document.createElement('p'); p.textContent = recentDocuments.length ? '没有找到这篇论文' : '导入的论文会保存在这里'; $('recentList').append(p); }
  docs.forEach(doc => {
    const button = document.createElement('button'); button.className = 'recent-item'; button.dataset.documentId = doc.id; button.classList.toggle('active',job?.id === doc.id); if(job?.id === doc.id) button.setAttribute('aria-current','page');
    button.append(icon('file')); const copy = document.createElement('div'); copy.className = 'recent-copy';
    const name = document.createElement('b'); name.className = 'recent-name'; name.textContent = doc.name; name.style.fontWeight = '500';
    const state = document.createElement('small'); state.className = 'recent-state'; state.textContent = `${doc.page_count} 页 · ${doc.settings?.pages ? `译 ${doc.settings.pages} 页` : '全文范围'} · ${statuses[doc.status] || doc.status}`;
    copy.append(name,state); button.append(copy); button.title = doc.name; button.addEventListener('click', () => openDocument(doc.id)); $('recentList').append(button);
  });
}
$('recentSearch').addEventListener('input',drawRecent);
$('refreshRecent').addEventListener('click', recent);
async function openDocument(id) {
  const request = ++openingRequest;
  try { const document = await api(`/api/documents/${id}`); if(request === openingRequest) await openJob(document); }
  catch(error) { if(request === openingRequest) toast(error.message); }
}
async function openJob(document, newDocument = false) {
  const revision = ++viewRevision; openingRequest++; closeModal('documentTermsModal');
  job = document; page = PaperUI.readingPage(localStorage,job.id,job.page_count); targetedSegment = null; pageSegments = []; $('segmentFilter').value = 'all'; $('segmentSearch').value = ''; $('issuePanel').hidden = true; $('issuePanel').open = false; $('issueList').replaceChildren(); $('segmentList').replaceChildren(); $('downloadMenu').hidden = true; setSidebar(false); drawRecent();
  $('translationOptions').open = !job.has_output;
  $('welcome').hidden = true; $('workspace').hidden = false; $('domain').value = job.settings.domain || 'general'; $('engine').value = job.settings.engine || (job.translated ? 'conservative' : 'full'); $('profile').value = PaperUI.initialProfile(job, currentStatus); $('pages').value = job.settings.pages || ''; $('polish').checked = job.settings.polish !== false; terms = PaperReader.documentTerms(job.settings, localStorage.getItem('paperbridge.terms') || '', newDocument); $('customTerms').value = terms; history.replaceState(null, '', `/?job=${job.id}`);
  $('autoCleanup').checked = job.settings.auto_cleanup !== false;
  $('forceCPU').checked = !!job.settings.force_cpu;
  if(job.settings.provider === 'api') refreshServiceOptions(`api:${job.settings.api_profile_id}`);
  else if(job.settings.provider === 'local') $('provider').value = 'local';
  else if(job.settings.provider === 'deepseek') {
    const matching = apiProfiles.find(p => p.kind === 'deepseek' && p.model === job.settings.api_model);
    if(matching) refreshServiceOptions(`api:${matching.id}`);
  }
  settingsSummary(); drawJob(); await updatePage(); if(viewRevision === revision) await updateIssues();
}
const statuses = {ready:'待翻译',queued:'等待处理',running:'正在翻译',exporting:'正在排版',complete:'译稿已生成',failed:'运行中断',interrupted:'可继续',cancelled:'已停止'};
function drawJob() {
  if(!job) return;
  renderBatch();
  $('docName').textContent = job.name;
  const figures = job.pages.reduce((n,p) => n + p.images,0), tables = job.table_count ?? job.pages.reduce((n,p) => n + p.tables,0);
  $('docInfo').textContent = `${job.page_count} 页 · ${job.candidates} 个可翻译区域 · ${figures} 个嵌入图像 · ${tables} 个检测到的表格`;
  $('jobStatus').textContent = statuses[job.status] || job.status; $('jobMessage').textContent = job.message;
  if(job.status === 'exporting' && job.validation_progress) $('jobStatus').textContent = '正在校验';
  const failure = job.status === 'failed' && job.failure?.kind === 'local_runtime';
  const localActivity = busy() && job.settings.provider !== 'api' && job.settings.provider !== 'deepseek' && job.local_runtime;
  $('runtimeNotice').hidden = !failure && !localActivity;
  $('runtimeNotice').classList.toggle('runtime-error',!!failure);
  $('runtimeNoticeText').textContent = failure ? job.failure.message : localActivity?.message || '';
  $('runtimeRecovery').hidden = !failure;
  const progress = job.status === 'queued' ? 0 : job.progress;
  $('progressCard').dataset.state = job.status;
  $('jobPercent').textContent = `${progress}%`; $('progressBar').style.width = `${progress}%`;
  const coverage = job.export_report?.paragraph_coverage_percent;
  const wordCoverage = job.export_report?.source_word_coverage_percent;
  $('jobMetrics').replaceChildren();
  const metrics = coverage !== undefined ? [['正文已排入',`${coverage}%`,busy() ? '上次导出 · 选定页' : '选定页 · 完整度'],['已生成译文',`${job.translated} 段`,`${job.exported} 段已导出`],PaperUI.issueMetric(job),['翻译范围',job.settings.pages || '全部页',`${job.page_count} 页原稿`]] : [['可翻译区域',`${job.candidates} 处`,'正文与图表文字'],['已生成译文',`${job.translated} 段`,($('provider').value.startsWith('api:') ? '所选 API 翻译' : '在本机处理')],['保留原文',`${job.preserved_count} 处`,'公式、引文等区域'],['翻译范围',job.settings.pages || '全部页',`${job.page_count} 页原稿`]];
  metrics.forEach(([name,value,note],index) => { const card = document.createElement('div'); card.className = `metric${coverage !== undefined && index === 2 ? ' note' : ''}`; const label = document.createElement('small'); label.textContent = name; const number = document.createElement('strong'); number.textContent = value; const caption = document.createElement('span'); caption.textContent = note; card.append(label,number,caption); $('jobMetrics').append(card); });
  const [durationLabel,durationValue,durationNote] = PaperUI.durationMetric(job);
  $('durationLabel').textContent = durationLabel; $('durationValue').textContent = durationValue; $('durationNote').textContent = durationNote;
  const usage = ['deepseek','api'].includes(job.settings.provider) ? job.api_usage || job.export_report?.inference_performance : null;
  $('apiUsage').hidden = !['deepseek','api'].includes(usage?.provider);
  $('apiUsage').textContent = usage ? `API 用量：输入 ${(usage.prompt_tokens || 0).toLocaleString()} / 输出 ${(usage.completion_tokens || 0).toLocaleString()} tokens` : '';
  const compression = job.export_report?.pdf_compression;
  $('pdfSize').hidden = !compression || busy();
  if(compression) { $('pdfSize').textContent = `中文 PDF ${PaperUI.formatBytes(compression.after_bytes)}`; $('pdfSize').title = compression.status === 'optimized' ? `已无损缩小 ${PaperUI.formatBytes(compression.saved_bytes)}；逐页内容与外观检查通过` : '已保留原导出文件'; }
  $('jobCounts').textContent = `${coverage !== undefined ? `${busy() ? '上次导出' : '选定页面'}的正文段落覆盖 ${coverage}% · 源文词数覆盖 ${wordCoverage}%　 · 　` : ''}已生成 ${job.translated} 段译文　 · 　已排入 PDF ${job.exported} 段　 · 　尚未译出 ${Math.max(0, job.candidates - job.translated)} 个区域　 · 　自动检查提示 ${job.review_count} 段　 · 　保留原文 ${job.preserved_count} 个区域`;
  if(job.document_terminology) $('jobCounts').textContent += `　 · 　本篇术语 ${job.document_terminology.entries.length} 条 · 续接联译 ${job.document_context?.joint_success?.length || 0} 组`;
  const cleanup = job.cleanup_report;
  $('cleanupNote').hidden = !cleanup || busy();
  if(cleanup) {
    const bytes = cleanup.removed_bytes || 0;
    const freed = bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : `${(bytes / 1e6).toFixed(1)} MB`;
    const memory = cleanup.model_memory_released ? '模型内存已释放 · ' : '';
    $('cleanupNote').textContent = memory + (cleanup.status === 'disabled' ? '已按设置保留临时文件和旧自动备份' : cleanup.status === 'partial' ? '部分文件未能清理，最新 PDF 已保留' : `${cleanup.manual ? '所选内容已清理' : '临时文件已清理'}${bytes ? ` · 移除 ${freed} 文件` : ''}${cleanup.old_versions_removed ? ` · 清除 ${cleanup.old_versions_removed} 份旧自动备份` : ''}`);
  }
  $('saveDocumentTerms').disabled = busy() || documentTermSaving; $('documentTermOverrides').disabled = busy() || documentTermSaving;
  $('reviewBadge').hidden = !job.review_count; $('reviewBadge').textContent = job.review_count;
  const processing = busy(); $('startTranslate').hidden = processing; $('cancelTranslate').hidden = !processing;
  $('segmentList').querySelectorAll('.segment-card').forEach(card => {
    card.querySelector('textarea').disabled = processing || card.querySelector('.segment-status').classList.contains('preserved');
    card.querySelectorAll('.segment-actions button').forEach(button => button.disabled = processing);
  });
  if(processing) $('issuePanel').hidden = true;
  $('startTranslate').replaceChildren(icon('spark'), document.createTextNode(job.translated ? (job.status === 'complete' ? '重新翻译' : '继续翻译') : '开始翻译'));
  $('domain').disabled = $('pages').disabled = $('polish').disabled = $('engine').disabled = $('profile').disabled = $('autoCleanup').disabled = processing;
  $('provider').disabled = processing;
  $('forceCPU').disabled = processing;
  $('autoCleanup').disabled = processing || cleanupSaving;
  $('downloadBtn').disabled = !job.has_output || processing;
  $('repairTranslate').disabled = !currentStatus.repair_available || processing || !job.translated || job.settings.engine !== 'full';
  $('warnings').replaceChildren(); $('warnings').hidden = !job.warnings.length;
  job.warnings.forEach(w => {const p = document.createElement('p'); p.textContent = `第 ${w.page} 页：${w.message}`; $('warnings').append(p);});
  $('outputNote').textContent = job.has_output ? (busy() ? '当前显示上次导出，新版处理中' : job.settings.pages ? `已译页 ${job.settings.pages}；其余页保留原文` : '译文已生成，可直接阅读') : '';
}
async function updateIssues() {
  if(!job) return;
  if(busy() || !currentStatus.issue_navigation) { $('issuePanel').hidden = true; return; }
  const id = job.id, revision = viewRevision;
  try {
    const result = await api(`/api/documents/${id}/issues`);
    if(job?.id !== id || viewRevision !== revision || busy()) return;
    $('issuePanel').hidden = !result.count; $('issueCount').textContent = `(${result.count})`;
    $('issueList').replaceChildren();
    result.issues.slice(0,30).forEach(issue => {
      const button = document.createElement('button'); button.className = 'issue-item';
      const location = document.createElement('b'); location.textContent = `第 ${issue.page} 页`;
      const text = document.createElement('span'); text.textContent = [issue.reason,...issue.notes].filter(Boolean).join('；');
      button.title = issue.source; button.append(location,text);
      button.addEventListener('click', async () => {
        if(job?.id !== id || viewRevision !== revision) return;
        page = issue.page; targetedSegment = issue.id; $('segmentFilter').value = 'all'; $('segmentSearch').value = '';
        await selectTab('text',false); await updatePage();
        if(job?.id !== id || viewRevision !== revision) return;
        const card = [...$('segmentList').children].find(el => el.dataset.segmentId === issue.id);
        (card || $('textView')).scrollIntoView({behavior:'smooth',block:'center'});
      });
      $('issueList').append(button);
    });
  } catch(error) { if(!busy()) toast(error.message); }
}
async function updatePage() {
  if(!job) return;
  PaperUI.rememberPage(localStorage,job.id,page);
  $('pageNumber').textContent = `/ ${job.page_count}`;
  $('pageJump').value = page; $('pageJump').max = job.page_count;
  reader.reset();
  $('prevPage').disabled = page <= 1; $('nextPage').disabled = page >= job.page_count;

  const image = $('translatedPreview'), empty = $('translatedContainer').querySelector('.translation-empty');
  image.hidden = !job.has_output; empty.hidden = job.has_output;
  if(!job.has_output) image.parentElement.querySelector('.preview-status').hidden = true;
  refreshPreview();
  if(activeTab === 'text') await updateSegments();
}
$('prevPage').addEventListener('click', () => { if(page > 1) { page--; updatePage(); } });
$('nextPage').addEventListener('click', () => { if(job && page < job.page_count) { page++; updatePage(); } });
function jumpToPage() {
  const number = Number($('pageJump').value);
  if (job && Number.isInteger(number) && number >= 1 && number <= job.page_count) { page = number; updatePage(); }
  else { $('pageJump').value = page; toast('请输入这篇论文中的有效页码'); }
}
$('pageJump').addEventListener('change', jumpToPage);
$('pageJump').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); jumpToPage(); }
});
async function selectTab(tab, reload = true) { activeTab = tab; $('pdfView').hidden = tab !== 'pdf'; $('readingControls').hidden = tab !== 'pdf'; $('textView').hidden = tab !== 'text'; $('pdfTab').classList.toggle('selected',tab === 'pdf'); $('textTab').classList.toggle('selected',tab === 'text'); $('pdfTab').setAttribute('aria-selected',String(tab === 'pdf')); $('textTab').setAttribute('aria-selected',String(tab === 'text')); if(tab === 'text' && reload) await updateSegments(); else reader.resize(); }
$('pdfTab').addEventListener('click', () => selectTab('pdf')); $('textTab').addEventListener('click', () => selectTab('text'));
const segmentStatuses = {pending:'待翻译',translated:'译文已生成',review:'有检查提示',failed:'未译成功 · 保留英文',preserved:'保留原文',edited:'已手动修改'};
async function updateSegments() {
  if(!job) return;
  const id = job.id, revision = viewRevision, selectedPage = page, request = ++segmentRequest;
  pageSegments = []; $('segmentList').replaceChildren(); $('segmentCount').textContent = '正在读取…';
  try { const segments = await api(`/api/documents/${id}/segments?page=${selectedPage}`); if(job?.id !== id || viewRevision !== revision || page !== selectedPage || segmentRequest !== request) return; pageSegments = segments; renderSegments(); }
  catch(error) { if(job?.id === id && viewRevision === revision && page === selectedPage && segmentRequest === request) { $('segmentCount').textContent = '读取失败'; toast(error.message); } }
}
function renderSegments() {
  if(!job) return;
  const id = job.id, revision = viewRevision;
  const segments = pageSegments.filter(seg => PaperUI.matchesSegment({...seg,translation:PaperUI.readDraft(localStorage,id,seg) ?? seg.translation},$('segmentFilter').value,$('segmentSearch').value));
  $('segmentList').replaceChildren(); $('segmentCount').textContent = `${segments.length} / ${pageSegments.length} 段`;
  segments.forEach(seg => {
      const card = document.createElement('article'); card.className = 'segment-card'; card.dataset.segmentId = seg.id; card.classList.toggle('targeted',seg.id === targetedSegment);
      const header = document.createElement('div'); header.className = 'segment-header';
      const label = document.createElement('span');
      const kind = seg.kind === 'table' ? '表格单元格' : ({title:'标题',figure_caption:'图注',table_caption:'表注',abandon:'页边文字'}[seg.layout_label] || '正文');
      label.textContent = `第 ${seg.page} 页 · ${kind}`;
      const badge = document.createElement('span'); badge.className = `segment-status ${seg.status}`; badge.textContent = segmentStatuses[seg.status] || seg.status; header.append(label,badge);
      const content = document.createElement('div'); content.className = 'segment-content';
      const source = document.createElement('p'); source.className = 'source-text'; source.textContent = seg.source;
      const draft = PaperUI.readDraft(localStorage,id,seg);
      const textarea = document.createElement('textarea'); textarea.value = draft ?? seg.translation; textarea.placeholder = seg.reason || '译文将在这里显示'; textarea.disabled = busy() || seg.status === 'preserved'; textarea.setAttribute('aria-label',`第 ${seg.page} 页${kind}的中文译文`); content.append(source,textarea);
      const notes = document.createElement('p'); notes.className = 'segment-notes'; notes.textContent = [seg.reason,...seg.notes].filter(Boolean).join('；');
      card.append(header,content,notes);
      if(seg.formula_hints && Object.keys(seg.formula_hints).length) {
        const details = document.createElement('details'); details.className = 'formula-hints';
        const summary = document.createElement('summary'); summary.textContent = '本段公式对照'; details.append(summary);
        Object.entries(seg.formula_hints).forEach(([marker,formula]) => { const line = document.createElement('p'); line.textContent = `${marker}　${formula}`; details.append(line); });
        card.append(details);
      }
      if(seg.status !== 'preserved') {
        const actions = document.createElement('div'); actions.className = 'segment-actions';
        const draftStatus = document.createElement('span'); draftStatus.className = 'draft-status'; draftStatus.setAttribute('role','status');
        const discard = document.createElement('button'); discard.className = 'text-button'; discard.textContent = '还原已保存译文'; discard.disabled = busy();
        function markDraft() { const changed = textarea.value !== (seg.translation || ''); draftStatus.textContent = changed ? '本机草稿 · 尚未更新 PDF' : '修改后可保存到 PDF'; discard.hidden = !changed; }
        markDraft();
        textarea.addEventListener('input', () => { try { PaperUI.saveDraft(localStorage,id,seg,textarea.value); markDraft(); } catch { draftStatus.textContent = '草稿未能暂存，请及时保存'; } });
        discard.addEventListener('click', () => { try { PaperUI.clearDraft(localStorage,id,seg); textarea.value = seg.translation || ''; markDraft(); } catch { toast('草稿未能还原，请重试'); } });
        const save = document.createElement('button'); save.className = 'secondary compact'; save.textContent = '保存修改并更新 PDF'; save.disabled = busy();
        save.addEventListener('click', async () => {
          if(job?.id !== id || viewRevision !== revision) return;
          if(!textarea.value.trim()) return toast('译文不能为空');
          const submitted = textarea.value; save.disabled = true; textarea.disabled = true; discard.disabled = true; save.textContent = '正在更新 PDF…';
          try {
            const updated = await api(`/api/documents/${id}/segments/${seg.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({translation:submitted})});
            try { if(PaperUI.readDraft(localStorage,id,seg) === submitted) PaperUI.clearDraft(localStorage,id,seg); } catch { /* Keep any newer draft written while the save was in progress. */ }
            if(job?.id === id && viewRevision === revision) { job = updated; drawJob(); await updatePage(); await updateIssues(); }
            toast('译文已保存，PDF 已更新');
          } catch(error) { toast(error.message); }
          finally { save.disabled = busy(); textarea.disabled = busy(); discard.disabled = busy(); save.textContent = '保存修改并更新 PDF'; }
        });
        actions.append(draftStatus,discard,save); card.append(actions);
      }
      $('segmentList').append(card);
    });
    if(!segments.length) { const p = document.createElement('p'); p.className = 'empty-results'; p.textContent = pageSegments.length ? '没有符合筛选条件的段落，试试调整筛选或搜索词。' : '本页没有可提取文字，原始页面已保留。'; $('segmentList').append(p); }
}
$('segmentFilter').addEventListener('change',renderSegments);
$('segmentSearch').addEventListener('input',renderSegments);
async function submitTranslation(id, revision, settings) {
  if(job?.id !== id || viewRevision !== revision || busy()) return;
  $('startTranslate').disabled = true;
  try { const updated = await api(`/api/documents/${id}/translate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)}); if(job?.id === id && viewRevision === revision) { job = updated; drawJob(); if(activeTab === 'text') await updateSegments(); } }
  catch(error) { handleHardwareFailure(error,settings,chosen => submitTranslation(id,revision,chosen)); } finally { $('startTranslate').disabled = false; }
}
$('startTranslate').addEventListener('click', async () => {
  if(!job || startingTranslation) return;
  startingTranslation = true;
  $('startTranslate').disabled = true;
  $('startTranslate').textContent = '正在检查配置…';
  try {
  const id = job.id, revision = viewRevision, settings = translationSettings(); await modelStatus();
  if(job?.id !== id || viewRevision !== revision) return;
  if(settings.provider === 'api') {
    if(!selectedAPIProfile()?.key_configured) { showAPISettings(); return; }
  } else if(settings.profile === 'precision' ? !currentStatus.precision_installed : !currentStatus.model_installed) { showModal('modelModal'); return; }
  await withLocalHardware(settings,chosen => submitTranslation(id,revision,chosen));
  } catch(error) { toast(error.message); }
  finally { startingTranslation = false; $('startTranslate').disabled = false; if(job) drawJob(); }
});
$('cancelTranslate').addEventListener('click', async () => { if(!job) return; try { const result = await api(`/api/documents/${job.id}/cancel`,{method:'POST'}); toast(result.message); } catch(error) { toast(error.message); } });
$('repairTranslate').addEventListener('click', async () => {
  if(!job || busy()) return;
  const id = job.id, revision = viewRevision;
  $('repairTranslate').disabled = true;
  const repair = async settings => {
    if(job?.id !== id || viewRevision !== revision || busy()) return;
    // Changing model/provider is a translation, while CPU approval preserves
    // the current translation and follows the normal repair path.
    if(settings.provider !== (job.settings.provider || 'local') || settings.profile !== job.settings.profile) return submitTranslation(id,revision,settings);
    const updated = await api(`/api/documents/${id}/repair?allow_slow_cpu=${!!settings.allow_slow_cpu}&force_cpu=${!!settings.force_cpu}`,{method:'POST'});
    if(job?.id === id && viewRevision === revision) { job = updated; drawJob(); } toast('沿用本篇翻译设置，复查疑点；上一版 PDF 已保存');
  };
  try { await withLocalHardware({...translationSettings(),...job.settings},repair); }
  catch(error) { handleHardwareFailure(error,job.settings,repair); drawJob(); }
});
$('downloadBtn').addEventListener('click', event => { event.stopPropagation(); $('downloadMenu').hidden = !$('downloadMenu').hidden; $('downloadBtn').setAttribute('aria-expanded',String(!$('downloadMenu').hidden)); if(!$('downloadMenu').hidden) $('downloadMenu').querySelector('button').focus(); });
document.addEventListener('click', () => $('downloadMenu').hidden = true);
document.querySelectorAll('[data-download]').forEach(button => button.addEventListener('click', () => { if(job) window.location.assign(`/api/documents/${job.id}/download/${button.dataset.download}`); }));
let libraryTerms = {}, libraryDetails = new Map();
function renderLibrary() {
  const query = $('termSearch').value.trim().toLocaleLowerCase();
  const rows = Object.entries(libraryTerms).filter(([en,zh]) => `${en} ${zh}`.toLocaleLowerCase().includes(query));
  $('termCount').textContent = `${$('domain').selectedOptions[0].textContent} · ${rows.length} / ${Object.keys(libraryTerms).length} 条译名`;
  $('defaultTerms').replaceChildren();
  rows.forEach(([en,zh]) => {
    const row = document.createElement('div'); row.className = 'term-row';
    const pair = document.createElement('div'); pair.className = 'term-pair';
    const source = document.createElement('span'); source.textContent = en;
    const target = document.createElement('span'); target.textContent = zh; pair.append(source,target); row.append(pair);
    const detail = libraryDetails.get(en.toLocaleLowerCase());
    if (detail) {
      row.title = detail.note || '';
      if (detail.sources.length) {
        const link = document.createElement('a'); link.className = 'term-source'; link.textContent = '用词参考';
        link.href = detail.sources[0].url; link.title = detail.sources[0].title; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.append(link);
      }
      if (detail.note) { const note = document.createElement('small'); note.className = 'term-note'; note.textContent = detail.note; row.append(note); }
    }
    $('defaultTerms').append(row);
  });
  if (!rows.length) { const empty = document.createElement('p'); empty.className = 'term-note'; empty.textContent = '没有匹配的词条，可以直接加入你的译名。'; $('defaultTerms').append(empty); }
}
$('termSearch').addEventListener('input', renderLibrary);
async function openGlossary() {
  try {
    const domain = $('domain').value;
    const [defaults, pack] = await Promise.all([api(`/api/glossary/${domain}`),api(`/api/terminology/${domain}`)]);
    if (domain !== $('domain').value) return openGlossary();
    libraryTerms = defaults; libraryDetails = new Map();
    pack.entries.forEach(entry => [entry.english,...(entry.variants || [])].forEach(en => libraryDetails.set(en.toLocaleLowerCase(),entry)));
    $('termSearch').value = ''; renderLibrary(); $('customTerms').value = terms; showModal('glossaryModal');
  }
  catch(error) { toast(error.message); }
}
$('openGlossary').addEventListener('click', openGlossary); $('editGlossary').addEventListener('click',openGlossary);
$('saveGlossary').addEventListener('click', () => { terms = $('customTerms').value; localStorage.setItem('paperbridge.terms',terms); closeModal('glossaryModal'); toast('术语表已保存，下次翻译时采用'); });
let documentTermOwner = null, documentTermRequest = 0, documentTermRows = [], documentTermSaving = false;
function renderDocumentTerms() {
  const query = $('documentTermSearch').value.trim().toLocaleLowerCase();
  const rows = documentTermRows.filter(e => `${e.source} ${e.target}`.toLocaleLowerCase().includes(query));
  $('documentTermCount').textContent = `本篇使用的译名 · ${rows.length} / ${documentTermRows.length} 条`;
  $('documentTermList').replaceChildren();
  for(const entry of rows) {
    const row = document.createElement('div'); row.className = 'term-row';
    const pair = document.createElement('div'); pair.className = 'term-pair';
    for(const text of [entry.source, entry.target]) { const span = document.createElement('span'); span.textContent = text; pair.append(span); }
    const info = document.createElement('small'); info.className = 'term-note';
    info.textContent = `${entry.origin === 'user' ? '本篇已指定' : entry.origin === 'model' ? '模型结合语境建议' : '专业词库'}${entry.pages?.length ? ` · 第 ${entry.pages.join('、')} 页` : ''}`;
    const choose = document.createElement('button'); choose.className = 'text-button'; choose.textContent = '采用 / 修改';
    choose.addEventListener('click', () => {
      if(job?.id !== documentTermOwner || busy() || documentTermSaving) return;
      const lines = $('documentTermOverrides').value.split('\n').filter(line => line.split('=')[0].trim().toLocaleLowerCase() !== entry.source.toLocaleLowerCase());
      $('documentTermOverrides').value = [...lines.filter(Boolean), `${entry.source} = ${entry.target}`].join('\n');
      $('documentTermOverrides').focus();
    });
    row.append(pair, info, choose); $('documentTermList').append(row);
  }
  if(!rows.length) { const p = document.createElement('p'); p.className = 'term-note'; p.textContent = '暂无匹配词条。完成全文翻译后会生成本篇术语，也可直接指定译名。'; $('documentTermList').append(p); }
}
$('documentTermSearch').addEventListener('input', renderDocumentTerms);
$('openDocumentTerms').addEventListener('click', async () => {
  if(!job) return;
  const id = job.id, revision = viewRevision, request = ++documentTermRequest;
  documentTermOwner = id;
  try {
    const result = await api(`/api/documents/${id}/terminology`);
    if(job?.id !== id || viewRevision !== revision || request !== documentTermRequest || documentTermOwner !== id) return;
    documentTermRows = result.entries; $('documentTermsName').textContent = job.name;
    $('documentTermOverrides').value = result.overrides; $('documentTermSearch').value = '';
    $('documentTermOverrides').disabled = busy(); $('saveDocumentTerms').disabled = busy() || documentTermSaving;
    $('documentTermNotes').textContent = busy() ? '论文处理中，完成或停止后可修改译名。' : result.notes.join('；') || '本篇保存的译名优先，下一次翻译或复查时采用。';
    renderDocumentTerms(); showModal('documentTermsModal');
  } catch(error) { if(job?.id === id && request === documentTermRequest) toast(error.message); }
});
$('saveDocumentTerms').addEventListener('click', async () => {
  const id = documentTermOwner, revision = viewRevision;
  if(!id || job?.id !== id || busy() || documentTermSaving) return;
  const request = documentTermRequest; documentTermSaving = true; $('saveDocumentTerms').disabled = true; $('documentTermOverrides').disabled = true;
  try {
    const result = await api(`/api/documents/${id}/terminology`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({glossary:$('documentTermOverrides').value})});
    if(job?.id !== id || viewRevision !== revision || request !== documentTermRequest) return;
    job.document_term_overrides = result.overrides; documentTermRows = result.entries; renderDocumentTerms();
    toast('本篇译名已保存，下一次翻译或复查时采用');
  } catch(error) { if(job?.id === id && request === documentTermRequest) toast(error.message); }
  finally { documentTermSaving = false; $('saveDocumentTerms').disabled = busy(); $('documentTermOverrides').disabled = busy(); }
});
async function poll() {
  await modelStatus();
  if(job && busy()) { try { const previous = job.status; const id = job.id; const updated = await api(`/api/documents/${id}`); if(job?.id === id) { job = updated; drawJob(); if(!busy() && previous !== job.status) { await updatePage(); await recent(); await updateIssues(); toast(job.message); } } } catch(error) { toast(error.message); } }
  if(batch.items.length) await refreshBatch();
  timer = setTimeout(poll,2500);
}
async function boot() {
  await modelStatus();
  loadStorage();
  const listed = await recent();
  if(listed && !recentDocuments.length) {
    try { PaperUI.clearPaperRecords(localStorage); batch.clear(); } catch { /* Preserve normal use when browser storage is unavailable. */ }
    renderBatch();
  }
  await refreshBatch();
  const id = new URLSearchParams(location.search).get('job');
  if(id) { try { await openJob(await api(`/api/documents/${id}`)); } catch(error) { toast(error.message); } }
  timer = setTimeout(poll,2500);
}
boot();
