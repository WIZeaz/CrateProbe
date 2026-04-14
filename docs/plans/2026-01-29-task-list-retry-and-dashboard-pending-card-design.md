# 任务列表重试操作与Dashboard待处理任务卡片设计

**日期:** 2026-01-29

## 概述

本设计包含两个功能增强：
1. 在任务列表界面的操作列中添加重试按钮
2. 在Dashboard添加待处理任务统计卡片，并为所有统计卡片添加点击跳转功能

## 功能1: 任务列表重试操作

### 需求
在TaskList.vue的Actions列中添加重试按钮，允许用户直接从列表视图重试任何任务。

### 设计细节

#### UI修改
- 修改TaskList.vue的Actions列（目前只显示删除按钮）
- 为所有任务添加重试按钮
- 重试按钮使用绿色/蓝色样式，与红色的删除按钮区分
- 布局：在Actions列中同时显示删除和重试按钮

#### 按钮行为
- **运行中的任务**: 重试按钮显示为禁用状态"—"，与删除按钮保持一致
- **其他所有任务**: 显示可点击的重试按钮
- **点击确认**: 显示确认对话框，提示用户将重置并重新执行任务
- **成功后**: 刷新任务列表以显示更新的状态

#### API集成
- 复用`services/api.js`中现有的`api.retryTask(taskId)`方法
- 该方法调用`POST /tasks/{id}/retry`端点
- 重试行为：重置任务状态并重新执行，复用相同的task ID
- 错误处理：使用alert显示错误消息
- 刷新策略：调用`fetchTasks()`完整刷新任务列表

#### 实现文件
- `frontend/src/views/TaskList.vue` - 添加重试按钮和处理函数

## 功能2: Dashboard待处理任务卡片与交互增强

### 需求
在Dashboard添加第5张统计卡片显示待处理任务数量，并为所有统计卡片添加点击跳转到对应过滤视图的功能。

### 设计细节

#### Dashboard UI修改
- 将stats网格布局从4列改为5列：`grid-cols-1 md:grid-cols-2 lg:grid-cols-5`
- 添加第5张Pending统计卡片：
  - 标题：`"Pending"` 或 `"待处理"`
  - 图标：`"⏳"` 或 `"📋"`
  - 颜色：`color="purple"` 或 `color="orange"`
  - 数据源：`dashboard.pending_tasks`（数据已在`fetchDashboard`中获取）

#### 所有卡片的点击跳转功能
所有统计卡片都应支持点击跳转到对应的任务列表过滤视图：

| 卡片 | 跳转路径 | 过滤状态 |
|------|---------|---------|
| Total Tasks | `/tasks` | `all` |
| Running | `/tasks?status=running` | `running` |
| Completed | `/tasks?status=completed` | `completed` |
| Failed | `/tasks?status=failed` | `failed` |
| Pending | `/tasks?status=pending` | `pending` |

- 为所有卡片添加`cursor-pointer`样式和hover效果
- 提示用户卡片可点击

#### TaskList URL参数解析
在TaskList.vue中添加URL查询参数解析功能：

- 在`onMounted`钩子中读取`route.query.status`
- 如果存在status参数，自动设置`filterStatus.value`为对应值
- 示例：访问`/tasks?status=pending`时，自动过滤显示pending状态的任务

#### StatCard组件适配
需要检查并可能修改StatCard.vue组件：

**方案A（首选）**: 如果StatCard已支持点击事件
- 在Dashboard中为每个StatCard添加`@click`事件处理
- 使用`router.push()`跳转到对应路径

**方案B**: 如果StatCard不支持点击
- 修改StatCard组件添加可选的`clickable`属性
- 添加可选的`onClick`回调属性
- 当`clickable=true`时，添加hover样式和cursor-pointer
- 触发点击时调用`onClick`回调

#### 实现文件
- `frontend/src/views/Dashboard.vue` - 添加第5张卡片，为所有卡片添加点击处理
- `frontend/src/views/TaskList.vue` - 添加URL参数解析
- `frontend/src/components/StatCard.vue` - 可能需要修改以支持点击（取决于当前实现）

## 数据流

### 重试操作流程
1. 用户在TaskList点击重试按钮
2. 显示确认对话框
3. 调用`api.retryTask(taskId)`
4. 后端重置任务状态并重新排队执行
5. 刷新任务列表显示最新状态

### Dashboard卡片点击流程
1. 用户点击统计卡片
2. 调用`router.push('/tasks?status=xxx')`跳转到任务列表
3. TaskList组件挂载时读取URL参数
4. 自动设置过滤器为对应状态
5. 显示过滤后的任务列表

## 用户体验考虑

- **一致性**: 重试按钮行为与TaskDetail中的重试功能保持一致
- **反馈**: 重试操作前显示确认对话框，避免误操作
- **导航**: 统计卡片提供快速过滤导航，提高操作效率
- **视觉提示**: 卡片hover效果明确表示可点击性

## 测试要点

### 重试功能测试
- 验证重试按钮仅在非running任务上可点击
- 验证确认对话框正确显示任务信息
- 验证重试后任务状态正确重置
- 验证错误处理（网络错误、后端错误）

### Dashboard跳转测试
- 验证所有5张卡片都能正确跳转
- 验证URL参数正确传递
- 验证TaskList正确解析并应用过滤
- 验证响应式布局在不同屏幕尺寸下正常显示（1-5列自适应）
