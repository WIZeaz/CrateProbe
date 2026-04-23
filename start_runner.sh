export RUNNER_SERVER_URL="http://127.0.0.1:8080"
export RUNNER_ID="Adam"
export RUNNER_TOKEN="rnr_3q0hOkMNjqPoWVt76wmsiEaNCuaX71Ln"
export RUNNER_MAX_JOBS="32"
export RUNNER_DOCKER_IMAGE="lifesonar-env:latest"
export RUNNER_WORKSPACE_DIR="/home/wizeaz/crate-probe-runner/"
export RUNNER_DOCKER_PULL_POLICY="never"
export RUNNER_DOCKER_MOUNTS="/home/wizeaz/exp-plat/.ltgenconfig:/workspace/.ltgenconfig"
export RUNNER_MAX_RUNTIME_SECONDS="86400"
export RUNNER_LOG_FLUSH_INTERVAL_SECONDS="30"
export RUNNER_LOG_SYNC_INTERVAL_SECONDS="5"
cd ./backend
uv run python -m runner