<script setup lang="ts">
import { NButton } from "naive-ui";

const props = defineProps<{
  visible: boolean;
  retryAfterMs: number | null;
}>();

const emit = defineEmits<{
  (e: "retry"): void;
}>();

const onRetry = (): void => {
  emit("retry");
};
</script>

<template>
  <section v-if="props.visible" class="reconnect-banner" role="status">
    <p class="banner-text">
      网络连接已断开，正在自动重连
      <span v-if="props.retryAfterMs !== null">
        （{{ Math.ceil(props.retryAfterMs / 1000) }}s）
      </span>
    </p>
    <n-button size="tiny" secondary @click="onRetry">立即重试</n-button>
  </section>
</template>

<style scoped>
.reconnect-banner {
  align-items: center;
  background: color-mix(in srgb, var(--warn) 20%, transparent);
  border: 1px solid color-mix(in srgb, var(--warn) 56%, transparent);
  border-radius: 0.7rem;
  display: flex;
  justify-content: space-between;
  gap: 0.6rem;
  margin-bottom: 0.7rem;
  padding: 0.5rem 0.65rem;
}

.banner-text {
  color: color-mix(in srgb, var(--text) 86%, var(--warn));
  font-size: 0.85rem;
  margin: 0;
}
</style>
