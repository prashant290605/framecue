import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";
import { cleanup } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, vi } from "vitest";
import { resetConnectionForTests } from "@/lib/storage";

// jsdom lacks a few browser APIs that Radix and the virtualizer touch.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
Element.prototype.scrollTo ??= function scrollTo() {};
Element.prototype.scrollIntoView ??= function scrollIntoView() {};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};
URL.createObjectURL ??= () => "blob:test";
// jsdom has no layout. The virtualized list measures its scroll box with clientWidth/Height,
// so give elements a laptop-sized box (tests render about 11 rows of 76 px).
for (const [prop, value] of [["clientHeight", 900], ["clientWidth", 420], ["offsetHeight", 900], ["offsetWidth", 420]] as const) {
  Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value });
}
URL.revokeObjectURL ??= () => {};

beforeEach(() => {
  // A brand-new, empty IndexedDB for every test.
  globalThis.indexedDB = new IDBFactory();
  resetConnectionForTests();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
