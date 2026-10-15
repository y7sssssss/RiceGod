import { createApp } from 'vue'
import { ElButton, ElInput, ElTabs, ElTabPane, ElCollapse, ElCollapseItem, ElDialog, ElTooltip, ElIcon } from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/theme.css'
import './styles/markdown.css'
import App from './App.vue'

const app = createApp(App)
for (const component of [ElButton, ElInput, ElTabs, ElTabPane, ElCollapse, ElCollapseItem, ElDialog, ElTooltip, ElIcon]) app.use(component)
app.mount('#app')
