---
name: Task Queue Management Page Design
description: Queue page for viewing and managing task execution order with priority (置顶) and batch cancel features
type: project
---

# Task Queue Management Page Design

**Date:** 2026-03-30
**Status:** Approved for implementation

## Overview

Add a new `/queue` page that displays the task execution queue, showing running tasks first, followed by pending tasks ordered by priority. Users can multi-select tasks to:
1. **置顶 (Pin to top)**: Set high priority on pending tasks so they execute next
2. **Cancel**: Batch cancel running tasks (with confirmation)

## Motivation

Currently, the Task List page shows all tasks but doesn't provide a clear view of the execution queue or the ability to influence execution order. Users need a dedicated queue management interface to:
- See which task is running and what's queued next
- Prioritize urgent tasks by pinning them to the front of the queue
- Cancel multiple running tasks efficiently

## Design

### UI Layout

**Single unified table** with two visual sections:

```
┌─────────────────────────────────────────────────────────────────┐
│ [置顶 Selected] [Cancel Selected]          1 Running | 5 Pending │
├─────────────────────────────────────────────────────────────────┤
│ ▶ Running Tasks (1)                                            │
│ ┌────┬────┬────────┬─────────┬────────┬────────────┬──────────┐ │
│ │ ☑  │ ID │ Crate  │ Version │ Status │ Queue Pos  │ Created  │ │
│ ├────┼────┼────────┼─────────┼────────┼────────────┼──────────┤ │
│ │ ☑  │ 42 │ serde  │ 1.0.123 │ running│ -          │ 2m ago   │ │
│ └────┴────┴────────┴─────────┴────────┴────────────┴──────────┘ │
│ ⏳ Pending Queue (5)                                            │
│ ┌────┬────┬────────┬─────────┬────────┬────────────┬──────────┐ │
│ │ ☑  │ 43 │ tokio  │ 1.15.0  │ pending│ ⭐ 置顶     │ 1m ago   │ │
│ │ ☑  │ 44 │ async  │ 0.1.57  │ pending│ 1st        │ 30s ago  │ │
│ │ ☐  │ 45 │ rayon  │ 1.5.1   │ pending│ 2nd        │ just now │ │
│ └────┴────┴────────┴─────────┴────────┴────────────┴──────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**Key UI Elements:**
- **Action buttons**: "置顶 Selected" (orange) for pending, "Cancel Selected" (red) for running
- **Section headers**: Visual separation between Running and Pending sections
- **Queue Position column**: Shows ⭐ for pinned tasks, 1st/2nd/3rd for queue order
- **Multi-select checkboxes**: Per-row selection with header checkbox for bulk select
- **Status badges**: Color-coded status labels (green=running, orange=pending)

### Backend Changes

#### Database Schema

Add `priority` column to `tasks` table (via migration in `database.py init_db()`):

```python
# In database.py init_db(), add after existing migrations:
if "priority" not in columns:
    cursor.execute("ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 0")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pending_priority
        ON tasks(status, priority DESC, created_at ASC)
    """)
```

**Priority values:**
- `100` = Pinned/置顶 (high priority)
- `0` = Normal priority (default)

**Note on retry:** Priority is preserved when a task is retried (pinned tasks stay pinned).

#### Scheduler Logic Update

Modify `TaskScheduler.schedule_tasks()` to order by priority:

```python
# Current: pending = self.db.get_tasks_by_status(TaskStatus.PENDING)
# New: Order by priority DESC, then created_at ASC
pending = self.db.get_pending_tasks_ordered()
```

New method in Database class:
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

#### Data Model Updates

**TaskRecord dataclass** (`database.py`):
```python
@dataclass
class TaskRecord:
    id: int
    crate_name: str
    version: str
    workspace_path: str
    stdout_log: str
    stderr_log: str
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    case_count: Optional[int] = None
    poc_count: Optional[int] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    message: Optional[str] = None
    memory_used_mb: Optional[float] = None
    compile_failed: Optional[int] = None
    priority: Optional[int] = None  # NEW: priority field
```

**TaskDetailResponse** (`main.py`):
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
    priority: Optional[int]  # NEW: priority field
```

**Update converter functions** in `main.py`:
- `_task_to_dict()`: Add `"priority": task.priority`
- `_task_to_response()`: Add `priority=task.priority`

#### API Endpoints

**POST /api/tasks/batch-priority** - Batch set priority on pending tasks

Skips tasks that are not in pending status:
```json
// Request
{
  "task_ids": [43, 44],
  "priority": 100
}

// Response
{
  "updated": [43],
  "skipped": [44],
  "not_found": []
}
```

**POST /api/tasks/batch-cancel** - Batch cancel running tasks (NEW endpoint)

Skips tasks that are not running:
```json
{
  "cancelled": [42],
  "skipped": [43],
  "not_found": []
}
```

**GET /api/queue** - Get queue state (running + pending tasks ordered)
```json
{
  "running": [
    {"id": 42, "crate_name": "serde", "status": "running", ...}
  ],
  "pending": [
    {"id": 43, "crate_name": "tokio", "status": "pending", "priority": 100, ...},
    {"id": 44, "crate_name": "async-trait", "status": "pending", "priority": 0, ...}
  ]
}
```

### Frontend Changes

#### New Route

Add `/queue` route in `frontend/src/router/index.js`:
```javascript
{
  path: '/queue',
  name: 'TaskQueue',
  component: () => import('../views/TaskQueue.vue')
}
```

#### Navigation

Add "Queue" link in `frontend/src/App.vue` between Dashboard and Tasks (around line 30):

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

#### API Service Updates

Add to `frontend/src/services/api.js`:
```javascript
async batchSetPriority(taskIds, priority) {
  const response = await api.post('/tasks/batch-priority', { task_ids: taskIds, priority })
  return response.data
},

async batchCancel(taskIds) {
  const response = await api.post('/tasks/batch-cancel', { task_ids: taskIds })
  return response.data
},

async getQueue() {
  const response = await api.get('/queue')
  return response.data
}
```

#### TaskQueue.vue Component

**State management:**
```javascript
const runningTasks = ref([])
const pendingTasks = ref([])
const selectedRunningIds = ref(new Set())
const selectedPendingIds = ref(new Set())
const loading = ref(true)
```

**Key methods:**
```javascript
// Fetch queue data
async function fetchQueue() {
  const data = await api.getQueue()
  runningTasks.value = data.running
  pendingTasks.value = data.pending
}

// Pin selected pending tasks
async function handlePinSelected() {
  const ids = [...selectedPendingIds.value]
  if (ids.length === 0) return

  await api.batchSetPriority(ids, 100)
  selectedPendingIds.value = new Set()
  await fetchQueue()
}

// Cancel selected running tasks with confirmation
async function handleCancelSelected() {
  const ids = [...selectedRunningIds.value]
  if (ids.length === 0) return

  if (!confirm(`Cancel ${ids.length} running task(s)?`)) return

  await api.batchCancel(ids)
  selectedRunningIds.value = new Set()
  await fetchQueue()
}
```

**Computed properties:**
```javascript
// Combined list for display: running first, then pending by priority
const displayTasks = computed(() => {
  return [
    ...runningTasks.value.map(t => ({ ...t, section: 'running' })),
    ...pendingTasks.value.map((t, idx) => ({
      ...t,
      section: 'pending',
      queuePosition: t.priority > 0 ? '⭐ 置顶' : `${idx + 1}${getOrdinalSuffix(idx + 1)}`
    }))
  ]
})
```

**WebSocket updates:**
```javascript
// Real-time updates when tasks change status
websocket.on('task_update', fetchQueue)
websocket.on('task_created', fetchQueue)
websocket.on('task_completed', fetchQueue)
```

## Error Handling

1. **Invalid task state**: If user tries to pin a running task, silently skip it
2. **Already pinned**: Allow re-pinning (idempotent operation)
3. **Cancel failed**: Show alert with which tasks couldn't be cancelled
4. **Network errors**: Display error banner, allow retry

## Testing

### Unit Tests (Backend)
- `test_get_pending_tasks_ordered`: Verify correct ordering by priority then creation time
- `test_batch_set_priority`: Verify priority update works
- `test_batch_set_priority_skips_non_pending`: Verify running tasks are skipped

### Integration Tests
- End-to-end: Create tasks → Pin some → Verify order → Cancel running

### Frontend Tests
- Component renders with empty queue
- Selection state management
- Batch actions trigger correct API calls

## Implementation Notes

**Why:** Priority field approach
- Simple and intuitive (higher number = higher priority)
- Persistent across server restarts
- Extensible (could add more priority levels later)
- Minimal scheduler changes required

**Trade-offs:**
- Database migration required
- Queue order only visible on Queue page (not Task List)

## Future Enhancements

- Drag-and-drop reordering for pending tasks
- Pause/resume queue processing
- Estimated wait time for pending tasks
