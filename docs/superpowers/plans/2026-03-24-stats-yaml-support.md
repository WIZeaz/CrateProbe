# Stats.yaml Support Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time stats.yaml reading in LogViewer and display CompileFailed count in TaskList with sorting support.

**Architecture:**
- Backend: Add `stats-yaml` entry to LOG_PATH_RESOLVERS to enable reading stats.yaml via existing log endpoints
- LogViewer: Add `stats` tab that displays parsed YAML content
- TaskList: Add API endpoint to fetch stats.yaml for all tasks, extract CompileFailed values, display as sortable column

**Tech Stack:** Python 3.10+, FastAPI, Vue 3, Axios, PyYAML (backend), js-yaml or plain text parsing (frontend optional)

---

## File Structure

| File | Purpose | Change Type |
|------|---------|-------------|
| `backend/app/main.py` | Add stats-yaml to LOG_PATH_RESOLVERS | Modify |
| `frontend/src/components/LogViewer.vue` | Add stats tab to log viewer | Modify |
| `frontend/src/views/TaskList.vue` | Add CompileFailed column and fetch logic | Modify |
| `frontend/src/services/api.js` | Add method to fetch stats for task | Modify |
| `backend/pyproject.toml` | Verify PyYAML dependency exists | Check/Add |
| `frontend/package.json` | Add js-yaml for YAML parsing (optional) | Check/Add |

---

## Chunk 1: Backend - Add stats-yaml to LOG_PATH_RESOLVERS

### Task 1: Add stats-yaml path resolver

**Files:**
- Modify: `backend/app/main.py:52-59`

- [ ] **Step 1: Modify LOG_PATH_RESOLVERS to add stats-yaml entry**

Add `"stats-yaml"` entry to the `LOG_PATH_RESOLVERS` dictionary that points to `{workspace}/testgen/stats.yaml`:

```python
LOG_PATH_RESOLVERS = {
    "stdout": lambda task, _cfg: Path(task.stdout_log),
    "stderr": lambda task, _cfg: Path(task.stderr_log),
    "runner": lambda task, cfg: cfg.workspace_path / "logs" / f"{task.id}-runner.log",
    "miri_report": lambda task, _cfg: Path(task.workspace_path)
    / "testgen"
    / "miri_report.txt",
    "stats-yaml": lambda task, _cfg: Path(task.workspace_path) / "testgen" / "stats.yaml",
}
```

- [ ] **Step 2: Test the endpoint works**

Run backend tests:
```bash
cd /home/wizeaz/exp-plat/backend
uv run pytest tests/unit/ -v -k "test_" 2>&1 | head -50
```

Expected: Tests pass, no import errors

- [ ] **Step 3: Manual test the endpoint**

Start the backend (or use existing running instance):
```bash
cd /home/wizeaz/exp-plat/backend
uv run python -m app.main &
```

Test the endpoint with curl:
```bash
curl -s http://localhost:8080/api/tasks/1/logs/stats-yaml | head -20
```

Expected: Returns 404 if file doesn't exist, or YAML content if it does

- [ ] **Step 4: Commit**

```bash
cd /home/wizeaz/exp-plat
git add backend/app/main.py
git commit -m "feat: add stats-yaml to LOG_PATH_RESOLVERS for reading testgen/stats.yaml"
```

---

## Chunk 2: Frontend - LogViewer Add Stats Tab

### Task 2: Add stats tab to LogViewer

**Files:**
- Modify: `frontend/src/components/LogViewer.vue`

- [ ] **Step 1: Add 'stats' to logs and logHtml refs**

Modify lines 17-29 to include 'stats' entry:

```javascript
const activeLog = ref('runner')
const logs = ref({
  runner: '',
  stdout: '',
  stderr: '',
  miri_report: '',
  stats: ''
})
const logHtml = ref({
  runner: '',
  stdout: '',
  stderr: '',
  miri_report: '',
  stats: ''
})
```

- [ ] **Step 2: Add 'stats' to loading and fetched refs**

Modify lines 38-49 to include 'stats' entry:

```javascript
const loading = ref({
  runner: false,
  stdout: false,
  stderr: false,
  miri_report: false,
  stats: false
})
const fetched = ref({
  runner: false,
  stdout: false,
  stderr: false,
  miri_report: false,
  stats: false
})
```

