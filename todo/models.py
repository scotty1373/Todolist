"""Task model definition."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from enum import Enum
from typing import Self
import random
import string

from .utils import parse_iso_time


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
    INTERRUPTED = "interrupted"
    DONE = "done"

    @property
    def icon(self) -> str:
        return {
            TaskStatus.PENDING: "⬜",
            TaskStatus.DOING: "🔄",
            TaskStatus.INTERRUPTED: "⏸",
            TaskStatus.DONE: "✅",
        }[self]

    @property
    def label(self) -> str:
        return {
            TaskStatus.PENDING: "待办",
            TaskStatus.DOING: "进行中",
            TaskStatus.INTERRUPTED: "已中断",
            TaskStatus.DONE: "已完成",
        }[self]

    @classmethod
    def from_string(cls, value: str) -> Self:
        """Create TaskStatus from string value."""
        match value.lower():
            case "pending" | "待办":
                return cls.PENDING
            case "doing" | "进行中":
                return cls.DOING
            case "interrupted" | "中断" | "已中断":
                return cls.INTERRUPTED
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
            start_time=parse_iso_time(data["start_time"]) if data.get("start_time") else None,
            end_time=parse_iso_time(data["end_time"]) if data.get("end_time") else None,
            reminder=data.get("reminder", False),
            reminded=data.get("reminded", False),
            created_at=parse_iso_time(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=parse_iso_time(data["updated_at"]) if data.get("updated_at") else datetime.now(),
        )

    def update_timestamp(self):
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now()

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.status.icon} {self.priority.stars} {self.name} [{self.id}]"


class ExecutionEnd(Enum):
    SWITCHED = "switched"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    DELETED = "deleted"

    @property
    def label(self) -> str:
        return {
            ExecutionEnd.SWITCHED: "切换任务",
            ExecutionEnd.INTERRUPTED: "主动中断",
            ExecutionEnd.COMPLETED: "任务完成",
            ExecutionEnd.DELETED: "任务删除",
        }[self]


@dataclass(frozen=True)
class ExecutionPeriod:
    task_id: str
    task_name: str
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: ExecutionEnd | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "end_reason": self.end_reason.value if self.end_reason else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        if not isinstance(data, dict) or not all(
            isinstance(data.get(field), str) for field in ("task_id", "task_name", "started_at")
        ):
            raise ValueError("Invalid execution period")

        start = datetime.fromisoformat(data["started_at"])
        end_value = data.get("ended_at")
        end = datetime.fromisoformat(end_value) if end_value is not None else None
        reason_value = data.get("end_reason")
        reason = ExecutionEnd(reason_value) if reason_value is not None else None
        if start.utcoffset() is None or (end is not None and (end.utcoffset() is None or end < start)):
            raise ValueError("Invalid execution timestamps")
        if (end is None) != (reason is None):
            raise ValueError("A closed execution period requires an end time and reason")

        return cls(data["task_id"], data["task_name"], start, end, reason)

    def elapsed_seconds(self, now: datetime) -> float:
        return max(0, ((self.ended_at or now) - self.started_at).total_seconds())


@dataclass(frozen=True)
class ExecutionDayRecord:
    period_index: int
    started_at: datetime
    ended_at: datetime | None
    end_reason: ExecutionEnd | None

    def elapsed_seconds(self, now: datetime) -> float:
        return max(0, ((self.ended_at or now) - self.started_at).total_seconds())


@dataclass(frozen=True)
class ProgressNote:
    task_id: str
    task_name: str
    text: str
    recorded_at: datetime

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "task_name": self.task_name, "text": self.text,
            "recorded_at": self.recorded_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        fields = ("task_id", "task_name", "text", "recorded_at")
        if not isinstance(data, dict) or not all(isinstance(data.get(field), str) for field in fields):
            raise ValueError("Invalid progress note")
        timestamp = datetime.fromisoformat(data["recorded_at"])
        if timestamp.utcoffset() is None:
            raise ValueError("Progress note timestamp requires a time zone")
        return cls(data["task_id"], data["task_name"], data["text"], timestamp)


@dataclass(frozen=True)
class ExecutionActivity:
    recorded_at: datetime
    title: str
    task: str
    detail: str = ""


@dataclass(frozen=True)
class ExecutionState:
    tasks: tuple[Task, ...]
    periods: tuple[ExecutionPeriod, ...]
    current_task: Task | None
    notes: tuple[ProgressNote, ...] = ()

    @property
    def active_period(self) -> ExecutionPeriod | None:
        return self.periods[-1] if self.current_task else None

    def records_for_day(
        self, day: date, now: datetime, *, zone: tzinfo | None = None,
    ) -> tuple[ExecutionDayRecord, ...]:
        """Return the parts of execution periods that belong to a local calendar day."""
        lower = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
        upper = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
        now = now.astimezone(timezone.utc)
        records = []
        for index, period in enumerate(self.periods):
            start = period.started_at.astimezone(timezone.utc)
            end = period.ended_at.astimezone(timezone.utc) if period.ended_at else None
            observed_end = min(end, now) if end is not None else now
            first, last = max(start, lower), min(observed_end, upper)
            started_today = lower <= start < upper and start <= now
            ended_today = end is not None and lower <= end < upper and end <= now
            if first >= last and not (started_today or ended_today):
                continue
            running_today = end is None and lower <= now < upper
            records.append(ExecutionDayRecord(
                index, first, None if running_today else last, period.end_reason if ended_today else None,
            ))
        return tuple(records)

    @property
    def activity(self) -> tuple[ExecutionActivity, ...]:
        """Derive the newest-first timeline from the persisted execution state."""
        seen_tasks = set()
        events = []
        previous = None
        for period in self.periods:
            if previous and previous.end_reason == ExecutionEnd.SWITCHED and previous.ended_at == period.started_at:
                title = "切换任务"
                task = f"{previous.task_name} → {period.task_name}"
            else:
                title = "继续执行" if period.task_id in seen_tasks else "开始执行"
                task = period.task_name
            events.append(ExecutionActivity(period.started_at, title, task))
            if period.ended_at and period.end_reason and period.end_reason != ExecutionEnd.SWITCHED:
                events.append(ExecutionActivity(period.ended_at, period.end_reason.label, period.task_name))
            seen_tasks.add(period.task_id)
            previous = period
        events.extend(
            ExecutionActivity(note.recorded_at, "补充进展", note.task_name, note.text)
            for note in self.notes
        )
        events.sort(key=lambda event: event.recorded_at)
        return tuple(reversed(events))

    @property
    def pending_resume(self) -> tuple[Task, ...]:
        """Return interrupted tasks in the order they can be resumed."""
        interrupted = {task.id: task for task in self.tasks if task.status == TaskStatus.INTERRUPTED}
        if not interrupted:
            return ()

        ordered = []
        for period in reversed(self.periods):
            if period.end_reason in (ExecutionEnd.SWITCHED, ExecutionEnd.INTERRUPTED):
                task = interrupted.pop(period.task_id, None)
                if task is not None:
                    ordered.append(task)
        # Imported interrupted tasks may have no execution periods yet.
        ordered.extend(sorted(interrupted.values(), key=lambda task: task.updated_at, reverse=True))
        return tuple(ordered)
