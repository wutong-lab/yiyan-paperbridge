const test = require('node:test');
const assert = require('node:assert/strict');
const ui = require('../paperbridge/static/workbench.js');
test('reading distinguishes automatic checks from blanket human approval, with actual issue filters',()=>{
  assert.deepEqual(ui.issueMetric({automatic_check:'complete',issue_count:0,review_count:87}),['自动检查','已完成','未发现规则疑点']);
  assert.deepEqual(ui.issueMetric({automatic_check:'complete',issue_count:2}),['自动检查','2 段提示','可一键复查修复']);
  assert.equal(ui.issueMetric({automatic_check:'pending',status:'running'})[1],'处理中');
  assert.equal(ui.issueMetric({automatic_check:'diagnostic'})[2],'未执行翻译');
  assert.ok(!ui.matchesSegment({status:'review',notes:['旧默认待核对'],has_issues:false},'review',''));
  assert.ok(ui.matchesSegment({status:'edited',notes:[],has_issues:true},'review',''));
});
test('storage cleanup selections preserve explicit choices, reject unknown keys and total only selected disk space',()=>{
  assert.deepEqual(ui.cleanupSelection(null),{memory:true,temporary:true,backups:true,downloads:false,logs:false});
  const choices=ui.cleanupSelection({memory:false,temporary:false,logs:true,downloads:'false',originals:true});
  assert.equal(choices.memory,false);assert.equal(choices.logs,true);assert.equal(choices.downloads,false);assert.equal(choices.originals,undefined);
  assert.equal(ui.cleanupEstimate([{id:'memory',bytes:0},{id:'temporary',bytes:30},{id:'backups',bytes:100},{id:'logs',bytes:5}],choices),105);
  assert.equal(ui.formatBytes(1.66e12),'1.66 TB');assert.equal(ui.formatBytes(24576),'24.6 KB');assert.equal(ui.formatBytes(0),'0 B');
});
test('duration distinguishes the queue, full task, failed attempt, old records and later PDF edits',()=>{
  assert.equal(ui.formatDuration(720.4),'12 分 0 秒');assert.equal(ui.formatDuration(3661),'1 小时 1 分 1 秒');
  assert.equal(ui.durationMetric({status:'queued',timing:{kind:'translation'}})[1],'等待开始');
  assert.equal(ui.durationMetric({status:'running',timing:{kind:'translation',started_at:1,elapsed_seconds:65}})[1],'1 分 5 秒');
  assert.equal(ui.durationMetric({status:'exporting',timing:{kind:'translation',started_at:1,elapsed_seconds:70}})[1],'1 分 10 秒');
  const last={kind:'translation',elapsed_seconds:720};
  assert.equal(ui.durationMetric({status:'complete',last_translation_timing:last,timing:{kind:'export',elapsed_seconds:8}})[1],'12 分 0 秒');
  assert.equal(ui.durationMetric({status:'failed',last_translation_timing:last,timing:{kind:'translation',finished_at:1,elapsed_seconds:25,status:'failed'}})[1],'25 秒');
  assert.equal(ui.durationMetric({status:'complete',export_report:{elapsed_seconds:720.4}})[1],'12 分 0 秒');
  assert.equal(ui.formatDuration(null),'—');assert.equal(ui.formatBytes(null),'—');
});
test('re-export timing without an earlier translation timer cannot be labelled full translation time',()=>{
  const job={status:'complete',timing:{kind:'export',status:'complete',finished_at:1,elapsed_seconds:8},export_report:{elapsed_seconds:8}};
  assert.deepEqual(ui.durationMetric(job),['导出用时','8 秒','复用已有译文，重新生成 PDF']);
  assert.equal(ui.durationMetric({...job,timing:{kind:'export',status:'complete',elapsed_seconds:null}})[1],'—');
  assert.equal(ui.durationMetric({...job,status:'exporting'})[2],'正在重新生成 PDF');
  assert.equal(ui.durationMetric({...job,status:'failed',timing:{...job.timing,status:'failed'}})[2],'本次导出未完成');
});
function storage(){const data=new Map();return {get length(){return data.size;},key:i=>[...data.keys()][i],getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};}