- [ ] **Step 3: Add stats to logFiles array**

Modify lines 53-58 to add stats entry:

```javascript
const logFiles = [
  { id: 'runner', label: 'runner', icon: '⚙' },
  { id: 'stdout', label: 'stdout', icon: '📄' },
  { id: 'stderr', label: 'stderr', icon: '📄' },
  { id: 'miri_report', label: 'miri_report', icon: '📄' },
  { id: 'stats', label: 'stats', icon: '📊' },
]
```

- [ ] **Step 4: Update downloadLog extension logic**

Modify the downloadLog function (around line 131) to handle stats file extension:

```javascript
const ext = activeLog.value === 'miri_report' ? 'txt' : activeLog.value === 'stats' ? 'yaml' : 'log'
```

- [ ] **Step 5: Test in browser**

Navigate to a task detail page and verify:
1. Stats tab appears in the log file list
2. Clicking stats tab shows content (or "No content available" if file doesn't exist)
3. Auto-refresh works (5 second interval)

- [ ] **Step 6: Commit**

```bash
cd /home/wizeaz/exp-plat
git add frontend/src/components/LogViewer.vue
git commit -m "feat: add stats tab to LogViewer for real-time stats.yaml display"
```

---

## Chunk 3: Frontend API Service - Add getTaskStats Method

### Task 3: Add getTaskStats method to api.js

**Files:**
- Modify: `frontend/src/services/api.js`

- [ ] **Step 1: Read the current api.js file**

Read `/home/wizeaz/exp-plat/frontend/src/services/api.js` to understand the existing API methods.

- [ ] **Step 2: Add getTaskStats method**

Add a new method to fetch and parse stats.yaml for a task. Add after the existing getLog method:

```javascript
  async getTaskStats(taskId) {
    try {
      const response = await this.client.get(`/tasks/${taskId}/logs/stats-yaml`)
      // Parse YAML content - simple key:value format
      const lines = response.data.lines || []
      const stats = {}
      lines.forEach(line => {
        const match = line.match(/^(\w+):\s*(.+)$/)
        if (match) {
          const [, key, value] = match
          // Try to parse as number, fallback to string
          const numValue = parseInt(value, 10)
          stats[key] = isNaN(numValue) ? value : numValue
        }
      })
      return stats
    } catch (err) {
      if (err.response?.status === 404) {
        return {} // File doesn't exist yet
      }
      throw err
    }
  }
```

- [ ] **Step 3: Verify the method exports correctly**

The api.js file should export an instance that includes the new method.

- [ ] **Step 4: Commit**

```bash
cd /home/wizeaz/exp-plat
git add frontend/src/services/api.js
git commit -m "feat: add getTaskStats method to fetch and parse stats.yaml"
```

---

## Chunk 4: Frontend - TaskList Add CompileFailed Column

### Task 4: Add CompileFailed column to TaskList

**Files:**
- Modify: `frontend/src/views/TaskList.vue`

- [ ] **Step 1: Add taskStats reactive ref**

Add after line 16 (`const batchLoading = ref(false)`):

```javascript
const taskStats = ref({}) // Map of taskId -> stats object
```

- [ ] **Step 2: Add fetchTaskStats function**

Add after the `fetchTasks` function (around line 114):

```javascript
async function fetchTaskStats() {
  // Fetch stats for all visible tasks
  const taskIds = tasks.value.map(t => t.id)
  const statsPromises = taskIds.map(async (taskId) => {
    try {
      const stats = await api.getTaskStats(taskId)
      return { taskId, stats }
    } catch (err) {
      return { taskId, stats: {} }
    }
  })

  const results = await Promise.all(statsPromises)
  const newStats = {}
  results.forEach(({ taskId, stats }) => {
    newStats[taskId] = stats
  })
  taskStats.value = newStats
}
```

- [ ] **Step 3: Add compile_failed to sort options**

Modify the `sortBy` function and sorting logic in `filteredAndSortedTasks` computed property (around line 29-61) to handle the `compile_failed` column:

Update the sorting logic around line 42-48:

```javascript
    // Handle runtime and compile_failed columns specially
    if (sortColumn.value === 'runtime') {
      aVal = getRuntimeSeconds(a.started_at, a.finished_at)
      bVal = getRuntimeSeconds(b.started_at, b.finished_at)
    } else if (sortColumn.value === 'compile_failed') {
      aVal = taskStats.value[a.id]?.CompileFailed ?? -1
      bVal = taskStats.value[b.id]?.CompileFailed ?? -1
    } else {
      aVal = a[sortColumn.value]
      bVal = b[sortColumn.value]
    }
```

- [ ] **Step 4: Add CompileFailed table header**

Add a new table header column after the POCs header (around line 340), before the Runtime header:

```html
            <th
              @click="sortBy('compile_failed')"
              class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-50"
            >
              Compile Failed
              <span v-if="sortColumn === 'compile_failed'">{{ sortDirection === 'asc' ? '↑' : '↓' }}</span>
            </th>
```

- [ ] **Step 5: Add CompileFailed table cell**

Add a new table cell in the tbody after the POCs cell (around line 384), before the Runtime cell:

```html
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 cursor-pointer" @click="viewTask(task.id)">
              {{ taskStats[task.id]?.CompileFailed ?? '-' }}
            </td>
```

- [ ] **Step 6: Modify fetchTasks to also fetch stats**

Modify the `fetchTasks` function to call `fetchTaskStats` after fetching tasks:

```javascript
async function fetchTasks() {
  try {
    tasks.value = await api.getAllTasks()
    loading.value = false
    // Fetch stats for all tasks
    await fetchTaskStats()
    // Remove selected ids that no longer exist
    const existingIds = new Set(tasks.value.map(t => t.id))
    const cleaned = new Set([...selectedIds.value].filter(id => existingIds.has(id)))
    selectedIds.value = cleaned
  } catch (err) {
    error.value = err.message
    loading.value = false
  }
}
```

- [ ] **Step 7: Add periodic stats refresh**

Add stats refresh interval. After the existing websocket listeners in onMounted (around line 200-204), add:

```javascript
  // Periodic stats refresh every 10 seconds
  const statsRefreshInterval = setInterval(() => {
    if (!loading.value) {
      fetchTaskStats()
    }
  }, 10000)

  // Store interval for cleanup
  onUnmounted(() => {
    websocket.off('task_update', handleTaskUpdate)
    websocket.off('task_created', handleTaskUpdate)
    websocket.off('task_completed', handleTaskUpdate)
    clearInterval(statsRefreshInterval)
  })
```

Note: Update the existing onUnmounted to also clear this interval.

- [ ] **Step 8: Test in browser**

Navigate to the Tasks list page and verify:
1. Compile Failed column appears
2. Values display correctly (or '-' if not available)
3. Sorting by Compile Failed works
4. Values update periodically (every 10 seconds)

- [ ] **Step 9: Commit**

```bash
cd /home/wizeaz/exp-plat
git add frontend/src/views/TaskList.vue
git commit -m "feat: add CompileFailed column to TaskList with sorting and real-time updates"
```

---

## Testing Summary

### Backend Tests
```bash
cd /home/wizeaz/exp-plat/backend
uv run pytest tests/unit/ -v
```

### Frontend Manual Tests
1. Open TaskList page - verify Compile Failed column shows
2. Sort by Compile Failed - verify ascending/descending works
3. Click a task to go to TaskDetail
4. Verify Stats tab exists in LogViewer
5. Verify Stats content displays (or shows "No content available")

### Integration Test
Create a test stats.yaml file and verify it's read correctly:
```bash
# Create a test stats.yaml
cat > /home/wizeaz/exp-plat/backend/workspace/repos/test-crate-1.0.0/testgen/stats.yaml << 'EOF'
CompileFailed: 5
TotalTests: 100
Passed: 95
EOF

# Test via API
curl -s http://localhost:8080/api/tasks/1/logs/stats-yaml
```

---

## Plan Review

@superpowers:plan-document-reviewer

Please review this implementation plan for:
1. Correctness of file paths and line numbers
2. Code snippets are complete and valid
3. Test commands are appropriate
4. No missing dependencies or steps