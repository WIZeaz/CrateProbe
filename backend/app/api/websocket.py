from typing import Dict, Set
from fastapi import WebSocket


class ConnectionManager:
    """Manages WebSocket connections"""

    def __init__(self):
        # Job-specific connections: job_id -> set of websockets
        self.job_connections: Dict[int, Set[WebSocket]] = {}
        # Dashboard connections
        self.dashboard_connections: Set[WebSocket] = set()

    async def connect_job(self, job_id: int, websocket: WebSocket):
        """Connect a WebSocket to a specific job"""
        await websocket.accept()
        if job_id not in self.job_connections:
            self.job_connections[job_id] = set()
        self.job_connections[job_id].add(websocket)

    async def connect_dashboard(self, websocket: WebSocket):
        """Connect a WebSocket to dashboard updates"""
        await websocket.accept()
        self.dashboard_connections.add(websocket)

    def disconnect_job(self, job_id: int, websocket: WebSocket):
        """Disconnect a WebSocket from a job"""
        if job_id in self.job_connections:
            self.job_connections[job_id].discard(websocket)
            if not self.job_connections[job_id]:
                del self.job_connections[job_id]

    def disconnect_dashboard(self, websocket: WebSocket):
        """Disconnect a WebSocket from dashboard"""
        self.dashboard_connections.discard(websocket)

    async def broadcast_job_update(self, job_id: int, data: dict):
        """Broadcast update to all connections watching a specific job"""
        if job_id not in self.job_connections:
            return

        disconnected = set()
        for websocket in self.job_connections[job_id]:
            try:
                await websocket.send_json(data)
            except Exception:
                disconnected.add(websocket)

        # Clean up disconnected websockets
        for websocket in disconnected:
            self.disconnect_job(job_id, websocket)

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
