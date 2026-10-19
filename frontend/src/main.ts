import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import { initPrefs } from './prefs'

// 先把记住的界面偏好落到 <html> 上（主题/字号/列宽），再挂载 —— 免得先按默认画一帧再跳
initPrefs()
createApp(App).mount('#app')
