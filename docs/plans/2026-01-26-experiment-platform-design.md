# 实验平台设计文档

**日期：** 2026-01-26
**版本：** 1.0

## 概述

这是一个用于自动化下载Rust crate、运行实验并提供Web界面实时展示实验进度和结果的实验平台。

## 整体架构

### 架构风格
前后端分离架构

### 后端（FastAPI + Python）
- **核心服务器**：提供RESTful API和WebSocket接口
- **任务调度器**：管理任务队列，根据max_jobs控制并发执行
- **任务执行器**：使用systemd-run（或resource模块）启动受限子进程执行cargo命令
- **数据层**：SQLite数据库存储任务元数据和状态
- **文件系统层**：管理workspace目录结构（repos/、logs/等）

### 前端（Vue 3 + Tailwind CSS）
- SPA单页应用，使用Vue Router管理路由
- 使用Axios与后端RESTful API通信
- 使用WebSocket接收实时任务状态更新（不包括日志）
- 采用Bento Grid布局风格，现代简洁设计

### 目录结构
```
exp-plat/
├── backend/          # FastAPI后端
├── frontend/         # Vue 3前端
├── config.toml       # 配置文件
└── workspace/        # 工作空间（运行时创建）
    ├── repos/        # 下载的crate源码
    ├── logs/         # 任务日志
    └── tasks.db      # SQLite数据库
```

## 配置文件设计

文件：`config.toml`

```toml
[server]
# Web服务端口
port = 8000
# 绑定地址
host = "0.0.0.0"

[workspace]
# 工作空间根目录（所有运行时文件都在此目录下）
path = "./workspace"

[execution]
# 最大并发任务数
max_jobs = 3
# 单个任务最大内存限制（GB）
max_memory_gb = 20
# 单个任务最长运行时间（小时）
max_runtime_hours = 24
# 是否优先使用systemd-run（如果不可用则回退到resource模块）
use_systemd = true

[database]
# SQLite数据库路径（相对于workspace或绝对路径）
path = "tasks.db"

[logging]
# 日志级别：DEBUG, INFO, WARNING, ERROR
level = "INFO"
# 是否输出到控制台
console = true
# 是否写入文件
file = true
# 日志文件路径
file_path = "server.log"
```

启动时会验证配置并创建必要的目录结构。如果配置文件不存在，使用默认值创建。

## 数据库设计

### Tasks表

```sql
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crate_name TEXT NOT NULL,
    version TEXT NOT NULL,  -- 具体版本号，创建时如果用户未指定则通过API获取最新版本
    status TEXT NOT NULL,   -- pending, running, completed, failed, cancelled, timeout, oom
    exit_code INTEGER,      -- 退出码（如果有）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,   -- 任务开始时间
    finished_at TIMESTAMP,  -- 任务结束时间
    workspace_path TEXT,    -- 仓库路径：workspace/repos/<crate_name>-<version>
    stdout_log TEXT,        -- stdout日志文件路径
    stderr_log TEXT,        -- stderr日志文件路径
    pid INTEGER,            -- 进程ID（运行中）
    case_count INTEGER DEFAULT 0,     -- testgen/tests文件夹数量
    poc_count INTEGER DEFAULT 0,      -- testgen/poc文件夹数量
    memory_used_mb REAL,    -- 实际使用内存（如果可获取）
    error_message TEXT      -- 错误信息（如果有）
);
```

### 索引
- 按创建时间排序：`CREATE INDEX idx_created_at ON tasks(created_at DESC);`
- 按状态筛选：`CREATE INDEX idx_status ON tasks(status);`

### 任务创建流程
1. 用户提交crate_name和version（可选）
2. 如果version为空，后端先调用crates.io API获取最新版本号
3. 将实际版本号写入数据库

## 后端API设计

### RESTful API端点

