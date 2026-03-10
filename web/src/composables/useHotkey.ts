import { onMounted, onUnmounted } from "vue";

export type HotkeyCombo =
  | "ctrlOrMeta+enter"
  | "ctrlOrMeta+l"
  | "ctrlOrMeta+n"
  | "ctrlOrMeta+d"
  | "ctrlOrMeta+k"
  | "escape";

export interface HotkeyBinding {
  combo: HotkeyCombo;
  handler: (event: KeyboardEvent) => void;
  preventDefault?: boolean;
}

function matchesCombo(event: KeyboardEvent, combo: HotkeyCombo): boolean {
  const key = event.key.toLowerCase();
  switch (combo) {
    case "ctrlOrMeta+enter":
      return (event.ctrlKey || event.metaKey) && key === "enter";
    case "ctrlOrMeta+l":
      return (event.ctrlKey || event.metaKey) && key === "l";
    case "ctrlOrMeta+n":
      return (event.ctrlKey || event.metaKey) && key === "n";
    case "ctrlOrMeta+d":
      return (event.ctrlKey || event.metaKey) && key === "d";
    case "ctrlOrMeta+k":
      return (event.ctrlKey || event.metaKey) && key === "k";
    case "escape":
      return key === "escape";
    default:
      return false;
  }
}

export function registerHotkeys(bindings: HotkeyBinding[]): () => void {
  const onKeyDown = (event: KeyboardEvent): void => {
    for (const binding of bindings) {
      if (!matchesCombo(event, binding.combo)) {
        continue;
      }
      if (binding.preventDefault ?? true) {
        event.preventDefault();
      }
      binding.handler(event);
      return;
    }
  };

  window.addEventListener("keydown", onKeyDown);
  return () => {
    window.removeEventListener("keydown", onKeyDown);
  };
}

export function useHotkey(bindings: HotkeyBinding[]): void {
  let cleanup: (() => void) | null = null;

  onMounted(() => {
    cleanup = registerHotkeys(bindings);
  });

  onUnmounted(() => {
    if (cleanup) {
      cleanup();
      cleanup = null;
    }
  });
}
