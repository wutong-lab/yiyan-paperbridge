/* Persistent PDF annotations use normalized display coordinates, independent of preview DPI. */
(function (root) {
  'use strict';
  const VERSIONS = ['original', 'translated'];
  const COLORS = [{value: '#ffe135', label: '明黄色'}, {value: '#6edb9a', label: '薄荷绿'}, {value: '#80b7ff', label: '天空蓝'}];
  const COLOR_STORAGE_KEY = 'paperbridge.annotationColor';
  function normalizeColor(value) {
    return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value.toLowerCase() : null;
  }
  function browserStorage() { try { return root.localStorage; } catch { return null; } }
  function readColor(storage) {
    try { return normalizeColor(storage?.getItem(COLOR_STORAGE_KEY)) || COLORS[0].value; }
    catch { return COLORS[0].value; }
  }
  function rememberColor(storage, color) {
    const valid = normalizeColor(color);
    if (!valid || !storage) return false;
    try { storage.setItem(COLOR_STORAGE_KEY, valid); return true; } catch { return false; }
  }
  function highlightOpacity(color) {
    const valid = normalizeColor(color) || COLORS[0].value;
    const rgb = [1, 3, 5].map(index => parseInt(valid.slice(index, index + 2), 16) / 255);
    const luminance = alpha => rgb.reduce((sum, channel, index) => {
      const value = 1 - alpha + alpha * channel;
      return sum + [0.2126, 0.7152, 0.0722][index] * (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    }, 0);
    // Match annotations.py: vivid light colors, with darker choices capped to
    // keep the composited paper luminance >= .30 (7:1 contrast against black).
    let low = 0, high = 0.68;
    if (luminance(high) >= 0.30) return high;
    for (let i = 0; i < 24; i++) { const mid = (low + high) / 2; if (luminance(mid) >= 0.30) low = mid; else high = mid; }
    return Math.floor(low * 1000) / 1000;
  }
  const clamp = value => Math.max(0, Math.min(1, value));
  function normalizeRect(start, end) {
    return [clamp(Math.min(start[0], end[0])), clamp(Math.min(start[1], end[1])),
      clamp(Math.max(start[0], end[0])), clamp(Math.max(start[1], end[1]))];
  }
  function validRect(rect) {
    return Array.isArray(rect) && rect.length === 4 && rect.every(Number.isFinite) &&
      rect[0] >= 0 && rect[1] >= 0 && rect[2] <= 1 && rect[3] <= 1 && rect[2] > rect[0] && rect[3] > rect[1];
  }
  function unionRects(rects) {
    return [Math.min(...rects.map(r => r[0])), Math.min(...rects.map(r => r[1])),
      Math.max(...rects.map(r => r[2])), Math.max(...rects.map(r => r[3]))];
  }
  function pointOnPage(event, bounds) {
    return [clamp((event.clientX - bounds.left) / Math.max(1, bounds.width)),
      clamp((event.clientY - bounds.top) / Math.max(1, bounds.height))];
  }
  function selectedTextRects(rect, lines) {
    // Select visible words whose centre lies inside the dragged area. Keeping line
    // groups separate prevents two-column text and inter-line whitespace being painted.
    const selected = [];
    for (const line of lines || []) {
      const words = (line.words || []).filter(word => {
        const box = word.rect;
        if (!validRect(box)) return false;
        const x = (box[0] + box[2]) / 2, y = (box[1] + box[3]) / 2;
        return x >= rect[0] && x <= rect[2] && y >= rect[1] && y <= rect[3];
      }).sort((a, b) => a.rect[0] - b.rect[0]);
      let group = [];
      for (const word of words) {
        if (group.length) {
          const previous = group[group.length - 1];
          const lineHeight = Math.max(previous[3] - previous[1], word.rect[3] - word.rect[1]);
          if (word.rect[0] - previous[2] > lineHeight * 2) {
            selected.push(unionRects(group)); group = [];
          }
        }
        group.push(word.rect);
      }
      if (group.length) selected.push(unionRects(group));
    }
    return selected;
  }
  function selectionGeometry(rect, lines, displayHeight, kind = 'highlight') {
    const height = Math.max(1, displayHeight);
    if (rect[2] <= rect[0]) return [];
    if ((rect[3] - rect[1]) * height < 6) {
      // A natural underline gesture is horizontal, including zero-height drags.
      // Snap to the nearest visible line within six screen pixels, keeping the
      // same tolerance when either pane is independently zoomed.
      const y = (rect[1] + rect[3]) / 2, tolerance = 6 / height;
      const candidates = [];
      for (const line of lines || []) {
        const words = (line.words || []).filter(word => validRect(word.rect) &&
          (word.rect[0] + word.rect[2]) / 2 >= rect[0] && (word.rect[0] + word.rect[2]) / 2 <= rect[2]);
        if (!words.length) continue;
        const bounds = unionRects(words.map(word => word.rect));
        const outside = Math.max(0, bounds[1] - y, y - bounds[3]);
        if (outside <= tolerance) candidates.push({line, bounds, outside, distance: Math.abs(y - (bounds[1] + bounds[3]) / 2)});
      }
      candidates.sort((a, b) => a.outside - b.outside || a.distance - b.distance);
      if (candidates.length) {
        const closest = candidates[0];
        const snapped = selectedTextRects([rect[0], closest.bounds[1], rect[2], closest.bounds[3]], [closest.line]);
        if (snapped.length) return snapped;
      }
      // Image-only/scanned pages still support a visible line with valid PDF
      // annotation geometry, even though there is no selectable text layer.
      const minimum = (kind === 'underline' ? 3 : 6) / height;
      const top = Math.max(0, Math.min(1 - minimum, y - minimum / 2));
      return [[rect[0], top, rect[2], Math.min(1, top + minimum)]];
    }
    const snapped = selectedTextRects(rect, lines);
    return snapped.length ? snapped : [rect];
  }
  function apiURL(jobId, suffix = '') { return `/api/documents/${encodeURIComponent(jobId)}/annotations${suffix}`; }
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await root.fetch(path, {...options, signal: controller.signal});
      if (!response.ok) {
        let detail;
        try { detail = (await response.json()).detail; } catch { /* Use the localized fallback. */ }
        const error = new Error(typeof detail === 'string' ? detail : '批注操作未完成，请重试');
        error.status = response.status; throw error;
      }
      return response.status === 204 ? null : await response.json();
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('保存或读取批注超时，请检查本机服务后重试');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  const freshState = () => ({revision: null, annotations: [], mutations: new Map(), mutationSequence: 0, text: null, loading: false, textLoading: false,
    error: '', stale: false, loadRequest: 0, textRequest: 0});
  class AnnotationSession {
    constructor(transport = request, changed = () => {}) {
      this.transport = transport; this.changed = changed; this.generation = 0;
      this.context = null; this.states = {original: freshState(), translated: freshState()};
      this.message = ''; this.messageKind = ''; this.pending = 0; this.networkPending = 0;
    }
    enabled(version) { return !!this.context && (version === 'original' || this.context.hasOutput); }
    isBusy() { return this.networkPending > 0; }
    current(generation, state, version) { return generation === this.generation && this.states[version] === state; }
    completionState(jobId, version, revision) {
      const state = this.states[version];
      return this.context?.jobId === jobId && (!state.revision || state.revision === revision) ? state : null;
    }
    applyMutation(state, revision, id, annotation) {
      // A paper can be reopened while its previous POST/DELETE is still in
      // flight. Keep individual completed writes over a stale GET snapshot.
      state.mutations.set(id, {revision, annotation, sequence: ++state.mutationSequence});
      if (state.revision === revision) {
        state.annotations = state.annotations.filter(item => item.id !== id);
        if (annotation) state.annotations.push(annotation);
      }
    }
    setPage(jobId, page, hasOutput, revision = '') {
      const previous = this.context;
      const reset = !previous || previous.jobId !== jobId || previous.revision !== revision || previous.hasOutput !== !!hasOutput;
      const pageChanged = !previous || previous.page !== page;
      if (!reset && !pageChanged && jobId) return Promise.resolve();
      this.context = jobId ? {jobId, page, hasOutput: !!hasOutput, revision} : null;
      if (reset || !jobId) {
        this.generation++; this.states = {original: freshState(), translated: freshState()};
        this.pending = 0; this.message = ''; this.messageKind = '';
        this.changed();
        return Promise.all(VERSIONS.filter(v => this.enabled(v)).map(v => this.loadVersion(v)));
      }
      if (pageChanged) {
        this.message = ''; this.messageKind = '';
        this.changed();
        return Promise.all(VERSIONS.filter(v => this.enabled(v) && this.states[v].revision).map(v => this.loadText(v)));
      }
      return Promise.resolve();
    }
    async loadVersion(version) {
      if (!this.enabled(version)) return;
      const state = this.states[version], generation = this.generation, jobId = this.context.jobId;
      const sequence = ++state.loadRequest, mutationSequence = state.mutationSequence;
      state.loading = true; state.error = ''; state.stale = false; this.changed();
      try {
        const data = await this.transport(`${apiURL(jobId)}?version=${version}`);
        if (!this.current(generation, state, version) || sequence !== state.loadRequest) return;
        state.revision = data.document_revision;
        state.annotations = data.annotations || [];
        for (const [id, mutation] of state.mutations) {
          if (mutation.revision !== state.revision) continue;
          if (mutation.sequence <= mutationSequence && !mutation.annotation?.failed) {
            state.mutations.delete(id); continue;
          }
          state.annotations = state.annotations.filter(item => item.id !== id);
          if (mutation.annotation) state.annotations.push(mutation.annotation);
        }
        state.staleCount = data.stale_count || 0;
        state.loading = false; this.changed();
        await this.loadText(version);
      } catch (error) {
        if (!this.current(generation, state, version) || sequence !== state.loadRequest) return;
        state.error = error.message; state.stale = error.status === 409; state.loading = false;
        this.changed();
      }
    }
    async loadText(version) {
      if (!this.enabled(version)) return;
      const state = this.states[version], generation = this.generation;
      const {jobId, page} = this.context, sequence = ++state.textRequest;
      state.text = null; state.textLoading = true; state.textError = ''; this.changed();
      try {
        const data = await this.transport(`${apiURL(jobId, '/text')}?version=${version}&page=${page}`);
        if (!this.current(generation, state, version) || sequence !== state.textRequest || this.context.page !== page) return;
        if (data.document_revision !== state.revision) {
          state.stale = true; state.error = 'PDF 已更新，请重新加载批注';
        } else { state.text = {page, lines: data.lines || []}; }
      } catch (error) {
        if (!this.current(generation, state, version) || sequence !== state.textRequest || this.context.page !== page) return;
        state.textError = '文字位置读取失败，可以框选区域批注';
        if (error.status === 409) { state.stale = true; state.error = 'PDF 已更新，请重新加载批注'; }
      } finally {
        if (this.current(generation, state, version) && sequence === state.textRequest) { state.textLoading = false; this.changed(); }
      }
    }
    async save(version, rects, kind, color, retry = null) {
      const state = this.states[version];
      if (!this.enabled(version) || !state.revision || state.stale || !rects.length || rects.length > 100 || !rects.every(validRect)) return;
      const generation = this.generation, {jobId, page} = this.context;
      const id = retry?.id || root.crypto.randomUUID();
      const annotation = {id, page, kind, color, rect: unionRects(rects), rects, pending: true, failed: false};
      const body = {id, version, document_revision: state.revision, page, kind, color, rect: annotation.rect, rects};
      state.annotations = [...state.annotations.filter(item => item.id !== id), annotation];
      this.pending++; this.networkPending++; this.message = '正在保存批注…'; this.messageKind = ''; this.changed();
      try {
        const result = await this.transport(apiURL(jobId), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const target = this.completionState(jobId, version, body.document_revision);
        if (!target) return;
        this.applyMutation(target, body.document_revision, id, result);
        this.message = '批注已保存到本机'; this.messageKind = 'saved';
      } catch (error) {
        const target = this.completionState(jobId, version, body.document_revision);
        if (!target) return;
        if (error.status === 410) {
          this.applyMutation(target, body.document_revision, id, null);
          this.message = '批注已删除'; this.messageKind = 'saved';
        } else {
          annotation.pending = false; annotation.failed = true;
          this.applyMutation(target, body.document_revision, id, annotation);
          this.message = `批注未保存：${error.message}`; this.messageKind = 'error';
          if (error.status === 409) { target.stale = true; target.error = 'PDF 已更新，请重新加载批注'; }
        }
      } finally {
        this.networkPending--;
        if (this.current(generation, state, version)) this.pending--;
        this.changed();
      }
      return annotation.id;
    }
    async remove(version, id) {
      const state = this.states[version], annotation = state.annotations.find(item => item.id === id);
      if (!this.enabled(version) || !annotation || annotation.pending || state.stale) return;
      const generation = this.generation, jobId = this.context.jobId, revision = state.revision;
      annotation.pending = true; this.pending++; this.networkPending++; this.message = '正在删除批注…'; this.messageKind = ''; this.changed();
      try {
        let alreadyRemoved = false;
        // A failed POST may already have committed on the server. Confirm the
        // same idempotent operation before deleting, so a timeout cannot leave
        // an invisible mark that reappears when the paper is reopened.
        if (annotation.failed) {
          const body = {id, version, document_revision: state.revision, page: annotation.page,
            kind: annotation.kind, color: annotation.color, rect: annotation.rect, rects: annotation.rects || [annotation.rect]};
          try {
            await this.transport(apiURL(jobId), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
          } catch (error) { if (error.status === 410) alreadyRemoved = true; else throw error; }
        }
        if (!alreadyRemoved) try {
          await this.transport(`${apiURL(jobId, `/${encodeURIComponent(id)}`)}?version=${version}&document_revision=${encodeURIComponent(state.revision)}`, {method: 'DELETE'});
        } catch (error) { if (error.status !== 404) throw error; }
        const target = this.completionState(jobId, version, revision);
        if (!target) return;
        this.applyMutation(target, revision, id, null);
        this.message = '批注已删除'; this.messageKind = 'saved';
      } catch (error) {
        const target = this.completionState(jobId, version, revision);
        if (!target) return;
        annotation.pending = false; this.message = `删除未完成：${error.message}`; this.messageKind = 'error';
        this.applyMutation(target, revision, id, annotation);
        if (error.status === 409) { target.stale = true; target.error = 'PDF 已更新，请重新加载批注'; }
      } finally {
        this.networkPending--;
        if (this.current(generation, state, version)) this.pending--;
        this.changed();
      }
    }
  }
  function element(document, tag, className, text) {
    const node = document.createElement(tag); if (className) node.className = className;
    if (text) node.textContent = text; return node;
  }
  function shortcutsAllowed(document, toolbar, visible, target) {
    return !!visible && !toolbar.hidden && !toolbar.closest('[hidden], [inert], [aria-hidden="true"]') &&
      !document.querySelector('.modal-backdrop:not([hidden]), dialog[open]') &&
      !/INPUT|TEXTAREA|SELECT/.test(target?.tagName || '') && !target?.isContentEditable;
  }
  class Controller {
    constructor({toolbar, panes, onMessage = () => {}, onRefresh = () => {}, transport = request, storage = browserStorage()}) {
      this.toolbar = toolbar; this.document = toolbar.ownerDocument; this.panes = panes;
      this.onMessage = onMessage; this.onRefresh = onRefresh; this.tool = 'select'; this.storage = storage; this.color = readColor(storage);
      this.selection = null; this.drag = null; this.visible = true;
      this.session = new AnnotationSession(transport, () => this.render());
      this.buildToolbar();
      this.panes.forEach(pane => this.buildLayer(pane));
      this.escapeHandler = event => {
        if (!shortcutsAllowed(this.document, this.toolbar, this.visible, event.target)) return;
        if (event.key === 'Escape' && (this.drag || this.selection || this.tool !== 'select')) {
          event.preventDefault(); event.stopImmediatePropagation(); this.cancelSelection(); this.setTool('select');
        } else if ((event.key === 'Delete' || event.key === 'Backspace') && this.selection) {
          event.preventDefault(); this.deleteSelected();
        }
      };
      this.document.addEventListener('keydown', this.escapeHandler, true);
      this.render();
    }
    buildToolbar() {
      const doc = this.document;
      this.toolbar.classList.add('annotation-toolbar'); this.toolbar.setAttribute('aria-label', '阅读批注');
      const tools = element(doc, 'div', 'annotation-tools'); tools.setAttribute('role', 'group'); tools.setAttribute('aria-label', '批注工具');
      const symbols = {select: '↖', highlight: '▰', underline: 'U̲'};
      this.toolButtons = {};
      for (const [kind, label] of [['select', '浏览'], ['highlight', '高亮'], ['underline', '下划线']]) {
        const button = element(doc, 'button', 'annotation-tool'); button.type = 'button'; button.dataset.annotationTool = kind;
        const mark = element(doc, 'span', `annotation-tool-symbol annotation-symbol-${kind}`, symbols[kind]); mark.setAttribute('aria-hidden', 'true');
        button.append(mark, element(doc, 'span', '', label));
        button.title = kind === 'select' ? '浏览页面，点击批注可选中或删除' : `拖选文字区域添加${label}`;
        button.addEventListener('click', () => this.setTool(kind)); tools.append(button); this.toolButtons[kind] = button;
      }
      const colors = element(doc, 'div', 'annotation-colors'); colors.setAttribute('role', 'group'); colors.setAttribute('aria-label', '批注颜色');
      this.colorButtons = COLORS.map(color => {
        const button = element(doc, 'button', 'annotation-color'); button.type = 'button';
        button.style.setProperty('--annotation-color', color.value); button.title = color.label;
        button.setAttribute('aria-label', color.label); button.dataset.color = color.value;
        button.addEventListener('click', () => this.setColor(color.value)); colors.append(button); return button;
      });
      this.customColor = element(doc, 'label', 'annotation-custom-color');
      this.colorPicker = element(doc, 'input', 'annotation-color-picker'); this.colorPicker.type = 'color';
      this.colorPicker.value = this.color; this.colorPicker.setAttribute('aria-label', '自选批注颜色');
      this.colorPicker.addEventListener('input', () => this.setColor(this.colorPicker.value, false));
      this.colorPicker.addEventListener('change', () => this.setColor(this.colorPicker.value));
      this.customColor.append(this.colorPicker, element(doc, 'span', '', '自选颜色')); colors.append(this.customColor);
      this.deleteButton = element(doc, 'button', 'annotation-action', '删除'); this.deleteButton.type = 'button';
      this.deleteButton.title = '先点击批注，再删除（Delete）'; this.deleteButton.addEventListener('click', () => this.deleteSelected());
      this.retryButton = element(doc, 'button', 'annotation-action annotation-retry', '重试保存'); this.retryButton.type = 'button';
      this.retryButton.addEventListener('click', () => {
        const selected = this.selectedAnnotation();
        if (selected?.failed) this.session.save(this.selection.version, selected.rects || [selected.rect], selected.kind, selected.color, selected);
      });
      this.status = element(doc, 'span', 'annotation-status'); this.status.setAttribute('role', 'status'); this.status.setAttribute('aria-live', 'polite');
      this.historyNote = element(doc, 'span', 'annotation-history', '旧版批注已保留 · 当前 PDF 独立批注');
      this.historyNote.title = '重新生成 PDF 后，旧版批注保存在本机，不会套到新版页面上。'; this.historyNote.hidden = true;
      this.reloadButton = element(doc, 'button', 'annotation-action', '重新加载'); this.reloadButton.type = 'button';
      this.reloadButton.addEventListener('click', () => { this.cancelSelection(); this.onRefresh(); VERSIONS.filter(v => this.session.enabled(v)).forEach(v => this.session.loadVersion(v)); });
      const exports = element(doc, 'div', 'annotation-exports'); exports.setAttribute('role', 'group'); exports.setAttribute('aria-label', '导出批注副本');
      this.exportButtons = {};
      for (const [version, label] of [['original', '原文批注 PDF'], ['translated', '译文批注 PDF']]) {
        const button = element(doc, 'button', 'annotation-export', label); button.type = 'button';
        button.addEventListener('click', () => this.export(version)); exports.append(button); this.exportButtons[version] = button;
      }
      this.toolbar.append(tools, colors, this.deleteButton, this.retryButton, this.status, this.historyNote, this.reloadButton, exports);
    }
    buildLayer(pane) {
      const doc = this.document;
      pane.container.classList.add('annotation-paper');
      pane.layer = element(doc, 'div', 'annotation-layer'); pane.layer.dataset.version = pane.version;
      pane.layer.setAttribute('aria-label', `${pane.version === 'original' ? '原文' : '译文'}批注`);
      pane.marks = element(doc, 'div', 'annotation-marks'); pane.draft = element(doc, 'div', 'annotation-draft');
      pane.layer.append(pane.marks, pane.draft); pane.container.append(pane.layer);
      pane.image.draggable = false;
      pane.layer.addEventListener('pointerdown', event => this.pointerDown(event, pane));
      pane.layer.addEventListener('pointermove', event => this.pointerMove(event, pane));
      pane.layer.addEventListener('pointerup', event => this.pointerUp(event, pane));
      pane.layer.addEventListener('pointercancel', () => this.cancelDrag());
      pane.layer.addEventListener('lostpointercapture', () => this.cancelDrag());
      pane.image.addEventListener('load', () => this.syncLayer(pane));
      pane.image.addEventListener('error', () => this.syncLayer(pane));
      if (root.ResizeObserver) { pane.sizeObserver = new root.ResizeObserver(() => this.syncLayer(pane)); pane.sizeObserver.observe(pane.image); }
      if (root.MutationObserver) {
        pane.imageObserver = new root.MutationObserver(() => this.syncLayer(pane));
        pane.imageObserver.observe(pane.image, {attributes: true, attributeFilter: ['src', 'hidden', 'class']});
      }
    }
    setPage(jobId, page, hasOutput, revision = '') {
      const previous = this.session.context;
      if (!previous || previous.jobId !== jobId || previous.page !== page || previous.revision !== revision || previous.hasOutput !== !!hasOutput) this.cancelSelection();
      return this.session.setPage(jobId, page, hasOutput, revision);
    }
    setVisible(visible) { this.visible = visible; this.toolbar.hidden = !visible; if (!visible) this.cancelDrag(); this.render(); }
    isBusy() { return this.session.isBusy(); }
    setColor(value, persist = true) {
      const color = normalizeColor(value);
      if (!color) return false;
      this.color = color;
      if (persist) rememberColor(this.storage, color);
      this.render(); return true;
    }
    setTool(tool) { this.cancelSelection(); this.tool = tool; this.render(); }
    cancelDrag() {
      const drag = this.drag; this.drag = null;
      if (drag) { try { drag.pane.layer.releasePointerCapture(drag.pointerId); } catch { /* Already released by the browser. */ } }
      this.panes.forEach(pane => pane.draft.replaceChildren());
    }
    cancelSelection() { this.cancelDrag(); this.selection = null; this.render(); }
    selectedAnnotation() {
      return this.selection && this.session.states[this.selection.version].annotations.find(item => item.id === this.selection.id);
    }
    deleteSelected() {
      if (!this.selection) return;
      const {version, id} = this.selection; this.selection = null; this.session.remove(version, id); this.render();
    }
    imageMatches(pane) {
      const context = this.session.context;
      if (!context || pane.image.hidden || !pane.image.complete || !pane.image.naturalWidth || pane.image.classList.contains('preview-loading')) return false;
      try {
        const url = new URL(pane.image.src, this.document.baseURI);
        return url.pathname === `/api/documents/${encodeURIComponent(context.jobId)}/page/${context.page}` && url.searchParams.get('version') === pane.version;
      } catch { return false; }
    }
    syncLayer(pane) {
      const ready = this.visible && this.session.enabled(pane.version) && this.imageMatches(pane);
      pane.layer.hidden = !ready;
      if (!ready && this.drag?.pane === pane) this.cancelDrag();
      pane.layer.style.left = `${pane.image.offsetLeft}px`; pane.layer.style.top = `${pane.image.offsetTop}px`;
      pane.layer.style.width = `${pane.image.clientWidth}px`; pane.layer.style.height = `${pane.image.clientHeight}px`;
    }
    pointerDown(event, pane) {
      if (event.button !== 0 || this.tool === 'select' || !this.session.context) return;
      const state = this.session.states[pane.version];
      if (!state.revision || state.loading || state.textLoading || state.stale || !this.imageMatches(pane)) return;
      event.preventDefault(); this.selection = null;
      const point = pointOnPage(event, pane.layer.getBoundingClientRect());
      this.drag = {pane, pointerId: event.pointerId, start: point, end: point, kind: this.tool, color: this.color,
        context: this.session.context, generation: this.session.generation};
      pane.layer.setPointerCapture(event.pointerId); this.render();
    }
    pointerMove(event, pane) {
      if (!this.drag || this.drag.pane !== pane || this.drag.pointerId !== event.pointerId) return;
      event.preventDefault(); this.drag.end = pointOnPage(event, pane.layer.getBoundingClientRect()); this.drawDraft();
    }
    pointerUp(event, pane) {
      const drag = this.drag;
      if (!drag || drag.pane !== pane || drag.pointerId !== event.pointerId) return;
      event.preventDefault(); drag.end = pointOnPage(event, pane.layer.getBoundingClientRect());
      const rect = normalizeRect(drag.start, drag.end);
      const pixels = pane.layer.getBoundingClientRect();
      const tiny = (rect[2] - rect[0]) * pixels.width < 4;
      const rects = this.draftRects(drag, rect);
      this.cancelDrag();
      if (tiny || !rects.length || !rects.every(validRect) || drag.context !== this.session.context || drag.generation !== this.session.generation) return;
      if (rects.length > 100) { this.session.message = '本次选择较长，请分成几次批注'; this.session.messageKind = 'error'; this.render(); return; }
      this.session.save(pane.version, rects, drag.kind, drag.color);
    }
    draftRects(drag, rect) {
      const text = this.session.states[drag.pane.version].text;
      return selectionGeometry(rect, text?.page === this.session.context.page ? text.lines : [],
        drag.pane.layer.getBoundingClientRect().height, drag.kind);
    }
    drawRect(parent, rect, kind, color, className, button = false) {
      if (!validRect(rect)) return null;
      const mark = element(this.document, button ? 'button' : 'span', `annotation-mark ${kind} ${className || ''}`);
      mark.style.left = `${rect[0] * 100}%`; mark.style.top = `${rect[1] * 100}%`;
      mark.style.width = `${(rect[2] - rect[0]) * 100}%`; mark.style.height = `${(rect[3] - rect[1]) * 100}%`;
      mark.style.setProperty('--annotation-color', normalizeColor(color) || COLORS[0].value);
      mark.style.setProperty('--annotation-opacity', String(highlightOpacity(color)));
      if (button) mark.type = 'button'; parent.append(mark); return mark;
    }
    drawDraft() {
      if (!this.drag) return;
      const drag = this.drag, rect = normalizeRect(drag.start, drag.end);
      drag.pane.draft.replaceChildren();
      this.draftRects(drag, rect).slice(0, 100).forEach(box => this.drawRect(drag.pane.draft, box, drag.kind, drag.color, 'draft'));
      this.drawRect(drag.pane.draft, rect, 'selection', drag.color, 'selection-area');
    }
    render() {
      if (!this.toolbar || !this.toolButtons) return;
      const context = this.session.context;
      Object.entries(this.toolButtons).forEach(([kind, button]) => { button.setAttribute('aria-pressed', String(kind === this.tool)); button.disabled = !context; });
      this.colorButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.color === this.color)));
      this.colorPicker.value = this.color; this.colorPicker.title = `选择任意批注颜色 · 当前 ${this.color.toUpperCase()}`;
      this.customColor.dataset.active = String(!COLORS.some(color => color.value === this.color));
      const selected = this.selectedAnnotation();
      this.deleteButton.disabled = !selected || selected.pending;
      this.retryButton.hidden = !selected?.failed;
      this.retryButton.disabled = !selected || selected.pending || !!this.session.states[this.selection?.version]?.stale;
      const active = VERSIONS.filter(version => this.session.enabled(version)).map(version => this.session.states[version]);
      const error = active.find(state => state.error)?.error;
      const loading = active.some(state => state.loading || state.textLoading);
      this.reloadButton.hidden = !error && !active.some(state => state.textError);
      this.reloadButton.disabled = loading || this.session.pending > 0;
      const failed = active.some(state => state.annotations.some(annotation => annotation.failed));
      this.status.textContent = error || (this.session.pending ? '正在保存批注…' : this.session.message) ||
        (loading ? '正在读取批注与文字位置…' : active.find(state => state.textError)?.textError) ||
        (selected ? '已选中批注 · 按 Delete 删除' : this.tool === 'select' ? '中英文均可批注 · 自动保存' : '拖选文字区域，松开后自动保存');
      this.status.dataset.state = error || failed || this.session.messageKind === 'error' ? 'error' : this.session.pending ? 'saving' : this.session.messageKind;
      this.status.title = active.some(state => state.staleCount) ? '重新生成的 PDF 使用独立批注，旧版批注不会错位显示。' : this.status.textContent;
      this.historyNote.hidden = !active.some(state => state.staleCount);
      for (const pane of this.panes) {
        const state = this.session.states[pane.version];
        pane.layer.dataset.tool = this.tool;
        pane.layer.classList.toggle('annotation-waiting', state.loading || state.textLoading || state.stale);
        pane.marks.replaceChildren();
        if (context && !state.stale) for (const annotation of state.annotations.filter(item => item.page === context.page)) {
          const isSelected = this.selection?.version === pane.version && this.selection.id === annotation.id;
          (annotation.rects || [annotation.rect]).forEach((rect, index) => {
            const mark = this.drawRect(pane.marks, rect, annotation.kind, annotation.color,
              [isSelected ? 'selected' : '', annotation.pending ? 'pending' : '', annotation.failed ? 'failed' : ''].join(' '), true);
            if (!mark) return;
            const label = `${pane.version === 'original' ? '原文' : '译文'}第 ${annotation.page} 页${annotation.kind === 'highlight' ? '高亮' : '下划线'}${annotation.failed ? '，尚未保存，点击重试' : '，点击选中'}`;
            mark.setAttribute('aria-label', label); mark.title = label; mark.setAttribute('aria-pressed', String(isSelected));
            mark.tabIndex = index === 0 ? 0 : -1; mark.disabled = !!annotation.pending;
            mark.addEventListener('click', event => {
              if (this.tool !== 'select') return;
              event.stopPropagation(); this.selection = {version: pane.version, id: annotation.id}; this.render(); this.deleteButton.focus({preventScroll: true});
            });
          });
        }
        this.syncLayer(pane);
        this.exportButtons[pane.version].disabled = !this.session.enabled(pane.version) || !state.revision || state.loading || state.stale ||
          state.annotations.some(annotation => annotation.pending || annotation.failed);
      }
      if (this.drag) this.drawDraft();
    }
    export(version) {
      if (this.exportButtons[version].disabled || !this.session.context) return;
      const link = this.document.createElement('a');
      link.href = `${apiURL(this.session.context.jobId, '/export')}?version=${version}&document_revision=${encodeURIComponent(this.session.states[version].revision)}`;
      link.download = ''; this.document.body.append(link); link.click(); link.remove();
      this.session.message = '请在保存窗口或下载列表中查看批注 PDF'; this.session.messageKind = ''; this.render();
    }
    destroy() {
      this.cancelDrag(); this.document.removeEventListener('keydown', this.escapeHandler, true);
      this.panes.forEach(pane => { pane.sizeObserver?.disconnect(); pane.imageObserver?.disconnect(); pane.layer.remove(); pane.container.classList.remove('annotation-paper'); });
      this.toolbar.replaceChildren(); this.session.generation++;
    }
  }
  const exports = {Controller, AnnotationSession, normalizeRect, validRect, unionRects, pointOnPage, selectedTextRects, selectionGeometry, shortcutsAllowed,
    normalizeColor, readColor, rememberColor, highlightOpacity, COLOR_STORAGE_KEY, COLORS};
  root.PaperAnnotations = exports;
  if (typeof module !== 'undefined') module.exports = exports;
})(typeof window === 'undefined' ? globalThis : window);
