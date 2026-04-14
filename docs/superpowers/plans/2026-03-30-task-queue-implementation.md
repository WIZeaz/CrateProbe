---
name: Task Queue Page Implementation Plan
description: Step-by-step implementation plan for the Queue page with priority management and batch cancel features
type: project
---

# Task Queue Page Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a new `/queue` page showing running and pending tasks, allowing users to pin tasks to front of queue and batch cancel running tasks.

**Architecture:** Add `priority` field to tasks table. Scheduler orders pending tasks by `priority DESC, created_at ASC`. New API endpoints for batch priority update and batch cancel. Vue 3 frontend with unified table showing running tasks first, then pending by priority.

**Tech Stack:** Python/FastAPI backend, SQLite database, Vue 3 + Vite frontend, Tailwind CSS, WebSocket for real-time updates.

---

## File Structure

**Backend:**
- `backend/app/database.py` - Add priority column migration, TaskRecord field, get_pending_tasks_ordered() method
- `backend/app/main.py` - Add batch-priority and batch-cancel endpoints, update response models
- `backend/app/services/scheduler.py` - Update schedule_tasks() to use priority ordering

**Frontend:**
- `frontend/src/views/TaskQueue.vue` - New queue page component
- `frontend/src/services/api.js` - Add batchSetPriority, batchCancel, getQueue methods
- `frontend/src/router/index.js` - Add /queue route
- `frontend/src/App.vue` - Add Queue navigation link

**Tests:**
- `backend/tests/unit/test_database.py` - Test priority ordering
- `backend/tests/unit/test_scheduler.py` - Test scheduler uses priority
- `backend/tests/integration/test_queue_api.py` - Test new API endpoints

---

## Chunk 1: Database Layer

### Task 1: Add priority column to database schema

**Files:**
- Modify: `backend/app/database.py:80-94` (init_db migrations section)

**Context:** The database already has migration logic in `init_db()` that adds missing columns. Follow the existing pattern for `message` and `compile_failed` columns.

- [ ] **Step 1: Add priority column migration**

```python
# In init_db(), after the compile_failed migration:
if "priority" not in columns:
    cursor.execute("ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 0")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pending_priority
        ON tasks(status, priority DESC, created_at ASC)
    """)
```

- [ ] **Step 2: Add priority field to TaskRecord dataclass**

```python
# In TaskRecord dataclass, after compile_failed:
priority: Optional[int] = None
```

- [ ] **Step 3: Update _row_to_task_record to include priority**

```python
# In _row_to_task_record(), add to TaskRecord constructor:
priority=row["priority"],
```

- [ ] **Step 4: Add get_pending_tasks_ordered() method**

```python
def get_pending_tasks_ordered(self) -> List[TaskRecord]:
    """Get pending tasks ordered by priority (high first), then creation time."""
    cursor = self.conn.cursor()
    cursor.execute(
        "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at ASC",
        (TaskStatus.PENDING.value,)
    )
    rows = cursor.fetchall()
    return [self._row_to_task_record(row) for row in rows]
```

- [ ] **Step 5: Add update_task_priority() method**

```python
def update_task_priority(self, task_id: int, priority: int):
    """Update task priority.

    Args:
        task_id: Task ID to update
        priority: Priority value (higher = earlier execution)
    """
    cursor = self.conn.cursor()
    cursor.execute(
        "UPDATE tasks SET priority = ? WHERE id = ?",
        (priority, task_id)
    )
    self.conn.commit()
```

- [ ] **Step 6: Commit database changes**

```bash
cd /home/wizeaz/exp-plat
git add backend/app/database.py
git commit -m "feat(database): add priority column and ordering support

- Add priority column migration in init_db()
- Add priority field to TaskRecord
- Add get_pending_tasks_ordered() for scheduler
- Add update_task_priority() for batch operations

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 2: Scheduler Integration

### Task 2: Update scheduler to use priority ordering

**Files:**
- Modify: `backend/app/services/scheduler.py:39-43`

**Context:** The scheduler currently uses `get_tasks_by_status(TaskStatus.PENDING)` which returns tasks in creation order. We need to switch to the new priority-aware method.

- [ ] **Step 1: Update schedule_tasks() to use priority ordering**

```python
# Replace line 40:
# pending = self.db.get_tasks_by_status(TaskStatus.PENDING)
# With:
pending = self.db.get_pending_tasks_ordered()
```

- [ ] **Step 2: Commit scheduler changes**

```bash
git add backend/app/services/scheduler.py
git commit -m "feat(scheduler): use priority ordering for pending tasks

