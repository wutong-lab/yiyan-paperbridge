(function(root) {
  const normalize = text => String(text || '').normalize('NFKC').toLocaleLowerCase().trim();
  function matchesSegment(segment, filter, query) {
    const selected = filter === 'all' || (filter === 'review'
      ? (typeof segment.has_issues === 'boolean' ? segment.has_issues : ['review', 'failed'].includes(segment.status) || !!segment.notes?.length)
      : segment.status === filter);
    return selected && normalize(`${segment.source} ${segment.translation} ${segment.reason || ''} ${(segment.notes || []).join(' ')}`).includes(normalize(query));
  }
  function readingPage(storage, id, count) {
    try {
      const saved = Number(JSON.parse(storage.getItem('paperbridge.reading') || '{}')[id]);
      return Number.isInteger(saved) && saved > 0 ? Math.min(saved, count) : 1;
    } catch { return 1; }
  }
  function rememberPage(storage, id, page) {
    try {
      let pages;
      try { pages = JSON.parse(storage.getItem('paperbridge.reading') || '{}'); } catch { pages = {}; }
      if (!pages || typeof pages !== 'object' || Array.isArray(pages)) pages = {};
      delete pages[id]; pages[id] = page;
      const entries = Object.entries(pages).slice(-60);
      storage.setItem('paperbridge.reading', JSON.stringify(Object.fromEntries(entries)));
    } catch { /* Reading can continue when browser storage is unavailable. */ }
  }
  const draftKey = (id, segment) => `paperbridge.draft.${id}.${segment.id}`;
  function readDraft(storage, id, segment) {
    try {
      const draft = JSON.parse(storage.getItem(draftKey(id, segment)) || 'null');
      return draft?.source === segment.source && typeof draft.text === 'string' ? draft.text : null;
    } catch { return null; }
  }
  function saveDraft(storage, id, segment, text) {
    if (text === (segment.translation || '')) storage.removeItem(draftKey(id, segment));
    else storage.setItem(draftKey(id, segment), JSON.stringify({source:segment.source, text}));
  }
  function clearDraft(storage, id, segment) { storage.removeItem(draftKey(id, segment)); }
  function clearPaperRecords(storage) {
    const keys = Array.from({length:storage.length}, (_, index) => storage.key(index));
    const records = keys.filter(key => key === 'paperbridge.reading' || key?.startsWith('paperbridge.draft.'));
    records.forEach(key => storage.removeItem(key));
    return records.length;
  }
  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '—';
    const total = Math.floor(seconds), hours = Math.floor(total / 3600), minutes = Math.floor(total / 60) % 60;
    return `${hours ? `${hours} 小时 ` : ''}${minutes || hours ? `${minutes} 分 ` : ''}${total % 60} 秒`;
  }
  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return '—';
    if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(2)} TB`;
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
    if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
    if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(1)} KB`;
    return `${Math.round(bytes)} B`;
  }
  const cleanupDefaults = {memory:true,temporary:true,backups:true,downloads:false,logs:false};
  function cleanupSelection(saved) {
    return Object.fromEntries(Object.entries(cleanupDefaults).map(([id,value]) => [id,typeof saved?.[id] === 'boolean' ? saved[id] : value]));
  }
  function cleanupEstimate(choices, selected) {
    return (choices || []).reduce((sum, row) => sum + (selected[row.id] && Number.isFinite(row.bytes) ? row.bytes : 0),0);
  }
  function durationMetric(job) {
    const timing = job.timing;
    if (['queued','running','exporting'].includes(job.status) && timing?.kind !== 'export') {
      return timing?.started_at ? ['已用时间',formatDuration(timing.elapsed_seconds),'本次任务 · 不含排队'] : ['已用时间','等待开始','排队时间单独计算'];
    }
    if (timing?.finished_at && timing.kind !== 'export' && timing.status !== 'complete') return ['已用时间',formatDuration(timing.elapsed_seconds),'本次任务未完成'];
    const last = job.last_translation_timing;
    if (last) return [last.kind === 'repair' ? '复查用时' : '翻译用时',formatDuration(last.elapsed_seconds),last.legacy ? '已有任务记录' : '包含准备、翻译与导出'];
    if (timing?.kind === 'export') return ['导出用时',formatDuration(timing.elapsed_seconds),
      ['queued','running','exporting'].includes(job.status) ? '正在重新生成 PDF' : timing.status === 'complete' ? '复用已有译文，重新生成 PDF' : '本次导出未完成'];
    if (timing?.finished_at && timing.kind !== 'export') return ['已用时间',formatDuration(timing.elapsed_seconds),'本次任务未完成'];
    return ['翻译用时',formatDuration(job.export_report?.elapsed_seconds),job.export_report?.elapsed_seconds != null ? '已有任务记录' : '完成后显示'];
  }
  function issueMetric(job) {
    if (job.automatic_check === 'diagnostic') return ['自动检查','排版诊断','未执行翻译'];
    if (job.automatic_check !== 'complete') return ['自动检查',['running','queued','exporting'].includes(job.status) ? '处理中' : '尚未完成','完成后显示结果'];
    const count = job.issue_count ?? job.review_count ?? 0;
    return ['自动检查',count ? `${count} 段提示` : '已完成',count ? '可一键复查修复' : '未发现规则疑点'];
  }
  function initialProfile(job, status) {
    if (['standard', 'precision'].includes(job.settings?.profile)) return job.settings.profile;
    if (job.translated && !job.settings?.engine) return 'standard';
    return ['standard', 'precision'].includes(status?.default_profile) ? status.default_profile : 'precision';
  }
  const api = {initialProfile, normalize, matchesSegment, readingPage, rememberPage, readDraft, saveDraft, clearDraft, clearPaperRecords, formatDuration, formatBytes, durationMetric, issueMetric, cleanupSelection, cleanupEstimate};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PaperUI = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
