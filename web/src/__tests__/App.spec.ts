import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { flushUi, installMockWebSocket } from "@/test/utils";
import App from "../App.vue";

let originalInnerWidth: number;

beforeEach(() => {
  installMockWebSocket();
  originalInnerWidth = window.innerWidth;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }),
  );
});

afterEach(() => {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: originalInnerWidth,
  });
  vi.unstubAllGlobals();
});

describe("App", () => {
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
