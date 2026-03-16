# ANSI Color Logs Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Task Detail 界面的 Logs 输出添加 ANSI 颜色支持，确保后端命令强制输出颜色

**Architecture:** 前端使用 `ansi_up` 库将 ANSI 颜色码转换为 HTML，后端通过设置环境变量和 TTY 强制 cargo 等命令输出颜色

**Tech Stack:** Vue 3, ansi_up, Python/FastAPI, Docker SDK

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `frontend/package.json` | Modify | 添加 `ansi_up` 依赖 |
| `frontend/src/components/LogViewer.vue` | Modify | 集成 ansi_up 渲染 ANSI 颜色为 HTML |
| `frontend/src/style.css` | Modify | 添加 ANSI 16 色 CSS 变量 |
| `backend/app/services/task_executor.py` | Modify | 设置 `CARGO_TERM_COLOR=always` 环境变量 |
| `backend/app/utils/docker_runner.py` | Modify | 启用 `tty=True` 并设置颜色环境变量 |

---

## Task 1: Install ansi_up Dependency

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Add ansi_up to dependencies**

```bash
cd /home/wizeaz/exp-plat/frontend
npm install ansi_up
```

- [ ] **Step 2: Verify installation**

检查 `frontend/package.json` 中是否添加了 `ansi_up` 依赖

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "deps: add ansi_up for ANSI color rendering"
```

---

## Task 2: Update LogViewer Component

**Files:**
- Modify: `frontend/src/components/LogViewer.vue`

- [ ] **Step 1: Import ansi_up**

在 `<script setup>` 顶部添加：

```javascript
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { AnsiUp } from 'ansi_up'  // ADD THIS LINE
import api from '../services/api'

// Create ansi_up instance
const ansiUp = new AnsiUp()
```

- [ ] **Step 2: Add computed HTML content**

添加用于存储转换后 HTML 的 ref：

```javascript
const logHtml = ref({
  runner: '',
  stdout: '',
  stderr: '',
  miri_report: ''
})

// Function to convert ANSI to HTML
function ansiToHtml(text) {
  if (!text || text === 'No content available') {
    return '<span class="text-gray-400">No content available</span>'
  }
  return ansiUp.ansi_to_html(text)
}
```

- [ ] **Step 3: Update log loading to convert ANSI**

修改 `loadLog` 函数，在设置 logs 后同时更新 logHtml：

```javascript
// Inside loadLog function, replace the existing logs.value assignment with:

if (data.lines && Array.isArray(data.lines)) {
  const content = data.lines.join('\n') || 'No content available'
  logs.value[logType] = content
  logHtml.value[logType] = ansiToHtml(content)  // ADD THIS LINE
} else {
  const content = data.content || 'No content available'
  logs.value[logType] = content
  logHtml.value[logType] = ansiToHtml(content)  // ADD THIS LINE
}
```

- [ ] **Step 4: Update template to use v-html**

将 `<pre>` 标签改为渲染 HTML：

```vue
<!-- Replace: -->
<pre v-else class="text-sm">{{ logs[activeLog] || 'No content available' }}</pre>

<!-- With: -->
<pre v-else class="text-sm ansi-color" v-html="logHtml[activeLog]"></pre>
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LogViewer.vue
git commit -m "feat: add ANSI color support to LogViewer"
```

---

## Task 3: Add ANSI Color CSS Styles

**Files:**
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Add ANSI color styles**

在 `style.css` 末尾添加：

```css
/* ANSI Color Support for Log Viewer */
.ansi-color {
  /* Ensure background matches terminal */
  background-color: #111827;
}

/* ANSI 16-color palette optimized for dark terminal background */
.ansi-color .ansi-black {
  color: #000000;
}
.ansi-color .ansi-red {
  color: #ff5555;
}
.ansi-color .ansi-green {
  color: #50fa7b;
}
.ansi-color .ansi-yellow {
  color: #f1fa8c;
}
.ansi-color .ansi-blue {
  color: #8be9fd;
}
.ansi-color .ansi-magenta {
  color: #ff79c6;
}
.ansi-color .ansi-cyan {
  color: #8be9fd;
}
.ansi-color .ansi-white {
  color: #f8f8f2;
}

