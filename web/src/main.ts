import { registerSW } from "virtual:pwa-register";
import { createApp } from "vue";

import App from "./App.vue";
import "./style.css";

registerSW({ immediate: true });

createApp(App).mount("#app");
