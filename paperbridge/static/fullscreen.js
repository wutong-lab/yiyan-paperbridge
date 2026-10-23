/* Use the browser fullscreen lifecycle in both WKWebView and WebView2. */
(function (root) {
  class ReaderFullscreen {
    constructor({document: doc, button, onChange = () => {}, onMessage = () => {}}) {
      this.document = doc;
      this.button = button;
      this.onChange = onChange;
      this.onMessage = onMessage;
      this.active = false;
      this.pending = false;
      this.desired = false;
      this.transitionTarget = null;
      this.previousFocus = null;
      this.previousScroll = 0;
      doc.addEventListener('fullscreenchange', () => {
        if (!doc.fullscreenElement && this.active) this.apply(false);
        else if (doc.fullscreenElement === doc.documentElement) this.apply(true);
        // Escape may arrive while the native entry promise is still settling.
        // An exit notification during an exit transition must preserve a queued
        // re-entry, however, so the most recent button click is not discarded.
        if (!this.pending || (!doc.fullscreenElement && this.transitionTarget === true)) this.desired = this.active;
      });
      button.addEventListener('click', () => this.toggle());
    }
    apply(enabled) {
      if (this.active === enabled) return;
      const doc = this.document;
      if (enabled) {
        this.previousFocus = doc.activeElement;
        this.previousScroll = doc.defaultView?.scrollY || 0;
      }
      this.active = enabled;
      doc.body.classList.toggle('reader-focus', enabled);
      this.button.textContent = enabled ? '退出全屏 · Esc' : '全屏对照';
      this.button.setAttribute('aria-pressed', String(enabled));
      this.button.title = enabled ? '退出全屏阅读（Esc）' : '全屏对照阅读（F）';
      if (enabled) doc.defaultView?.scrollTo(0, 0);
      this.onChange(enabled);
      if (!enabled) {
        doc.defaultView?.scrollTo(0, this.previousScroll);
        this.previousFocus?.focus?.({preventScroll: true});
      }
    }
    async toggle() { return this.set(!this.desired); }
    async set(enabled) {
      this.desired = !!enabled;
      if (this.pending || this.desired === this.active) return;
      const doc = this.document;
      enabled = this.desired;
      this.pending = true;
      this.transitionTarget = enabled;
      try {
        if (enabled) {
          // Apply the layout first so unsupported embedded browsers still have a
          // useful edge-to-edge reading mode with the same exit control.
          this.apply(true);
          if (doc.documentElement.requestFullscreen && doc.fullscreenEnabled !== false) {
            try { await doc.documentElement.requestFullscreen(); }
            catch { if (this.desired) this.onMessage('已进入专注阅读；当前窗口不支持系统全屏，可使用窗口最大化。'); }
          } else this.onMessage('已进入专注阅读；当前窗口不支持系统全屏，可使用窗口最大化。');
        } else {
          if (doc.fullscreenElement && doc.exitFullscreen) await doc.exitFullscreen();
          this.apply(false);
        }
      } catch {
        // A failed exit must not recursively retry forever. A fresh user click
        // can retry it; a different queued intent still wins.
        if (this.desired === enabled) this.desired = this.active;
        this.onMessage('暂时无法退出全屏，请按 Esc 或使用窗口的全屏按钮。');
      } finally {
        this.pending = false;
        this.transitionTarget = null;
        if (this.desired !== this.active) await this.set(this.desired);
      }
    }
  }
  root.PaperFullscreen = {ReaderFullscreen};
  if (typeof module !== 'undefined') module.exports = {ReaderFullscreen};
})(typeof window === 'undefined' ? globalThis : window);
