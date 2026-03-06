<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    language?: string;
    height?: string;
    readonly?: boolean;
  }>(),
  {
    language: "yaml",
    height: "420px",
    readonly: false,
  },
);

const emit = defineEmits<{
  (event: "update:modelValue", value: string): void;
}>();

const container = ref<HTMLElement | null>(null);
const fallback = ref(false);
let editor: any = null;

watch(
  () => props.modelValue,
  (nextValue) => {
    if (!editor) {
      return;
    }
    if (editor.getValue() !== nextValue) {
      editor.setValue(nextValue);
    }
  },
);

onMounted(async () => {
  if (!container.value) {
    fallback.value = true;
    return;
  }
  try {
    const monacoLoader = await import("@monaco-editor/loader");
    const monaco = await monacoLoader.default.init();
    editor = monaco.editor.create(container.value, {
      value: props.modelValue,
      language: props.language,
      minimap: { enabled: false },
      automaticLayout: true,
      readOnly: props.readonly,
      fontSize: 13,
      scrollBeyondLastLine: false,
    });
    editor?.onDidChangeModelContent(() => {
      emit("update:modelValue", editor?.getValue() ?? "");
    });
  } catch {
    fallback.value = true;
  }
});

onUnmounted(() => {
  editor?.dispose();
  editor = null;
});
</script>

<template>
  <div class="monaco-wrapper" :style="{ height }">
    <textarea
      v-if="fallback"
      class="fallback-textarea"
      :value="modelValue"
      :readonly="readonly"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
    />
    <div
      v-else
      ref="container"
      class="monaco-host"
    />
  </div>
</template>

<style scoped>
.monaco-wrapper {
  border: 1px solid var(--panel-edge);
  border-radius: 10px;
  overflow: hidden;
  width: 100%;
}

.monaco-host {
  height: 100%;
  width: 100%;
}

.fallback-textarea {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 0;
  color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 13px;
  height: 100%;
  outline: none;
  padding: 10px;
  resize: none;
  width: 100%;
}
</style>
