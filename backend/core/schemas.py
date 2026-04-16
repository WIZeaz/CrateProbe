from pydantic import BaseModel
from typing import Optional


class RunnerHeartbeatPayload(BaseModel):
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    active_tasks: int


class TaskClaimResponse(BaseModel):
    task_id: int
    lease_token: str
    crate_name: str
    crate_version: str
    command: str
