"""Command-line interface for TodoList tool."""

import argparse
from datetime import datetime, timedelta
from typing import Callable

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from .models import Task, Priority, TaskStatus
from .storage import TaskStorage
from .utils import parse_time, format_datetime, format_time_window, get_relative_time, validate_time_window
from .reminder import ReminderService


console = Console()


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for CLI."""
    parser = argparse.ArgumentParser(
        prog="todo",
        description="TodoList - A CLI and Web based task management tool",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # add command
    add_parser = subparsers.add_parser("add", aliases=["a"], help="Add a new task")
    add_parser.add_argument("name", help="Task name")
    add_parser.add_argument("-d", "--description", default="", help="Task description")
    add_parser.add_argument(
        "-p", "--priority",
        choices=["high", "medium", "low", "h", "m", "l"],
        default="medium",
        help="Task priority (high/medium/low, default: medium)",
    )
    add_parser.add_argument("-t", "--tags", nargs="*", default=[], help="Task tags")
    add_parser.add_argument("-s", "--start", help="Start time (e.g., '09:00', 'tomorrow 10:00')")
    add_parser.add_argument("-e", "--end", help="End time / deadline")
    add_parser.add_argument("--remind", action="store_true", help="Enable reminder for this task")

    # list command
    list_parser = subparsers.add_parser("list", aliases=["ls", "l"], help="List tasks")
    list_parser.add_argument("--all", action="store_true", help="Show all tasks, not just today")
    list_parser.add_argument("--tag", help="Filter by tag")
    list_parser.add_argument("--status", choices=["pending", "doing", "done"], help="Filter by status")
    list_parser.add_argument("--priority", choices=["high", "medium", "low"], help="Filter by priority")

    # done command
    done_parser = subparsers.add_parser("done", aliases=["d"], help="Mark task as done")
    done_parser.add_argument("id", help="Task ID (or prefix)")

    # start command
    start_parser = subparsers.add_parser("start", help="Mark task as in progress")
    start_parser.add_argument("id", help="Task ID (or prefix)")

    # delete command
    delete_parser = subparsers.add_parser("delete", aliases=["del", "rm"], help="Delete a task")
    delete_parser.add_argument("id", help="Task ID (or prefix)")
    delete_parser.add_argument("-f", "--force", action="store_true", help="Force delete without confirmation")

    # edit command
    edit_parser = subparsers.add_parser("edit", help="Edit a task")
    edit_parser.add_argument("id", help="Task ID (or prefix)")
    edit_parser.add_argument("-n", "--name", help="New task name")
    edit_parser.add_argument("-d", "--description", help="New description")
    edit_parser.add_argument("-p", "--priority", choices=["high", "medium", "low"])
    edit_parser.add_argument("-t", "--tags", nargs="*", help="New tags (replaces existing)")
    edit_parser.add_argument("-s", "--start", help="New start time")
    edit_parser.add_argument("-e", "--end", help="New end time")

    # remind command
    remind_parser = subparsers.add_parser("remind", help="Check and send reminders")
    remind_parser.add_argument("--ahead", type=int, default=15, help="开始后宽限分钟数，超过则不再提醒 (default: 15)")
    remind_parser.add_argument("--daemon", action="store_true", help="Run as background service")

    # web command
    web_parser = subparsers.add_parser("web", help="Start web interface")
    web_parser.add_argument("-p", "--port", type=int, default=5000, help="Port number (default: 5000)")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")

    # clear command
    clear_parser = subparsers.add_parser("clear", help="Clear completed tasks")
    clear_parser.add_argument("--all", action="store_true", help="Clear all tasks")

    return parser


def cmd_add(args, storage: TaskStorage):
    """Handle add command."""
    start_time = None
    end_time = None

    try:
        start_time = parse_time(args.start, allow_next_day=False) if args.start else None
    except ValueError as e:
        console.print(f"[red]错误: 开始时间格式错误 - {e}[/red]")
        return

    try:
        end_time = parse_time(args.end, allow_next_day=False) if args.end else None
    except ValueError as e:
        console.print(f"[red]错误: 结束时间格式错误 - {e}[/red]")
        return

    # Validate and adjust time window
    start_time, end_time, error = validate_time_window(start_time, end_time)
    if error:
        console.print(f"[red]错误: {error}[/red]")
        return

    priority = Priority.from_string(args.priority)

    task = Task(
        name=args.name,
        description=args.description,
        priority=priority,
        tags=args.tags,
        start_time=start_time,
        end_time=end_time,
        reminder=args.remind,
    )

    storage.add_task(task)

    console.print(Panel(
        f"[green]✓[/green] 已添加任务: {task.name}\n"
        f"  ID: [cyan]{task.id}[/cyan]\n"
        f"  优先级: {task.priority.stars}\n"
        f"  时间: {format_time_window(start_time, end_time)}",
        title="添加成功",
        border_style="green",
    ))


def cmd_list(args, storage: TaskStorage):
    """Handle list command."""
    if args.all:
        tasks = storage.get_all_tasks()
    else:
        tasks = storage.get_today_tasks()

    # Apply filters
    if args.tag:
        tasks = [t for t in tasks if args.tag in t.tags]
    if args.status:
        status = TaskStatus.from_string(args.status)
        tasks = [t for t in tasks if t.status == status]
    if args.priority:
        priority = Priority.from_string(args.priority)
        tasks = [t for t in tasks if t.priority == priority]

    # Sort by priority
    tasks = storage.sort_tasks(tasks)

    if not tasks:
        console.print("[yellow]暂无任务[/yellow]")
        return

    # Create table
    table = Table(title="📋 今日任务" if not args.all else "📋 所有任务", show_header=True)
    table.add_column("ID", style="cyan", width=6)
    table.add_column("状态", width=4)
    table.add_column("优先级", width=6)
    table.add_column("任务名称", style="bold")
    table.add_column("标签")
    table.add_column("时间窗口")
    table.add_column("剩余时间")

    for task in tasks:
        status_icons = {
            TaskStatus.PENDING: "⬜",
            TaskStatus.DOING: "🔄",
            TaskStatus.DONE: "✅",
        }

        # Determine remaining time
        remaining = ""
        if task.end_time and task.status != TaskStatus.DONE:
            remaining = get_relative_time(task.end_time)
            if "已过期" in remaining:
                remaining = f"[red]{remaining}[/red]"
            elif "分钟" in remaining:
                remaining = f"[yellow]{remaining}[/yellow]"

        tags_str = ", ".join(task.tags) if task.tags else "-"
        reminder_str = " 🔔" if task.reminder and task.status != TaskStatus.DONE else ""

        table.add_row(
            task.id,
            status_icons[task.status],
            task.priority.stars,
            task.name + reminder_str,
            tags_str,
            format_time_window(task.start_time, task.end_time),
            remaining,
        )

    console.print(table)


def cmd_done(args, storage: TaskStorage):
    """Handle done command."""
    try:
        task = storage.mark_done(args.id)
        console.print(f"[green]✓[/green] 已完成任务: {task.name} [{task.id}]")
    except ValueError as e:
        console.print(f"[red]错误: {e}[/red]")


def cmd_start(args, storage: TaskStorage):
    """Handle start command."""
    try:
        task = storage.mark_doing(args.id)
        console.print(f"[green]✓[/green] 开始任务: {task.name} [{task.id}]")
    except ValueError as e:
        console.print(f"[red]错误: {e}[/red]")


def cmd_delete(args, storage: TaskStorage):
    """Handle delete command."""
    if not args.force:
        try:
            task = storage.get_task_by_id(args.id)
        except ValueError as e:
            console.print(f"[red]错误: {e}[/red]")
            return
        if task:
            console.print(f"[yellow]将删除任务: {task.name} [{task.id}][/yellow]")
            confirm = console.input("确认删除? [y/N]: ")
            if confirm.lower() != "y":
                console.print("[yellow]已取消[/yellow]")
                return
        else:
            console.print(f"[red]未找到任务: {args.id}[/red]")
            return

    try:
        deleted = storage.delete_task(args.id)
    except ValueError as e:
        console.print(f"[red]错误: {e}[/red]")
        return

    if deleted:
        console.print(f"[green]✓[/green] 已删除任务")
    else:
        console.print(f"[red]未找到任务: {args.id}[/red]")


def cmd_edit(args, storage: TaskStorage):
    """Handle edit command."""
    try:
        task = storage.get_task_by_id(args.id)
    except ValueError as e:
        console.print(f"[red]错误: {e}[/red]")
        return
    if not task:
        console.print(f"[red]未找到任务: {args.id}[/red]")
        return

    if args.name:
        task.name = args.name
    if args.description:
        task.description = args.description
    if args.priority:
        task.priority = Priority.from_string(args.priority)
    if args.tags is not None:
        task.tags = args.tags
    try:
        if args.start:
            task.start_time = parse_time(args.start)
        if args.end:
            task.end_time = parse_time(args.end)
    except ValueError as e:
        console.print(f"[red]错误: 时间格式错误 - {e}[/red]")
        return

    # Validate the resulting window; handle cross-day (e.g. 23:00 - 05:00)
    if task.start_time and task.end_time and task.end_time <= task.start_time:
        if task.end_time.date() == task.start_time.date():
            task.end_time += timedelta(days=1)
        if task.end_time <= task.start_time:
            console.print(
                f"[red]错误: 结束时间 ({format_datetime(task.end_time)}) "
                f"必须晚于开始时间 ({format_datetime(task.start_time)})[/red]")
            return

    storage.update_task(task)
    console.print(f"[green]✓[/green] 已更新任务: {task.name} [{task.id}]")


def cmd_remind(args, storage: TaskStorage):
    """Handle remind command."""
    service = ReminderService(storage, ahead_minutes=args.ahead)

    if args.daemon:
        console.print(f"[cyan]启动后台提醒服务 (检查间隔: 60秒, 开始后宽限: {args.ahead}分钟)[/cyan]")
        console.print("[yellow]按 Ctrl+C 停止[/yellow]")
        service.start()
        try:
            while service.is_running():
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            service.stop()
            console.print("\n[yellow]已停止提醒服务[/yellow]")
    else:
        service.run_once()


def cmd_web(args, storage: TaskStorage):
    """Handle web command."""
    import sys
    import os

    # Add project root to path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)

    from web.app import create_app

    app = create_app(storage)
    console.print(f"[cyan]启动 Web 服务: http://{args.host}:{args.port}[/cyan]")
    console.print("[yellow]按 Ctrl+C 停止[/yellow]")
    app.run(host=args.host, port=args.port, debug=False)


def cmd_clear(args, storage: TaskStorage):
    """Handle clear command."""
    tasks = storage.get_all_tasks()

    if args.all:
        count = len(tasks)
        for task in tasks:
            storage.delete_task(task.id)
        console.print(f"[green]✓[/green] 已清除所有 {count} 个任务")
    else:
        done_tasks = [t for t in tasks if t.status == TaskStatus.DONE]
        for task in done_tasks:
            storage.delete_task(task.id)
        console.print(f"[green]✓[/green] 已清除 {len(done_tasks)} 个已完成任务")


def run_cli(args=None):
    """Run the CLI with given arguments."""
    parser = create_parser()
    args = parser.parse_args(args)

    storage = TaskStorage()

    if args.command is None:
        parser.print_help()
        return

    commands: dict[str, Callable] = {
        "add": cmd_add,
        "a": cmd_add,
        "list": cmd_list,
        "ls": cmd_list,
        "l": cmd_list,
        "done": cmd_done,
        "d": cmd_done,
        "start": cmd_start,
        "delete": cmd_delete,
        "del": cmd_delete,
        "rm": cmd_delete,
        "edit": cmd_edit,
        "remind": cmd_remind,
        "web": cmd_web,
        "clear": cmd_clear,
    }

    if args.command in commands:
        commands[args.command](args, storage)
    else:
        parser.print_help()
