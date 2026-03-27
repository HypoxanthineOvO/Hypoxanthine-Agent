import { registerSW } from "virtual:pwa-register";
import { createApp } from "vue";

import App from "./App.vue";
import "./style.css";

const updateSW = registerSW({
  immediate: true,
  onNeedRefresh() {
    updateSW(true);
  },
});

createApp(App).mount("#app");
