<script setup lang="ts">
import CompressedMessage from "./CompressedMessage.vue";
import ErrorStateCard from "./ErrorStateCard.vue";
import FileAttachment from "./FileAttachment.vue";
import MarkdownPreview from "./MarkdownPreview.vue";
import MediaMessage from "./MediaMessage.vue";
import TextMessage from "./TextMessage.vue";
import ToolCallMessage from "./ToolCallMessage.vue";
import type { Message } from "@/types/message";
import {
  hasCodeFilePreview,
  hasFileAttachment,
  hasMarkdownPreview,
  hasMedia,
  isCompressedToolResult,
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
</script>

<template>
  <CompressedMessage
    v-if="isCompressedToolResult(message)"
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
  <MarkdownPreview
    v-else-if="hasMarkdownPreview(message)"
    :content="message.text ?? ''"
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
  />
  <MarkdownPreview
    v-else-if="(message.text ?? '').trim().length > 0"
    :content="message.text ?? ''"
    :show-source-toggle="message.sender === 'assistant' && message.message_tag !== 'narration'"
  />
</template>
