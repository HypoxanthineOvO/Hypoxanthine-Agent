<script setup lang="ts">
import { NTooltip } from "naive-ui";

interface NavItem {
  key: "chat" | "dashboard" | "config" | "memory";
  icon: string;
  label: string;
  disabled: boolean;
}

const props = withDefaults(
  defineProps<{
    active?: NavItem["key"];
    collapsed?: boolean;
  }>(),
  {
    active: "chat",
    collapsed: false,
  },
);

const emit = defineEmits<{
  (e: "select", key: NavItem["key"]): void;
}>();

const navItems: NavItem[] = [
  { key: "chat", icon: "💬", label: "Chat", disabled: false },
  { key: "dashboard", icon: "📊", label: "Dashboard", disabled: false },
  { key: "config", icon: "⚙️", label: "Config", disabled: false },
  { key: "memory", icon: "🧠", label: "Memory", disabled: false },
];

const onClick = (item: NavItem): void => {
  if (item.disabled) {
    return;
  }
  emit("select", item.key);
};
</script>

<template>
  <nav class="side-nav" :data-collapsed="collapsed">
    <button
      v-for="item in navItems"
      :key="item.key"
      type="button"
      class="nav-item"
      :data-active="item.key === active"
      :disabled="item.disabled"
      @click="onClick(item)"
    >
      <n-tooltip v-if="item.disabled" trigger="hover">
        <template #trigger>
          <span class="nav-icon">{{ item.icon }}</span>
        </template>
        Coming in M7b
      </n-tooltip>
      <span v-else class="nav-icon">{{ item.icon }}</span>
      <span v-if="!collapsed" class="nav-label">{{ item.label }}</span>
    </button>
  </nav>
</template>

<style scoped>
.side-nav {
  align-content: start;
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 0.95rem;
  display: grid;
  gap: 0.55rem;
  padding: 0.7rem;
}

.nav-item {
  align-items: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 0.75rem;
  color: var(--text);
  cursor: pointer;
  display: grid;
  font-weight: 600;
  gap: 0.5rem;
  grid-template-columns: 1.35rem 1fr;
  justify-items: start;
  min-height: 2.5rem;
  padding: 0.45rem 0.55rem;
}

.side-nav[data-collapsed="true"] .nav-item {
  grid-template-columns: 1fr;
  justify-items: center;
}

.nav-item[data-active="true"] {
  background: color-mix(in srgb, var(--brand) 18%, transparent);
  border-color: color-mix(in srgb, var(--brand) 52%, var(--panel-edge));
}

.nav-item:disabled {
  color: var(--muted);
  cursor: not-allowed;
  opacity: 0.85;
}

.nav-icon {
  font-size: 1.05rem;
  line-height: 1;
}

.nav-label {
  font-size: 0.9rem;
}
</style>
