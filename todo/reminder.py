"""Windows notification reminder module."""

import os
import sys
import threading

from .models import Task
from .storage import TaskStorage
from .utils import format_time_window

# 通知按钮的推迟选项：(分钟数, 按钮文案)
POSTPONE_OPTIONS = [
    (10, "10 分钟"),
    (30, "30 分钟"),
    (60, "1 小时"),
    (120, "2 小时"),
]

# --- 跨进程互斥：保证同一时刻只有一个进程在发提醒（Web 内嵌线程 / 独立 daemon）---
if sys.platform == "win32":
    import msvcrt

    def _try_reminder_lock(fh) -> bool:
        fh.seek(0)
        if os.fstat(fh.fileno()).st_size == 0:
            fh.write(b"\0")
            fh.flush()
            fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
else:
    import fcntl

    def _try_reminder_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


class ReminderService:
    """Background service for Windows task notifications.

    Reminds when a task's start time arrives (within a grace window).
    The toast carries postpone buttons (via windows-toasts, WinRT);
    without that library it falls back to a plain win10toast reminder.
    """

    def __init__(self, storage: TaskStorage, ahead_minutes: int = 15, check_interval: int = 60):
        """
        Initialize reminder service.

        Args:
            storage: TaskStorage instance
            ahead_minutes: Grace window — a task is still reminded if its
                start time was up to this many minutes ago
            check_interval: Seconds between checks
        """
        self.storage = storage
        self.ahead_minutes = ahead_minutes
        self.check_interval = check_interval
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock_fh = None  # 提醒互斥锁文件句柄，持有期间本进程独占提醒

    def _send_interactive_toast(self, task: Task) -> None:
        """Send a toast with postpone buttons (requires windows-toasts).

        Raises ImportError when the library is missing; other exceptions
        propagate as transient errors so the next cycle retries.
        """
        from windows_toasts import InteractableWindowsToaster, Toast, ToastButton

        message = f"「{task.name}」已到开始时间"
        if task.start_time or task.end_time:
            message += f"\n时间: {format_time_window(task.start_time, task.end_time)}"
        if task.tags:
            message += f"\n标签: {', '.join(task.tags)}"

        toaster = InteractableWindowsToaster("TodoList")

        def on_activated(args):
            # 点通知本体（非按钮）时 arguments 为空，直接忽略
            if not args.arguments or not args.arguments.startswith("postpone="):
                return
            try:
                minutes = int(args.arguments.split("=", 1)[1])
                postponed = self.storage.postpone_task(task.id, minutes)
            except Exception as e:
                print(f"推迟任务失败: {e}")
                return
            new_start = postponed.start_time.strftime("%H:%M") if postponed.start_time else "未设置"
            confirm = Toast(text_fields=[
                "已推迟",
                f"「{postponed.name}」已推迟，新的开始时间 {new_start}",
            ])
            toaster.show_toast(confirm)

        new_toast = Toast(
            text_fields=["📋 任务提醒", message],
            actions=[
                ToastButton(content=f"推迟 {label}", arguments=f"postpone={minutes}")
                for minutes, label in POSTPONE_OPTIONS
            ],
        )
        new_toast.on_activated = on_activated
        toaster.show_toast(new_toast)

    def _send_notification(self, task: Task):
        """Send Windows notification for a task.

        The task is marked as reminded when the notification was shown
        or when the toast libraries are missing (ImportError is
        permanent, retrying every cycle would just spam).
        Transient errors are left unmarked so the next cycle retries.
        """
        try:
            self._send_interactive_toast(task)

        except ImportError:
            try:
                from win10toast_click import ToastNotifier

                toaster = ToastNotifier()
                toaster.show_toast(
                    title="📋 任务提醒",
                    msg=f"「{task.name}」已到开始时间",
                    duration=10,
                    threaded=True,
                )
            except ImportError:
                # Fallback to console notification (plain text: GBK-safe)
                print(f"\n[提醒] 「{task.name}」已到开始时间！")

            except Exception as e:
                print(f"发送通知失败: {e}")
                return  # transient error: leave unmarked, retry next cycle

        except Exception as e:
            print(f"发送通知失败: {e}")
            return  # transient error: leave unmarked, retry next cycle

        # Mark as reminded
        self.storage.mark_reminded(task.id)

    def _check_and_notify(self):
        """Check tasks and send notifications."""
        pending_tasks = self.storage.get_pending_reminders(self.ahead_minutes)

        for task in pending_tasks:
            self._send_notification(task)

    def _run_loop(self):
        """Main loop for background checking."""
        while not self._stop_event.is_set():
            try:
                self._check_and_notify()
            except Exception as e:
                print(f"提醒检查出错: {e}")

            # Wakeable sleep: stop() takes effect immediately
            self._stop_event.wait(self.check_interval)

    def start(self):
        """Start the reminder service in background.

        Acquires a cross-process lock first: if another reminder service
        is already running (e.g. a standalone `remind --daemon` alongside
        the web server), this instance stays idle instead of producing
        duplicate notifications.
        """
        if self._running:
            return

        lock_path = self.storage.file_path.with_suffix(".json.remindlock")
        self._lock_fh = open(lock_path, "a+b")
        if not _try_reminder_lock(self._lock_fh):
            self._lock_fh.close()
            self._lock_fh = None
            print("[提醒] 其他提醒服务已在运行，本进程不重复启动提醒线程")
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print(f"[提醒] 提醒线程已启动 (检查间隔 {self.check_interval}s, 开始后宽限 {self.ahead_minutes} 分钟)")

    def stop(self):
        """Stop the reminder service."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._lock_fh:
            try:
                if sys.platform == "win32":
                    self._lock_fh.seek(0)
                    import msvcrt
                    msvcrt.locking(self._lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass  # 进程退出时 OS 会释放锁
            finally:
                self._lock_fh.close()
                self._lock_fh = None

    def run_once(self):
        """Run a single check (for CLI command)."""
        pending_tasks = self.storage.get_pending_reminders(self.ahead_minutes)

        if not pending_tasks:
            print("暂无需要提醒的任务")
            return

        print(f"发现 {len(pending_tasks)} 个需要提醒的任务:")
        for task in pending_tasks:
            self._send_notification(task)
            print(f"  - {task.name} [{task.id}]")

    def is_running(self) -> bool:
        """Check if service is running (thread actually alive)."""
        return (
            self._running
            and self._thread is not None
            and self._thread.is_alive()
        )
