import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ConfigView from "../ConfigView.vue";

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

describe("ConfigView", () => {
  it("loads files, reads selected yaml, and saves with token", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/config/files")) {
        return {
          ok: true,
          json: async () => ({
            files: [
              { filename: "models.yaml", editable: true, exists: true },
              { filename: "skills.yaml", editable: true, exists: true },
            ],
          }),
        };
      }
      if (url.includes("/config/models.yaml") && (!init || init.method !== "PUT")) {
        return {
          ok: true,
          json: async () => ({
            filename: "models.yaml",
            content: "default_model: Gemini3Pro\nmodels: {}\ntask_routing: {}",
          }),
        };
      }
      if (url.includes("/config/models.yaml") && init?.method === "PUT") {
        return {
          ok: true,
          json: async () => ({ reloaded: true }),
        };
      }
      return {
        ok: true,
        json: async () => ({}),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ConfigView, {
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

    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url) === "http://localhost:8000/api/config/files?token=test-token",
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url) === "http://localhost:8000/api/config/models.yaml?token=test-token",
      ),
    ).toBe(true);

    const saveButton = wrapper.findAll("button").find((item) => item.text().includes("保存"));
    if (!saveButton) {
      throw new Error("save button not found");
    }
    await saveButton.trigger("click");
    await flushUi();
    const putCalls = fetchMock.mock.calls.filter(([url, init]) =>
      String(url).includes("/config/models.yaml") && (init as RequestInit | undefined)?.method === "PUT",
    );
    expect(putCalls.length).toBeGreaterThanOrEqual(1);
  });
});
