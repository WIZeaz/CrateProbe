import asyncio
import json
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect
from app.database import Database


class ConnectionManager:
    """Manages WebSocket connections"""

    def __init__(self):
        # Task-specific connections: task_id -> set of websockets
        self.task_connections: Dict[int, Set[WebSocket]] = {}
        # Dashboard connections
        self.dashboard_connections: Set[WebSocket] = set()

    async def connect_task(self, task_id: int, websocket: WebSocket):
        """Connect a WebSocket to a specific task"""
        await websocket.accept()
        if task_id not in self.task_connections:
            self.task_connections[task_id] = set()
        self.task_connections[task_id].add(websocket)

    async def connect_dashboard(self, websocket: WebSocket):
        """Connect a WebSocket to dashboard updates"""
        await websocket.accept()
        self.dashboard_connections.add(websocket)

    def disconnect_task(self, task_id: int, websocket: WebSocket):
        """Disconnect a WebSocket from a task"""
        if task_id in self.task_connections:
            self.task_connections[task_id].discard(websocket)
            if not self.task_connections[task_id]:
                del self.task_connections[task_id]

    def disconnect_dashboard(self, websocket: WebSocket):
        """Disconnect a WebSocket from dashboard"""
        self.dashboard_connections.discard(websocket)

    async def broadcast_task_update(self, task_id: int, data: dict):
        """Broadcast update to all connections watching a specific task"""
        if task_id not in self.task_connections:
            return

        disconnected = set()
        for websocket in self.task_connections[task_id]:
            try:
                await websocket.send_json(data)
            except Exception:
                disconnected.add(websocket)

        # Clean up disconnected websockets
        for websocket in disconnected:
            self.disconnect_task(task_id, websocket)

    async def broadcast_dashboard_update(self, data: dict):
        """Broadcast update to all dashboard connections"""
        disconnected = set()
        for websocket in self.dashboard_connections:
            try:
                await websocket.send_json(data)
            except Exception:
                disconnected.add(websocket)

        # Clean up disconnected websockets
        for websocket in disconnected:
            self.disconnect_dashboard(websocket)


# Global connection manager instance
manager = ConnectionManager()


def get_manager() -> ConnectionManager:
    """Get the global connection manager instance"""
    return manager