- Replace get_tasks_by_status() with get_pending_tasks_ordered()
- Pinned tasks (priority=100) now execute before normal tasks

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 3: Backend API Endpoints

### Task 3: Update TaskDetailResponse and converter functions

**Files:**
- Modify: `backend/app/main.py:37-50` (TaskDetailResponse)
- Modify: `backend/app/main.py:432-448` (_task_to_dict)
- Modify: `backend/app/main.py:451-467` (_task_to_response)

- [ ] **Step 1: Add priority to TaskDetailResponse**

```python
class TaskDetailResponse(BaseModel):
    id: int
    crate_name: str
    version: str
    status: str
    exit_code: Optional[int]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    case_count: int
    poc_count: int
    error_message: Optional[str]
    message: Optional[str]
    compile_failed: Optional[int]
    priority: Optional[int]  # NEW
```

- [ ] **Step 2: Add priority to _task_to_dict**

```python
def _task_to_dict(task: TaskRecord) -> dict:
    return {
        # ... existing fields ...
        "compile_failed": task.compile_failed,
        "priority": task.priority,  # NEW
    }
```

- [ ] **Step 3: Add priority to _task_to_response**

```python
def _task_to_response(task: TaskRecord) -> TaskDetailResponse:
    return TaskDetailResponse(
        # ... existing fields ...
        compile_failed=task.compile_failed,
        priority=task.priority,  # NEW
    )
```

### Task 4: Add batch-priority API endpoint

**Files:**
- Modify: `backend/app/main.py` (add after batch_delete_tasks)

- [ ] **Step 4: Add BatchPriorityRequest model**

```python
class BatchPriorityRequest(BaseModel):
    task_ids: List[int]
    priority: int
```

- [ ] **Step 5: Add batch_set_priority endpoint**

```python
@app.post("/api/tasks/batch-priority")
async def batch_set_priority(request: BatchPriorityRequest):
    """Batch set priority on pending tasks. Skips non-pending tasks."""
    results = {"updated": [], "skipped": [], "not_found": []}

    for task_id in request.task_ids:
        task = db.get_task(task_id)
        if not task:
            results["not_found"].append(task_id)
        elif task.status != TaskStatus.PENDING:
            results["skipped"].append(task_id)
        else:
            db.update_task_priority(task_id, request.priority)
            results["updated"].append(task_id)

    return results
```

### Task 5: Add batch-cancel API endpoint

- [ ] **Step 6: Add batch_cancel endpoint**

```python
@app.post("/api/tasks/batch-cancel")
async def batch_cancel_tasks(request: BatchTaskRequest):
    """Batch cancel running tasks. Skips non-running tasks."""
    results = {"cancelled": [], "skipped": [], "not_found": []}

    for task_id in request.task_ids:
        task = db.get_task(task_id)
        if not task:
            results["not_found"].append(task_id)
        elif task.status != TaskStatus.RUNNING:
            results["skipped"].append(task_id)
        else:
            await scheduler.cancel_task(task_id)
            results["cancelled"].append(task_id)

    return results
```

### Task 6: Add queue API endpoint

- [ ] **Step 7: Add get_queue endpoint**

```python
@app.get("/api/queue")
async def get_queue():
    """Get queue state: running tasks and pending tasks ordered by priority."""
    running = db.get_tasks_by_status(TaskStatus.RUNNING)
    pending = db.get_pending_tasks_ordered()

    return {
        "running": [_task_to_dict(t) for t in running],
        "pending": [_task_to_dict(t) for t in pending]
    }
```

- [ ] **Step 8: Commit API changes**

