# Runner API Delete/Disable/Enable 语义拆分设计文档

## 背景

当前 runner 管理接口中，`DELETE /api/admin/runners/{runner_id}` 实际执行的是软禁用（`enabled=false`），这与 HTTP `DELETE` 的语义不一致。前端也把“Delete”文案和“禁用”行为混在一起，容易误导运维操作。

## 目标

1. 明确区分三类管理动作：`delete`（真删除）、`disable`（禁用）、`enable`（启用）。
2. 保持现有 runner 鉴权与任务租约恢复机制不变。
3. 让前端动作、文案和后端语义一致，降低误操作风险。

## 非目标

- 不改动 runner token 的生成与存储算法。
- 不变更任务调度核心策略（包含租约过期回收机制）。
- 不引入新的 runner 生命周期状态字段（继续使用 `enabled` + health 计算）。

## 已确认决策

1. `DELETE /api/admin/runners/{runner_id}` 改为真正删除 runner 记录。
2. 新增 `POST /api/admin/runners/{runner_id}/disable` 表示禁用。
3. 新增 `POST /api/admin/runners/{runner_id}/enable` 表示启用。
4. delete、disable、enable 三者都要求 `X-Admin-Token`。
5. delete/disable 后，runner 侧接口鉴权应立即失败；已 claim 任务保持运行态直到 lease 过期后由调度器回收。

## 总体方案

采用“显式动作接口”方案：

- 保留资源级 delete：`DELETE /api/admin/runners/{runner_id}`（真删除）。
- 新增动作接口：
  - `POST /api/admin/runners/{runner_id}/disable`
  - `POST /api/admin/runners/{runner_id}/enable`

该方案与现有任务接口的动作风格（如 `/cancel`, `/retry`）一致，且改造成本低、语义清晰。

## API 合约设计

### 1) DELETE `/api/admin/runners/{runner_id}`

语义：永久删除 runner。

- 请求头：`X-Admin-Token`
- 成功：`200 OK`，返回 `RunnerResponse`（删除前快照）
- 失败：
  - `404 Runner not found`
  - `403 Forbidden`（admin token 无效）

效果：

- `GET /api/admin/runners` 列表不再出现该 runner。
- runner 之后调用 `/heartbeat`、`/claim`、`/events`、`/logs/chunks` 均因 runner 不存在而 `403`。

### 2) POST `/api/admin/runners/{runner_id}/disable`

语义：禁用 runner（`enabled=false`），不删除记录。

- 请求头：`X-Admin-Token`
- 成功：`200 OK`，返回 `RunnerResponse`（`enabled=false`）
- 失败：
  - `404 Runner not found`
  - `403 Forbidden`

幂等性约定：

- 若 runner 已经是 `enabled=false`，接口仍返回 `200` + 当前 runner 状态（不报错）。

效果：

- runner 仍在管理列表中，health 为 `disabled`。
- runner 鉴权立即失效，与当前 disable 行为保持一致。

### 3) POST `/api/admin/runners/{runner_id}/enable`

语义：启用 runner（`enabled=true`）。

- 请求头：`X-Admin-Token`
- 成功：`200 OK`，返回 `RunnerResponse`（`enabled=true`）
- 失败：
  - `404 Runner not found`
  - `403 Forbidden`

幂等性约定：

- 若 runner 已经是 `enabled=true`，接口仍返回 `200` + 当前 runner 状态（不报错）。

效果：

- runner 恢复鉴权资格；health 按 `last_seen_at` 重新计算为 `online/offline`。

## 后端设计

### 1) `backend/app/database.py`

新增/调整 runner 相关数据访问方法：

1. 保留 `disable_runner(runner_id)`。
2. 新增 `enable_runner(runner_id)`，将 `enabled` 更新为 `1`。
3. 新增 `delete_runner(runner_id)`，从 `runners` 表物理删除记录。

返回值约定：

- `disable_runner` / `enable_runner` 返回“runner 是否存在”语义（而非“状态是否发生变化”）。
- `delete_runner` 返回“是否成功删除”。

实现建议：

- handler 先按 `runner_id` 查询 runner，不存在返回 `404`；存在则执行状态写入，再读回响应。
- 通过该流程保证 disable/enable 幂等请求始终返回 `200`。

### 2) `backend/app/main.py`

