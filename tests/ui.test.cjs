const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../paperbridge/static');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const tags = [...html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*)>/gi)].map(match => {
  const attributes = Object.fromEntries([...match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(attr => [attr[1], attr[2] ?? true]));
  return {tag: match[1], attributes};
});
const byId = id => tags.find(node => node.attributes.id === id);
// Frozen functional surface before the visual redesign, including the new reader tools.
const requiredIds = [
  "advancedSettings",
  "annotationToolbar",
  "apiBase",
  "apiHeading",
  "apiJsonMode",
  "apiKey",
  "apiKeyHint",
  "apiModal",
  "apiModelId",
  "apiModelSuggestions",
  "apiName",
  "apiPreset",
  "apiPresetHint",
  "apiProfileList",
  "apiSelectionInfo",
  "apiStatus",
  "apiStreamUsage",
  "apiUsage",
  "autoCleanup",
  "backendMemory",
  "batchList",
  "batchPanel",
  "batchSummary",
  "cancelTranslate",
  "chooseFile",
  "cleanBackups",
  "cleanDownloads",
  "cleanLogs",
  "cleanMemory",
  "cleanResources",
  "cleanTemporary",
  "cleanupAvailability",
  "cleanupEstimate",
  "cleanupHint",
  "cleanupNote",
  "clearBatch",
  "configureAPI",
  "cpuModeSetting",
  "customTerms",
  "defaultTerms",
  "deleteAPIProfile",
  "diskFree",
  "diskTotal",
  "diskUsed",
  "diskUsedBar",
  "docInfo",
  "docName",
  "documentTermCount",
  "documentTermList",
  "documentTermNotes",
  "documentTermOverrides",
  "documentTermSearch",
  "documentTermsHeading",
  "documentTermsModal",
  "documentTermsName",
  "domain",
  "downloadBtn",
  "downloadMenu",
  "dropzone",
  "durationLabel",
  "durationNote",
  "durationValue",
  "editGlossary",
  "engine",
  "fileInput",
  "forceCPU",
  "glossaryHeading",
  "glossaryModal",
  "hardwareAPIChoice",
  "hardwareContinueCPU",
  "hardwareFacts",
  "hardwareHeading",
  "hardwareMessage",
  "hardwareModal",
  "hardwareSummary",
  "hardwareUse4B",
  "hardwareUseAPI",
  "helpButton",
  "helpHeading",
  "helpModal",
  "installModel",
  "issueCount",
  "issueList",
  "issuePanel",
  "jobCounts",
  "jobMessage",
  "jobMetrics",
  "jobPercent",
  "jobStatus",
  "linkZoom",
  "localModelSetting",
  "manualCleanupResult",
  "memoryResourceTab",
  "memoryResources",
  "memoryValue",
  "modelBadge",
  "modelHeading",
  "modelIntro",
  "modelMessage",
  "modelModal",
  "modelTitle",
  "newAPIProfile",
  "newPaper",
  "nextPage",
  "openAPIFromModel",
  "openAPISettings",
  "openDocumentTerms",
  "openGlossary",
  "openResources",
  "originalPaper",
  "originalPreview",
  "originalScroll",
  "originalZoom",
  "outputNote",
  "pageJump",
  "pageNumber",
  "pages",
  "pdfSize",
  "pdfTab",
  "pdfView",
  "polish",
  "prepareFull",
  "presetSummary",
  "prevPage",
  "privacyLabel",
  "profile",
  "progressBar",
  "progressCard",
  "provider",
  "readerFocus",
  "readerZoom",
  "readingControls",
  "recentList",
  "recentSearch",
  "refreshRecent",
  "refreshStorage",
  "repairTranslate",
  "replacePaper",
  "resourceBadge",
  "resourceHeading",
  "resourceMemory",
  "resourceModal",
  "reviewBadge",
  "runtimeNotice",
  "runtimeNoticeText",
  "runtimeRecovery",
  "saveAPIProfile",
  "saveDocumentTerms",
  "saveGlossary",
  "segmentCount",
  "segmentFilter",
  "segmentList",
  "segmentSearch",
  "serviceLabel",
  "settingsHint",
  "settingsSummary",
  "sidebar",
  "sidebarBackdrop",
  "softwareStorage",
  "startBatch",
  "startTranslate",
  "storageBreakdown",
  "storageMeasured",
  "storageResourceTab",
  "storageResources",
  "storageValue",
  "syncScroll",
  "tableTranslationHint",
  "termCount",
  "termSearch",
  "testAPI",
  "textTab",
  "textView",
  "toast",
  "toggleSidebar",
  "translateTables",
  "translatedContainer",
  "translatedPreview",
  "translatedScroll",
  "translatedZoom",
  "translationOptions",
  "tryDemo",
  "useLocalProvider",
  "warnings",
  "welcome",
  "windowsRequirements",
  "workerMemory",
  "workspace"
];