test('reading pages survive reopening, stay separate by paper, and clamp after a shorter upload',()=>{
  const saved=storage();ui.rememberPage(saved,'a',43);ui.rememberPage(saved,'b',2);
  assert.equal(ui.readingPage(saved,'a',71),43);assert.equal(ui.readingPage(saved,'b',8),2);
  assert.equal(ui.readingPage(saved,'a',20),20);assert.equal(ui.readingPage(saved,'c',8),1);
  saved.setItem('paperbridge.reading','invalid');assert.equal(ui.readingPage(saved,'a',71),1);
  ui.rememberPage(saved,'a',4);assert.equal(ui.readingPage(saved,'a',71),4);
  saved.setItem('paperbridge.reading','null');ui.rememberPage(saved,'a',3);assert.equal(ui.readingPage(saved,'a',71),3);
});
test('browser storage failure does not stop navigation',()=>{
  const unavailable={getItem(){throw Error('unavailable')},setItem(){throw Error('unavailable')}};
  assert.equal(ui.readingPage(unavailable,'a',8),1);assert.doesNotThrow(()=>ui.rememberPage(unavailable,'a',2));
});
test('drafts persist only for the matching paper and unchanged source, never edit the saved translation',()=>{
  const saved=storage(), segment={id:'p1-ab',source:'Original scientific text',translation:'原译文'};
  ui.saveDraft(saved,'a',segment,'未保存中文');assert.equal(ui.readDraft(saved,'a',segment),'未保存中文');
  assert.equal(ui.readDraft(saved,'b',segment),null);assert.equal(segment.translation,'原译文');
  assert.equal(ui.readDraft(saved,'a',{...segment,source:'Different original text'}),null);
  ui.saveDraft(saved,'a',segment,'原译文');assert.equal(ui.readDraft(saved,'a',segment),null);
  ui.saveDraft(saved,'a',segment,'另一个草稿');ui.clearDraft(saved,'a',segment);assert.equal(ui.readDraft(saved,'a',segment),null);
});
test('review filters retain failed and noted regions; search supports Chinese, case, and full-width characters',()=>{
  const failed={status:'failed',source:'Flux ratio',translation:'',reason:'公式缺失',notes:[]};
  assert.ok(ui.matchesSegment(failed,'review','ＦＬＵＸ'));assert.ok(ui.matchesSegment(failed,'all','公式'));
  assert.ok(!ui.matchesSegment(failed,'edited',''));
  assert.ok(ui.matchesSegment({status:'translated',source:'',translation:'通量比',notes:['需核对']},'review','通量'));
  assert.ok(!ui.matchesSegment({status:'preserved',source:'Figure',notes:[]},'review',''));
  assert.ok(ui.matchesSegment({status:'edited',source:'',translation:'通量比'},'edited','通量比'));
});

test('clearing paper records removes drafts and reading positions while preserving vocabulary and other preferences',()=>{
  const saved=storage();saved.setItem('paperbridge.terms','flux ratio = 通量比');saved.setItem('paperbridge.zoom','150');saved.setItem('another-app','keep');
  ui.rememberPage(saved,'a',3);ui.saveDraft(saved,'a',{id:'one',source:'Text'},'草稿甲');ui.saveDraft(saved,'b',{id:'two',source:'Text'},'草稿乙');
  assert.equal(ui.clearPaperRecords(saved),3);assert.equal(ui.readingPage(saved,'a',10),1);
  assert.equal(ui.readDraft(saved,'a',{id:'one',source:'Text'}),null);
  assert.equal(saved.getItem('paperbridge.terms'),'flux ratio = 通量比');assert.equal(saved.getItem('paperbridge.zoom'),'150');assert.equal(saved.getItem('another-app'),'keep');
  assert.equal(ui.clearPaperRecords(saved),0);
});

test('new documents use the installed model on Mac and Windows while existing translations keep their selected profile',()=>{
  assert.equal(ui.initialProfile({settings:{},translated:false},{platform:'macos',default_profile:'standard'}),'standard');
  assert.equal(ui.initialProfile({settings:{},translated:false},{platform:'macos',default_profile:'precision'}),'precision');
  assert.equal(ui.initialProfile({settings:{profile:'standard'}},{default_profile:'precision'}),'standard');
  assert.equal(ui.initialProfile({settings:{},translated:true},{default_profile:'precision'}),'standard');
  assert.equal(ui.initialProfile({settings:{},translated:false},{platform:'windows',default_profile:'standard'}),'standard');
});
