import { describe, expect, it } from "vitest";

import type { Message } from "@/types/message";

import {
  hasCodeFilePreview,
  hasFileAttachment,
  hasMarkdownPreview,
  hasMedia,
  isErrorCard,
  isToolCall,
  mediaType,
  resolveAssetUrl,
  stripLegacySourcePrefix,
} from "../messageRouting";

const baseMessage = (overrides: Partial<Message>): Message => ({
  kind: "text",
  sender: "assistant",
  session_id: "main",
  text: null,
  ...overrides,
});

describe("messageRouting", () => {
  it("detects markdown, media, code preview and generic files", () => {
    expect(hasMarkdownPreview(baseMessage({ file: "notes.md", text: "# hi" }))).toBe(true);
    expect(hasMedia(baseMessage({ file: "photo.png" }))).toBe(true);
    expect(mediaType(baseMessage({ file: "clip.mp4" }))).toBe("video");
    expect(hasCodeFilePreview(baseMessage({ file: "script.py", text: "print('hi')" }))).toBe(true);
    expect(hasFileAttachment(baseMessage({ file: "report.pdf" }))).toBe(true);
  });

  it("detects tool and error-card messages", () => {
    expect(
      isToolCall(baseMessage({ kind: "tool_call", event_type: "tool_call_result" })),
    ).toBe(true);
    expect(
      isErrorCard(baseMessage({ kind: "error", metadata: { error_card: true } })),
    ).toBe(true);
  });

  it("resolves relative asset paths against api base", () => {
    expect(resolveAssetUrl("/tmp/report.pdf", "http://localhost:8765/api", "secret")).toBe(
      "http://localhost:8765/api/files?path=%2Ftmp%2Freport.pdf&token=secret",
    );
    expect(resolveAssetUrl("https://cdn.example.com/file.png", "http://localhost:8765/api", "secret")).toBe(
      "https://cdn.example.com/file.png",
    );
  });

  it("strips legacy channel prefixes from message text when channel metadata exists", () => {
    expect(
      stripLegacySourcePrefix(
        baseMessage({ sender: "user", channel: "feishu", text: "[飞书] 同步一下" }),
      ).text,
    ).toBe("同步一下");
    expect(
      stripLegacySourcePrefix(
        baseMessage({ sender: "user", channel: "weixin", text: "[微信] 你好" }),
      ).text,
    ).toBe("你好");
    expect(
      stripLegacySourcePrefix(
        baseMessage({ sender: "user", channel: "qq", text: "[QQ] 早上好" }),
      ).text,
    ).toBe("早上好");
  });
});