```bash
git add backend/app/main.py
git commit -m "feat(api): add queue endpoints and priority support

- Add priority field to TaskDetailResponse
- Add POST /api/tasks/batch-priority endpoint
- Add POST /api/tasks/batch-cancel endpoint
- Add GET /api/queue endpoint

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 4: Frontend API Service

### Task 7: Add API methods

**Files:**
- Modify: `frontend/src/services/api.js` (add after batchDelete)

- [ ] **Step 1: Add batchSetPriority method**

```javascript
async batchSetPriority(taskIds, priority) {
  const response = await api.post('/tasks/batch-priority', { task_ids: taskIds, priority })
  return response.data
},
```

- [ ] **Step 2: Add batchCancel method**

```javascript
async batchCancel(taskIds) {
  const response = await api.post('/tasks/batch-cancel', { task_ids: taskIds })
  return response.data
},
```

- [ ] **Step 3: Add getQueue method**

```javascript
async getQueue() {
  const response = await api.get('/queue')
  return response.data
},
```

- [ ] **Step 4: Commit API service changes**

```bash
git add frontend/src/services/api.js
git commit -m "feat(api): add queue and batch operation methods

- Add batchSetPriority() for pinning tasks
- Add batchCancel() for cancelling running tasks
- Add getQueue() for fetching queue state

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 5: Frontend Routing and Navigation

### Task 8: Add queue route

**Files:**
- Modify: `frontend/src/router/index.js` (add before tasks routes)

- [ ] **Step 1: Add /queue route**

```javascript
{
  path: '/queue',
  name: 'TaskQueue',
  component: () => import('../views/TaskQueue.vue')
},
```

### Task 9: Add navigation link

**Files:**
- Modify: `frontend/src/App.vue:28-38` (after Dashboard link, before Tasks link)

- [ ] **Step 2: Add Queue navigation link**

```vue
<router-link
  to="/queue"
  class="inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
  :class="route.path === '/queue'
    ? 'border-blue-500 text-gray-900'
    : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'"
>
  Queue
</router-link>
```

- [ ] **Step 3: Commit routing changes**

```bash
git add frontend/src/router/index.js frontend/src/App.vue
git commit -m "feat(router): add /queue route and navigation

- Add TaskQueue route to router
- Add Queue link in main navigation

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 6: TaskQueue.vue Component

### Task 10: Create TaskQueue.vue

**Files:**
- Create: `frontend/src/views/TaskQueue.vue`

**Context:** This is the main queue page. It shows running tasks first, then pending tasks ordered by priority. Users can select tasks and perform batch operations.

- [ ] **Step 1: Create TaskQueue.vue component**

```vue
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import api from '../services/api'
import websocket from '../services/websocket'

const runningTasks = ref([])
const pendingTasks = ref([])
const selectedRunningIds = ref(new Set())
const selectedPendingIds = ref(new Set())
const loading = ref(true)
const error = ref(null)

const runningSelectedCount = computed(() => selectedRunningIds.value.size)
const pendingSelectedCount = computed(() => selectedPendingIds.value.size)

const allRunningSelected = computed(() => {
  return runningTasks.value.length > 0 && runningTasks.value.every(t => selectedRunningIds.value.has(t.id))
})

const someRunningSelected = computed(() => {
  return selectedRunningIds.value.size > 0 && !allRunningSelected.value
})

const allPendingSelected = computed(() => {
  return pendingTasks.value.length > 0 && pendingTasks.value.every(t => selectedPendingIds.value.has(t.id))
})

const somePendingSelected = computed(() => {
  return selectedPendingIds.value.size > 0 && !allPendingSelected.value
})

function toggleSelectAllRunning() {
  if (allRunningSelected.value) {
    selectedRunningIds.value = new Set()
  } else {
    selectedRunningIds.value = new Set(runningTasks.value.map(t => t.id))
  }
}

function toggleSelectAllPending() {
  if (allPendingSelected.value) {
    selectedPendingIds.value = new Set()
  } else {
    selectedPendingIds.value = new Set(pendingTasks.value.map(t => t.id))
  }
}

function toggleRunningSelect(taskId) {
  const next = new Set(selectedRunningIds.value)
  if (next.has(taskId)) {
    next.delete(taskId)
  } else {
    next.add(taskId)
  }
  selectedRunningIds.value = next
}

