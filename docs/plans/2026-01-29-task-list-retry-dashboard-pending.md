# 任务列表重试操作与Dashboard待处理任务卡片实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在任务列表添加重试按钮，在Dashboard添加待处理任务卡片并为所有统计卡片添加点击跳转功能

**Architecture:**
- 修改TaskList.vue添加重试按钮，复用现有retryTask API
- 修改StatCard.vue支持可选的点击回调
- 修改Dashboard.vue添加第5张卡片并为所有卡片添加点击处理
- 修改TaskList.vue支持URL参数解析自动过滤

**Tech Stack:** Vue 3 Composition API, Vue Router, Axios

---

## Task 1: 修改StatCard组件支持点击

**Files:**
- Modify: `frontend/src/components/StatCard.vue`

**Step 1: 添加clickable属性和点击事件**

在StatCard.vue的script部分添加clickable属性：

```vue
<script setup>
defineProps({
  title: String,
  value: [String, Number],
  icon: String,
  color: {
    type: String,
    default: 'blue'
  },
  clickable: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['click'])

const colorClasses = {
  blue: 'bg-blue-50 text-blue-600',
  green: 'bg-green-50 text-green-600',
  red: 'bg-red-50 text-red-600',
  yellow: 'bg-yellow-50 text-yellow-600',
  purple: 'bg-purple-50 text-purple-600',
  gray: 'bg-gray-50 text-gray-600'
}

function handleClick() {
  if (clickable) {
    emit('click')
  }
}
</script>
```

**Step 2: 修改template支持点击样式**

修改template部分添加点击样式和事件处理：

```vue
<template>
  <div
    :class="['bento-card', { 'cursor-pointer hover:shadow-lg transition-shadow': clickable }]"
    @click="handleClick"
  >
    <div class="flex items-center justify-between">
      <div>
        <p class="text-sm font-medium text-gray-600">{{ title }}</p>
        <p class="mt-2 text-3xl font-bold text-gray-900">{{ value }}</p>
      </div>
      <div :class="['p-3 rounded-lg', colorClasses[color]]" v-if="icon">
        <span class="text-2xl">{{ icon }}</span>
      </div>
    </div>
  </div>
</template>
```

**Step 3: 提交修改**

```bash
cd /home/wizeaz/exp-plat/.worktrees/task-list-retry-dashboard-pending
git add frontend/src/components/StatCard.vue
git commit -m "feat: add clickable support to StatCard component

- Add clickable prop to enable click interactions
- Add click event emission
- Add hover styles when clickable

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: 修改Dashboard添加Pending卡片和点击跳转

**Files:**
- Modify: `frontend/src/views/Dashboard.vue`

**Step 1: 修改stats网格为5列**

在Dashboard.vue的template中，找到stats grid部分（约第119行），修改grid样式：

```vue
<!-- Stats Grid -->
<div class="bento-grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 mb-8">
```

**Step 2: 添加卡片点击处理函数**

在Dashboard.vue的script部分添加navigateToTasks函数（在formatDuration函数之后）：

```javascript
function navigateToTasks(status = 'all') {
  if (status === 'all') {
    router.push('/tasks')
  } else {
    router.push(`/tasks?status=${status}`)
  }
}
```

**Step 3: 修改现有的4张卡片添加点击支持**

修改现有的StatCard组件调用，添加clickable和@click：

```vue
<StatCard
  title="Total Tasks"
  :value="dashboard.total_tasks"
  icon="📊"
  color="blue"
  :clickable="true"
  @click="navigateToTasks('all')"
/>
<StatCard
  title="Running"
  :value="dashboard.running_tasks"
  icon="▶️"
  color="yellow"
  :clickable="true"
  @click="navigateToTasks('running')"
/>
<StatCard
  title="Completed"
  :value="dashboard.completed_tasks"
  icon="✅"
  color="green"
  :clickable="true"
  @click="navigateToTasks('completed')"
/>
<StatCard
  title="Failed"
  :value="dashboard.failed_tasks"
  icon="❌"
  color="red"
  :clickable="true"
  @click="navigateToTasks('failed')"
/>
```

**Step 4: 添加第5张Pending卡片**

在Failed卡片之后添加：

```vue
<StatCard
  title="Pending"
  :value="dashboard.pending_tasks || 0"
  icon="⏳"
  color="purple"
  :clickable="true"
  @click="navigateToTasks('pending')"
/>
```

**Step 5: 提交修改**

```bash
git add frontend/src/views/Dashboard.vue
git commit -m "feat: add pending tasks card and click navigation to Dashboard

- Change stats grid from 4 to 5 columns
- Add pending tasks stat card with purple theme
- Add click handlers to all stat cards for filtered navigation
- Clicking cards navigates to task list with status filter

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: 修改TaskList支持URL参数过滤

**Files:**
- Modify: `frontend/src/views/TaskList.vue`

**Step 1: 在onMounted中添加URL参数解析**

在TaskList.vue的script部分，修改onMounted函数（约第112行）：

```javascript
onMounted(() => {
  // Parse URL query parameter for status filter
  const statusParam = route.query.status
  if (statusParam && statusOptions.find(opt => opt.value === statusParam)) {
    filterStatus.value = statusParam
  }

  fetchTasks()
  websocket.on('task_update', handleTaskUpdate)
  websocket.on('task_created', handleTaskUpdate)
  websocket.on('task_completed', handleTaskUpdate)
})
```

