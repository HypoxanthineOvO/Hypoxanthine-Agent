<script setup lang="ts">
import { NCard, NTag } from "naive-ui";

withDefaults(
  defineProps<{
    title: string;
    icon: string;
    statusLabel: string;
    tagType?: "success" | "warning" | "error" | "default";
    details?: string[];
    embedded?: boolean;
    bordered?: boolean;
  }>(),
  {
    tagType: "default",
    details: () => [],
    embedded: true,
    bordered: true,
  },
);
</script>

<template>
  <n-card
    size="small"
    :title="title"
    :embedded="embedded"
    :bordered="bordered"
    class="dashboard-card channel-card"
  >
    <template #header-extra>
      <n-tag :type="tagType">{{ statusLabel }}</n-tag>
    </template>

    <div class="channel-overview">
      <span class="channel-icon">{{ icon }}</span>
      <p class="channel-status-text">{{ statusLabel }}</p>
    </div>

    <ul class="channel-details">
      <li v-for="detail in details" :key="detail">{{ detail }}</li>
    </ul>
  </n-card>
</template>

<style scoped>
.dashboard-card {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.dashboard-card :deep(.n-card__content) {
  flex: 1;
}

.channel-card {
  min-height: 168px;
}

.channel-overview {
  align-items: center;
  display: flex;
  gap: 0.75rem;
}

.channel-icon {
  font-size: 1.25rem;
  line-height: 1;
}

.channel-status-text {
  color: var(--muted);
  font-size: 0.82rem;
  font-weight: 600;
  margin: 0;
}

.channel-details {
  color: var(--muted);
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 14px 0 0;
  padding: 0;
}
</style>