function togglePendingSelect(taskId) {
  const next = new Set(selectedPendingIds.value)
  if (next.has(taskId)) {
    next.delete(taskId)
  } else {
    next.add(taskId)
  }
  selectedPendingIds.value = next
}

async function fetchQueue() {
  try {
    const data = await api.getQueue()
    runningTasks.value = data.running
    pendingTasks.value = data.pending
    loading.value = false

    // Clean up selections for tasks that no longer exist
    const runningIds = new Set(runningTasks.value.map(t => t.id))
    const pendingIds = new Set(pendingTasks.value.map(t => t.id))
    selectedRunningIds.value = new Set([...selectedRunningIds.value].filter(id => runningIds.has(id)))
    selectedPendingIds.value = new Set([...selectedPendingIds.value].filter(id => pendingIds.has(id)))
  } catch (err) {
    error.value = err.message
    loading.value = false
  }
}

async function handlePinSelected() {
  const ids = [...selectedPendingIds.value]
  if (ids.length === 0) return

  try {
    const result = await api.batchSetPriority(ids, 100)
    selectedPendingIds.value = new Set()
    await fetchQueue()
    if (result.skipped?.length > 0) {
      alert(`Pinned ${result.updated.length} task(s). Skipped ${result.skipped.length} non-pending task(s).`)
    }
  } catch (err) {
    alert(`Failed to pin tasks: ${err.message}`)
  }
}

async function handleCancelSelected() {
  const ids = [...selectedRunningIds.value]
  if (ids.length === 0) return

  if (!confirm(`Cancel ${ids.length} running task(s)?`)) return

  try {
    const result = await api.batchCancel(ids)
    selectedRunningIds.value = new Set()
    await fetchQueue()
    if (result.skipped?.length > 0) {
      alert(`Cancelled ${result.cancelled.length} task(s). Skipped ${result.skipped.length} non-running task(s).`)
    }
  } catch (err) {
    alert(`Failed to cancel tasks: ${err.message}`)
  }
}

