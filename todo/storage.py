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
from .models import Task, Priority, TaskStatus


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


class TaskStorage:
    """JSON-based task storage."""

    def __init__(self, file_path: str | Path = None):
        if file_path is None:
            # Default to data/tasks.json in project root
            project_root = Path(__file__).parent.parent
            file_path = project_root / "data" / "tasks.json"

        self.file_path = Path(file_path)
        self._rlock = threading.RLock()
        self._lock_depth = 0
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
        self._rlock.acquire()
        self._lock_depth += 1
        fh = None
        try:
            if self._lock_depth == 1:
                fh = open(self.file_path.with_suffix(".json.lock"), "a+b")
                _os_lock(fh)
            yield
        finally:
            self._lock_depth -= 1
            if fh is not None:
                try:
                    _os_unlock(fh)
                finally:
                    fh.close()
            self._rlock.release()

    def _ensure_file_exists(self):
        """Ensure the storage file exists."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            if not self.file_path.exists():
                self._write_tasks([])

    def _read_tasks(self) -> list[dict]:
        """Read tasks from JSON file.

        On corruption: archive the damaged file, try to recover from the
        .bak backup, and only fall back to an empty list with a loud
        warning if no backup is usable.
        """
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            corrupt = self.file_path.with_suffix(
                f".corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
            shutil.copy2(self.file_path, corrupt)
            bak = self.file_path.with_suffix(".json.bak")
            if bak.exists():
                try:
                    with open(bak, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    print(f"[警告] 数据文件损坏，已从备份恢复（损坏文件已保存: {corrupt.name}）")
                    return data
                except (json.JSONDecodeError, OSError):
                    pass
            print(f"[警告] 数据文件损坏且无可用备份，已返回空列表（损坏文件: {corrupt.name}）")
            return []

    def _write_tasks(self, tasks: list[dict]):
        """Write tasks to JSON file atomically.

        Writes to a temp file first, backs up the current good file as
        .bak, then replaces the target so a crash mid-write never leaves
        a truncated main file.
        """
        tmp_path = self.file_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if self.file_path.exists():
            shutil.copy2(self.file_path, self.file_path.with_suffix(".json.bak"))
        os.replace(tmp_path, self.file_path)

    def get_all_tasks(self) -> list[Task]:
        """Get all tasks."""
        with self._locked():
            tasks_data = self._read_tasks()
        return [Task.from_dict(data) for data in tasks_data]

    def _find_by_prefix(self, task_id: str) -> Task | None:
        """Find a task by ID or unique prefix.

        Exact match wins. Returns None if no match; raises ValueError
        if the prefix matches multiple tasks.
        """
        tasks = self.get_all_tasks()
        for t in tasks:
            if t.id == task_id:
                return t
        matching = [t for t in tasks if t.id.startswith(task_id)]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            raise ValueError(
                f"ID '{task_id}' matches multiple tasks: {[t.id for t in matching]}")
        return None

    def get_task_by_id(self, task_id: str) -> Task | None:
        """Get a task by ID (supports prefix matching)."""
        return self._find_by_prefix(task_id)

    def get_tasks_for_date(self, target_date: date) -> list[Task]:
        """Get tasks belonging to a specific date.

        A task belongs to a date if:
        1. Its start/end range spans that date, or
        2. Its start_time (or end_time, if no start) falls on it, or
        3. It has no times and was created on that date.

        This is the single source of truth shared by CLI and web.
        """
        def belongs(task: Task) -> bool:
            if task.start_time and task.end_time:
                return task.start_time.date() <= target_date <= task.end_time.date()
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

    def postpone_task(self, task_id: str, minutes: int) -> Task:
        """Shift a task later by minutes, keeping its duration.

        Both start_time and end_time move together (whichever exist) and
        the reminder flag is reset so the reminder re-fires around the
        new start time.
        """
        with self._locked():
            task = self.get_task_by_id(task_id)
            if task is None:
                raise ValueError(f"Task not found: {task_id}")

            delta = timedelta(minutes=minutes)
            if task.start_time:
                task.start_time += delta
            if task.end_time:
                task.end_time += delta
            task.reminded = False
            return self.update_task(task)

    def add_task(self, task: Task) -> Task:
        """Add a new task."""
        with self._locked():
            tasks = self._read_tasks()

            # Ensure unique ID
            existing_ids = {t["id"] for t in tasks}
            while task.id in existing_ids:
                task.id = Task().id  # Generate new ID

            tasks.append(task.to_dict())
            self._write_tasks(tasks)
        return task

    def update_task(self, task: Task) -> Task:
        """Update an existing task."""
        with self._locked():
            tasks = self._read_tasks()
            task.update_timestamp()

            for i, t in enumerate(tasks):
                if t["id"] == task.id:
                    tasks[i] = task.to_dict()
                    self._write_tasks(tasks)
                    return task

        raise ValueError(f"Task not found: {task.id}")

    def delete_task(self, task_id: str) -> bool:
        """Delete a single task by ID or unique prefix.

        Raises ValueError if the prefix is ambiguous (matches multiple
        tasks) instead of silently deleting them all.
        """
        with self._locked():
            task = self._find_by_prefix(task_id)
            if task is None:
                return False

            tasks = [t for t in self._read_tasks() if t["id"] != task.id]
            self._write_tasks(tasks)
        return True

    def mark_done(self, task_id: str) -> Task:
        """Mark a task as done."""
        with self._locked():
            task = self.get_task_by_id(task_id)
            if task:
                task.status = TaskStatus.DONE
                return self.update_task(task)
        raise ValueError(f"Task not found: {task_id}")

    def mark_doing(self, task_id: str) -> Task:
        """Mark a task as in progress."""
        with self._locked():
            task = self.get_task_by_id(task_id)
            if task:
                task.status = TaskStatus.DOING
                return self.update_task(task)
        raise ValueError(f"Task not found: {task_id}")

    def mark_reminded(self, task_id: str) -> Task:
        """Mark a task as reminded."""
        with self._locked():
            task = self.get_task_by_id(task_id)
            if task:
                task.reminded = True
                return self.update_task(task)
        raise ValueError(f"Task not found: {task_id}")

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
