const test = require('node:test');
const assert = require('node:assert/strict');
const {AnnotationSession, Controller, normalizeRect, validRect, unionRects, pointOnPage, selectedTextRects, selectionGeometry, shortcutsAllowed,
  normalizeColor, readColor, rememberColor, highlightOpacity, COLOR_STORAGE_KEY, COLORS} = require('../paperbridge/static/annotations.js');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function serverRecord(body) {
  const {id, page, kind, color, rect, rects, document_revision, version} = body;
  return {id, page, kind, color, rect, rects, document_revision, version, created_at: '2026-10-23T00:00:00Z'};
}
const rect = [.15, .2, .6, .23];
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixtureTransport(intercept = () => undefined) {
  return async (url, options = {}) => {
    const intercepted = intercept(url, options);
    if (intercepted !== undefined) return intercepted;
    const address = new URL(url, 'http://localhost');
    const version = address.searchParams.get('version') || 'original';
    const document_revision = `revision-${version}`;
    if (options.method === 'POST') return serverRecord(JSON.parse(options.body));
    if (options.method === 'DELETE') return {deleted: url.split('/').at(-1).split('?')[0]};
    if (address.pathname.endsWith('/text')) return {document_revision, page: Number(address.searchParams.get('page')), lines: []};
    return {version, document_revision, annotations: [], stale_count: 0};
  };
}

function memoryStorage() {
  const values = new Map();
  return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
}

test('custom colors are validated, normalized and restored without changing the RGB', () => {
  const storage = memoryStorage();
  assert.equal(normalizeColor('#A124F0'), '#a124f0');
  for (const value of ['red', '#abc', '#ffffff;opacity:0', null, 0]) assert.equal(normalizeColor(value), null);
  assert.equal(rememberColor(storage, '#A124F0'), true);
  assert.equal(storage.getItem(COLOR_STORAGE_KEY), '#a124f0');
  assert.equal(readColor(storage), '#a124f0');
  storage.setItem(COLOR_STORAGE_KEY, 'invalid');
  assert.equal(readColor(storage), COLORS[0].value);
});

test('unavailable browser storage never prevents choosing a color', () => {
  const unavailable = {getItem() { throw new Error('Privacy mode'); }, setItem() { throw new Error('Full'); }};
  assert.equal(readColor(unavailable), COLORS[0].value);
  assert.equal(rememberColor(unavailable, '#123456'), false);
  assert.equal(rememberColor(null, '#123456'), false);
  const controller = Object.create(Controller.prototype);
  controller.storage = unavailable; controller.render = () => {};
  assert.equal(controller.setColor('#123456'), true);
  assert.equal(controller.color, '#123456');
  assert.equal(controller.setColor('bad'), false); assert.equal(controller.color, '#123456');
});

test('native color picker has an accessible name and persists its committed selection', () => {
  function node(tag) {
    return {tagName: tag.toUpperCase(), children: [], attrs: {}, listeners: {}, dataset: {}, style: {setProperty() {}},
      classList: {add() {}}, setAttribute(key, value) { this.attrs[key] = value; },
      append(...children) { this.children.push(...children); }, addEventListener(type, handler) { this.listeners[type] = handler; }};
  }
  const doc = {createElement: node}, storage = memoryStorage();
  const controller = Object.create(Controller.prototype);
  Object.assign(controller, {document: doc, toolbar: node('div'), color: COLORS[0].value, storage, render() {}});
  controller.buildToolbar();
  assert.equal(controller.colorPicker.tagName, 'INPUT'); assert.equal(controller.colorPicker.type, 'color');
  assert.equal(controller.colorPicker.attrs['aria-label'], '自选批注颜色');
  assert.equal(controller.colorButtons.length, 3);
  controller.colorPicker.value = '#A124F0'; controller.colorPicker.listeners.input();
  assert.equal(controller.color, '#a124f0'); assert.equal(storage.getItem(COLOR_STORAGE_KEY), null);
  controller.colorPicker.listeners.change();
  assert.equal(readColor(storage), '#a124f0');
  controller.colorButtons[0].listeners.click();
  assert.equal(readColor(storage), COLORS[0].value);
});

