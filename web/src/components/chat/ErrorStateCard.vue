<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  code: string;
  message: string;
  detail?: string;
  retryable?: boolean;
  loading?: boolean;
}>();

const emit = defineEmits<{
  retry: [];
}>();

const title = computed(() => {
  if (props.code === "LLM_TIMEOUT") {
    return "请求超时";
  }
  if (props.code === "NETWORK_ERROR") {
    return "网络连接失败";
  }
  if (props.code === "LLM_RUNTIME_ERROR") {
    return "模型服务异常";
  }
  return "调用失败";
});
</script>

<template>
  <section class="error-state-card">
    <div class="error-head">
      <span class="error-icon">{{ code === "LLM_TIMEOUT" ? "⏱️" : "⚠️" }}</span>
      <div class="error-copy">
        <strong>{{ title }}</strong>
        <p>{{ message }}</p>
      </div>
    </div>

    <details v-if="detail && detail !== message" class="error-details">
      <summary>详情</summary>
      <pre>{{ detail }}</pre>
    </details>

    <div v-if="retryable" class="error-actions">
      <button type="button" class="retry-button" :disabled="loading" @click="emit('retry')">
        {{ loading ? "重试中..." : "重试" }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.error-state-card {
  background: color-mix(in srgb, var(--error) 10%, var(--surface));
  border: 1px solid color-mix(in srgb, var(--error) 35%, var(--panel-edge));
  border-radius: 0.9rem;
  display: grid;
  gap: 0.8rem;
  max-width: 100%;
  min-width: 0;
  padding: 0.9rem;
}

.error-head {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: auto 1fr;
}

.error-icon {
  align-items: center;
  background: color-mix(in srgb, var(--error) 16%, transparent);
  border-radius: 0.75rem;
  display: inline-flex;
  font-size: 1.1rem;
  height: 2.2rem;
  justify-content: center;
  width: 2.2rem;
}

.error-copy {
  display: grid;
  gap: 0.25rem;
}

.error-copy strong {
  color: var(--text);
  font-size: 0.95rem;
}

.error-copy p {
  color: var(--text-soft);
  margin: 0;
}

.error-details {
  background: color-mix(in srgb, var(--surface-soft) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 88%, transparent);
  border-radius: 0.75rem;
  padding: 0.7rem 0.8rem;
}

.error-details summary {
  color: var(--muted);
  cursor: pointer;
  font-weight: 600;
}

.error-details pre {
  margin: 0.7rem 0 0;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.error-actions {
  display: flex;
  justify-content: flex-end;
}

.retry-button {
  background: linear-gradient(135deg, color-mix(in srgb, var(--error) 78%, var(--brand)), var(--error));
  border: 0;
  border-radius: 0.7rem;
  color: #fff;
  cursor: pointer;
  font-weight: 700;
  padding: 0.5rem 0.9rem;
}

.retry-button:disabled {
  cursor: progress;
  opacity: 0.7;
}
</style>