/* Bright variants */
.ansi-color .ansi-bright-black {
  color: #6272a4;
}
.ansi-color .ansi-bright-red {
  color: #ff6e6e;
}
.ansi-color .ansi-bright-green {
  color: #69ff94;
}
.ansi-color .ansi-bright-yellow {
  color: #ffffa5;
}
.ansi-color .ansi-bright-blue {
  color: #d6acff;
}
.ansi-color .ansi-bright-magenta {
  color: #ff92df;
}
.ansi-color .ansi-bright-cyan {
  color: #a4ffff;
}
.ansi-color .ansi-bright-white {
  color: #ffffff;
}

/* Bold */
.ansi-color .ansi-bold {
  font-weight: bold;
}

/* Italic */
.ansi-color .ansi-italic {
  font-style: italic;
}

/* Underline */
.ansi-color .ansi-underline {
  text-decoration: underline;
}

/* Dim */
.ansi-color .ansi-dim {
  opacity: 0.5;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/style.css
git commit -m "styles: add ANSI 16-color palette for log viewer"
```

---

## Task 4: Backend - Force Color Output for Non-Docker Mode

**Files:**
- Modify: `backend/app/services/task_executor.py`

- [ ] **Step 1: Update execute_task to set environment variables**

修改 `execute_task` 方法，在 `_execute_with_limiter` 调用前设置环境变量：

```python
# In execute_task method, in the else branch (around line 218):
else:
    # Set environment variables to force color output
    import os
    os.environ['CARGO_TERM_COLOR'] = 'always'
    os.environ['TERM'] = 'xterm-256color'

    # Use traditional execution with systemd/resource
    await self._execute_with_limiter(task_id, workspace_dir, task)
```

- [ ] **Step 2: Verify the change**

确认 `execute_task` 方法现在设置了这两个环境变量

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/task_executor.py
git commit -m "feat: force color output for cargo commands in non-docker mode"
```

---

## Task 5: Backend - Enable TTY for Docker Mode

**Files:**
- Modify: `backend/app/utils/docker_runner.py`

- [ ] **Step 1: Add environment and tty parameters to container.run**

修改 `run` 方法中的 `containers.run` 调用（约第106行）：

```python
container = self.client.containers.run(
    image=self.image,
    command=command,
    working_dir="/workspace",
    volumes=volumes,
    detach=True,
    stdout=True,
    stderr=True,
    tty=True,  # ADD THIS LINE - allocate pseudo-TTY
    environment={  # ADD THIS BLOCK - force color output
        'CARGO_TERM_COLOR': 'always',
        'TERM': 'xterm-256color',
    },
    **resource_limits,
)
```

- [ ] **Step 2: Update docstring**

更新 `run` 方法的 docstring 添加 TTY 信息：

```python
"""
Run a command in a Docker container with resource limits.

Allocates a pseudo-TTY (tty=True) to ensure applications output ANSI color codes.

Args:
    ...
"""
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/utils/docker_runner.py
git commit -m "feat: enable TTY and force color output in Docker mode"
```

---

## Task 6: Integration Testing

**Files:**
- Test: Manual browser verification

- [ ] **Step 1: Start backend and frontend**

```bash
# Terminal 1 - Backend
cd /home/wizeaz/exp-plat/backend
uv run python -m app.main

# Terminal 2 - Frontend
cd /home/wizeaz/exp-plat/frontend
npm run dev
```

- [ ] **Step 2: Create a test task**

访问 http://localhost:5173，创建一个测试任务（例如 crate: `serde`, version: `1.0`）

- [ ] **Step 3: Verify ANSI colors in logs**

1. 打开任务详情页面
2. 等待任务运行完成
3. 检查 stdout/stderr 日志，确认颜色正确显示
4. 使用浏览器开发者工具检查 HTML，确认有 `<span class="ansi-red">` 等元素

- [ ] **Step 4: Test both Docker and non-Docker modes**

如果可能，测试两种执行模式确保都输出颜色

- [ ] **Step 5: Commit any fixes if needed**

```bash
git commit -m "fix: adjustments after testing"  # if needed
```

---

## Verification Checklist

- [ ] `ansi_up` 已安装并可在 `LogViewer.vue` 中导入
- [ ] LogViewer 正确渲染 ANSI 颜色为 HTML
- [ ] CSS 样式正确定义了 16 种 ANSI 颜色
- [ ] 非 Docker 模式设置了 `CARGO_TERM_COLOR=always` 和 `TERM=xterm-256color`
- [ ] Docker 模式启用了 `tty=True` 并设置了颜色环境变量
- [ ] 浏览器中日志显示彩色输出