test('bright highlights are stronger while dark custom colors retain readable paper contrast', () => {
  for (const color of COLORS.map(preset => preset.value)) assert.equal(highlightOpacity(color), .68);
  const colors = ['#000000', '#102030', '#9b0f44', '#a124f0', '#ff0000', '#00ff00', '#0000ff', '#ffffff'];
  for (const color of colors) {
    const alpha = highlightOpacity(color);
    const luminance = [1, 3, 5].map((i, index) => {
      const value = (1 - alpha) + alpha * parseInt(color.slice(i, i + 2), 16) / 255;
      return [0.2126, .7152, .0722][index] * (value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    }).reduce((a, b) => a + b, 0);
    assert.ok(alpha > 0 && alpha <= .68, color);
    assert.ok((luminance + .05) / .05 >= 7, `black text contrast under ${color}`);
  }
  assert.ok(highlightOpacity('#000000') < highlightOpacity(COLORS[0].value));
});

test('custom annotation RGB is sent unchanged and later palette changes do not recolor saved marks', async () => {
  const bodies = [];
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') bodies.push(JSON.parse(options.body));
  }));
  const controller = Object.create(Controller.prototype);
  Object.assign(controller, {storage: memoryStorage(), render() {}});
  controller.setColor('#A124F0');
  await session.setPage('paper-a', 1, true);
  await session.save('original', [rect], 'highlight', controller.color);
  await session.save('translated', [rect], 'underline', controller.color);
  controller.setColor('#ffe135');
  assert.deepEqual(bodies.map(body => body.color), ['#a124f0', '#a124f0']);
  assert.equal(session.states.original.annotations[0].color, '#a124f0');
  assert.equal(session.states.translated.annotations[0].color, '#a124f0');
});

test('PDF coordinates remain unchanged across zoom levels and reverse drags', () => {
  assert.deepEqual(pointOnPage({clientX: 150, clientY: 300}, {left: 50, top: 100, width: 400, height: 800}), [.25, .25]);
  assert.deepEqual(pointOnPage({clientX: 250, clientY: 500}, {left: 50, top: 100, width: 800, height: 1600}), [.25, .25]);
  assert.deepEqual(normalizeRect([.6, .23], [.15, .2]), rect);
  assert.deepEqual(normalizeRect([-.4, 2], [.5, .3]), [0, .3, .5, 1]);
  assert.equal(validRect([0, 0, 0, 1]), false);
  assert.equal(validRect([0, 0, NaN, 1]), false);
});

test('multiline selection preserves line spacing and column gaps', () => {
  const lines = [
    {words: [{rect: [.1, .1, .2, .12]}, {rect: [.21, .1, .3, .12]}, {rect: [.6, .1, .7, .12]}]},
    {words: [{rect: [.1, .14, .25, .16]}]},
    {words: [{rect: [.1, .3, .25, .32]}]},
  ];
  const picked = selectedTextRects([.05, .08, .8, .18], lines);
  assert.deepEqual(picked, [[.1, .1, .3, .12], [.6, .1, .7, .12], [.1, .14, .25, .16]]);
  assert.deepEqual(unionRects(picked), [.1, .1, .7, .16]);
});

test('selection clips to selected words and ignores malformed coordinates', () => {
  const picked = selectedTextRects([.2, .05, .4, .2], [{words: [
    {rect: [.1, .1, .15, .12]}, {rect: [.2, .1, .3, .12]}, {rect: [.3, .1, .4, .12]}, {rect: [0, 0, Infinity, 1]},
  ]}]);
  assert.deepEqual(picked, [[.2, .1, .4, .12]]);
});

test('a horizontal underline snaps to only the closest line within screen-pixel tolerance', () => {
  const lines = [
    {words: [{rect: [.1, .1, .2, .12]}, {rect: [.21, .1, .3, .12]}]},
    {words: [{rect: [.1, .135, .2, .155]}, {rect: [.21, .135, .3, .155]}]},
  ];
  const selection = [.09, .123, .31, .123];
  assert.deepEqual(selectionGeometry(selection, lines, 1000, 'underline'), [[.1, .1, .3, .12]]);
  assert.deepEqual(selectionGeometry([.09, .121, .31, .125], lines, 1000, 'underline'), [[.1, .1, .3, .12]]);
  // After 200% zoom, the same three-screen-pixel distance is 0.0015 PDF units.
  assert.deepEqual(selectionGeometry([.09, .1215, .31, .1215], lines, 2000, 'underline'), [[.1, .1, .3, .12]]);
});

