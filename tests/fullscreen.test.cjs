const test = require('node:test');
const assert = require('node:assert/strict');
const {ReaderFullscreen} = require('../paperbridge/static/fullscreen.js');

function environment() {
  const events = {}, classes = new Set(), attrs = {}, messages = [], changes = [];
  const doc = {
    addEventListener(name, callback) { events[name] = callback; },
    body: {classList: {toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }}},
    defaultView: {scrollY: 345, scrollTo(x, y) { this.scrollY = y; }},
    activeElement: {focus() { this.restored = true; }},
    fullscreenEnabled: true, fullscreenElement: null,
    documentElement: {},
    async exitFullscreen() { this.fullscreenElement = null; events.fullscreenchange(); }
  };
  doc.documentElement.requestFullscreen = async () => { doc.fullscreenElement = doc.documentElement; events.fullscreenchange(); };
  const button = {addEventListener(){}, setAttribute(key, value){attrs[key] = value;}};
  const controller = new ReaderFullscreen({document: doc, button, onChange: value => changes.push(value), onMessage: value => messages.push(value)});
  return {doc, events, classes, attrs, messages, changes, controller, button};
}
test('fullscreen enters once and browser Escape restores layout, scroll and focus', async () => {
  const e = environment();
  await e.controller.set(true);
  assert.equal(e.doc.fullscreenElement, e.doc.documentElement);
  assert.deepEqual(e.changes, [true]);
  assert.equal(e.attrs['aria-pressed'], 'true');
  assert.equal(e.doc.defaultView.scrollY, 0);
  await e.doc.exitFullscreen();
  assert.equal(e.controller.active, false);
  assert.equal(e.classes.has('reader-focus'), false);
  assert.equal(e.doc.defaultView.scrollY, 345);
  assert.equal(e.doc.activeElement.restored, true);
  assert.deepEqual(e.changes, [true, false]);
});
test('explicit exit works and repeated requests do not reset reading state', async () => {
  const e = environment();
  await e.controller.set(true); await e.controller.set(true);
  await e.controller.toggle();
  assert.equal(e.doc.fullscreenElement, null);
  assert.deepEqual(e.changes, [true, false]);
});
test('embedded browser rejection leaves an escapable reading mode', async () => {
  const e = environment();
  e.doc.documentElement.requestFullscreen = async () => { throw new Error('unsupported'); };
  await e.controller.set(true);
  assert.equal(e.controller.active, true);
  assert.equal(e.messages.length, 1);
  await e.controller.set(false);
  assert.equal(e.classes.size, 0);
  assert.equal(e.controller.pending, false);
});
test('double click while entering cannot issue overlapping native requests', async () => {
  const e = environment();
  let finish, calls = 0;
  e.doc.documentElement.requestFullscreen = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const entering = e.controller.set(true);
  await e.controller.toggle();
  assert.equal(calls, 1);
  finish(); await entering;
  assert.equal(e.controller.pending, false);
  assert.equal(e.controller.active, false);
  assert.equal(e.controller.desired, false);
  assert.deepEqual(e.changes, [true, false]);
});

function deferredEntry(e) {
  let resolve, reject;
  const wait = new Promise((yes, no) => { resolve = yes; reject = no; });
  let entries = 0;
  e.doc.documentElement.requestFullscreen = async () => {
    entries++; await wait;
    e.doc.fullscreenElement = e.doc.documentElement;
    e.events.fullscreenchange();
  };
  return {resolve, reject, entries: () => entries};
}

test('new-paper exit requested during native entry is applied after entry settles', async () => {
  const e = environment(), entry = deferredEntry(e);
  let exits = 0;
  const nativeExit = e.doc.exitFullscreen.bind(e.doc);
  e.doc.exitFullscreen = async () => { exits++; await nativeExit(); };
  const entering = e.controller.set(true);
  await e.controller.set(false);
  assert.equal(entry.entries(), 1);
  entry.resolve(); await entering;
  assert.equal(exits, 1);
  assert.equal(e.doc.fullscreenElement, null);
  assert.equal(e.controller.active, false);
  assert.equal(e.controller.pending, false);
  assert.equal(e.classes.has('reader-focus'), false);
  assert.equal(e.doc.defaultView.scrollY, 345);
});

test('queued exit also leaves fallback reading mode after the browser rejects entry', async () => {
  const e = environment(), entry = deferredEntry(e);
  const entering = e.controller.set(true);
  await e.controller.set(false);
  entry.reject(new Error('unsupported')); await entering;
  assert.equal(e.controller.active, false);
  assert.equal(e.controller.desired, false);
  assert.equal(e.controller.pending, false);
  assert.equal(e.classes.size, 0);
  assert.deepEqual(e.messages, [], 'do not announce fallback entry after the user has left it');
});

test('a third toggle during entry preserves the final enter intent without duplicate requests', async () => {
  const e = environment(), entry = deferredEntry(e);
  let exits = 0;
  e.doc.exitFullscreen = async () => { exits++; };
  const entering = e.controller.toggle();
  await e.controller.toggle();
  await e.controller.toggle();
  entry.resolve(); await entering;
  assert.equal(entry.entries(), 1);
  assert.equal(exits, 0);
  assert.equal(e.controller.active, true);
  assert.equal(e.controller.desired, true);
  assert.equal(e.doc.fullscreenElement, e.doc.documentElement);
  assert.deepEqual(e.changes, [true]);
});

test('re-entry queued during exit survives the native exit notification', async () => {
  const e = environment();
  await e.controller.set(true);
  let finish;
  e.doc.exitFullscreen = () => new Promise(resolve => { finish = () => {
    e.doc.fullscreenElement = null; e.events.fullscreenchange(); resolve();
  }; });
  const exiting = e.controller.set(false);
  await e.controller.toggle();
  finish(); await exiting;
  assert.equal(e.controller.active, true);
  assert.equal(e.controller.desired, true);
  assert.equal(e.doc.fullscreenElement, e.doc.documentElement);
  assert.deepEqual(e.changes, [true, false, true]);
});

test('failed native exit stops retrying and the next toggle can retry once', async () => {
  const e = environment();
  await e.controller.set(true);
  const exit = e.doc.exitFullscreen.bind(e.doc);
  let calls = 0;
  e.doc.exitFullscreen = async () => { calls++; throw new Error('busy'); };
  await e.controller.set(false);
  assert.equal(calls, 1);
  assert.equal(e.controller.active, true);
  assert.equal(e.controller.desired, true);
  assert.equal(e.controller.pending, false);
  e.doc.exitFullscreen = exit;
  await e.controller.toggle();
  assert.equal(e.controller.active, false);
});
