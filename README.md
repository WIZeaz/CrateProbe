# 实验平台 (Experiment Platform)

自动化下载Rust crate、运行实验并提供Web界面实时展示实验进度和结果的平台。

## 功能特性

- 自动下载和测试Rust crates
- 并发任务执行（可配置）
- 实时Web界面监控
- 资源限制（内存、时间）
- 详细的日志记录和展示

## 技术栈

- **后端**: Python 3.10+, FastAPI, SQLite
- **前端**: Vue 3, Tailwind CSS, Vite

## 文档

详细设计文档请见：[docs/plans/2026-01-26-experiment-platform-design.md](docs/plans/2026-01-26-experiment-platform-design.md)

## 快速开始

### 1. 配置

项目使用**统一的配置文件** `config.toml` 管理前后端配置。

```bash
# 复制示例配置文件
cp config.toml.example config.toml

# 根据需要编辑配置（可选）
# 默认配置：后端端口 8080，前端端口 5173
vim config.toml
```

**配置文件说明**：
- `[server]` - 后端 FastAPI 服务器配置（端口、地址）
- `[workspace]` - 工作空间目录
- `[execution]` - 任务执行配置（并发数、资源限制）
- `[database]` - 数据库配置
- `[logging]` - 日志配置
- `[frontend]` - 前端开发服务器配置和代理设置

### 2. 启动后端

本项目使用 [uv](https://github.com/astral-sh/uv) 进行Python依赖管理。

```bash
cd backend

# 安装依赖
uv sync

# 运行测试（可选）
uv run pytest

# 启动后端服务
uv run python -m app.main
```

后端将在配置文件指定的端口启动（默认 `http://localhost:8080`）

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端将自动：
- 从 `config.toml` 读取配置
- 在配置的端口启动（默认 `http://localhost:5173`）
- 代理 `/api` 和 `/ws` 请求到后端

### 4. 访问应用

打开浏览器访问 `http://localhost:5173`

## 开发

本项目遵循TDD原则，请在实现功能前编写测试。

详细实施计划请见 docs/plans/ 目录。