test('horizontal gestures on scanned pages become valid narrow marks without snapping distant text', () => {
  const lines = [{words: [{rect: [.1, .1, .3, .12]}]}];
  const mark = selectionGeometry([.1, .5, .3, .5], lines, 1000, 'underline');
  assert.equal(mark.length, 1); assert.equal(validRect(mark[0]), true);
  assert.ok(Math.abs(mark[0][1] - .5) < .002); assert.ok(mark[0][3] - mark[0][1] < .004);
  for (const edge of [0, 1]) assert.equal(validRect(selectionGeometry([.1, edge, .3, edge], [], 1000, 'highlight')[0]), true);
});

test('multi-line selection keeps the existing rectangular selection geometry', () => {
  const lines = [
    {words: [{rect: [.1, .1, .3, .12]}]},
    {words: [{rect: [.1, .14, .25, .16]}]},
  ];
  assert.deepEqual(selectionGeometry([.05, .08, .35, .18], lines, 1000, 'underline'),
    [[.1, .1, .3, .12], [.1, .14, .25, .16]]);
});

test('horizontal pointer-up saves the same geometry as its draft and ignores tiny clicks', () => {
  const controller = Object.create(Controller.prototype), context = {page: 1};
  const bounds = {left: 0, top: 0, width: 500, height: 1000};
  const pane = {version: 'original', layer: {getBoundingClientRect: () => bounds}};
  const saved = [];
  controller.session = {context, generation: 1, states: {original: {text: {page: 1, lines: [{words: [{rect: [.1, .1, .3, .12]}]}]}}},
    save: (...args) => saved.push(args)};
  controller.cancelDrag = () => { controller.drag = null; };
  const drag = {pane, pointerId: 1, start: [.09, .123], end: [.31, .123], kind: 'underline', color: '#f4c95d', context, generation: 1};
  const draft = controller.draftRects(drag, normalizeRect(drag.start, drag.end));
  controller.drag = drag;
  controller.pointerUp({pointerId: 1, clientX: .31 * 500, clientY: 123, preventDefault() {}}, pane);
  assert.equal(saved.length, 1); assert.deepEqual(saved[0][1], draft);
  controller.drag = {...drag, start: [.309, .123]};
  controller.pointerUp({pointerId: 1, clientX: .31 * 500, clientY: 123, preventDefault() {}}, pane);
  assert.equal(saved.length, 1);
});

test('annotation keyboard shortcuts do not intercept modal dialogs or hidden readers', () => {
  const doc = {querySelector: () => null}, toolbar = {hidden: false, closest: () => null}, target = {tagName: 'BUTTON'};
  assert.equal(shortcutsAllowed(doc, toolbar, true, target), true);
  assert.equal(shortcutsAllowed(doc, toolbar, false, target), false);
  assert.equal(shortcutsAllowed(doc, {...toolbar, hidden: true}, true, target), false);
  assert.equal(shortcutsAllowed(doc, {...toolbar, closest: () => ({hidden: true})}, true, target), false);
  assert.equal(shortcutsAllowed({...doc, querySelector: () => ({role: 'dialog'})}, toolbar, true, target), false);
  assert.equal(shortcutsAllowed(doc, toolbar, true, {tagName: 'TEXTAREA'}), false);
  assert.equal(shortcutsAllowed(doc, toolbar, true, {tagName: 'DIV', isContentEditable: true}), false);
});

test('original and translated annotations use separate revision and persistence paths', async () => {
  const posted = [];
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') posted.push(JSON.parse(options.body));
  }));
  await session.setPage('paper-a', 2, true, 'view-1');
  await session.save('original', [rect], 'highlight', '#f4c95d');
  await session.save('translated', [rect], 'underline', '#86c8a1');
  assert.deepEqual(posted.map(body => [body.version, body.page, body.document_revision]), [
    ['original', 2, 'revision-original'], ['translated', 2, 'revision-translated']]);
  assert.equal(session.states.original.annotations[0].kind, 'highlight');
  assert.equal(session.states.translated.annotations[0].kind, 'underline');
  assert.equal(session.pending, 0);
});

test('out-of-order document loads never paint annotations on another paper', async () => {
  const oldLoad = deferred();
  const session = new AnnotationSession(fixtureTransport(url => url.startsWith('/api/documents/old/annotations?') ? oldLoad.promise : undefined));
  const old = session.setPage('old', 1, false);
  await session.setPage('new', 1, false);
  oldLoad.resolve({document_revision: 'old-revision', annotations: [{id: 'old-mark', page: 1, rect}]});
  await old;
  assert.equal(session.context.jobId, 'new');
  assert.equal(session.states.original.revision, 'revision-original');
  assert.deepEqual(session.states.original.annotations, []);
});

