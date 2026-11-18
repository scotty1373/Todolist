"""Task storage module using JSON file."""

import contextlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Callable
from .models import Task, Priority, TaskStatus, generate_short_id
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


class TaskStorage:
    """JSON-based task storage."""

    def __init__(self, file_path: str | Path = None):
        if file_path is None:
            # Default to data/tasks.json in project root
            project_root = Path(__file__).parent.parent
            file_path = project_root / "data" / "tasks.json"

        self.file_path = Path(file_path)
        self._backup_path = self.file_path.with_suffix(".json.bak")
        self._rlock = threading.RLock()
        self._has_file_lock = False
        self._ensure_file_exists()

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

    def _ensure_file_exists(self):
        """Ensure the storage file exists."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            if not self.file_path.exists() and not self._backup_path.exists():
                self._write_tasks([])

    @staticmethod
    def _load_tasks_file(path: Path) -> list[dict]:
        """Validate the JSON structure and records before trusting a file."""
        with path.open(encoding="utf-8") as fh:
            try:
                records = json.load(fh)
                if not isinstance(records, list):
                    raise ValueError("expected a list of tasks")
                for record in records:
                    if not isinstance(record, dict):
                        raise ValueError("expected a task object")
                    if not isinstance(record.get("id"), str) or not isinstance(record.get("name"), str):
                        raise ValueError("tasks require a string ID and name")
                    Task.from_dict(record)
            except (ValueError, TypeError, OverflowError) as error:
                raise TaskStorageError(f"无法读取任务文件 {path}: {error}") from error
        return records

    def _read_tasks(self) -> list[dict]:
        """Recover a missing or damaged task file from a validated backup."""
        damaged = False
        try:
            return self._load_tasks_file(self.file_path)
        except FileNotFoundError:
            pass
        except TaskStorageError:
            damaged = True

        try:
            tasks = self._load_tasks_file(self._backup_path)
        except (TaskStorageError, FileNotFoundError) as error:
            raise TaskStorageError(f"任务文件不可用且无有效备份，已停止写入: {self.file_path}") from error

        message = "[警告] 任务数据已从备份恢复"
        if damaged:
            modified = self.file_path.stat().st_mtime_ns
            archive = self.file_path.with_suffix(f".corrupt-{modified}.json")
            if not archive.exists():
                shutil.copy2(self.file_path, archive)
            message += f"（损坏文件已保存: {archive.name}）"
        self._replace_tasks(self.file_path, tasks)
        print(message)
        return tasks

    @staticmethod
    def _replace_tasks(path: Path, tasks: list[dict]) -> None:
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(tasks, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _write_tasks(self, tasks: list[dict], previous: list[dict] | None = None) -> None:
        """Rotate a validated snapshot into the backup before committing."""
        if previous is not None:
            self._replace_tasks(self._backup_path, previous)
        self._replace_tasks(self.file_path, tasks)

    def get_all_tasks(self) -> list[Task]:
        """Get all tasks."""
        with self._locked():
            tasks_data = self._read_tasks()
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

    def get_task_by_id(self, task_id: str) -> Task | None:
        """Get a task by ID (supports prefix matching)."""
        with self._locked():
            tasks = self._read_tasks()
            index = self._find_index(tasks, task_id)
            return Task.from_dict(tasks[index]) if index is not None else None

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

    def add_task(self, task: Task) -> Task:
        """Add a new task."""
        with self._locked():
            tasks = self._read_tasks()

            # Ensure unique ID
            existing_ids = {t["id"] for t in tasks}
            while task.id in existing_ids:
                task.id = generate_short_id()

            task.start_time = normalize_local_time(task.start_time)
            task.end_time = normalize_local_time(task.end_time)
            validate_time_window(task.start_time, task.end_time)
            self._write_tasks([*tasks, task.to_dict()], tasks)
        return task

    def update_task(self, task: Task) -> Task:
        """Replace an entire task by its exact ID. For edits, use update_task_fields."""
        with self._locked():
            tasks = self._read_tasks()
            index = self._find_index(tasks, task.id, exact=True)
            if index is None:
                raise TaskNotFound(f"Task not found: {task.id}")
            return self._save_task(tasks, index, task)

    def _save_task(self, records: list[dict], index: int, task: Task) -> Task:
        task.update_timestamp()
        updated = records.copy()
        updated[index] = task.to_dict()
        self._write_tasks(updated, records)
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
            records = self._read_tasks()
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
            return self._save_task(records, index, task) if changed else task

    def delete_task(self, task_id: str) -> bool:
        """Delete a single task by ID or unique prefix.

        Raises ValueError if the prefix is ambiguous (matches multiple
        tasks) instead of silently deleting them all.
        """
        with self._locked():
            tasks = self._read_tasks()
            index = self._find_index(tasks, task_id)
            if index is None:
                return False

            self._write_tasks(tasks[:index] + tasks[index + 1:], tasks)
        return True

    def mark_done(self, task_id: str) -> Task:
        """Mark a task as done."""
        return self.update_task_fields(task_id, {"status": TaskStatus.DONE})

    def mark_doing(self, task_id: str) -> Task:
        """Mark a task as in progress."""
        return self.update_task_fields(task_id, {"status": TaskStatus.DOING})

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
