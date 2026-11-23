"""Task storage module using JSON file."""

import contextlib
from dataclasses import dataclass, replace
import json
import os
import shutil
import sys
import threading
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from typing import Callable
from .models import ExecutionEnd, ExecutionPeriod, ExecutionState, ProgressNote, Task, Priority, TaskStatus, generate_short_id
from .utils import normalize_local_time, validate_time_window


# --- OS-level file lock helpers (dependency-free, cross-platform) ---
if sys.platform == "win32":
    import msvcrt

    def _os_lock(fh):
        fh.seek(0)
        if os.fstat(fh.fileno()).st_size == 0:
            fh.write(b"\0")  # msvcrt.locking needs at least 1 byte
            fh.flush()
            fh.seek(0)  # lock region starts at position 0
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _os_unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _os_lock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _os_unlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class TaskStorageError(RuntimeError):
    """The task file cannot be safely recovered."""


class TaskNotFound(ValueError):
    """The requested task no longer exists."""


class TaskConflict(ValueError):
    """The requested update no longer matches the stored task."""


@dataclass(frozen=True)
class _Snapshot:
    tasks: list[dict]
    periods: tuple[ExecutionPeriod, ...] = ()
    notes: tuple[ProgressNote, ...] = ()

    @property
    def active(self) -> ExecutionPeriod | None:
        if self.periods and self.periods[-1].ended_at is None:
            return self.periods[-1]
        return None

    def to_json(self) -> list | dict:
        if not self.periods and not self.notes:
            return self.tasks
        return {
            "tasks": self.tasks,
            "periods": [period.to_dict() for period in self.periods],
            "notes": [note.to_dict() for note in self.notes],
        }


def default_task_file() -> Path:
    custom = os.environ.get("TODO_DATA_FILE")
    if custom:
        return Path(custom).expanduser().resolve()
    if sys.platform == "win32":
        app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return app_data / "TodoList" / "tasks.json"
    app_data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return app_data / "TodoList" / "tasks.json"


