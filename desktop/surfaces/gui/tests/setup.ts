import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node 26+ installs a `localStorage` property on globalThis that reads
// `undefined` unless the process is started with --localstorage-file. It
// shadows the one jsdom provides, so every test touching window.localStorage
// dies with "Cannot read properties of undefined (reading 'clear')".
// Measured 2026-08-26: green on Node 22 and 24, 61 failures on Node 26.
// .nvmrc pins 24, but the suite should not depend on which Node you happen to
// have installed, so supply a working Storage when the runtime has not.
function installLocalStorage(): void {
  try {
    if (window.localStorage) return;
  } catch {
    // Accessing it can throw (opaque origin) -- fall through and install.
  }
  let store = new Map<string, string>();
  const shim: Storage = {
    get length() {
      return store.size;
    },
    clear: () => {
      store = new Map();
    },
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    removeItem: (key: string) => {
      store.delete(key);
    },
    setItem: (key: string, value: string) => {
      store.set(String(key), String(value));
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: shim,
  });
}

installLocalStorage();

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: ResizeObserverStub,
});
Object.defineProperty(HTMLElement.prototype, "scrollTo", {
  configurable: true,
  value: () => {},
});

afterEach(cleanup);
