# Docker Mounts Configuration Design

## Overview
Add a configuration option to the docker runner to allow users to specify custom mounting options (a list of mounts) in `config.toml`.

## Architecture & Data Flow

1. **Configuration (`config.toml.example` & `config.py`)**:
   - Add a `mounts` array of strings to the `[execution.docker]` section.
   - Example format: `["/host/path:/container/path:rw"]`.
   - Update `app.config.AppConfig` to include a `docker_mounts: list[str]` field (default empty list).
   - Update config parsing logic in `AppConfig.load()` to parse the `mounts` array.

2. **Task Executor (`task_executor.py`)**:
   - Update `DockerRunner` instantiation to pass `config.docker_mounts`.

3. **Docker Runner (`docker_runner.py`)**:
   - Update `DockerRunner.__init__` to accept `mounts: List[str] = None`.
   - In `DockerRunner.run()`, construct the `volumes` parameter for `client.containers.run()`.
   - Use the list format for volumes: `[f"{workspace_dir.resolve()}:/workspace:rw"] + self.mounts`.

4. **Testing (`test_config.py` & `test_docker_runner.py` & `test_task_executor.py`)**:
   - Add tests for parsing `mounts` in `config.toml`.
   - Add tests for `DockerRunner` receiving and using the `mounts` properly when running containers.
   - Update `TaskExecutor` tests to include the new field.

## Dependencies
- `docker-py` handles string list format for volumes natively.

## Error Handling
- Invalid mount formats (e.g., missing host path) will be caught by the Docker daemon at container creation time and handled by the existing try-except block in `DockerRunner.run()`.