test('switching pages during text extraction discards the old page positions', async () => {
  const page1 = deferred();
  const session = new AnnotationSession(fixtureTransport(url => url.includes('/text?') && url.endsWith('page=1') ? page1.promise : undefined));
  const first = session.setPage('paper-a', 1, false);
  await tick();
  await session.setPage('paper-a', 2, false);
  page1.resolve({document_revision: 'revision-original', page: 1, lines: [{rect}]}); await first;
  assert.equal(session.states.original.text.page, 2);
  assert.deepEqual(session.states.original.text.lines, []);
});

test('save finishes on its original page without adding the annotation to another document', async () => {
  const saving = deferred(); let body;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') { body = JSON.parse(options.body); return saving.promise; }
  }));
  await session.setPage('paper-a', 3, false);
  const save = session.save('original', [rect], 'highlight', '#f4c95d');
  assert.equal(session.isBusy(), true);
  await session.setPage('paper-b', 1, false);
  assert.equal(session.isBusy(), true);
  assert.equal(session.pending, 0);
  saving.resolve(serverRecord(body)); await save;
  assert.equal(body.page, 3);
  assert.equal(session.context.jobId, 'paper-b');
  assert.deepEqual(session.states.original.annotations, []);
  assert.equal(session.pending, 0);
  assert.equal(session.isBusy(), false);
});

test('failed save stays visible and retries use the same id to avoid duplicates', async () => {
  let fail = true; const ids = [];
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); ids.push(body.id);
      if (fail) return Promise.reject(new Error('连接中断'));
    }
  }));
  await session.setPage('paper-a', 1, false);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  const annotation = session.states.original.annotations[0];
  assert.equal(annotation.failed, true); assert.match(session.message, /未保存/);
  fail = false;
  await session.save('original', annotation.rects, annotation.kind, annotation.color, annotation);
  assert.equal(ids[0], ids[1]); assert.equal(session.states.original.annotations.length, 1);
  assert.equal(session.states.original.annotations[0].failed, undefined);
  assert.equal(session.pending, 0);
});

test('changed PDF revision blocks stale annotations and further writes', async () => {
  let writes = 0;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') { writes++; return Promise.reject(Object.assign(new Error('PDF changed'), {status: 409})); }
  }));
  await session.setPage('paper-a', 1, false);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  assert.equal(session.states.original.stale, true);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  assert.equal(writes, 1);
});

test('text extraction revision mismatch cannot snap to an older PDF', async () => {
  const session = new AnnotationSession(fixtureTransport(url => url.includes('/text?') ?
    {document_revision: 'updated-pdf', page: 1, lines: [{words: [{rect}]}]} : undefined));
  await session.setPage('paper-a', 1, false);
  assert.equal(session.states.original.stale, true);
  assert.equal(session.states.original.text, null);
});

test('deleting includes PDF revision and removes only the chosen language annotation', async () => {
  const deletes = [];
  const session = new AnnotationSession(fixtureTransport((url, options) => { if (options.method === 'DELETE') deletes.push(url); }));
  await session.setPage('paper-a', 1, true);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  await session.save('translated', [rect], 'underline', '#f4c95d');
  await session.remove('original', session.states.original.annotations[0].id);
  assert.match(deletes[0], /version=original&document_revision=revision-original$/);
  assert.equal(session.states.original.annotations.length, 0);
  assert.equal(session.states.translated.annotations.length, 1);
});

test('a timed-out save is idempotently confirmed then deleted, preventing resurrected annotations', async () => {
  const persisted = new Map(), methods = [];
  let first = true;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); methods.push('POST');
      if (!persisted.has(body.id)) persisted.set(body.id, serverRecord(body));
      if (first) { first = false; return Promise.reject(new Error('Response lost after commit')); }
      return persisted.get(body.id);
    }
    if (options.method === 'DELETE') {
      methods.push('DELETE'); const id = new URL(url, 'http://localhost').pathname.split('/').at(-1);
      persisted.delete(id); return {deleted: id};
    }
  }));
  await session.setPage('paper-a', 1, false);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  assert.equal(persisted.size, 1);
  const failed = session.states.original.annotations[0]; assert.equal(failed.failed, true);
  await session.remove('original', failed.id);
  assert.deepEqual(methods, ['POST', 'POST', 'DELETE']);
  assert.equal(persisted.size, 0); assert.equal(session.states.original.annotations.length, 0);
  assert.equal(session.isBusy(), false);
});

