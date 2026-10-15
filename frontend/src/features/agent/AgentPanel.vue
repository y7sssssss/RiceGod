<script setup lang="ts">
import { ref } from 'vue'
import { Connection, InfoFilled } from '@element-plus/icons-vue'
import type { ChatResponse } from '../../types/api'
import SourceList from '../../components/SourceList.vue'
defineProps<{ response?: ChatResponse }>()
const expanded = ref(['route', 'sources'])
const routeNames: Record<string, string> = { recipe_query: '菜谱检索', food_knowledge: '饮食文化', general_query: '日常对话', additional_query: '补充信息' }
const statuses: Record<string, string> = { saved: '已保存', updated: '已更正', deleted: '已删除', unchanged: '保持不变', no_candidates: '没有待保存内容', unavailable: '服务不可用', partial: '部分完成', failed: '失败', skipped: '已跳过', needs_clarification: '需要澄清', not_found: '未找到', rejected: '已拒绝' }
const ms = (n: number) => `${(n / 1000).toFixed(2)} s`
</script>

<template>
  <div class="agent-panel">
    <div class="agent-intro"><h2>每一次回答，都有迹可循</h2><p>展示所选回答的真实后端执行信息。</p></div>
    <div v-if="!response" class="agent-empty"><div class="agent-empty-icon"><el-icon><Connection /></el-icon></div><strong>等待一次新的回答</strong><p>回答生成后，这里会展示实际路由、<br />检索来源与记忆使用情况。</p><small>历史消息接口不返回执行元数据，<br />刷新后不会重建或模拟执行信息。</small></div>
    <template v-else>
      <div v-if="response.performance" class="performance-summary"><span>本轮执行耗时</span><strong>{{ ms(response.performance.total_ms) }}</strong><small>后端 performance.total_ms</small></div>
      <el-collapse v-model="expanded">
        <el-collapse-item v-if="response.route || response.logic" name="route" title="任务路由">
          <div v-if="response.route" class="route-tags"><span v-for="route in response.route.split('+')" :key="route">{{ routeNames[route] || route }}</span></div>
          <code v-if="response.route" class="raw-route">{{ response.route }}</code><p v-if="response.logic" class="logic">{{ response.logic }}</p>
        </el-collapse-item>
        <el-collapse-item v-if="response.sources?.length" name="sources" :title="`检索来源 · ${response.sources.length}`"><SourceList :sources="response.sources" /></el-collapse-item>
        <el-collapse-item v-if="response.used_memories?.length" name="memories" :title="`使用的长期记忆 · ${response.used_memories.length}`">
          <div v-for="memory in response.used_memories" :key="memory.id" class="used-memory"><p>{{ memory.content }}</p><small>#{{ memory.id }} · {{ memory.type }}</small><small>记忆召回分数：{{ memory.score }}</small></div>
        </el-collapse-item>
        <el-collapse-item v-if="response.memory_write" name="write" title="记忆写入回执">
          <div class="receipt-status">{{ statuses[response.memory_write.status] || response.memory_write.status }}<code>{{ response.memory_write.status }}</code></div>
          <dl><div><dt>已保存</dt><dd>{{ response.memory_write.saved.length }}</dd></div><div><dt>未变化</dt><dd>{{ response.memory_write.unchanged.length }}</dd></div><div><dt>已删除</dt><dd>{{ response.memory_write.deleted_ids.length }}</dd></div><div><dt>失败 / 跳过</dt><dd>{{ response.memory_write.failed_count }} / {{ response.memory_write.skipped_count }}</dd></div><div><dt>提取状态</dt><dd>{{ response.memory_write.extraction_status }}</dd></div></dl>
          <p v-if="response.memory_write.operation?.message" class="logic">{{ response.memory_write.operation.message }}</p>
          <p v-if="response.memory_write.extraction_skip_reason" class="raw-value">跳过原因：{{ response.memory_write.extraction_skip_reason }}</p>
          <p v-if="response.memory_write.extraction_error" class="raw-value">提取错误：{{ response.memory_write.extraction_error }}</p>
          <details><summary>完整记忆回执</summary><pre>{{ JSON.stringify(response.memory_write, null, 2) }}</pre></details>
        </el-collapse-item>
        <el-collapse-item v-if="response.performance" name="performance" title="执行详情">
          <p class="trace">Trace · {{ response.performance.trace_id }}</p>
          <div v-for="(stage, i) in response.performance.stages" :key="i" class="stage"><div><code>{{ stage.name }}</code><span :class="{ 'stage-error': stage.status !== 'ok' }">{{ stage.status }}</span></div><small>{{ ms(stage.duration_ms) }}<span v-if="stage.task_id"> · {{ stage.task_id }}</span></small></div>
          <details v-if="response.performance.events?.length"><summary>后端事件（{{ response.performance.events.length }}）</summary><pre>{{ JSON.stringify(response.performance.events, null, 2) }}</pre></details>
        </el-collapse-item>
      </el-collapse>
      <p v-if="!response.sources?.length" class="field-note"><el-icon><InfoFilled /></el-icon>本轮未返回参考来源。</p>
    </template>
    <div class="agent-footnote">仅展示 API 已返回的数据。<br />任务阶段可能重叠，耗时不宜直接相加。</div>
  </div>
