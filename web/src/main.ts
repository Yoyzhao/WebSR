import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/tokens.css'
import './style.css'

import App from './App.vue'
import router from './router'
import { initTheme } from './composables/useTheme'

// 主题必须在挂载前落盘：避免首帧闪白（02 §6.2）
initTheme()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