test('deleting an already removed annotation treats 404 as successful', async () => {
  const session = new AnnotationSession(fixtureTransport((url, options) => options.method === 'DELETE' ?
    Promise.reject(Object.assign(new Error('Missing'), {status: 404})) : undefined));
  await session.setPage('paper-a', 1, false);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  await session.remove('original', session.states.original.annotations[0].id);
  assert.equal(session.states.original.annotations.length, 0); assert.equal(session.messageKind, 'saved');
});

test('global busy state remains true while removing a failed annotation after changing papers', async () => {
  const confirmation = deferred(), deletion = deferred(); let calls = 0, body;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') {
      body = JSON.parse(options.body); calls++;
      return calls === 1 ? Promise.reject(new Error('Timeout')) : confirmation.promise;
    }
    if (options.method === 'DELETE') return deletion.promise;
  }));
  await session.setPage('paper-a', 1, false);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  const removing = session.remove('original', session.states.original.annotations[0].id);
  await session.setPage('paper-b', 1, false);
  assert.equal(session.isBusy(), true); assert.equal(session.pending, 0);
  confirmation.resolve(serverRecord(body)); await tick();
  assert.equal(session.isBusy(), true);
  deletion.resolve({deleted: body.id}); await removing;
  assert.equal(session.isBusy(), false); assert.deepEqual(session.states.original.annotations, []);
});

test('a failed cleanup confirmation retains the unsaved annotation for retry', async () => {
  let deletes = 0;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') return Promise.reject(new Error('Offline'));
    if (options.method === 'DELETE') deletes++;
  }));
  await session.setPage('paper-a', 1, false);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  const id = session.states.original.annotations[0].id;
  await session.remove('original', id);
  assert.equal(deletes, 0); assert.equal(session.states.original.annotations[0].failed, true);
  assert.equal(session.states.original.annotations[0].pending, false); assert.equal(session.isBusy(), false);
  assert.match(session.message, /删除未完成/);
});

test('a tombstoned late POST clears its temporary overlay and reports deletion', async () => {
  const session = new AnnotationSession(fixtureTransport((url, options) => options.method === 'POST' ?
    Promise.reject(Object.assign(new Error('Deleted'), {status: 410})) : undefined));
  await session.setPage('paper-a', 1, false);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  assert.deepEqual(session.states.original.annotations, []);
  assert.equal(session.message, '批注已删除'); assert.equal(session.messageKind, 'saved');
  assert.equal(session.isBusy(), false);
});

test('a tombstone during failed-save cleanup is treated as already deleted', async () => {
  let posts = 0, deletes = 0;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') {
      posts++;
      return Promise.reject(posts === 1 ? new Error('Timeout') : Object.assign(new Error('Deleted'), {status: 410}));
    }
    if (options.method === 'DELETE') deletes++;
  }));
  await session.setPage('paper-a', 1, false);
  await session.save('original', [rect], 'highlight', '#f4c95d');
  await session.remove('original', session.states.original.annotations[0].id);
  assert.equal(posts, 2); assert.equal(deletes, 0);
  assert.deepEqual(session.states.original.annotations, []);
  assert.equal(session.message, '批注已删除'); assert.equal(session.isBusy(), false);
});

test('an unchanged page refresh preserves the drag context and does not refetch', async () => {
  let requests = 0;
  const session = new AnnotationSession(fixtureTransport(() => { requests++; }));
  await session.setPage('paper-a', 1, false, 'v1');
  const context = session.context, count = requests;
  await session.setPage('paper-a', 1, false, 'v1');
  assert.equal(session.context, context); assert.equal(requests, count);
});

test('image loading and stale preview URLs keep annotation overlays hidden', () => {
  const controller = Object.create(Controller.prototype);
  controller.session = {context: {jobId: 'paper-a', page: 2}};
  controller.document = {baseURI: 'http://127.0.0.1:8765/'};
  const pane = {version: 'original', image: {hidden: false, complete: true, naturalWidth: 1600,
    classList: {contains: () => false}, src: 'http://127.0.0.1:8765/api/documents/paper-a/page/2?version=original&width=1600'}};
  assert.equal(controller.imageMatches(pane), true);
  pane.image.src = 'http://127.0.0.1:8765/api/documents/paper-a/page/1?version=original';
  assert.equal(controller.imageMatches(pane), false);
  pane.image.src = 'http://127.0.0.1:8765/api/documents/paper-a/page/2?version=original';
  pane.image.classList.contains = () => true;
  assert.equal(controller.imageMatches(pane), false);
});

