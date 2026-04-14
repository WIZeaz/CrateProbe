# Settings 页面设计文档

## 目标
为实验平台前端添加一个 Settings 页面，允许用户输入并保存 Admin Token，所有设置项持久化到 `localStorage`。

## 非目标
- 不实现用户登录/登出系统
- 不实现服务端设置同步
- 不添加除 Admin Token 外的其他设置项（但架构需支持扩展）

## 数据模型（localStorage）

**Key:** `lifesonar_settings`

**Value 结构:**
```json
{
  "version": 1,
  "security": {
    "adminToken": ""
  }
}
```

- 使用 `version` 字段，便于未来做 schema 迁移。
- `useSettings` composable 负责读取、合并默认值、reactive 暴露、延迟持久化。

## 组件与模块设计

### 1. `frontend/src/composables/useSettings.js`（新增）
- 读取 `localStorage['lifesonar_settings']`
- 与默认值做深度合并
- 返回 `{ settings, updateSetting, saveSettings, isLoaded }`
- `settings` 为 Vue reactive 对象
- 任何修改通过 `saveSettings()` 显式触发持久化

### 2. `frontend/src/views/Settings.vue`（新增）
- 页面布局：分类侧边栏 + 右侧表单区域（响应式）
- 当前分类：Security
  - Admin Token 输入框（支持显示/隐藏切换）
  - 保存按钮
- 保存逻辑：
  1. 调用 `HEAD /api/admin/runners` 验证 token
  2. 若返回 200，写入 localStorage，显示绿色成功提示
  3. 若返回 403，显示红色错误提示，**不**写入 localStorage
  4. 若网络错误，显示网络错误提示

### 3. 导航栏修改（`frontend/src/App.vue`）
- 导航末尾添加 "Settings" 链接
- 当 `security.adminToken` 为空时，Settings 链接右侧显示 ⚠️ 警告图标
- 移动端菜单同步更新

### 4. 路由修改（`frontend/src/router/index.js`）
- 新增 `/settings` 路由，懒加载 `Settings.vue`

### 5. Admin Token 服务迁移（`frontend/src/services/adminAuth.js`）
- `getAdminToken()` 改为从 `useSettings` 读取 `security.adminToken`
- `setAdminToken(token)` 改为更新 `useSettings` 并持久化
- `clearAdminToken()` 清空 token
- 移除直接依赖 `sessionStorage`

### 6. API 拦截器（`frontend/src/services/api.js`）
- 保持不变，继续使用 `adminAuth.js` 的 `getAdminToken()`

### 7. RunnerList 页面（`frontend/src/views/RunnerList.vue`）
- 检测到 `!hasAdminToken` 时，使用 `router.replace('/settings')` 自动跳转
- 移除原有的静态错误提示（或改为一闪而过的提示）

### 8. Backend 支持 HEAD 验证（`backend/app/main.py`）
- 为 `/api/admin/runners` 添加 `HEAD` 方法
- HEAD 请求复用 `require_admin_token` 依赖和 `list_runners` 处理函数，但返回空 body
- FastAPI 默认对同一个 path operation 支持注册 `methods=["GET", "HEAD"]`

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| token 验证 403 | Settings 页面显示 "Admin token is invalid." |
| token 验证 200 | 显示 "Settings saved successfully." |
| 网络错误 | 显示 "Unable to reach server. Please try again." |
| localStorage 被禁用 | 静默降级，仅在内存中保存（控制台 warn） |

## 扩展性

- 添加新分类：在 `Settings.vue` 的 `categories` 数组中新增项
- 添加新字段：在 `useSettings.js` 的 `defaultSettings` 中新增默认值
- 不同分类的表单字段可通过 `<component :is="fieldComponent" />` 动态渲染

## 验收标准

1. 用户可以在 Settings 页面输入 Admin Token 并保存。
2. 保存前通过 HEAD 请求验证 token 有效性。
3. token 保存后，RunnerList 页面可以正常加载数据。
4. token 未设置时，导航栏 Settings 入口显示警告图标。
5. token 未设置时，直接访问 `/runners` 自动重定向到 `/settings`。
6. 刷新页面后，已保存的 token 仍然有效。
