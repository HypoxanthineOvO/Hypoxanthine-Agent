<script setup lang="ts">
import { computed } from "vue";

import CodexStatusCard from "./CodexStatusCard.vue";
import CompressedMessage from "./CompressedMessage.vue";
import ErrorStateCard from "./ErrorStateCard.vue";
import FileAttachment from "./FileAttachment.vue";
import MarkdownPreview from "./MarkdownPreview.vue";
import PipelineProgress from "./PipelineProgress.vue";
import MediaMessage from "./MediaMessage.vue";
import TextMessage from "./TextMessage.vue";
import ToolCallMessage from "./ToolCallMessage.vue";
import type { Message } from "@/types/message";
import {
  hasCodeFilePreview,
  hasFileAttachment,
  hasMarkdownPreview,
  hasMedia,
  codexStatusInfo,
  isCompressedToolResult,
  isCodexStatusMessage,
  isErrorCard,
  isToolCall,
  mediaType,
  resolveAssetUrl,
  resolveCompressedFilePath,
} from "@/utils/messageRouting";

const props = withDefaults(
  defineProps<{
    message: Message;
    apiBase?: string;
    token?: string;
    retryingFailedMessage?: boolean;
  }>(),
  {
    apiBase: "/api",
    token: "",
    retryingFailedMessage: false,
  },
);

const emit = defineEmits<{
  retry: [];
}>();

const mediaSource = (): string =>
  resolveAssetUrl(props.message.image ?? props.message.file ?? "", props.apiBase, props.token);

const markdownCacheKey = computed(() =>
  String(
    props.message.metadata?.render_key ??
      props.message.tool_call_id ??
      props.message.timestamp ??
      `${props.message.session_id}:${props.message.sender}`,
  ),
);

const markdownCacheVersion = computed(() =>
  String(
    props.message.metadata?.render_version ??
      `${props.message.text?.length ?? 0}:${props.message.metadata?.streaming === true ? "streaming" : "final"}`,
  ),
);

const isStreaming = computed(() => props.message.metadata?.streaming === true);
const codexInfo = computed(() => codexStatusInfo(props.message));
</script>

<template>
  <PipelineProgress
    v-if="message.kind === 'pipeline_event'"
    :message="message"
  />
  <CompressedMessage
    v-else-if="isCompressedToolResult(message)"
    :summary="String(message.result ?? '')"
    :compressed-meta="message.compressed_meta"
    :api-base="apiBase"
    :token="token"
    :tool-name="message.tool_name"
    :file-path="resolveCompressedFilePath(message)"
  />
  <ToolCallMessage
    v-else-if="isToolCall(message)"
    :tool-name="message.tool_name ?? ''"
    :status="message.status"
    :params="message.arguments"
    :result="message.result"
  />
  <ErrorStateCard
    v-else-if="isErrorCard(message)"
    :code="String(message.metadata?.error_code ?? 'INTERNAL_ERROR')"
    :message="message.text ?? '调用失败'"
    :detail="String(message.metadata?.error_detail ?? message.text ?? '')"
    :retryable="Boolean(message.metadata?.retryable)"
    :loading="retryingFailedMessage"
    @retry="emit('retry')"
  />
  <CodexStatusCard
    v-else-if="isCodexStatusMessage(message)"
    :task-id="codexInfo.taskId"
    :status="codexInfo.status"
    :summary="codexInfo.summary"
  />
  <MarkdownPreview
    v-else-if="hasMarkdownPreview(message)"
    :content="message.text ?? ''"
    :cache-key="markdownCacheKey"
    :cache-version="markdownCacheVersion"
    :streaming="isStreaming"
  />
  <MediaMessage
    v-else-if="hasMedia(message)"
    :src="mediaSource()"
    :media-type="mediaType(message)"
  />
  <FileAttachment
    v-else-if="hasCodeFilePreview(message)"
    :path="message.file ?? ''"
    :content="message.text ?? ''"
  />
  <FileAttachment
    v-else-if="hasFileAttachment(message)"
    :path="resolveAssetUrl(message.file, apiBase, token)"
  />
  <TextMessage
    v-else-if="(message.text ?? '').trim().length > 0 && message.sender === 'user'"
    :text="message.text ?? ''"
    :cache-key="markdownCacheKey"
    :cache-version="markdownCacheVersion"
    :streaming="isStreaming"
  />
  <MarkdownPreview
    v-else-if="(message.text ?? '').trim().length > 0"
    :content="message.text ?? ''"
    :show-source-toggle="message.sender === 'assistant' && message.message_tag !== 'narration'"
    :cache-key="markdownCacheKey"
    :cache-version="markdownCacheVersion"
    :streaming="isStreaming"
  />
</template>
