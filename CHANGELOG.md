# 更新日志 (Changelog)

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- 统一配置文件系统 - 单一 `config.toml` 管理前后端配置
- 详细的配置文档 `docs/CONFIGURATION.md`
- 前端自动从 config.toml 读取代理配置
- 完整的 Vue 3 前端应用（Dashboard, TaskList, TaskDetail）
- WebSocket 实时更新支持
- 日志查看和下载功能
- 系统资源监控（CPU, Memory, Disk）

### Changed
- **重要**: 配置文件从 `backend/config.toml` 移动到项目根目录 `config.toml`
- 前端 Vite 配置改为从 config.toml 动态读取
- 更新 README 说明统一配置方式
- 后端默认端口从 8000 改为 8080

### Fixed
- 修复 crate 解压嵌套目录问题

## [2026-01-26] - Phase 4 & 5 实现

### Added
- 后端 Phase 4: Dashboard APIs, Log endpoints, WebSocket support
- 前端 Phase 5: 完整 Vue 3 SPA 应用
- 55 个测试（全部通过）
- Git worktrees 并行开发

### Technical Details
- FastAPI WebSocket 实时推送
- Vue 3 Composition API
- Tailwind CSS Bento Grid 布局
- Axios HTTP 客户端
- psutil 系统监控

## [Earlier] - Phase 1-3 实现

### Added
- 配置管理系统
- SQLite 数据库层
- Crates.io API 客户端
- 资源限制工具（systemd-run / resource）
- 任务执行器
- 任务调度器
- FastAPI 基础应用
- 35 个测试（全部通过）

---

## 迁移指南

### 从旧版本迁移到统一配置

如果你之前使用 `backend/config.toml`：

1. 将 `backend/config.toml` 移动到项目根目录：
   ```bash
   mv backend/config.toml ./config.toml
   ```

2. 在 `config.toml` 中添加前端配置：
   ```toml
   [frontend]
   dev_port = 5173
   dist_dir = "dist"
   api_proxy_target = "http://localhost:8080"
   ws_proxy_target = "ws://localhost:8080"
   ```

3. 更新代码（如果有自定义修改）：
   - 后端从根目录读取配置
   - 前端安装 toml 依赖: `cd frontend && npm install toml --save-dev`

4. 测试配置：
   ```bash
   cd backend && uv run pytest
   ```
