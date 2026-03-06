import { computed, ref } from "vue";
import { darkTheme, useOsTheme } from "naive-ui";
import type { GlobalTheme } from "naive-ui";

export type ThemeMode = "light" | "dark";

const manualMode = ref<ThemeMode | null>(null);

export function useThemeMode() {
  const osTheme = useOsTheme();

  const mode = computed<ThemeMode>(() => {
    if (manualMode.value) {
      return manualMode.value;
    }
    return osTheme.value === "dark" ? "dark" : "light";
  });

  const theme = computed<GlobalTheme | null>(() =>
    mode.value === "dark" ? darkTheme : null,
  );

  const isDark = computed(() => mode.value === "dark");

  const setMode = (next: ThemeMode | null): void => {
    manualMode.value = next;
  };

  const toggleMode = (): void => {
    manualMode.value = mode.value === "dark" ? "light" : "dark";
  };

  return {
    isDark,
    mode,
    setMode,
    theme,
    toggleMode,
  };
}