#### 任务管理
- `POST /api/tasks` - 创建新任务
  - 请求体：`{"crate_name": "serde", "version": "1.0.0"}`（version可选）
  - 响应：`{"task_id": 1, "crate_name": "serde", "version": "1.0.193", "status": "pending"}`

- `GET /api/tasks` - 获取所有任务列表
  - 响应：任务数组，包含基本信息

- `GET /api/tasks/{task_id}` - 获取任务详情
  - 响应：完整任务信息

- `DELETE /api/tasks/{task_id}` - 取消/终止任务
  - 仅对running状态的任务有效，会终止进程

#### 日志查看
- `GET /api/tasks/{task_id}/logs/stdout?lines=1000` - 获取stdout最新N行
- `GET /api/tasks/{task_id}/logs/stderr?lines=1000` - 获取stderr最新N行
- `GET /api/tasks/{task_id}/logs/miri_report?lines=1000` - 获取miri_report.txt最新N行
- `GET /api/tasks/{task_id}/logs/stdout/raw` - 下载完整stdout
- `GET /api/tasks/{task_id}/logs/stderr/raw` - 下载完整stderr
- `GET /api/tasks/{task_id}/logs/miri_report/raw` - 下载完整miri_report.txt

#### 统计信息
- `GET /api/dashboard/stats` - 获取仪表盘统计（当前运行任务数、总任务数、成功/失败数等）
- `GET /api/dashboard/system` - 获取系统资源信息（CPU、内存、磁盘）

### WebSocket连接

- `WS /ws/tasks/{task_id}` - 订阅特定任务的状态更新
  - 连接建立后，实时推送该任务的状态变化
  - 推送消息格式：
    ```json
    {
      "task_id": 1,
      "status": "running",
      "started_at": "2026-01-26T10:30:00",
      "case_count": 15,
      "poc_count": 3,
      "memory_used_mb": 8192.5
    }
    ```

- `WS /ws/dashboard` - 订阅仪表盘统计信息的实时更新
  - 推送消息格式：
    ```json
    {
      "running_tasks": 2,
      "total_tasks": 50,
      "cpu_percent": 45.2,
      "memory_percent": 62.1,
      "disk_percent": 38.5
    }
    ```

## 前端页面设计

### 路由结构
```
/ (redirect to /dashboard)
/dashboard - 仪表盘
/tasks/new - 新建任务
/tasks - 所有任务
/tasks/:id - 任务详情
```

### 仪表盘页面（/dashboard）
- Bento Grid布局，包含信息卡片：
  - 当前运行任务数（大卡片，突出显示）
  - 总计任务数
  - 成功任务数
  - 失败任务数
- 系统资源监控区域（实时图表或进度条）：
  - CPU占用率
  - 内存使用情况
  - 磁盘占用
- 使用WebSocket (`/ws/dashboard`) 实时更新数据

### 新建任务页面（/tasks/new）
- 简洁表单：
  - Crate名称输入框（必填）
  - 版本号输入框（可选，为空则使用最新版本）
  - 提交按钮
- 提交后跳转到任务详情页

### 所有任务页面（/tasks）
- 任务列表表格，显示：
  - ID
  - Crate名称
  - 版本
  - 状态（带颜色标识：running-蓝色, completed-绿色, failed-红色, timeout-橙色等）
  - Case数量（可排序）
  - POC数量（可排序）
  - 创建时间（可排序）
  - 运行时间
- 点击任意行跳转到详情页
- 功能：
  - 按状态筛选（下拉菜单或标签页）
  - 表头支持点击排序（ID、创建时间、Case数量、POC数量）
  - 默认按创建时间降序排列

### 任务详情页面（/tasks/:id）

#### 顶部信息区域
- Crate名称和版本（大标题）
- 状态徽章（带颜色和图标）
- 取消任务按钮（仅running状态显示，确认后调用DELETE API）