test('redesign retains every functional ID and does not duplicate event targets', () => {
  const ids = tags.map(node => node.attributes.id).filter(Boolean);
  assert.equal(new Set(ids).size, ids.length, 'duplicate IDs break event targeting');
  assert.deepEqual(requiredIds.filter(id => !ids.includes(id)), [], 'a feature control was removed');
  const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
  const referenced = [...app.matchAll(/\$\('([^']+)'\)/g)].map(match => match[1]);
  assert.deepEqual([...new Set(referenced)].filter(id => !ids.includes(id)), [], 'app.js references missing static controls');
});

test('desktop load order includes the reader, annotations and theme assets', () => {
  const scripts = tags.filter(node => node.tag === 'script').map(node => node.attributes.src);
  assert.deepEqual(scripts, ['/static/reader.js', '/static/fullscreen.js', '/static/annotations.js', '/static/batch.js', '/static/workbench.js', '/static/app.js']);
  const sheets = tags.filter(node => node.tag === 'link' && node.attributes.rel === 'stylesheet').map(node => node.attributes.href);
  assert.deepEqual(sheets, ['/static/style.css', '/static/reader.css', '/static/workbench.css', '/static/studio.css', '/static/annotations.css']);
  for (const uri of [...scripts, ...sheets]) assert.ok(fs.existsSync(path.join(root, uri.replace('/static/', ''))), `missing bundled asset ${uri}`);
});

test('welcome, dialogs and translation settings retain safe initial states', () => {
  assert.equal(byId('welcome').attributes.hidden, undefined);
  for (const id of ['workspace', 'batchPanel', 'resourceModal', 'modelModal', 'glossaryModal', 'documentTermsModal', 'helpModal', 'apiModal', 'hardwareModal']) assert.equal(byId(id).attributes.hidden, true, id);
  assert.equal(byId('translateTables').attributes.checked, undefined);
  assert.equal(byId('linkZoom').attributes.checked, undefined);
  for (const id of ['polish', 'autoCleanup', 'syncScroll']) assert.equal(byId(id).attributes.checked, true, id);
  assert.equal(byId('fileInput').attributes.multiple, true);
  assert.equal(byId('fileInput').attributes.accept, 'application/pdf,.pdf');
  assert.equal(byId('apiKey').attributes.type, 'password');
});

function values(id) {
  const select = html.match(new RegExp(`<select[^>]*id="${id}"[^>]*>([\\s\\S]*?)</select>`));
  assert.ok(select, id);
  return [...select[1].matchAll(/<option[^>]*value="([^"]*)"/g)].map(match => match[1]);
}
test('local models, API choice, processing modes and independent zoom remain available', () => {
  assert.deepEqual(values('profile'), ['precision', 'standard']);
  assert.deepEqual(values('engine'), ['full', 'conservative']);
  assert.deepEqual(values('provider'), ['local', 'configure']);
  assert.deepEqual(values('domain'), ['general', 'astronomy', 'biomed', 'materials', 'engineering']);
  const zooms = ['50', '100', '125', '150', '200', '250', '300', '400'];
  assert.deepEqual(values('originalZoom'), zooms);
  assert.deepEqual(values('translatedZoom'), zooms);
  assert.deepEqual(values('readerZoom'), ['', ...zooms]);
  for (const kind of ['translated', 'bilingual', 'report', 'original']) assert.ok(html.includes(`data-download="${kind}"`), kind);
});

test('detailed task metrics stay available inside an explicit disclosure', () => {
  const details = html.match(/<details class="processing-details">([\s\S]*?)<\/details>/);
  assert.ok(details, 'task detail disclosure');
  assert.ok(details[1].includes('id="jobMetrics"'));
  assert.ok(details[1].includes('id="jobCounts"'));
  assert.ok(!details[1].includes('id="durationValue"'), 'elapsed time must remain visible without expanding');
  assert.equal(byId('advancedSettings').attributes.open, undefined);
  assert.equal(byId('pdfTab').attributes['aria-controls'], 'pdfView');
  assert.equal(byId('textTab').attributes['aria-controls'], 'textView');
  assert.equal(byId('readerFocus').attributes['aria-pressed'], 'false');
});
