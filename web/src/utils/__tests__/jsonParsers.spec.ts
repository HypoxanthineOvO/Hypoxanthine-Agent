import { describe, expect, it } from "vitest";

import {
  normalizeTimestamp,
  parseCompressedMeta,
  parseJsonObject,
} from "../jsonParsers";

describe("jsonParsers", () => {
  it("parses json objects and falls back to empty objects", () => {
    expect(parseJsonObject('{"command":"echo hi"}')).toEqual({ command: "echo hi" });
    expect(parseJsonObject("[1,2,3]")).toEqual({});
    expect(parseJsonObject("oops")).toEqual({});
  });

  it("parses compressed meta payloads and ignores invalid shapes", () => {
    expect(
      parseCompressedMeta('{"cache_id":"cache-1","original_chars":1000,"compressed_chars":120}'),
    ).toEqual({
      cache_id: "cache-1",
      original_chars: 1000,
      compressed_chars: 120,
    });
    expect(parseCompressedMeta('"text"')).toBeUndefined();
  });

  it("normalizes sqlite timestamps to iso strings", () => {
    expect(normalizeTimestamp("2026-03-06 10:02:00")).toBe("2026-03-06T10:02:00Z");
    expect(normalizeTimestamp("2026-03-06T10:02:00+08:00")).toBe("2026-03-06T10:02:00+08:00");
    expect(normalizeTimestamp(null)).toBeUndefined();
  });
});
