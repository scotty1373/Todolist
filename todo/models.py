"""Task model definition."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Self
import random
import string


class Priority(Enum):
    """Task priority levels with emoji stars."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def stars(self) -> str:
        """Return emoji stars representation."""
        match self:
            case Priority.HIGH:
                return "⭐⭐⭐"
            case Priority.MEDIUM:
                return "⭐⭐"
            case Priority.LOW:
                return "⭐"

    @classmethod
    def from_string(cls, value: str) -> Self:
        """Create Priority from string value."""
        match value.lower():
            case "high" | "h" | "高":
                return cls.HIGH
            case "medium" | "m" | "中":
                return cls.MEDIUM
            case "low" | "l" | "低":
                return cls.LOW
            case _:
                raise ValueError(f"Invalid priority: {value}")


class TaskStatus(Enum):
    """Task status."""
    PENDING = "pending"
    DOING = "doing"
    DONE = "done"

    @classmethod
    def from_string(cls, value: str) -> Self:
        """Create TaskStatus from string value."""
        match value.lower():
            case "pending" | "待办":
                return cls.PENDING
            case "doing" | "进行中":
                return cls.DOING
            case "done" | "已完成":
                return cls.DONE
            case _:
                raise ValueError(f"Invalid status: {value}")


def generate_short_id(length: int = 4) -> str:
    """Generate a random short ID."""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))


@dataclass
class Task:
    """Task model with all properties."""
    name: str
    id: str = field(default_factory=lambda: generate_short_id(4))
    description: str = ""
    priority: Priority = Priority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    tags: list[str] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    reminder: bool = False
    reminded: bool = False  # Track if reminder was already sent
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert task to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority.value,
            "status": self.status.value,
            "tags": self.tags,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "reminder": self.reminder,
            "reminded": self.reminded,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create Task from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            priority=Priority(data.get("priority", "medium")),
            status=TaskStatus(data.get("status", "pending")),
            tags=data.get("tags", []),
            start_time=datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None,
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            reminder=data.get("reminder", False),
            reminded=data.get("reminded", False),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
        )

    def update_timestamp(self):
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now()

    def __str__(self) -> str:
        """Return string representation."""
        status_icon = {"pending": "⬜", "doing": "🔄", "done": "✅"}[self.status.value]
        return f"{status_icon} {self.priority.stars} {self.name} [{self.id}]"
