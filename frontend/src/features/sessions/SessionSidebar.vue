<script setup lang="ts">
import { computed, ref } from 'vue'
import { Plus, ChatLineRound, EditPen, Lock, ArrowRight } from '@element-plus/icons-vue'
import type { Session } from './useSessions'

const props = defineProps<{ sessions: Session[]; activeId: string; userId: string; storageError: string }>()
const emit = defineEmits<{ create: []; select: [id: string]; rename: [id: string, name: string] }>()
const editingId = ref('')
const name = ref('')
const dialogOpen = ref(false)
const groups = computed(() => {
  const today = new Date().toLocaleDateString()
  return [
    { label: '今天', items: props.sessions.filter(s => new Date(s.updatedAt).toLocaleDateString() === today) },
    { label: '更早的会话', items: props.sessions.filter(s => new Date(s.updatedAt).toLocaleDateString() !== today) },
  ].filter(g => g.items.length)
})
function edit(session: Session) {
  editingId.value = session.id
  name.value = session.name
  dialogOpen.value = true
}
function save() {
  if (!name.value.trim()) return
  emit('rename', editingId.value, name.value)
  dialogOpen.value = false
}
</script>

<template>
  <aside class="sidebar" aria-label="会话管理">
    <a class="brand" href="#" aria-label="RiceGod 首页" @click.prevent>
      <img src="/ricegod.svg" width="40" height="40" alt="" />
      <span><strong>RiceGod<span class="brand-dot">.</span></strong><small>你的 AI 饮食助手</small></span>
    </a>
    <el-button class="new-chat" type="primary" :icon="Plus" @click="emit('create')">开启新对话 <kbd>＋</kbd></el-button>
    <div class="section-heading"><span>我的对话</span><span>{{ sessions.length }}</span></div>
    <nav class="session-list" aria-label="历史会话">
      <section v-for="group in groups" :key="group.label">
        <h2>{{ group.label }}</h2>
        <div v-for="session in group.items" :key="session.id" class="session-row" :class="{ active: session.id === activeId }">
          <button class="session-select" :aria-current="session.id === activeId ? 'page' : undefined" @click="emit('select', session.id)">
            <el-icon><ChatLineRound /></el-icon><span>{{ session.name }}</span>
          </button>
          <el-tooltip content="重命名会话" placement="right">
            <button class="rename icon-button" :aria-label="`重命名 ${session.name}`" @click="edit(session)"><el-icon><EditPen /></el-icon></button>
          </el-tooltip>
        </div>
      </section>
    </nav>
    <p v-if="storageError" class="storage-error" role="alert">{{ storageError }}</p>
    <div class="sidebar-note"><span class="note-icon">✳</span><p>每一餐，都更懂你。<small>聊聊口味，让灵感慢慢发生。</small></p></div>
    <div class="identity">
      <span class="identity-avatar">我</span>
      <div><strong>本地演示访客</strong><small><el-icon><Lock /></el-icon> 此浏览器的演示身份</small></div>
      <el-tooltip :content="userId"><el-icon class="identity-info"><ArrowRight /></el-icon></el-tooltip>
    </div>
    <p class="identity-disclaimer">尚未接入身份认证，不提供真实用户隔离。</p>
    <el-dialog v-model="dialogOpen" title="重命名会话" width="420px" :close-on-click-modal="false" append-to-body>
      <label for="session-name" class="field-label">会话名称（仅保存在此浏览器）</label>
      <el-input id="session-name" v-model="name" maxlength="60" show-word-limit autofocus @keydown.enter="save" />
      <template #footer><el-button @click="dialogOpen = false">取消</el-button><el-button type="primary" :disabled="!name.trim()" @click="save">保存名称</el-button></template>
    </el-dialog>
  </aside>
</template>

<style scoped>
.sidebar { background: var(--sidebar-bg); border-right: 1px solid var(--line); display: flex; flex-direction: column; padding: 28px 17px 15px; min-height: 0; }
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--ink); padding: 0 7px; margin-bottom: 30px; }
.brand strong { font-size: 24px; letter-spacing: -.9px; font-weight: 750; }
.brand-dot { color: var(--orange); }
.brand small { display: block; font-size: 10px; color: var(--muted); letter-spacing: 1.6px; margin-top: 4px; }
.new-chat { width: 100%; height: 44px; border-radius: 11px; font-size: 13px; justify-content: flex-start; box-shadow: 0 4px 10px #b7663920; }
kbd { margin-left: auto; opacity: .6; font-size: 16px; font-family: inherit; }
.new-chat :deep(> span) { flex: 1; display: flex; align-items: center; }
.section-heading { display: flex; justify-content: space-between; padding: 30px 10px 12px; color: var(--muted); font-size: 11px; letter-spacing: 1px; }
.section-heading span:last-child { background: #eae8e0; border-radius: 5px; padding: 1px 5px; }
.session-list { flex: 1; overflow-y: auto; min-height: 50px; }
h2 { font-size: 10px; color: var(--muted); padding: 2px 11px; margin: 10px 0; font-weight: 400; }
.session-row { display: flex; align-items: center; border: 1px solid transparent; border-radius: 9px; margin: 4px 0; min-height: 42px; }
.session-row:hover { background: #edece5; }
.session-row.active { background: var(--orange-soft); border-color: #efd7c6; color: var(--orange-dark); }
.session-select { border: 0; background: none; color: inherit; text-align: left; display: flex; gap: 9px; align-items: center; min-width: 0; flex: 1; padding: 12px 8px 12px 10px; cursor: pointer; font-size: 12px; }
.session-select span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-select .el-icon { flex-shrink: 0; font-size: 15px; opacity: .7; }
.rename { flex-shrink: 0; width: 28px; height: 30px; margin-right: 3px; opacity: 0; }
.session-row:hover .rename, .session-row:focus-within .rename, .active .rename { opacity: .8; }
.sidebar-note { margin: 18px 4px 22px; display: flex; gap: 9px; align-items: flex-start; padding: 15px 10px; border-top: 1px solid var(--line); }
.note-icon { font-size: 28px; color: var(--sage); line-height: 1.2; }
.sidebar-note p { font-size: 11px; margin: 3px 0 0; color: #5b6557; }
.sidebar-note small { display: block; margin-top: 7px; font-size: 10px; color: var(--muted); }
.identity { display: flex; gap: 9px; align-items: center; padding: 0 6px; }
.identity-avatar { background: #e4e9de; color: var(--sage-dark); font-size: 12px; border-radius: 50%; width: 33px; height: 33px; display: grid; place-items: center; }
.identity strong { font-size: 11px; font-weight: 550; }
.identity small { display: flex; align-items: center; gap: 4px; font-size: 9px; margin-top: 5px; color: var(--muted); }
.identity-info { margin-left: auto; color: var(--muted); font-size: 11px; }
.identity-disclaimer { font-size: 9px; color: var(--muted); padding: 4px 5px 0; margin-bottom: 0; }
.storage-error { font-size: 11px; color: var(--danger); line-height: 1.6; }
.field-label { display: block; margin-bottom: 10px; color: var(--muted); font-size: 12px; }
@media (max-height: 800px) { .sidebar { padding-top: 22px; } .sidebar-note { margin-bottom: 8px; padding-top: 11px; padding-bottom: 8px; } }
</style>
