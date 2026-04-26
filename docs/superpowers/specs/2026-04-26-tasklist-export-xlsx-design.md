# TaskList 导出 XLSX 功能设计

## 目标
在 TaskList 页面添加一个导出按钮，允许用户将所有任务数据下载为 `.xlsx` 表格文件。

## 方案
采用**前端方案 A**：在浏览器端使用 `xlsx`（SheetJS）库将任务数组直接生成并下载 xlsx 文件。无需后端改动。

## 改动范围
- `frontend/package.json` — 新增 `xlsx` 依赖
- `frontend/src/views/TaskList.vue` — 添加导出按钮及点击处理
- `frontend/src/utils/exportXlsx.js` — 新建工具函数，负责数据转换与文件下载

## 导出内容
导出全部任务（`tasks.value`），不受当前页面筛选/排序影响。

| 表头（中文） | 数据源 / 计算方式 |
|---|---|
| ID | `id` |
| Crate | `crate_name` |
| Version | `version` |
| Status | `status` |
| Cases | `case_count` |
| POCs | `poc_count` |
| Compile Failed | `compile_failed` |
| Runtime (s) | `finished_at - started_at` 的秒数，未结束则为空 |
| Runner | `runner_id` |
| Created At | `created_at` |
| Started At | `started_at` |
| Finished At | `finished_at` |

## 文件名
`tasks_YYYYMMDD_HHMMSS.xlsx`

## UI 设计
- 在 TaskList.vue 标题栏右侧（"Batch Create" 左侧或右侧）新增 **"Export XLSX"** 按钮
- 样式：灰色边框按钮（`border border-gray-300 bg-white text-gray-700`），与现有操作按钮风格一致
- 当 `tasks.value.length === 0` 或 `loading` 时，按钮 `disabled`

## 错误处理
- 无任务时按钮禁用
- 导出异常时通过 `alert()` 提示用户

## 依赖
- `xlsx`（npm 包，SheetJS 社区版）
