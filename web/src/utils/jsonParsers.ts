import type { CompressedMeta } from "@/types/message";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export function parseJsonObject(value: string | null | undefined): Record<string, unknown> {
  if (!value) {
    return {};
  }

  try {
    const parsed = JSON.parse(value);
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function parseCompressedMeta(
  value: string | null | undefined,
): CompressedMeta | undefined {
  if (!value) {
    return undefined;
  }

  try {
    const parsed = JSON.parse(value);
    if (
      isRecord(parsed) &&
      typeof parsed.cache_id === "string" &&
      typeof parsed.original_chars === "number" &&
      typeof parsed.compressed_chars === "number"
    ) {
      return {
        cache_id: parsed.cache_id,
        original_chars: parsed.original_chars,
        compressed_chars: parsed.compressed_chars,
      };
    }
  } catch {
    return undefined;
  }

  return undefined;
}

export function normalizeTimestamp(value: string | null | undefined): string | undefined {
  if (!value) {
    return undefined;
  }

  const normalized = value.trim();
  const sqliteUtcPattern = /^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(\.\d+)?$/;
  if (sqliteUtcPattern.test(normalized)) {
    return `${normalized.replace(/\s+/, "T")}Z`;
  }

  return normalized;
}
