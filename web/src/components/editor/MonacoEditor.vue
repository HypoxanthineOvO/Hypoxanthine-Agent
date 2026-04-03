<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import type { Monaco } from "@monaco-editor/loader";

import { useThemeMode } from "@/composables/useThemeMode";

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
const isMounted = ref(false);
const { isDark } = useThemeMode();
let editorInstance: Monaco["editor"]["IStandaloneCodeEditor"] | null = null;
let contentListener: Monaco["IDisposable"] | null = null;
let monacoApi: Monaco | null = null;

watch(
  () => props.modelValue,
  (nextValue) => {
    if (!editorInstance) {
      return;
    }
    if (editorInstance.getValue() !== nextValue) {
      editorInstance.setValue(nextValue);
    }
  },
);

watch(
  () => props.language,
  (nextLanguage) => {
    const model = editorInstance?.getModel();
    if (!model || !monacoApi) {
      return;
    }
    monacoApi.editor.setModelLanguage(model, nextLanguage);
  },
);

watch(
  () => props.readonly,
  (readonly) => {
    editorInstance?.updateOptions({ readOnly: readonly });
  },
);

watch(isDark, (dark) => {
  monacoApi?.editor.setTheme(dark ? "vs-dark" : "vs");
});

onMounted(async () => {
  isMounted.value = true;
  if (!container.value) {
    fallback.value = true;
    return;
  }
  try {
    const monacoLoader = await import("@monaco-editor/loader");
    const monaco = await monacoLoader.default.init();
    if (!isMounted.value || !container.value) {
      return;
    }
    monacoApi = monaco;
    monaco.editor.setTheme(isDark.value ? "vs-dark" : "vs");
    editorInstance = monaco.editor.create(container.value, {
      value: props.modelValue,
      language: props.language,
      minimap: { enabled: false },
      automaticLayout: true,
      readOnly: props.readonly,
      fontSize: 13,
      scrollBeyondLastLine: false,
      theme: isDark.value ? "vs-dark" : "vs",
    });
    contentListener = editorInstance.onDidChangeModelContent(() => {
      emit("update:modelValue", editorInstance?.getValue() ?? "");
    });
  } catch {
    fallback.value = true;
  }
});

onUnmounted(() => {
  isMounted.value = false;
  contentListener?.dispose();
  contentListener = null;
  editorInstance?.dispose();
  editorInstance = null;
  monacoApi = null;
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