调整 runner admin 路由：

1. 现有 `DELETE /api/admin/runners/{runner_id}` 从“禁用”改为“真删除”。
2. 新增 `POST /api/admin/runners/{runner_id}/disable`。
3. 新增 `POST /api/admin/runners/{runner_id}/enable`。

实现约束：

- 三个接口都先通过依赖完成 admin 鉴权（无效 token 返回 `403`），再读取 runner，不存在返回 `404`。
- delete 返回删除前快照，避免删除后无法组装响应。
- disable/enable 返回更新后状态。

### 3) 鉴权与租约兼容性

不改 `require_runner_auth` 现有逻辑：runner 不存在或 `enabled=false` 都是 `403`。

对已 claim 任务的影响：

- delete/disable 不直接强制改任务状态。
- 任务仍为 `running`，由 lease 到期机制在 `TaskScheduler.reconcile_expired_leases()` 中回收至 `pending`。

### 4) 数据库迁移

无需 schema 迁移。

- `runners.enabled` 字段已存在，可直接支撑 enable/disable。
- 删除 runner 不影响 `tasks.runner_id` 历史数据读取（当前无外键约束）。

## 前端设计

### 1) `frontend/src/services/api.js`

管理接口拆分：

1. `deleteRunner(runnerId)`：继续存在，语义改为真删除。
2. 新增 `disableRunner(runnerId)`。
3. 新增 `enableRunner(runnerId)`。

### 2) `frontend/src/views/RunnerList.vue`

动作与文案调整：

1. “Delete”按钮文案与确认提示改为“永久删除，不可恢复”。
2. 对 `runner.enabled=true` 显示 `Disable` 按钮。
3. 对 `runner.enabled=false` 显示 `Enable` 按钮。

交互约束：

- delete/disable/enable 使用独立 loading 状态，防止重复点击。
- 动作成功后统一刷新 `fetchRunners()`。
- 动作失败时沿用现有错误提示模式（alert 或页面错误文案）。

### 3) 用户可见效果

1. Disable 后 runner 保留在列表，状态变 `disabled`。
2. Enable 后 runner 状态回到 `online/offline`。
3. Delete 后 runner 从列表移除。

## 文档更新

需要同步更新：

- `Project.md` 中 distributed runner API 表格与删除语义说明。
- 如存在 README 或运维手册中的 runner 管理说明，也需同步替换 delete/disable 描述。

## 测试设计

### 后端

1. `backend/tests/integration/test_runner_admin_api.py`
   - 原 `test_delete_runner_soft_disables` 改为验证 delete 真删除。
   - 新增 disable 接口测试（`enabled=false`）。
   - 新增 enable 接口测试（disable 后 enable 恢复为 true）。
2. `backend/tests/unit/test_database.py`
   - 保留 disable 测试。
   - 新增 enable 与 delete_runner（runner 维度）测试。
3. `backend/tests/integration/test_runner_control_api.py`
   - 将依赖“DELETE=disable”的用例改为调用 `/disable`。
   - 新增“enable 后 runner 恢复鉴权可访问”验证。
4. 新增 lease 回收回归用例（可放在 `test_runner_control_api.py`）：
   - 场景 A：claim 任务后 `disable` runner，确认 lease 未过期前任务仍为 `running`，过期并触发调度回收后变 `pending`。
   - 场景 B：claim 任务后 `delete` runner，确认 lease 未过期前任务仍为 `running`，过期并触发调度回收后变 `pending`。

### 前端

至少完成功能回归验证：

1. Runner 列表可正常 delete/disable/enable。
2. 按钮状态、文案、loading 与结果一致。
3. `npm run build` 通过。

## 风险与权衡

1. 操作习惯迁移风险：历史上“Delete=Disable”，切换后需要更明确确认文案。
2. 删除不可恢复：误删后需重新创建 runner 并重新分发 token。
3. token 失效预期：delete 后 runner 端会立刻 403，属于预期行为。

## 验收标准

1. `DELETE /api/admin/runners/{runner_id}` 真正删除 runner。
2. `POST /disable` 与 `POST /enable` 均可用且语义正确。
3. 前端 Runner 页面提供 Delete/Disable/Enable 区分动作。
4. 相关测试通过，`Project.md` 文档与实际行为一致。
