# TaskList 导出 XLSX 功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 TaskList 页面添加导出按钮，将所有任务数据下载为 `.xlsx` 文件。

**Architecture：** 前端使用 `xlsx`（SheetJS）库在浏览器端生成 xlsx workbook 并触发下载。新增一个工具函数模块处理数据转换与文件生成，TaskList.vue 负责 UI 按钮与调用。零后端改动。

**Tech Stack：** Vue 3, Vite, SheetJS (`xlsx` npm 包)

---

### Task 1: 安装 xlsx 依赖

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: 安装依赖**

```bash
cd /home/wizeaz/exp-plat/frontend
npm install xlsx
```

Expected: `package.json` 中新增 `"xlsx": "^0.18.5"`（或类似版本），`package-lock.json` 同步更新。

- [ ] **Step 2: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "deps(frontend): add xlsx for excel export"
```

---

### Task 2: 创建 exportXlsx 工具函数

**Files:**
- Create: `frontend/src/utils/exportXlsx.js`

- [ ] **Step 1: 编写工具函数**

创建 `frontend/src/utils/exportXlsx.js`：

```javascript
import * as XLSX from 'xlsx'

function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return ''
  return d.toISOString().replace('T', ' ').slice(0, 19)
}

function getRuntimeSeconds(startStr, endStr) {
  if (!startStr) return ''
  const start = new Date(startStr)
  const end = endStr ? new Date(endStr) : new Date()
  const seconds = Math.floor((end - start) / 1000)
  return seconds
}

export function exportTasksToXlsx(tasks) {
  if (!tasks || tasks.length === 0) {
    throw new Error('No tasks to export')
  }

  const data = tasks.map((task) => ({
    ID: task.id,
    Crate: task.crate_name,
    Version: task.version,
    Status: task.status,
    Cases: task.case_count,
    'POCs': task.poc_count,
    'Compile Failed': task.compile_failed ?? '',
    'Runtime (s)': getRuntimeSeconds(task.started_at, task.finished_at),
    Runner: task.runner_id || '',
    'Created At': formatDate(task.created_at),
    'Started At': formatDate(task.started_at),
    'Finished At': formatDate(task.finished_at),
  }))

  const ws = XLSX.utils.json_to_sheet(data)

  // 设置列宽
  const colWidths = [
    { wch: 8 },   // ID
    { wch: 24 },  // Crate
    { wch: 12 },  // Version
    { wch: 12 },  // Status
    { wch: 8 },   // Cases
    { wch: 8 },   // POCs
    { wch: 16 },  // Compile Failed
    { wch: 14 },  // Runtime (s)
    { wch: 20 },  // Runner
    { wch: 20 },  // Created At
    { wch: 20 },  // Started At
    { wch: 20 },  // Finished At
  ]
  ws['!cols'] = colWidths

  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'Tasks')

  const now = new Date()
  const timestamp = now.toISOString().slice(0, 19).replace(/[-T:]/g, '')
  const filename = `tasks_${timestamp.slice(0, 8)}_${timestamp.slice(8)}.xlsx`

  XLSX.writeFile(wb, filename)
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/utils/exportXlsx.js
git commit -m "feat(frontend): add exportTasksToXlsx utility"
```

---

### Task 3: 在 TaskList.vue 中添加导出按钮

**Files:**
- Modify: `frontend/src/views/TaskList.vue`

- [ ] **Step 1: 导入工具函数**

在 `frontend/src/views/TaskList.vue` 的 `<script setup>` 顶部添加导入：

```javascript
import { exportTasksToXlsx } from '../utils/exportXlsx'
```

放在第 9 行 `import { filterTasksByCrateName } from './taskListFilters'` 之后。

- [ ] **Step 2: 添加导出处理函数**

在 `<script setup>` 中 `handleBatchDelete` 函数之后（约第 224 行）添加：

```javascript
function handleExportXlsx() {
  if (tasks.value.length === 0) {
    alert('No tasks to export')
    return
  }
  try {
    exportTasksToXlsx(tasks.value)
  } catch (err) {
    alert(`Export failed: ${err.message}`)
  }
}
```

- [ ] **Step 3: 添加导出按钮到模板**

在模板标题栏按钮区域（约第 251 行的 `<div class="flex gap-3">` 内），在 "Batch Create" 按钮之前添加：

```html
        <button
          @click="handleExportXlsx"
          :disabled="loading || tasks.length === 0"
          class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Export XLSX
        </button>
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/TaskList.vue
git commit -m "feat(frontend): add export xlsx button to TaskList"
```

---

### Task 4: 验证功能

- [ ] **Step 1: 启动开发服务器并验证**

```bash
cd /home/wizeaz/exp-plat/frontend
npm run dev
```

打开 http://localhost:5173/tasks，确认：
1. 页面加载后右上角出现 "Export XLSX" 按钮
2. 按钮样式与描述一致（灰色边框）
3. 点击按钮后浏览器下载 `.xlsx` 文件
4. 文件内容包含全部任务数据，列头与字段正确
5. 无任务时按钮禁用

- [ ] **Step 2: 最终 Commit（如需调整）**

如有调整，commit 后结束。

---

## 自查

**1. Spec coverage:**
- 导出全部任务数据 ✅ Task 2 使用 `tasks.value`
- 导出字段与表头 ✅ Task 2 映射完整
- 文件名带时间戳 ✅ Task 2 `filename`
- UI 按钮位置与样式 ✅ Task 3
- 错误处理（无任务禁用、异常 alert）✅ Task 2 & Task 3

**2. Placeholder scan:** 无 TBD/TODO，所有步骤含完整代码。

**3. Type consistency:** 任务对象字段名与 TaskList.vue 现有使用一致（`crate_name`, `case_count`, `poc_count`, `compile_failed`, `runner_id` 等）。
