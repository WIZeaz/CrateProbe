# crates.io API 使用政策合规设计

## 摘要

当前 runner 通过 `backend/runner/crates_api.py` 访问 crates.io：

- `get_latest_version()` / `verify_version_exists()` 调用 `https://crates.io/api/v1/crates/{crate}`（crates.io API）。
- `download_crate()` 调用 `https://static.crates.io/crates/...`（static.crates.io CDN）。

根据 crates.io 的 [Data Access Policy](https://crates.io/data-access)（源码：[`rust-lang/crates.io/svelte/src/routes/data-access/+page.svelte`](https://github.com/rust-lang/crates.io/blob/main/svelte/src/routes/data-access/+page.svelte)），对 crates.io API 的调用必须满足：

1. 最多 **1 request / second**；
2. `User-Agent` 必须能识别应用，并**建议**提供联系方式；
3. 大量/全量数据应优先使用 sparse index、git index 或 database dumps。

static.crates.io 的 crate 文件下载目前**没有**速率限制。

本设计在保持现有行为的前提下，让 runner（以及复用同一客户端的 app）满足上述 API 政策，并增加缓存、退避和可观测性。

## 目标

- 对 `crates.io/api/v1/...` 的调用限速在 **1 req/s** 以内。
- 使用符合政策的 `User-Agent`，默认 `crateprobe-runner`，可通过环境变量覆盖。
  - 政策**建议**在 User-Agent 中提供联系方式；若需完全合规，建议通过 `CRATES_IO_USER_AGENT` 设置包含联系信息的字符串。
- 缓存版本元数据，避免在短时间内对同一 crate 重复调用 API。
- 对 429 / 5xx / 网络错误实现指数退避重试，并尊重 `Retry-After` 响应头。
- 将 crate 下载相关能力集中到共享的 `CrateDownloader`，方便并发任务统一复用。
- 补充单元测试覆盖新增逻辑。

## 非目标

- 本次不引入持久化缓存（磁盘/SQLite），仅使用进程内 TTL 缓存。
- 本次不替换 cargo 在 Docker 容器内的依赖下载行为（依赖 cargo 自身的 registry 机制）。
- 不解决多 runner 进程/容器共享同一出口 IP 时的全局限速问题；本次限流器为单进程级别。

## 架构与组件

```
+----------------------------+
|         Runner             |
|  (single process)          |
|                            |
|  +----------------------+  |
|  |  CrateDownloader     |  |<------ shared by all TaskExecutors
|  |  - resolve_version() |  |
|  |  - download()        |  |
|  +----------+-----------+  |
|             |              |
|             v              |
|  +----------------------+  |
|  |  CratesAPI           |  |<------ shared by all callers
|  |  - get_latest_version|  |
|  |  - verify_version_...|  |
|  |  - token bucket      |  |
|  |  - TTL cache         |  |
|  +----------+-----------+  |
|             |              |
|    crates.io/api/v1        |
|    static.crates.io        |
+----------------------------+
```

### `CratesAPI`（元数据客户端）

文件：`backend/runner/crates_api.py`

职责：只负责 `crates.io/api/v1/...` 的元数据查询。

改造点：

- 接收可选 `user_agent` 参数，默认 `"crateprobe-runner"`。
- 所有实例共享一个异步 **token-bucket 限流器**，默认 `1 req/s`，burst=1。
- 所有实例共享一个 **in-memory TTL 缓存**，key 为 `(crate_name, endpoint)`，默认 TTL 300 秒。
- `get_latest_version()` 和 `verify_version_exists()` 先查缓存，未命中则 `await rate_limiter.acquire()`，再发请求。
- 保留 `CrateNotFoundError` / `VersionNotFoundError` 语义。

### `CrateDownloader`（下载入口）

新增文件，建议：`backend/runner/crate_downloader.py`

职责：作为 `TaskExecutor` 与 crates.io 交互的统一入口，所有 executor 实例共享同一个 `CrateDownloader`。

提供方法：

- `async resolve_version(crate_name: str, version: Optional[str]) -> str`
  - 若 `version` 为 `None`，调用 `CratesAPI.get_latest_version()`。
  - 若 `version` 不为 `None`，调用 `CratesAPI.verify_version_exists()`；不存在则抛 `VersionNotFoundError`。
- `async download(crate_name: str, version: str, output_path: Path) -> None`
  - 从 `static.crates.io` 下载 `.crate` 文件。
  - 支持最大并发下载数限制（信号量），默认与 `max_jobs` 一致或单独配置。
  - 失败时按指数退避重试，遇到 429 优先读取 `Retry-After`。

### 配置项

在 `backend/runner/config.py` 的 `RunnerConfig` 中新增：

| 配置名 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `crates_io_user_agent` | `CRATES_IO_USER_AGENT` | `crateprobe-runner` | 发送给 crates.io 的 User-Agent |
| `crates_io_rate_limit_rps` | `CRATES_IO_RATE_LIMIT_RPS` | `1.0` | API 调用最大速率 |
| `crates_io_cache_ttl_seconds` | `CRATES_IO_CACHE_TTL_SECONDS` | `300` | 元数据缓存 TTL |
| `crates_io_max_concurrent_downloads` | `CRATES_IO_MAX_CONCURRENT_DOWNLOADS` | 运行时 `max_jobs` 的值 | static.crates.io 并发下载限制 |

`app/main.py` 直接复用 `CratesAPI`，因此 `app/main.py` 也应读取 `CRATES_IO_USER_AGENT` 环境变量（或在 `app/config.py` 中增加等价配置项），确保 API 调用侧也使用合规的 User-Agent。

## 数据流

以 `TaskExecutor` 执行一个任务为例：

1. **Runner 启动**
   - 创建共享 `CratesAPI()`（带限流器、缓存、UA）。
   - 创建共享 `CrateDownloader(crates_api=crates_api)`。
   - 将该 `CrateDownloader` 注入每个 `TaskExecutor`。

2. **版本解析**
   - `TaskExecutor` 调用 `await crate_downloader.resolve_version(crate_name, version)`。
   - `CrateDownloader` 内部调用 `CratesAPI` 的对应方法。
   - `CratesAPI` 先查缓存；未命中则获取 token 再发请求；缓存命中直接返回，不消耗 API 配额。

3. **crate 下载**
   - `TaskExecutor` 调用 `await crate_downloader.download(crate_name, version, output_path)`。
   - `CrateDownloader` 从 `static.crates.io` 下载，受可选并发信号量限制。
   - 失败时按指数退避重试。

## 错误处理

| 场景 | 行为 |
| --- | --- |
| `404`（crate/版本不存在） | 立即抛 `CrateNotFoundError` / `VersionNotFoundError`，不重试 |
| `429 Too Many Requests` | 读取 `Retry-After`，按该值等待；未提供则指数退避（如 1s、2s、4s…） |
| 其他 `5xx` / 网络错误 | 指数退避重试，最多 `MAX_RETRIES` 次 |
| 限速等待超时 | 记录 warning，若超过任务允许时间则让任务失败 |

## 日志

每次 API 调用记录：

- `crate_name`
- `endpoint`
- `user_agent`
- `cache_hit`（是否命中缓存）
- `wait_seconds`（因限速等待的时间）

每次 429 记录：

- `retry_after`
- `backoff_seconds`

保持现有任务生命周期日志：`pending -> running -> terminal state`。

## 测试

新增/更新以下测试：

1. **`backend/tests/unit/runner/test_crates_api.py`（新增）**
   - 用 `httpx` + `respx` mock crates.io API。
   - 验证 `User-Agent` 默认值与环境变量覆盖。
   - 验证 1 req/s 限速生效。
   - 验证缓存生效及 TTL 过期。
   - 验证 429 指数退避 / `Retry-After` 处理。
   - 验证 404 立即抛异常、不重试。
   - 验证网络异常重试上限。

2. **`backend/tests/unit/runner/test_crate_downloader.py`（新增）**
   - mock `CratesAPI` 测试 `resolve_version()` 各分支。
   - mock static.crates.io 下载，验证正常写入、429 退避、并发限制。

3. **`backend/tests/unit/runner/test_executor.py`（更新）**
   - 更新 mock，确保 `TaskExecutor` 通过 `CrateDownloader` 完成下载。
   - 验证 `CrateDownloader` 被正确注入。

4. **`backend/tests/unit/test_main.py`（更新）**
   - 若 `CratesAPI` 改为模块级单例，调整 mock 策略，确保测试仍可控制。

5. **配置测试**
   - 验证 `RunnerConfig` 正确读取新增环境变量及默认值。

## 风险与回退

- **限速导致任务变慢**：缓存会显著抵消；若仍太慢，用户可调高 `CRATES_IO_CACHE_TTL_SECONDS`，或后续升级持久化缓存/数据库 dump。
- **进程内缓存不一致**：多个 runner 进程各自缓存，但对 crates.io 而言仍是独立客户端，符合当前政策。
- **测试 mock 复杂度**：使用 `respx` 可保持测试简洁；如果项目未引入 `respx`，可用 `pytest-httpx` 或手工 mock `httpx.AsyncClient`。

## 参考

- [crates.io Data Access Policy](https://crates.io/data-access)
- [crates.io Data Access Policy 页面源码](https://github.com/rust-lang/crates.io/blob/main/svelte/src/routes/data-access/+page.svelte)
- [Cargo Registry Index 文档](https://doc.rust-lang.org/cargo/reference/registry-index.html)
