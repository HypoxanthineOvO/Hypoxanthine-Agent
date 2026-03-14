<script setup lang="ts">
import { computed } from "vue";
import { NCard, NSelect, NTag } from "naive-ui";

export interface ConfigFileMeta {
  filename: string;
  label: string;
  icon: string;
  description: string;
  exists?: boolean;
  editable?: boolean;
}

const props = defineProps<{
  files: ConfigFileMeta[];
  selectedFilename: string;
}>();

const emit = defineEmits<{
  "update:selectedFilename": [value: string];
}>();

const selectOptions = computed(() =>
  props.files.map((item) => ({
    label: `${item.icon} ${item.label}`,
    value: item.filename,
  })),
);

const updateSelection = (value: string | null): void => {
  if (!value) {
    return;
  }
  emit("update:selectedFilename", value);
};
</script>

<template>
  <n-card class="config-file-list" title="配置中心" :bordered="false">
    <template #header-extra>
      <n-tag size="small" round type="success">
        {{ files.length }} 项
      </n-tag>
    </template>

    <p class="list-description">
      选择一个配置文件后，可以用结构化表单编辑，必要时再切换到原始 YAML。
    </p>

    <n-select
      class="mobile-file-select"
      :value="selectedFilename"
      :options="selectOptions"
      @update:value="updateSelection"
    />

    <div class="file-grid">
      <button
        v-for="item in files"
        :key="item.filename"
        type="button"
        class="file-item"
        :data-active="item.filename === selectedFilename"
        :data-testid="`config-file-item-${item.filename}`"
        @click="emit('update:selectedFilename', item.filename)"
      >
        <div class="file-item-top">
          <span class="file-icon">{{ item.icon }}</span>
          <span class="file-label">{{ item.label }}</span>
        </div>
        <p class="file-description">{{ item.description }}</p>
      </button>
    </div>
  </n-card>
</template>

<style scoped>
.config-file-list {
  background:
    linear-gradient(160deg, color-mix(in srgb, var(--surface) 94%, transparent), transparent 68%),
    color-mix(in srgb, var(--panel) 94%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 92%, transparent);
  border-radius: 1rem;
  height: 100%;
}

.list-description {
  color: var(--muted);
  font-size: 0.9rem;
  line-height: 1.5;
  margin: 0 0 1rem;
}

.mobile-file-select {
  display: none;
  margin-bottom: 1rem;
}

.file-grid {
  display: grid;
  gap: 0.75rem;
}

.file-item {
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--surface) 96%, transparent), transparent),
    color-mix(in srgb, var(--surface) 90%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 88%, transparent);
  border-radius: 0.95rem;
  color: var(--text);
  cursor: pointer;
  padding: 0.9rem 1rem;
  text-align: left;
  transition:
    transform 140ms ease,
    border-color 140ms ease,
    background 140ms ease;
}

.file-item:hover {
  border-color: color-mix(in srgb, var(--brand) 55%, var(--panel-edge));
  transform: translateY(-1px);
}

.file-item[data-active="true"] {
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--brand) 12%, transparent), transparent 72%),
    color-mix(in srgb, var(--surface) 92%, transparent);
  border-color: color-mix(in srgb, var(--brand) 58%, var(--panel-edge));
  box-shadow: 0 12px 28px color-mix(in srgb, var(--brand) 12%, transparent);
}

.file-item-top {
  align-items: center;
  display: flex;
  gap: 0.65rem;
}

.file-icon {
  font-size: 1.1rem;
}

.file-label {
  font-size: 0.98rem;
  font-weight: 700;
}

.file-description {
  color: var(--muted);
  font-size: 0.84rem;
  line-height: 1.45;
  margin: 0.55rem 0 0;
}

@media (max-width: 900px) {
  .mobile-file-select {
    display: block;
  }

  .file-grid {
    display: none;
  }
}
</style>