#### 统计信息卡片（Bento Grid布局）
- 任务运行时间（实时更新，格式：2h 35m 18s）
- 任务状态详情（正常运行/退出码/超时/OOM）
- Case生成数量
- POC数量
- 内存使用情况（如果可获取）

#### 日志查看区域（标签页切换）
- Tab 1: stdout（最新1000行）
  - 代码块显示，等宽字体
  - "查看完整日志"按钮 → 新窗口打开 `/api/tasks/{id}/logs/stdout/raw`
- Tab 2: stderr（最新1000行）
  - 同上格式
  - "查看完整日志"按钮
- Tab 3: miri_report.txt（最新1000行）
  - 同上格式
  - "查看完整日志"按钮

#### 实时更新
- 连接到 `WS /ws/tasks/{id}` 获取任务状态、统计信息的实时更新
- 日志内容仅在页面加载时获取，不通过WebSocket推送

## 任务执行流程

### 任务调度器（TaskScheduler）
- 维护一个任务队列（pending状态的任务）
- 维护一个运行池（running状态的任务，最多max_jobs个）
- 后台线程定期检查：
  - 如果运行池未满且队列非空，取出任务执行
  - 监控运行中任务的状态、资源使用、超时等

### 单个任务执行流程

#### 1. 创建阶段（用户提交）
- 验证crate_name格式
- 如果version为空，调用crates.io API获取最新版本
- 在数据库中创建任务记录，状态=pending
- 通过WebSocket通知dashboard更新

#### 2. 下载阶段（调度器分配执行）
- 更新状态为running，记录started_at
- 创建目录：`workspace/repos/<crate_name>-<version>`
- 调用crates.io API下载.crate文件
- 解压到目标目录
- 创建日志文件：`workspace/logs/<task_id>-stdout.log` 和 `stderr.log`

#### 3. 执行阶段
- 优先尝试使用systemd-run启动进程：
  ```bash
  systemd-run --user --scope \
    --property=MemoryMax=20G \
    --property=CPUQuota=400% \
    cargo rapx -testgen -test-crate=<crate_name>
  ```
- 如果systemd-run不可用，使用subprocess + resource限制
- 启动timeout监控线程（24小时）
- 实时写入stdout/stderr到日志文件
- 记录进程PID

#### 4. 监控阶段
- 定期更新case_count和poc_count（扫描目录）
- 通过WebSocket推送任务状态更新
- 检测进程退出、超时、OOM

#### 5. 完成阶段
- 记录finished_at、exit_code
- 更新最终的case_count和poc_count
- 更新状态（completed/failed/timeout/oom）
- 从运行池移除，调度下一个任务

## 错误处理和边界情况

### Crates.io API错误处理
- Crate不存在：返回404错误给前端，提示"Crate未找到"
- 指定版本不存在：返回400错误，提示"版本不存在"
- API限流/超时：重试3次，失败后返回503错误
- 下载失败：标记任务为failed，记录error_message

### 任务执行错误处理
- 工作目录创建失败：标记为failed
- cargo命令不存在：标记为failed，提示"cargo或rapx未安装"
- 进程启动失败：标记为failed，记录错误信息
- 超时（24小时）：强制终止进程，标记为timeout
- OOM（超过20G）：进程被系统杀死，标记为oom

### 并发和资源竞争
- 数据库操作使用事务保证一致性
- 任务调度器使用锁保护共享状态
- 同一个crate+version可以创建多个任务（不冲突，使用不同目录）

### WebSocket连接管理
- 客户端断开自动清理连接
- 任务结束后保持连接，继续推送最终状态
- 连接异常自动重连机制（前端实现）

### 日志文件管理
- 日志文件不限制大小（由用户磁盘空间决定）
- 如果日志文件不存在返回404
- 读取日志使用tail方式避免加载整个文件

## 测试策略（TDD原则）

### 后端测试（pytest）

