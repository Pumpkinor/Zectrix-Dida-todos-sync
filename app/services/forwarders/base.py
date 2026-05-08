from abc import ABC, abstractmethod
from app.models import Todo


class BaseForwarder(ABC):
    """Pluggable forwarder interface. Implement this to add a new sync target."""

    @abstractmethod
    async def create_todo(self, todo: Todo) -> str:
        """Create todo on remote. Returns the remote ID."""
        ...

    @abstractmethod
    async def update_todo(self, remote_id: str, todo: Todo):
        """Update an existing remote todo."""
        ...

    @abstractmethod
    async def complete_todo(self, remote_id: str):
        """Mark a remote todo as completed."""
        ...

    @abstractmethod
    async def delete_todo(self, remote_id: str):
        """Delete a remote todo."""
        ...

    @abstractmethod
    async def fetch_remote_todos(self) -> list[dict]:
        """Fetch all todos from remote. Returns list of raw dicts."""
        ...
