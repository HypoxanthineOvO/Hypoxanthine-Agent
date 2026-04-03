import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import CompressedMessage from "../CompressedMessage.vue";

describe("CompressedMessage", () => {
  it("lazy-loads original text on first expand", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ original_output: "print('ok')" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(CompressedMessage, {
      props: {
        summary: "[📦 已压缩]",
        compressedMeta: {
          cache_id: "cache-1",
          original_chars: 3000,
          compressed_chars: 500,
        },
        apiBase: "http://localhost:8000/api",
        token: "test-token",
        toolName: "exec_command",
      },
    });

    await wrapper.get(".source-button").trigger("click");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/compressed/cache-1?token=test-token",
    );
    expect(wrapper.text()).toContain("print('ok')");

    await wrapper.get(".source-button").trigger("click");
    await wrapper.get(".source-button").trigger("click");
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders markdown preview for .md file", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ original_output: "# Title" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(CompressedMessage, {
      props: {
        summary: "[📦 已压缩]",
        compressedMeta: {
          cache_id: "cache-2",
          original_chars: 3000,
          compressed_chars: 500,
        },
        apiBase: "http://localhost:8000/api",
        token: "test-token",
        filePath: "notes.md",
      },
    });

    await wrapper.get(".source-button").trigger("click");
    await flushPromises();

    expect(wrapper.html()).toContain("markdown-preview");
  });
});