#### 单元测试
- `test_config.py` - 配置文件加载和验证
- `test_database.py` - 数据库操作（CRUD、查询）
- `test_crates_api.py` - crates.io API调用（使用mock）
- `test_task_executor.py` - 任务执行器逻辑（使用mock subprocess）
- `test_scheduler.py` - 任务调度器逻辑

#### 集成测试
- `test_api_endpoints.py` - API端点完整流程测试
- `test_websocket.py` - WebSocket连接和消息推送
- `test_task_lifecycle.py` - 任务完整生命周期（使用测试workspace）

### 前端测试（Vitest + Vue Test Utils）
- 组件单元测试：各页面组件渲染和交互
- API集成测试：使用mock后端验证API调用
- 路由测试：页面导航和参数传递

### 测试数据
- 使用测试专用workspace目录
- 使用内存SQLite数据库（`:memory:`）
- Mock crates.io API响应

### CI/CD
- 每次commit运行所有测试
- 测试覆盖率要求：后端>80%，前端>70%

## 技术栈

### 后端技术栈
- Python 3.10+
- FastAPI 0.100+
- SQLite3（标准库）
- uvicorn（ASGI服务器）
- aiofiles（异步文件操作）
- psutil（系统资源监控）
- httpx（异步HTTP客户端，调用crates.io API）
- websockets（WebSocket支持）
- tomli/tomllib（配置文件解析）

### 前端技术栈
- Vue 3.3+ (Composition API)
- Vue Router 4
- Axios（HTTP客户端）
- Tailwind CSS 3
- Vite（构建工具）
- Vitest（测试框架）

## 详细项目结构

```
exp-plat/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI应用入口
│   │   ├── config.py            # 配置加载
│   │   ├── database.py          # 数据库操作
│   │   ├── models.py            # 数据模型
│   │   ├── api/
│   │   │   ├── tasks.py         # 任务API
│   │   │   ├── dashboard.py     # 仪表盘API
│   │   │   └── websocket.py     # WebSocket端点
│   │   ├── services/
│   │   │   ├── crates_api.py    # crates.io API客户端
│   │   │   ├── task_executor.py # 任务执行器
│   │   │   ├── scheduler.py     # 任务调度器
│   │   │   └── system_monitor.py# 系统监控
│   │   └── utils/
│   │       ├── file_utils.py
│   │       └── resource_limit.py
│   ├── tests/
│   │   ├── unit/
│   │   └── integration/
│   ├── requirements.txt
│   └── pytest.ini
├── frontend/
│   ├── src/
│   │   ├── main.js
│   │   ├── App.vue
│   │   ├── router/
│   │   │   └── index.js
│   │   ├── views/
│   │   │   ├── Dashboard.vue
│   │   │   ├── TaskNew.vue
│   │   │   ├── TaskList.vue
│   │   │   └── TaskDetail.vue
│   │   ├── components/
│   │   │   ├── StatCard.vue
│   │   │   ├── SystemMonitor.vue
│   │   │   └── LogViewer.vue
│   │   ├── services/
│   │   │   ├── api.js           # API客户端
│   │   │   └── websocket.js     # WebSocket管理
│   │   └── assets/
│   ├── tests/
│   ├── package.json
│   ├── vite.config.js
│   └── tailwind.config.js
├── config.toml                   # 配置文件
├── docs/
│   └── plans/
│       └── 2026-01-26-experiment-platform-design.md  # 本文档
├── README.md
└── workspace/                    # 运行时创建
    ├── repos/
    ├── logs/
    └── tasks.db
```

## 启动方式

### 后端
```bash
cd backend
pip install -r requirements.txt
python -m app.main
```

### 前端
```bash
# 开发模式
cd frontend
npm install
npm run dev

# 生产构建
npm run build
```

## 下一步

此设计文档已完成并验证。准备进入实施阶段：

1. 使用git worktrees创建隔离开发环境
2. 编写详细的实施计划
3. 按TDD原则逐步实现功能