**Step 2: 提交修改**

```bash
git add frontend/src/views/TaskList.vue
git commit -m "feat: support URL query parameter for status filtering

- Parse status query param on mount
- Auto-set filter when navigating from Dashboard
- Validate param against available status options

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: 在TaskList添加重试按钮

**Files:**
- Modify: `frontend/src/views/TaskList.vue`

**Step 1: 添加重试处理函数**

在TaskList.vue的script部分，在handleDelete函数之后添加handleRetry函数（约第110行之后）：

```javascript
async function handleRetry(task) {
  if (!confirm(`重试任务 #${task.id} (${task.crate_name} ${task.version})?\n\n这将重置任务并重新执行。`)) {
    return
  }

  try {
    await api.retryTask(task.id)
    // Refresh task list to show updated status
    fetchTasks()
  } catch (err) {
    alert(`重试任务失败: ${err.message}`)
  }
}
```

**Step 2: 修改Actions列显示重试和删除按钮**

在template中找到Actions列的td元素（约第273-285行），替换为：

```vue
<td class="px-4 py-3 whitespace-nowrap text-right text-sm font-medium">
  <div class="flex items-center justify-end gap-2">
    <button
      v-if="task.status !== 'running'"
      @click.stop="handleRetry(task)"
      class="text-green-600 hover:text-green-900 transition-colors"
      title="重试任务"
    >
      🔄 Retry
    </button>
    <button
      v-if="task.status !== 'running'"
      @click.stop="handleDelete(task)"
      class="text-red-600 hover:text-red-900 transition-colors"
      title="删除任务"
    >
      🗑️ Delete
    </button>
    <span v-if="task.status === 'running'" class="text-gray-400" title="任务运行中">
      —
    </span>
  </div>
</td>
```

**Step 3: 提交修改**

```bash
git add frontend/src/views/TaskList.vue
git commit -m "feat: add retry button to task list actions

- Add retry handler function with confirmation dialog
- Display retry button for non-running tasks
- Show both retry and delete buttons in actions column
- Refresh task list after successful retry

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: 手动测试验证

**Step 1: 启动开发服务器**

```bash
# Terminal 1: 启动backend
cd /home/wizeaz/exp-plat/.worktrees/task-list-retry-dashboard-pending/backend
uv run uvicorn app.main:app --reload --port 8000

# Terminal 2: 启动frontend
cd /home/wizeaz/exp-plat/.worktrees/task-list-retry-dashboard-pending/frontend
npm run dev
```

**Step 2: 测试Dashboard卡片点击**

1. 访问 http://localhost:5173/
2. 检查是否显示5张统计卡片（Total, Running, Completed, Failed, Pending）
3. 鼠标悬停在卡片上，确认有hover效果（阴影增强）
4. 点击Pending卡片，确认跳转到 /tasks?status=pending
5. 确认任务列表自动过滤为pending状态
6. 依次测试其他卡片的点击跳转

**Step 3: 测试任务列表重试按钮**

1. 访问 http://localhost:5173/tasks
2. 找到一个非running状态的任务
3. 确认Actions列显示 "🔄 Retry" 和 "🗑️ Delete" 两个按钮
4. 点击Retry按钮，确认弹出确认对话框
5. 确认对话框，观察任务是否被重置并重新执行
6. 检查running状态的任务是否只显示 "—"

**Step 4: 测试响应式布局**

1. 调整浏览器窗口大小
2. 确认Dashboard在小屏幕上统计卡片正确堆叠（1列 → 2列 → 5列）
3. 确认TaskList在小屏幕上正确显示

**Step 5: 记录测试结果**

如果所有测试通过，继续下一步。如果有问题，记录并修复。

---

## Task 6: 最终提交和清理

**Step 1: 检查所有修改**

```bash
cd /home/wizeaz/exp-plat/.worktrees/task-list-retry-dashboard-pending
git log --oneline -6
git diff master
```

**Step 2: 验证所有文件已提交**

```bash
git status
```

应该显示 "working tree clean"

**Step 3: 推送分支（如果需要）**

```bash
git push -u origin feature/task-list-retry-dashboard-pending
```

---

## 实现完成检查清单

- [ ] StatCard组件支持clickable和点击事件
- [ ] Dashboard显示5张统计卡片（包括Pending）
- [ ] Dashboard所有卡片支持点击跳转
- [ ] TaskList支持URL参数自动过滤
- [ ] TaskList Actions列显示重试和删除按钮
- [ ] 重试按钮仅对非running任务显示
- [ ] 重试功能正常工作（确认对话框、API调用、列表刷新）
- [ ] 所有功能手动测试通过
- [ ] 响应式布局正常工作
- [ ] 所有修改已提交到git

---

## 注意事项

1. **DRY原则**: 复用现有的api.retryTask()方法，不重复实现
2. **YAGNI原则**: 只实现需求中的功能，不添加额外特性
3. **用户体验**: 确认对话框提供清晰的操作说明
4. **错误处理**: 所有API调用都有try-catch和用户友好的错误提示
5. **代码风格**: 遵循现有代码的风格和命名约定