function formatDuration(startStr, endStr) {
  if (!startStr) return 'N/A'
  const start = new Date(startStr)
  const end = endStr ? new Date(endStr) : new Date()
  const diff = Math.floor((end - start) / 1000)

  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`
  const hours = Math.floor(diff / 3600)
  const minutes = Math.floor((diff % 3600) / 60)
  return `${hours}h ${minutes}m`
}

function getOrdinalSuffix(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return s[(v - 20) % 10] || s[v] || s[0]
}

function getQueuePosition(task, index) {
  if (task.priority > 0) return '⭐ 置顶'
  return `${index + 1}${getOrdinalSuffix(index + 1)}`
}

onMounted(() => {
  fetchQueue()
  websocket.on('task_update', fetchQueue)
  websocket.on('task_created', fetchQueue)
  websocket.on('task_completed', fetchQueue)
})

onUnmounted(() => {
  websocket.off('task_update', fetchQueue)
  websocket.off('task_created', fetchQueue)
  websocket.off('task_completed', fetchQueue)
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Task Queue</h1>
      <div class="flex gap-3">
        <button
          v-if="pendingSelectedCount > 0"
          @click="handlePinSelected"
          class="px-4 py-2 text-sm font-medium text-white bg-orange-500 rounded-lg hover:bg-orange-600 transition-colors"
        >
          置顶 Selected ({{ pendingSelectedCount }})
        </button>
        <button
          v-if="runningSelectedCount > 0"
          @click="handleCancelSelected"
          class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors"
        >
          Cancel Selected ({{ runningSelectedCount }})
        </button>
      </div>
    </div>

    <div class="mb-4 flex items-center justify-between text-sm text-gray-600">
      <span>{{ runningTasks.length }} Running | {{ pendingTasks.length }} Pending</span>
    </div>

    <div v-if="loading" class="flex justify-center py-12">
      <div class="spinner border-blue-500"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
      Failed to load queue: {{ error }}
    </div>

    <div v-else-if="runningTasks.length === 0 && pendingTasks.length === 0" class="bento-card text-center py-12">
      <p class="text-gray-500">No tasks in queue.</p>
    </div>

    <div v-else class="bento-card overflow-x-auto">
      <table class="min-w-full divide-y divide-gray-200">
        <thead>
          <tr>
            <th class="px-4 py-3 text-left">
              <input
                v-if="runningTasks.length > 0"
                type="checkbox"
                :checked="allRunningSelected"
                :indeterminate="someRunningSelected"
                @change="toggleSelectAllRunning"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">ID</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Crate</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Version</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Queue Position</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Runtime</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-200">
          <!-- Running Tasks Section -->
          <tr v-if="runningTasks.length > 0" class="bg-green-50">
            <td colspan="7" class="px-4 py-2 text-sm font-semibold text-green-800">
              ▶ Running Tasks ({{ runningTasks.length }})
            </td>
          </tr>
          <tr v-for="task in runningTasks" :key="task.id" class="hover:bg-gray-50 transition-colors">
            <td class="px-4 py-3 whitespace-nowrap">
              <input
                type="checkbox"
                :checked="selectedRunningIds.has(task.id)"
                @change="toggleRunningSelect(task.id)"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">#{{ task.id }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{{ task.crate_name }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ task.version }}</td>
            <td class="px-4 py-3 whitespace-nowrap">
              <span class="status-badge status-running">{{ task.status }}</span>
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">-</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ formatDuration(task.started_at, task.finished_at) }}</td>
          </tr>

          <!-- Pending Tasks Section -->
          <tr v-if="pendingTasks.length > 0" class="bg-orange-50">
            <td colspan="7" class="px-4 py-2 text-sm font-semibold text-orange-800">
              ⏳ Pending Queue ({{ pendingTasks.length }})
            </td>
          </tr>
          <tr v-for="(task, idx) in pendingTasks" :key="task.id" class="hover:bg-gray-50 transition-colors" :class="{ 'bg-yellow-50': task.priority > 0 }">
            <td class="px-4 py-3 whitespace-nowrap">
              <input
                type="checkbox"
                :checked="selectedPendingIds.has(task.id)"
                @change="togglePendingSelect(task.id)"
                class="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">#{{ task.id }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{{ task.crate_name }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ task.version }}</td>
            <td class="px-4 py-3 whitespace-nowrap">
              <span class="status-badge status-pending">{{ task.status }}</span>
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium" :class="task.priority > 0 ? 'text-orange-600' : 'text-gray-500'">
              {{ getQueuePosition(task, idx) }}
            </td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">-</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
```

- [ ] **Step 2: Commit TaskQueue.vue**

```bash
git add frontend/src/views/TaskQueue.vue
git commit -m "feat(frontend): add TaskQueue page component

- Display running tasks first, then pending by priority
- Multi-select with batch pin (置顶) and cancel actions
- Real-time updates via WebSocket
- Visual distinction for pinned tasks

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Testing Instructions

### Manual Testing

1. **Start the backend:**
   ```bash
   cd backend && uv run python -m app.main
   ```

2. **Start the frontend:**
   ```bash
   cd frontend && npm run dev
   ```

3. **Test the queue page:**
   - Open http://localhost:5173/queue
   - Create several tasks via /tasks/new
   - Verify pending tasks appear in the queue
   - Select some pending tasks and click "置顶 Selected"
   - Verify pinned tasks show ⭐ and move to top of pending section
   - Start a task running (or wait for one to start)
   - Select running tasks and click "Cancel Selected"
   - Verify confirmation dialog appears and tasks are cancelled

### Unit Tests (to be implemented)

```python
# tests/unit/test_database.py
def test_get_pending_tasks_ordered_by_priority(db):
    # Create tasks with different priorities
    # Verify high priority tasks come first

def test_update_task_priority(db):
    # Update task priority
    # Verify change persisted

# tests/unit/test_scheduler.py
def test_scheduler_uses_priority_order(scheduler, db):
    # Mock available slots
    # Verify scheduler picks high priority tasks first
```

---

## Plan Review

Ready for review. Each chunk is self-contained and can be implemented independently.

**Execution order:**
1. Chunk 1: Database Layer
2. Chunk 2: Scheduler Integration
3. Chunk 3: Backend API Endpoints
4. Chunk 4: Frontend API Service
5. Chunk 5: Frontend Routing and Navigation
6. Chunk 6: TaskQueue.vue Component

