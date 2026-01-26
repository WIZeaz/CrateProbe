# 配置文档 (Configuration Guide)

本文档说明 `config.toml` 统一配置文件的使用方法。

## 配置文件位置

```
exp-plat/
├── config.toml.example    # 示例配置文件
└── config.toml            # 实际配置文件（需手动创建）
```

## 快速开始

```bash
# 1. 复制示例配置
cp config.toml.example config.toml

# 2. （可选）编辑配置
vim config.toml

# 3. 启动应用
cd backend && uv run python -m app.main  # 后端
cd frontend && npm run dev                # 前端
```

## 配置结构

配置文件分为两大部分：**后端配置** 和 **前端配置**

---

## 后端配置

### [server] - 服务器设置

```toml
[server]
port = 8080           # FastAPI 服务器监听端口
host = "0.0.0.0"      # 绑定地址（0.0.0.0 = 所有接口）
```

**说明**:
- `port`: Web API 服务端口，前端会通过此端口访问后端
- `host`:
  - `"0.0.0.0"` - 允许外部访问
  - `"127.0.0.1"` - 仅本地访问

**常见配置**:
```toml
# 开发环境
port = 8080
host = "127.0.0.1"

# 生产环境
port = 8000
host = "0.0.0.0"
```

---

### [workspace] - 工作空间

```toml
[workspace]
path = "./workspace"  # 工作空间根目录
```

**说明**:
- 所有运行时文件都存储在此目录
- 目录结构：
  ```
  workspace/
  ├── repos/      # 下载的 crate 源码
  ├── logs/       # 任务日志文件
  └── tasks.db    # SQLite 数据库
  ```

**路径类型**:
- 相对路径: `"./workspace"` (相对于项目根目录)
- 绝对路径: `"/data/exp-platform/workspace"`

---

### [execution] - 任务执行

```toml
[execution]
max_jobs = 3              # 最大并发任务数
max_memory_gb = 20        # 单个任务内存限制 (GB)
max_runtime_hours = 24    # 单个任务运行时间限制 (小时)
use_systemd = true        # 是否使用 systemd-run
```

**说明**:

**max_jobs** - 并发任务数
- 控制同时运行的任务数量
- 根据服务器 CPU 核心数设置
- 建议: `CPU核心数 - 1` 或 `CPU核心数 / 2`

**max_memory_gb** - 内存限制
- 单个任务最大可用内存
- 使用 systemd-run 或 resource 模块限制
- 超过限制会被终止 (状态: `oom`)

**max_runtime_hours** - 运行时间限制
- 单个任务最长运行时间
- 超时会被强制终止 (状态: `timeout`)

**use_systemd** - 资源限制方式
- `true`: 使用 systemd-run (Linux)
- `false`: 使用 Python resource 模块
- 如果 systemd 不可用会自动降级

**示例配置**:
```toml
# 高性能服务器
max_jobs = 8
max_memory_gb = 32
max_runtime_hours = 48

# 低配置开发机
max_jobs = 1
max_memory_gb = 4
max_runtime_hours = 2
```

---

### [database] - 数据库

```toml
[database]
path = "tasks.db"    # SQLite 数据库文件路径
```

**路径说明**:
- 相对路径: 相对于 `workspace` 目录
- 绝对路径: 指定完整路径

**示例**:
```toml
# 相对于 workspace
path = "tasks.db"
# 实际位置: workspace/tasks.db

# 绝对路径
path = "/var/lib/exp-platform/tasks.db"
```

---

### [logging] - 日志

```toml
[logging]
level = "INFO"           # 日志级别
console = true           # 输出到控制台
file = true              # 写入日志文件
file_path = "server.log" # 日志文件路径
```

**日志级别** (从详细到简略):
- `"DEBUG"` - 调试信息（最详细）
- `"INFO"` - 一般信息
- `"WARNING"` - 警告信息
- `"ERROR"` - 错误信息（最简略）

**示例配置**:
```toml
# 开发环境 - 详细日志
level = "DEBUG"
console = true
file = false

# 生产环境 - 精简日志
level = "WARNING"
console = false
file = true
file_path = "/var/log/exp-platform/server.log"
```

---

## 前端配置

### [frontend] - 前端开发服务器

