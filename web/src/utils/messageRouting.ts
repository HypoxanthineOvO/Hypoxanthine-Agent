import type { Message } from "@/types/message";

const CODE_FILE_PREVIEW_EXT = /\.(py|ya?ml|json|ts|js|sh|toml|ini)$/i;
const LEGACY_SOURCE_PREFIX_BY_CHANNEL: Record<string, RegExp[]> = {
  qq: [/^\[QQ\]\s*/i],
  qq_bot: [/^\[QQ\]\s*/i],
  qq_napcat: [/^\[QQ\]\s*/i],
  weixin: [/^\[微信\]\s*/],
  feishu: [/^\[飞书\]\s*/],
};

export function makeApiUrl(path: string, apiBase: string): string {
  return `${apiBase.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

export function withApiToken(url: string, token: string): string {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

export function resolveAssetUrl(
  rawPath: string | null | undefined,
  apiBase: string,
  token: string,
): string {
  if (!rawPath) {
    return "";
  }
  if (/^https?:\/\//i.test(rawPath)) {
    return rawPath;
  }

  return makeApiUrl(
    `files?path=${encodeURIComponent(rawPath)}`,
    apiBase,
  ).concat(`&token=${encodeURIComponent(token)}`);
}

export function stripLegacySourcePrefix(message: Message): Message {
  const text = message.text;
  if (typeof text !== "string" || text.length === 0) {
    return message;
  }

  const channel = String(message.channel ?? "").trim().toLowerCase();
  const patterns = LEGACY_SOURCE_PREFIX_BY_CHANNEL[channel];
  if (!patterns?.length) {
    return message;
  }

  let normalized = text;
  for (const pattern of patterns) {
    normalized = normalized.replace(pattern, "");
  }
  if (normalized === text) {
    return message;
  }

  return {
    ...message,
    text: normalized,
  };
}

export function hasMarkdownPreview(message: Message): boolean {
  return Boolean(
    message.file?.toLowerCase().endsWith(".md") &&
      typeof message.text === "string" &&
      message.text.length > 0,
  );
}

export function hasMedia(message: Message): boolean {
  const source = message.image ?? message.file ?? "";
  return /\.(png|jpe?g|gif|svg|webp|mp4|webm)$/i.test(source);
}

export function mediaType(message: Message): "image" | "video" {
  return /\.(mp4|webm)$/i.test(message.image ?? message.file ?? "") ? "video" : "image";
}

export function hasCodeFilePreview(message: Message): boolean {
  return Boolean(
    message.file &&
      CODE_FILE_PREVIEW_EXT.test(message.file) &&
      typeof message.text === "string" &&
      message.text.length > 0,
  );
}

export function hasFileAttachment(message: Message): boolean {
  return Boolean(
    message.file &&
      !hasMarkdownPreview(message) &&
      !hasMedia(message) &&
      !hasCodeFilePreview(message),
  );
}

export function isToolCall(message: Message): boolean {
  return message.kind === "tool_call" || message.event_type === "tool_call_result";
}

export function isErrorCard(message: Message): boolean {
  return message.kind === "error" || message.metadata?.error_card === true;
}

export function isEphemeralToolResult(message: Message): boolean {
  return message.event_type === "tool_call_result" && message.metadata?.ephemeral === true;
}

export function isHiddenSystemToolEvent(message: Message): boolean {
  return (
    message.event_type === "tool_call_start" ||
    isEphemeralToolResult(message) ||
    (message.message_tag === "tool_status" && message.metadata?.ephemeral === true)
  );
}

export interface CodexStatusInfo {
  taskId: string;
  status: string;
  summary: string;
}

export function isCodexStatusMessage(message: Message): boolean {
  if (message.message_tag !== "tool_status") {
    return false;
  }
  const metadata = message.metadata ?? {};
  return Boolean(
    message.sender === "hypo-coder" ||
      metadata.source === "hypo_coder" ||
      metadata.task_id ||
      metadata.codex_job_id,
  );
}

export function codexStatusInfo(message: Message): CodexStatusInfo {
  const metadata = message.metadata ?? {};
  const taskId = String(metadata.task_id ?? metadata.codex_job_id ?? "unknown");
  const status = String(metadata.status ?? message.status ?? "running");
  const summary = String(
    metadata.summary ??
      metadata.operation ??
      metadata.prompt_summary ??
      "详细输出已收起到 Codex Jobs 面板",
  );
  return { taskId, status, summary };
}

export function isCompressedToolResult(message: Message): boolean {
  return (
    message.event_type === "tool_call_result" &&
    Boolean(message.compressed_meta) &&
    typeof message.result === "string"
  );
}

export function resolveCompressedFilePath(message: Message): string {
  const params = message.arguments;
  if (params && typeof params.path === "string") {
    return params.path;
  }
  return "";
}