</template>

<style scoped>
.agent-panel { padding: 20px 23px 28px; }
h2 { font-size: 14px; font-weight: 550; margin: 0; }
.agent-intro p { font-size: 10px; color: var(--muted); line-height: 1.8; margin: 9px 0 22px; }
.agent-empty { padding: 30px 0; text-align: center; color: var(--muted); }
.agent-empty-icon { width: 60px; height: 60px; margin: 0 auto 20px; border: 1px dashed #c6cdbb; background: var(--sage-soft); display: grid; place-items: center; border-radius: 19px; color: var(--sage-dark); font-size: 25px; }
.agent-empty strong { display: block; color: var(--ink); font-size: 12px; font-weight: 500; }
.agent-empty p { font-size: 11px; line-height: 1.9; }
.agent-empty small { display: block; font-size: 10px; line-height: 1.9; margin-top: 18px; }
.performance-summary { background: var(--sage-soft); border: 1px solid #e0e5d8; border-radius: 12px; padding: 15px; margin-bottom: 20px; }
.performance-summary > span { font-size: 10px; color: var(--sage-dark); }
.performance-summary strong { display: block; font-size: 27px; font-weight: 500; color: var(--sage-dark); margin: 9px 0; font-variant-numeric: tabular-nums; }
.performance-summary small { color: var(--muted); font-size: 9px; }
:deep(.el-collapse) { border-color: var(--line); }
:deep(.el-collapse-item__header) { background: transparent; font-size: 11px; font-weight: 500; border-color: var(--line); }
:deep(.el-collapse-item__wrap) { background: transparent; border-color: var(--line); }
:deep(.el-collapse-item__content) { font-size: 11px; padding-bottom: 16px; color: var(--muted); }
.route-tags { display: flex; gap: 5px; flex-wrap: wrap; margin: 3px 0 10px; }
.route-tags span { color: var(--sage-dark); background: var(--sage-soft); border: 1px solid #e0e5d8; border-radius: 5px; padding: 3px 7px; font-size: 10px; }
.raw-route { color: var(--muted); font-size: 9px; overflow-wrap: anywhere; }
.logic { line-height: 1.9; overflow-wrap: anywhere; }
:deep(.sources) { border-top: 0; margin-top: 0; padding-top: 0; }
:deep(.sources-label) { display: none; }
.used-memory { background: #f4f5ef; border-radius: 9px; padding: 10px; margin: 7px 0; }
.used-memory p { margin: 0 0 6px; line-height: 1.8; overflow-wrap: anywhere; }
.used-memory small { display: block; font-size: 9px; overflow-wrap: anywhere; }
.receipt-status { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--sage-dark); font-size: 11px; }
.receipt-status code { font-size: 9px; color: var(--muted); }
dl { margin-bottom: 9px; } dl div { display: flex; justify-content: space-between; padding: 4px 0; gap: 10px; } dd { margin: 0; text-align: right; }
.raw-value { overflow-wrap: anywhere; font-size: 10px; }
.trace { font-size: 9px; overflow-wrap: anywhere; margin-top: 0; }
.stage { padding: 8px 0; border-bottom: 1px solid var(--line); }
.stage > div { display: flex; gap: 8px; justify-content: space-between; }
.stage code { overflow-wrap: anywhere; font-size: 10px; }
.stage > div > span { font-size: 9px; color: var(--sage-dark); }
.stage .stage-error { color: var(--danger); }
.stage small { font-size: 9px; }
details { margin-top: 13px; } summary { cursor: pointer; font-size: 10px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 9px; padding: 10px; background: #f1f0ea; border-radius: 7px; max-height: 350px; overflow: auto; }
.field-note { display: flex; align-items: center; gap: 5px; font-size: 10px; color: var(--muted); margin-top: 19px; }
.agent-footnote { border-top: 1px solid var(--line); margin-top: 23px; padding-top: 17px; font-size: 9px; line-height: 1.9; color: #a19c90; }
</style>
