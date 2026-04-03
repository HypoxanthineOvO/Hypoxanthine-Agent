import { describe, expect, it, vi } from "vitest";

import type { Message } from "@/types/message";

import {
  loadSessionMessages,
  mergeTimelineMessages,
  toToolInvocationMessages,
} from "../useSessionHistory";

describe("useSessionHistory", () => {
  it("maps tool invocation rows into start/result timeline messages", () => {
    expect(
      toToolInvocationMessages([
        {
          id: 1,
          session_id: "main",
          tool_name: "exec_command",
          status: "success",
          created_at: "2026-03-06 10:02:00",
          error_info: null,
          params_json: '{"command":"echo hi"}',
          compressed_meta_json: null,
        },
      ]),
    ).toMatchObject([
      { kind: "tool_call", event_type: "tool_call_start", tool_name: "exec_command" },
      { kind: "tool_call", event_type: "tool_call_result", tool_name: "exec_command" },
    ]);
  });

  it("merges session history and tool timeline by timestamp and phase", () => {
    const history: Message[] = [
      {
        kind: "text",
        text: "hello",
        sender: "user",
        session_id: "main",
        timestamp: "2026-03-06T10:00:00Z",
      },
    ];
    const merged = mergeTimelineMessages(
      history,
      toToolInvocationMessages([
        {
          id: 1,
          session_id: "main",
          tool_name: "exec_command",
          status: "success",
          created_at: "2026-03-06 10:02:00",
          error_info: null,
          params_json: '{"command":"echo hi"}',
          compressed_meta_json: null,
        },
      ]),
    );

    expect(merged.map((message) => message.event_type ?? message.text)).toEqual([
      "hello",
      "tool_call_start",
      "tool_call_result",
    ]);
  });

  it("loads session messages from api endpoints and returns merged timeline", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ kind: "text", text: "old", sender: "user", session_id: "main" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });

    const result = await loadSessionMessages({
      apiBase: "http://localhost:8765/api",
      token: "secret",
      sessionId: "main",
      fetchImpl: fetchMock,
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result).toHaveLength(1);
    expect(result[0]?.text).toBe("old");
  });

  it("normalizes legacy relay prefixes from restored history using channel metadata", () => {
    const merged = mergeTimelineMessages(
      [
        {
          kind: "text",
          text: "[飞书] 同步一下",
          sender: "user",
          session_id: "main",
          channel: "feishu",
        },
      ],
      [],
    );

    expect(merged[0]?.text).toBe("同步一下");
    expect(merged[0]?.channel).toBe("feishu");
  });
});
