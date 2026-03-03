<script setup lang="ts">
import { computed } from "vue";

import type { ConnectionStatus } from "../types/message";

const props = defineProps<{
  status: ConnectionStatus;
}>();

const label = computed(() => {
  switch (props.status) {
    case "connected":
      return "Connected";
    case "connecting":
      return "Connecting";
    case "error":
      return "Error";
    default:
      return "Disconnected";
  }
});
</script>

<template>
  <span class="status" :data-state="status">
    <span class="dot" />
    {{ label }}
  </span>
</template>

<style scoped>
.status {
  align-items: center;
  border-radius: 999px;
  display: inline-flex;
  font-size: 0.85rem;
  font-weight: 600;
  gap: 0.45rem;
  letter-spacing: 0.04em;
  padding: 0.38rem 0.8rem;
  text-transform: uppercase;
}

.dot {
  border-radius: 50%;
  height: 0.55rem;
  width: 0.55rem;
}

.status[data-state="connected"] {
  background: color-mix(in srgb, var(--ok) 22%, transparent);
  color: var(--ok);
}

.status[data-state="connected"] .dot {
  background: var(--ok);
}

.status[data-state="connecting"] {
  background: color-mix(in srgb, var(--warn) 22%, transparent);
  color: var(--warn);
}

.status[data-state="connecting"] .dot {
  background: var(--warn);
}

.status[data-state="disconnected"] {
  background: color-mix(in srgb, var(--muted) 35%, transparent);
  color: var(--muted);
}

.status[data-state="disconnected"] .dot {
  background: var(--muted);
}

.status[data-state="error"] {
  background: color-mix(in srgb, var(--error) 22%, transparent);
  color: var(--error);
}

.status[data-state="error"] .dot {
  background: var(--error);
}
</style>
