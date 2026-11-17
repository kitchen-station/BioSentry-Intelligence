// jsdom lacks a few browser APIs the hw-ui kit uses. Stubs only where missing, so a test that
// stubs one itself (matchMedia in Flip.test) still wins.
if (typeof window !== "undefined") {
  window.matchMedia ??= ((query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
  })) as typeof window.matchMedia;
  window.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
  Element.prototype.getAnimations ??= () => [];
  const dialog = window.HTMLDialogElement?.prototype;
  if (dialog && !dialog.showModal) {
    dialog.showModal = function (this: HTMLDialogElement) { this.open = true; };
    dialog.show = function (this: HTMLDialogElement) { this.open = true; };
    dialog.close = function (this: HTMLDialogElement, value?: string) {
      if (!this.open) return;
      this.open = false;
      if (value !== undefined) this.returnValue = value;
      this.dispatchEvent(new Event("close"));
    };
  }
}