class TaskStorage:
    """JSON-based task storage."""

    def __init__(self, file_path: str | Path = None):
        legacy = None
        if file_path is None and not os.environ.get("TODO_DATA_FILE") and not getattr(sys, "frozen", False):
            legacy = Path(__file__).resolve().parent.parent / "data" / "tasks.json"
        self.file_path = Path(file_path).resolve() if file_path is not None else default_task_file()
        self._backup_path = self.file_path.with_suffix(".json.bak")
        self._rlock = threading.RLock()
        self._has_file_lock = False
        self._ensure_file_exists(legacy)

    @contextlib.contextmanager
    def _locked(self):
        """Reentrant inter-process file lock.

        Serializes read-modify-write sequences across processes (CLI,
        web server, reminder daemon) via an OS lock on a sidecar
        .lock file, and across threads via an RLock. Reentrant so
        public methods may call each other (e.g. mark_done ->
        get_task_by_id -> update_task) without deadlocking.
        """
        with self._rlock:
            if self._has_file_lock:
                yield
                return

            with open(self.file_path.with_suffix(".json.lock"), "a+b") as fh:
                _os_lock(fh)
                self._has_file_lock = True
                try:
                    yield
                finally:
                    self._has_file_lock = False
                    _os_unlock(fh)

    def _ensure_file_exists(self, legacy: Path | None):
        """Ensure the storage file exists."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            if not self.file_path.exists() and not self._backup_path.exists():
                initial = self._load_snapshot(legacy) if legacy and legacy.is_file() else _Snapshot([])
                self._write_snapshot(initial)

    @staticmethod
    def _load_snapshot(path: Path) -> _Snapshot:
        """Read legacy task lists and snapshots containing execution history."""
        with path.open(encoding="utf-8") as fh:
            try:
                data = json.load(fh)
                records = data.get("tasks") if isinstance(data, dict) else data
                periods = data.get("periods", []) if isinstance(data, dict) else []
                notes = data.get("notes", []) if isinstance(data, dict) else []
                if not all(isinstance(values, list) for values in (records, periods, notes)):
                    raise ValueError("expected lists of tasks, execution periods, and progress notes")
                for record in records:
                    if not isinstance(record, dict):
                        raise ValueError("expected a task object")
                    if not isinstance(record.get("id"), str) or not isinstance(record.get("name"), str):
                        raise ValueError("tasks require a string ID and name")
                    Task.from_dict(record)
                if isinstance(data, list):
                    records = [
                        {**record, "status": TaskStatus.INTERRUPTED.value}
                        if record.get("status") == TaskStatus.DOING.value else record
                        for record in records
                    ]
                snapshot = _Snapshot(
                    records, tuple(ExecutionPeriod.from_dict(period) for period in periods),
                    tuple(ProgressNote.from_dict(note) for note in notes),
                )
                if any(period.ended_at is None for period in snapshot.periods[:-1]):
                    raise ValueError("only the latest execution period can be active")
                if snapshot.active and not any(
                    record["id"] == snapshot.active.task_id and record.get("status") == TaskStatus.DOING.value
                    for record in records
                ):
                    raise ValueError("active execution period must refer to an in-progress task")
            except (ValueError, TypeError, OverflowError) as error:
                raise TaskStorageError(f"无法读取任务文件 {path}: {error}") from error
        return snapshot

    def _read_snapshot(self) -> _Snapshot:
        """Recover a missing or damaged task file from a validated backup."""
        damaged = False
        try:
            return self._load_snapshot(self.file_path)
        except FileNotFoundError:
            pass
        except TaskStorageError:
            damaged = True

        try:
            snapshot = self._load_snapshot(self._backup_path)
        except (TaskStorageError, FileNotFoundError) as error:
            raise TaskStorageError(f"任务文件不可用且无有效备份，已停止写入: {self.file_path}") from error

        message = "[警告] 任务数据已从备份恢复"
        if damaged:
            modified = self.file_path.stat().st_mtime_ns
            archive = self.file_path.with_suffix(f".corrupt-{modified}.json")
            if not archive.exists():
                shutil.copy2(self.file_path, archive)
            message += f"（损坏文件已保存: {archive.name}）"
        self._replace_json(self.file_path, snapshot.to_json())
        print(message)
        return snapshot

    @staticmethod
    def _replace_json(path: Path, data: list | dict) -> None:
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _write_snapshot(self, snapshot: _Snapshot, previous: _Snapshot | None = None) -> None:
        """Rotate a validated snapshot into the backup before committing."""
        if previous is not None:
            self._replace_json(self._backup_path, previous.to_json())
        self._replace_json(self.file_path, snapshot.to_json())

    def get_all_tasks(self) -> list[Task]:
        """Get all tasks."""
        with self._locked():
            tasks_data = self._read_snapshot().tasks
        return [Task.from_dict(data) for data in tasks_data]

    @staticmethod
    def _find_index(tasks: list[dict], task_id: str, exact: bool = False) -> int | None:
        for index, task in enumerate(tasks):
            if task["id"] == task_id:
                return index
        if exact:
            return None
        matching = [index for index, task in enumerate(tasks) if task["id"].startswith(task_id)]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            raise TaskConflict(
                f"ID '{task_id}' matches multiple tasks: {[tasks[index]['id'] for index in matching]}")
        return None

    def get_task_by_id(self, task_id: str, *, exact: bool = False) -> Task | None:
        """Get a task by ID (supports prefix matching)."""
        with self._locked():
            tasks = self._read_snapshot().tasks
            index = self._find_index(tasks, task_id, exact=exact)
            return Task.from_dict(tasks[index]) if index is not None else None

    def get_execution_state(self) -> ExecutionState:
        """Read task statuses and their execution history from one snapshot."""
        with self._locked():
            snapshot = self._read_snapshot()
        tasks = tuple(Task.from_dict(record) for record in snapshot.tasks)
        active = snapshot.active
        current = next((task for task in tasks if active and task.id == active.task_id), None)
        return ExecutionState(tasks, snapshot.periods, current, snapshot.notes)

    def import_tasks(self, source: str | Path) -> tuple[int, int]:
        """Import tasks by ID, preserving existing records and their history."""
        incoming = self._load_snapshot(Path(source))
        with self._locked():
            snapshot = self._read_snapshot()
            if not snapshot.tasks and not snapshot.periods and not snapshot.notes:
                self._write_snapshot(incoming, snapshot)
                return len(incoming.tasks), 0

            known_ids = {record["id"] for record in snapshot.tasks}
            known_ids.update(period.task_id for period in snapshot.periods)
            known_ids.update(note.task_id for note in snapshot.notes)
            added = []
            for record in incoming.tasks:
                if record["id"] in known_ids:
                    continue
                if record.get("status") == TaskStatus.DOING.value:
                    record = {**record, "status": TaskStatus.INTERRUPTED.value}
                added.append(record)
                known_ids.add(record["id"])
            if added:
                self._write_snapshot(replace(snapshot, tasks=[*snapshot.tasks, *added]), snapshot)
            return len(added), len(incoming.tasks) - len(added)

    def get_tasks_for_date(self, target_date: date) -> list[Task]:
        """Get tasks belonging to a specific date.

        A task belongs to a date if:
        1. Its start/end range spans that date, or
        2. Its start_time (or end_time, if no start) falls on it, or
        3. It has no times and was created on that date.

        This is the single source of truth shared by CLI and web.
        """
        day_start = datetime.combine(target_date, datetime.min.time())

        def belongs(task: Task) -> bool:
            if task.start_time and task.end_time:
                return task.start_time.date() <= target_date and task.end_time > day_start
            if task.start_time:
                return task.start_time.date() == target_date
            if task.end_time:
                return task.end_time.date() == target_date
            return task.created_at.date() == target_date

        with self._locked():
            return [t for t in self.get_all_tasks() if belongs(t)]

    def get_today_tasks(self) -> list[Task]:
        """Get tasks for today (delegates to get_tasks_for_date)."""
        return self.get_tasks_for_date(date.today())

    def get_pending_reminders(self, ahead_minutes: int = 15) -> list[Task]:
        """Get tasks whose start time has arrived and need reminders.

        A task needs a reminder when: reminder enabled, not done, not yet
        reminded, and its start_time is within the grace window — i.e. it
        arrived between 0 and ahead_minutes minutes ago. Tasks whose start
        was missed by more than the window are left for explicit action.
        """
        with self._locked():
            tasks = self.get_all_tasks()
            now = datetime.now()

            result = []
            for task in tasks:
                if not task.reminder or task.reminded or task.status == TaskStatus.DONE:
                    continue
                if task.start_time:
                    minutes_since_start = (now - task.start_time).total_seconds() / 60
                    if 0 <= minutes_since_start <= ahead_minutes:
                        result.append(task)

        return result

    def postpone_task(self, task_id: str, minutes: int, *, expected_start: datetime | None = None) -> Task:
        """Shift a task later by minutes, keeping its duration.

        Both start_time and end_time move together (whichever exist) and
        the reminder flag is reset so the reminder re-fires around the
        new start time.
        """
        def changes(task: Task) -> dict:
            if expected_start is not None and (
                task.start_time != expected_start or not task.reminder or task.status == TaskStatus.DONE
            ):
                raise TaskConflict("任务的提醒计划已变更")
            delta = timedelta(minutes=minutes)
            updates = {"reminded": False}
            for field in ("start_time", "end_time"):
                value = getattr(task, field)
                if value is not None:
                    updates[field] = value + delta
            return updates

        return self.update_task_fields(task_id, changes, exact=expected_start is not None)

    def add_task(self, task: Task, *, activate: bool = False) -> Task:
        """Add a new task."""
        with self._locked():
            snapshot = self._read_snapshot()
            tasks = snapshot.tasks

            # Ensure unique ID
            existing_ids = {t["id"] for t in tasks}
            existing_ids.update(period.task_id for period in snapshot.periods)
            existing_ids.update(note.task_id for note in snapshot.notes)
            while task.id in existing_ids:
                task.id = generate_short_id()

            task.start_time = normalize_local_time(task.start_time)
            task.end_time = normalize_local_time(task.end_time)
            validate_time_window(task.start_time, task.end_time)
            updated = replace(snapshot, tasks=[*tasks, task.to_dict()])
            if activate:
                updated = self._activate_task(updated, len(tasks), task)
            self._write_snapshot(updated, snapshot)
        return task

    def update_task(self, task: Task) -> Task:
        """Replace an entire task by its exact ID. For edits, use update_task_fields."""
        with self._locked():
            snapshot = self._read_snapshot()
            index = self._find_index(snapshot.tasks, task.id, exact=True)
            if index is None:
                raise TaskNotFound(f"Task not found: {task.id}")
            return self._save_task(snapshot, index, task)

    @staticmethod
    def _end_period(
        snapshot: _Snapshot,
        task_id: str,
        reason: ExecutionEnd,
        now: datetime | None = None,
    ) -> tuple[ExecutionPeriod, ...]:
        active = snapshot.active
        if active is None or active.task_id != task_id:
            return snapshot.periods
        ended = replace(active, ended_at=max(active.started_at, now or datetime.now(timezone.utc)), end_reason=reason)
        return (*snapshot.periods[:-1], ended)

    def _save_task(self, snapshot: _Snapshot, index: int, task: Task) -> Task:
        task.update_timestamp()
        updated = snapshot.tasks.copy()
        updated[index] = task.to_dict()
        periods = snapshot.periods
        if task.status != TaskStatus.DOING:
            reason = ExecutionEnd.COMPLETED if task.status == TaskStatus.DONE else ExecutionEnd.INTERRUPTED
            periods = self._end_period(snapshot, task.id, reason)
        self._write_snapshot(replace(snapshot, tasks=updated, periods=periods), snapshot)
        return task

    def update_task_fields(
        self,
        task_id: str,
        changes: dict | Callable[[Task], dict],
        *,
        expected: dict | None = None,
        exact: bool = False,
    ) -> Task:
        """Apply a patch against the current task in one storage transaction."""
        with self._locked():
            snapshot = self._read_snapshot()
            records = snapshot.tasks
            index = self._find_index(records, task_id, exact=exact)
            if index is None:
                raise TaskNotFound(f"Task not found: {task_id}")
            task = Task.from_dict(records[index])
            for field, value in (expected or {}).items():
                if getattr(task, field) != value:
                    raise TaskConflict("任务已被修改，请刷新后重试")

            updates = changes(task) if callable(changes) else changes
            original_start, original_end, was_enabled = task.start_time, task.end_time, task.reminder
            changed = False
            for field, value in updates.items():
                if field in ("start_time", "end_time"):
                    value = normalize_local_time(value)
                if value != getattr(task, field):
                    setattr(task, field, value)
                    changed = True

            if task.start_time != original_start or task.end_time != original_end:
                validate_time_window(task.start_time, task.end_time)
            if task.start_time != original_start or (task.reminder and not was_enabled):
                task.reminded = False
            return self._save_task(snapshot, index, task) if changed else task

    def delete_task(self, task_id: str, *, exact: bool = False) -> bool:
        """Delete a single task by ID or unique prefix.

        Raises ValueError if the prefix is ambiguous (matches multiple
        tasks) instead of silently deleting them all.
        """
        with self._locked():
            snapshot = self._read_snapshot()
            tasks = snapshot.tasks
            index = self._find_index(tasks, task_id, exact=exact)
            if index is None:
                return False

            periods = self._end_period(snapshot, tasks[index]["id"], ExecutionEnd.DELETED)
            updated = replace(snapshot, tasks=tasks[:index] + tasks[index + 1:], periods=periods)
            self._write_snapshot(updated, snapshot)
        return True

    def mark_done(self, task_id: str, *, exact: bool = False) -> Task:
        """Mark a task as done."""
        return self.update_task_fields(task_id, {"status": TaskStatus.DONE}, exact=exact)

    def interrupt_task(self, task_id: str, *, exact: bool = False) -> Task:
        return self.update_task_fields(
            task_id, {"status": TaskStatus.INTERRUPTED}, expected={"status": TaskStatus.DOING}, exact=exact
        )

    def mark_doing(self, task_id: str, *, exact: bool = False, allow_completed: bool = True) -> Task:
        """Activate a task and atomically interrupt the previous execution."""
        with self._locked():
            snapshot = self._read_snapshot()
            index = self._find_index(snapshot.tasks, task_id, exact=exact)
            if index is None:
                raise TaskNotFound(f"Task not found: {task_id}")
            task = Task.from_dict(snapshot.tasks[index])
            if not allow_completed and task.status == TaskStatus.DONE:
                raise TaskConflict("该任务已经完成")
            active = snapshot.active
            if active and active.task_id == task.id:
                return task

            self._write_snapshot(self._activate_task(snapshot, index, task), snapshot)
            return task

    def _activate_task(self, snapshot: _Snapshot, index: int, task: Task) -> _Snapshot:
        now = datetime.now(timezone.utc)
        if snapshot.periods:
            latest = snapshot.periods[-1]
            now = max(now, latest.ended_at or latest.started_at)
        active = snapshot.active
        periods = self._end_period(snapshot, active.task_id, ExecutionEnd.SWITCHED, now) if active else snapshot.periods
        task.status = TaskStatus.DOING
        task.update_timestamp()
        records = snapshot.tasks.copy()
        for position, record in enumerate(records):
            if position != index and record.get("status") == TaskStatus.DOING.value:
                records[position] = {**record, "status": TaskStatus.INTERRUPTED.value,
                                     "updated_at": task.updated_at.isoformat()}
        records[index] = task.to_dict()
        return replace(snapshot, tasks=records, periods=(*periods, ExecutionPeriod(task.id, task.name, now)))

    def add_progress(self, task_id: str, text: str) -> ProgressNote:
        message = text.strip()
        if not message:
            raise ValueError("请填写进展后再保存")
        with self._locked():
            snapshot = self._read_snapshot()
            active = snapshot.active
            if not active or active.task_id != task_id:
                raise TaskConflict("当前执行任务已变更，请切回该任务后再保存进展")
            index = self._find_index(snapshot.tasks, task_id, exact=True)
            note = ProgressNote(
                task_id, snapshot.tasks[index]["name"], message, max(datetime.now(timezone.utc), active.started_at),
            )
            self._write_snapshot(replace(snapshot, notes=(*snapshot.notes, note)), snapshot)
            return note

    def mark_reminded(self, task_id: str, *, expected_start: datetime | None = None) -> Task:
        """Mark a task as reminded."""
        def changes(task: Task) -> dict:
            if expected_start is not None and (
                task.start_time != expected_start or not task.reminder or task.status == TaskStatus.DONE
            ):
                raise TaskConflict("任务的提醒计划已变更")
            return {"reminded": True}

        return self.update_task_fields(task_id, changes, exact=expected_start is not None)

    def filter_tasks(
        self,
        status: TaskStatus | None = None,
        priority: Priority | None = None,
        tag: str | None = None,
    ) -> list[Task]:
        """Filter tasks by status, priority, or tag."""
        with self._locked():
            tasks = self.get_all_tasks()

        if status:
            tasks = [t for t in tasks if t.status == status]
        if priority:
            tasks = [t for t in tasks if t.priority == priority]
        if tag:
            tasks = [t for t in tasks if tag in t.tags]

        return tasks

    def sort_tasks(self, tasks: list[Task]) -> list[Task]:
        """Sort tasks by priority (high first) then by end_time."""
        priority_order = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}

        return sorted(
            tasks,
            key=lambda t: (
                priority_order.get(t.priority, 1),
                t.end_time or datetime.max,
            )
        )
