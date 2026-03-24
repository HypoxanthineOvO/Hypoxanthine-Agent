/// <reference types="node" />

import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App.vue";

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  send(): void {}
  close(): void {}
}

beforeEach(() => {
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

const appSource = readFileSync(resolve(process.cwd(), "src/App.vue"), "utf8");
const globalStyleSource = readFileSync(resolve(process.cwd(), "src/style.css"), "utf8");
const mainBodyBlock = appSource.match(/\.main-body\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";

describe("App", () => {
  it("propagates the full-height chain into the chat surface", () => {
    expect(globalStyleSource).toMatch(/html,\s*[\r\n]+body\s*\{[\s\S]*height:\s*100%;/);
    expect(globalStyleSource).toMatch(/#app\s*\{[\s\S]*height:\s*100%;/);
    expect(appSource).toMatch(/\.app-shell\s*\{[\s\S]*height:\s*100vh;/);
    expect(appSource).toMatch(/\.app-main\s*\{[\s\S]*display:\s*flex;/);
    expect(appSource).toMatch(/\.app-main\s*\{[\s\S]*flex-direction:\s*column;/);
    expect(appSource).toMatch(/\.app-main\s*\{[\s\S]*height:\s*100%;/);
    expect(appSource).toMatch(/\.main-body\s*\{[\s\S]*display:\s*flex;/);
    expect(appSource).toMatch(/\.main-body\s*\{[\s\S]*flex:\s*1;/);
    expect(appSource).toMatch(/\.main-body\s*\{[\s\S]*min-height:\s*0;/);
  });

  it("keeps the main content area stretched to the full remaining width", () => {
    expect(mainBodyBlock).toMatch(/flex:\s*1;/);
    expect(mainBodyBlock).toMatch(/min-width:\s*0;/);
    expect(mainBodyBlock).toMatch(/width:\s*100%;/);
  });

  it("applies card radius and elevation through global Naive UI theme overrides", () => {
    expect(appSource).toMatch(/:theme-overrides="themeOverrides"/);
    expect(appSource).toMatch(/Card:\s*\{[\s\S]*borderRadius:\s*["'`]/);
    expect(appSource).toMatch(/Card:\s*\{[\s\S]*boxShadow\s*:/);
  });

  it("updates the browser tab title with the Hypo-Agent brand", async () => {
    document.title = "web";

    mount(App, {
      attachTo: document.body,
    });

    await flushUi();
    await flushUi();

    expect(document.title).toContain("Hypo-Agent");
    expect(document.title).not.toBe("web");
  });

  it("shows a reachable mobile navigation bar on small screens", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 390,
    });

    const wrapper = mount(App, {
      attachTo: document.body,
    });

    await flushUi();
    await flushUi();

    expect(wrapper.text()).toContain("Chat");
    expect(wrapper.text()).toContain("Dashboard");
    expect(wrapper.text()).toContain("Config");
  });
});