test('annotations cannot be submitted for a missing translation or invalid geometry', async () => {
  const calls = [];
  const session = new AnnotationSession(fixtureTransport((url, options) => { calls.push([url, options.method]); }));
  await session.setPage('paper-a', 1, false);
  await session.save('translated', [rect], 'highlight', '#f4c95d');
  await session.save('original', [[-.1, 0, .5, .2]], 'highlight', '#f4c95d');
  await session.save('original', [], 'underline', '#f4c95d');
  assert.equal(calls.some(([url, method]) => url.includes('translated') || method === 'POST'), false);
});

test('saving while turning the page preserves the original annotation page', async () => {
  const saving = deferred(); let body;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') { body = JSON.parse(options.body); return saving.promise; }
  }));
  await session.setPage('paper-a', 1, false);
  const save = session.save('original', [rect], 'highlight', '#f4c95d');
  await session.setPage('paper-a', 2, false);
  saving.resolve(serverRecord(body)); await save;
  assert.equal(session.context.page, 2);
  assert.equal(session.states.original.annotations[0].page, 1);
  assert.equal(session.pending, 0);
});

test('reopening a paper before its previous save finishes receives that completed annotation', async () => {
  const saving = deferred(); let body;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') { body = JSON.parse(options.body); return saving.promise; }
  }));
  await session.setPage('paper-a', 1, false);
  const save = session.save('original', [rect], 'highlight', '#ffe135');
  await session.setPage('paper-b', 1, false); await session.setPage('paper-a', 1, false);
  saving.resolve(serverRecord(body)); await save;
  assert.equal(session.states.original.annotations.length, 1);
  assert.equal(session.states.original.annotations[0].id, body.id);
  assert.equal(session.isBusy(), false);
});

test('a stale list response cannot overwrite a completed save on the reopened paper', async () => {
  const saving = deferred(), listing = deferred(); let body, paperLoads = 0;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') { body = JSON.parse(options.body); return saving.promise; }
    if (url === '/api/documents/paper-a/annotations?version=original' && ++paperLoads === 2) return listing.promise;
  }));
  await session.setPage('paper-a', 1, false);
  const save = session.save('original', [rect], 'highlight', '#ffe135');
  await session.setPage('paper-b', 1, false); const reopening = session.setPage('paper-a', 1, false);
  saving.resolve(serverRecord(body)); await save;
  listing.resolve({document_revision: 'revision-original', annotations: []}); await reopening;
  assert.equal(session.states.original.annotations[0].id, body.id);
  // A later deliberate reload is authoritative, e.g. another reader removed it.
  await session.loadVersion('original');
  assert.equal(session.states.original.annotations.length, 0);
});

test('a failed save remains retryable after quickly returning to the same paper', async () => {
  const saving = deferred();
  const session = new AnnotationSession(fixtureTransport((url, options) => options.method === 'POST' ? saving.promise : undefined));
  await session.setPage('paper-a', 1, false);
  const save = session.save('original', [rect], 'highlight', '#ffe135');
  await session.setPage('paper-b', 1, false); await session.setPage('paper-a', 1, false);
  saving.reject(new Error('Offline')); await save;
  assert.equal(session.states.original.annotations[0].failed, true);
  assert.match(session.message, /未保存/);
});

test('delete completion applies to the reopened paper without replacing other marks', async () => {
  const deleting = deferred(); let saved;
  const session = new AnnotationSession(fixtureTransport((url, options) => {
    if (options.method === 'POST') { saved = serverRecord(JSON.parse(options.body)); return saved; }
    if (options.method === 'DELETE') return deleting.promise;
    if (url === '/api/documents/paper-a/annotations?version=original' && saved) return {
      document_revision: 'revision-original', annotations: [saved, {...saved, id: 'other-mark'}]};
  }));
  await session.setPage('paper-a', 1, false); await session.save('original', [rect], 'highlight', '#ffe135');
  const removing = session.remove('original', saved.id);
  await session.setPage('paper-b', 1, false); await session.setPage('paper-a', 1, false);
  deleting.resolve({deleted: saved.id}); await removing;
  assert.deepEqual(session.states.original.annotations.map(mark => mark.id), ['other-mark']);
});