```toml
[frontend]
dev_port = 5173                              # 开发服务器端口
dist_dir = "dist"                            # 构建输出目录
api_proxy_target = "http://localhost:8080"   # API 代理目标
ws_proxy_target = "ws://localhost:8080"      # WebSocket 代理目标
```

**说明**:

**dev_port** - 开发服务器端口
- Vite 开发服务器监听端口
- 浏览器访问此端口查看应用

**dist_dir** - 构建输出目录
- `npm run build` 输出目录
- 生产部署时使用

**api_proxy_target** - API 代理
- 前端 `/api/*` 请求代理到此地址
- 开发环境避免 CORS 问题
- **必须与后端 `[server]` 端口一致**

**ws_proxy_target** - WebSocket 代理
- 前端 `/ws` WebSocket 连接代理到此地址
- **必须与后端 `[server]` 端口一致**

**重要**: 前端代理目标端口必须与后端服务端口匹配！

```toml
# ✅ 正确配置
[server]
port = 8080

[frontend]
api_proxy_target = "http://localhost:8080"
ws_proxy_target = "ws://localhost:8080"

# ❌ 错误配置 - 端口不匹配
[server]
port = 8080

[frontend]
api_proxy_target = "http://localhost:8000"  # 端口不对！
```

---

## 配置工作原理

### 后端读取配置

```python
# backend/app/main.py
from pathlib import Path
from app.config import Config

# 从项目根目录读取 config.toml
config_path = Path(__file__).parent.parent.parent / "config.toml"
config = Config.from_file(str(config_path))
```

### 前端读取配置

```javascript
// frontend/vite.config.js
import * as toml from 'toml'
import { readFileSync } from 'fs'

// 读取 ../config.toml
const configContent = readFileSync('../config.toml', 'utf-8')
const config = toml.parse(configContent)

// 使用配置
export default defineConfig({
  server: {
    port: config.frontend.dev_port,
    proxy: {
      '/api': { target: config.frontend.api_proxy_target }
    }
  }
})
```

---

## 环境特定配置

### 开发环境

```toml
[server]
port = 8080
host = "127.0.0.1"

[execution]
max_jobs = 2
max_memory_gb = 8
max_runtime_hours = 2
use_systemd = false

[logging]
level = "DEBUG"
console = true
file = false

[frontend]
dev_port = 5173
api_proxy_target = "http://localhost:8080"
```

### 生产环境

```toml
[server]
port = 8000
host = "0.0.0.0"

[workspace]
path = "/data/exp-platform/workspace"

[execution]
max_jobs = 8
max_memory_gb = 32
max_runtime_hours = 48
use_systemd = true

[database]
path = "/data/exp-platform/tasks.db"

[logging]
level = "INFO"
console = false
file = true
file_path = "/var/log/exp-platform/server.log"

[frontend]
dist_dir = "dist"
api_proxy_target = "http://localhost:8000"
```

---

## 常见问题

### Q: 配置文件不存在怎么办？

A: 后端会使用默认配置，前端 Vite 配置也有默认值。但建议始终创建 `config.toml`：

```bash
cp config.toml.example config.toml
```

### Q: 如何验证配置是否正确？

A: 运行后端测试：

```bash
cd backend
uv run pytest -v
```

所有测试通过说明配置正确。

### Q: 前端无法连接后端怎么办？

A: 检查端口配置是否一致：

1. 查看后端启动日志: `Uvicorn running on http://0.0.0.0:8080`
2. 确认 `config.toml` 中 `[frontend].api_proxy_target` 端口与 `[server].port` 一致
3. 重启前端开发服务器

### Q: 如何在不同环境使用不同配置？

A: 使用环境变量或多个配置文件：

```bash
# 方法 1: 符号链接
ln -s config.production.toml config.toml

# 方法 2: 环境变量（需修改代码支持）
export CONFIG_FILE=/path/to/production.toml
```

---

## 配置优先级

1. `config.toml` (如果存在)
2. 默认值 (代码中内置)

如果配置文件不存在，应用会使用硬编码的默认值正常运行。

---

## 配置更新

修改配置后：
- **后端**: 需要重启服务 (`Ctrl+C` 后重新运行)
- **前端**: Vite 会自动检测配置变化并重新加载

---

## 更多信息

- [README.md](../README.md) - 快速开始指南
- [设计文档](plans/2026-01-26-experiment-platform-design.md) - 系统架构
- [实施计划](plans/2026-01-26-experiment-platform-implementation.md) - 开发计划
