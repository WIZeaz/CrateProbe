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

### 配置

复制并编辑配置文件：
```bash
cp config.toml.example config.toml
```

### 后端

```bash
cd backend
pip install -r requirements.txt
python -m app.main
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

## 开发

本项目遵循TDD原则，请在实现功能前编写测试。

详细实施计划请见 docs/plans/ 目录。
