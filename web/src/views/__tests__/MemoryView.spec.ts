/// <reference types="node" />

import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import MemoryView from "../MemoryView.vue";

const memorySource = readFileSync(
  resolve(process.cwd(), "src/views/MemoryView.vue"),
  "utf8",
);
const memoryRootBlock = memorySource.match(/\.memory-view\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";

async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MemoryView", () => {
  it("fills the full width of the content area", () => {
    expect(memoryRootBlock).toMatch(/width:\s*100%;/);
  });

  it("loads l1 l2 l3 data with tokenized requests", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/sessions?")) {
        return {
          ok: true,
          json: async () => [
            { session_id: "s1", created_at: "2026-03-06", updated_at: "2026-03-06", message_count: 2 },
          ],
        };
      }
      if (url.includes("/sessions/s1/messages")) {
        return {
          ok: true,
          json: async () => [{ text: "hello", sender: "user", session_id: "s1" }],
        };
      }
      if (url.includes("/memory/tables?")) {
        return {
          ok: true,
          json: async () => ({ tables: [{ name: "preferences", row_count: 1, writable: true }] }),
        };
      }
      if (url.includes("/memory/tables/preferences")) {
        return {
          ok: true,
          json: async () => ({
            table: "preferences",
            page: 1,
            size: 50,
            total: 1,
            writable: true,
            rows: [{ id: "language", pref_key: "language", pref_value: "zh-CN" }],
          }),
        };
      }
      if (url.includes("/memory/files?")) {
        return {
          ok: true,
          json: async () => ({ files: ["notes/test.md"] }),
        };
      }
      if (url.includes("/memory/files/notes/test.md")) {
        return {
          ok: true,
          json: async () => ({ content: "# test" }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(MemoryView, {
      props: {
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
      global: {
        stubs: {
          MonacoEditor: {
            props: ["modelValue"],
            template: "<textarea :value='modelValue'></textarea>",
          },
        },
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();
    await flushUi();

    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url) === "http://localhost:8000/api/sessions?token=test-token",
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url) === "http://localhost:8000/api/memory/tables?token=test-token",
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(
        ([url]) =>
          String(url) === "http://localhost:8000/api/memory/tables/preferences?page=1&size=50&token=test-token",
      ),
    ).toBe(true);
    expect(wrapper.text()).toContain("s1");
  });
});
