"""Flask web application for TodoList."""

from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from todo.models import Task, Priority, TaskStatus
from todo.storage import TaskStorage
from todo.reminder import ReminderService
from todo.utils import parse_time, format_datetime, format_time_window, get_relative_time, validate_time_window


def _format_task(task: Task) -> dict:
    """Format a task object into a dict for JSON response."""
    status_icons = {
        TaskStatus.PENDING: "⬜",
        TaskStatus.DOING: "🔄",
        TaskStatus.DONE: "✅",
    }

    remaining = ""
    if task.end_time and task.status != TaskStatus.DONE:
        remaining = get_relative_time(task.end_time)

    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "priority": task.priority.value,
        "priority_stars": task.priority.stars,
        "status": task.status.value,
        "status_icon": status_icons[task.status],
        "tags": task.tags,
        "start_time": format_datetime(task.start_time) if task.start_time else None,
        "end_time": format_datetime(task.end_time) if task.end_time else None,
        "start_time_iso": task.start_time.isoformat() if task.start_time else None,
        "end_time_iso": task.end_time.isoformat() if task.end_time else None,
        "time_window": format_time_window(task.start_time, task.end_time),
        "remaining": remaining,
        "reminder": task.reminder,
    }


def create_app(storage: TaskStorage = None, start_reminder: bool = True) -> Flask:
    """Create and configure the Flask application.

    start_reminder=True (default) also starts the background reminder
    thread inside the web process; a cross-process lock in
    ReminderService prevents duplicates when a standalone
    `remind --daemon` is already running.
    """
    app = Flask(__name__)
    app.secret_key = "todo-secret-key"

    # 禁用模板缓存
    app.config['TEMPLATES_AUTO_RELOAD'] = True

    if storage is None:
        storage = TaskStorage()

    if start_reminder:
        app.reminder_service = ReminderService(storage)
        app.reminder_service.start()

    @app.route("/")
    def index():
        """Main page showing today's tasks."""
        target_date = date.today()
        try:
            date_str = request.args.get("date")
            if date_str:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = date.today()

        # Filter and format tasks for the target date
        filtered = storage.get_tasks_for_date(target_date)
        filtered = storage.sort_tasks(filtered)
        formatted_tasks = [_format_task(t) for t in filtered]

        return render_template("index.html", tasks=formatted_tasks, current_date=target_date.isoformat())

    @app.route("/api/tasks/<date_str>")
    def get_tasks_by_date(date_str):
        """Get tasks for a specific date."""
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Invalid date format"}), 400

        filtered = storage.get_tasks_for_date(target_date)
        filtered = storage.sort_tasks(filtered)
        formatted_tasks = [_format_task(t) for t in filtered]

        return jsonify(formatted_tasks)

    @app.route("/tasks")
    def list_tasks():
        """List all tasks."""
        tasks = storage.get_all_tasks()
        tasks = storage.sort_tasks(tasks)

        result = []
        for task in tasks:
            result.append({
                "id": task.id,
                "name": task.name,
                "priority": task.priority.value,
                "status": task.status.value,
                "tags": task.tags,
            })

        return jsonify(result)

    @app.route("/add", methods=["POST"])
    def add_task():
        """Add a new task."""
        data = request.get_json(silent=True) if request.is_json else request.form
        if data is None:
            return jsonify({"error": "Invalid JSON body"}), 400

        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "任务名称不能为空"}), 400

        try:
            priority = Priority.from_string(data.get("priority", "medium"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        start_time = None
        end_time = None

        if data.get("start_time"):
            try:
                start_time = parse_time(data["start_time"], allow_next_day=False)
            except ValueError as e:
                return jsonify({"error": f"开始时间格式错误: {str(e)}"}), 400

        if data.get("end_time"):
            try:
                end_time = parse_time(data["end_time"], allow_next_day=False)
            except ValueError as e:
                return jsonify({"error": f"结束时间格式错误: {str(e)}"}), 400

        # Validate and adjust time window (handle cross-day scenarios)
        start_time, end_time, error = validate_time_window(start_time, end_time)
        if error:
            return jsonify({"error": error}), 400

        task = Task(
            name=name,
            description=data.get("description", ""),
            priority=priority,
            tags=tags,
            start_time=start_time,
            end_time=end_time,
            reminder=data.get("reminder", False),
        )

        storage.add_task(task)

        return jsonify({"success": True, "id": task.id})

    @app.route("/task/<task_id>/edit", methods=["PUT"])
    def edit_task(task_id):
        """Edit task properties."""
        try:
            task = storage.get_task_by_id(task_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 409
        if not task:
            return jsonify({"error": "Task not found"}), 404

        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "Invalid JSON body"}), 400

        if "name" in data:
            name = str(data["name"] or "").strip()
            if not name:
                return jsonify({"error": "任务名称不能为空"}), 400
            task.name = name

        if "priority" in data:
            try:
                task.priority = Priority.from_string(data["priority"])
            except ValueError as e:
                return jsonify({"error": str(e)}), 400

        if "tags" in data:
            tags = data["tags"]
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            if not isinstance(tags, list):
                return jsonify({"error": "tags 必须是数组或逗号分隔字符串"}), 400
            task.tags = tags

        if "reminder" in data:
            task.reminder = data["reminder"]

        if "start_time" in data or "end_time" in data:
            # Keys are independent: present key updates (empty clears),
            # absent key keeps the existing value.
            new_start, new_end = task.start_time, task.end_time
            try:
                if "start_time" in data:
                    new_start = datetime.fromisoformat(data["start_time"]) if data["start_time"] else None
                if "end_time" in data:
                    new_end = datetime.fromisoformat(data["end_time"]) if data["end_time"] else None
            except ValueError:
                return jsonify({"error": "Invalid datetime format (expect ISO 8601)"}), 400
            if new_start and new_end and new_end <= new_start:
                return jsonify({"error": "结束时间必须晚于开始时间"}), 400
            task.start_time, task.end_time = new_start, new_end

        storage.update_task(task)
        return jsonify({"success": True})

    @app.route("/task/<task_id>/done", methods=["POST"])
    def mark_done(task_id):
        """Mark a task as done."""
        try:
            task = storage.mark_done(task_id)
            return jsonify({"success": True, "task": task.name})
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

    @app.route("/task/<task_id>/start", methods=["POST"])
    def mark_doing(task_id):
        """Mark a task as in progress."""
        try:
            task = storage.mark_doing(task_id)
            return jsonify({"success": True, "task": task.name})
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

    @app.route("/task/<task_id>", methods=["DELETE"])
    def delete_task(task_id):
        """Delete a task."""
        try:
            deleted = storage.delete_task(task_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 409
        if deleted:
            return jsonify({"success": True})
        return jsonify({"error": "Task not found"}), 404

    @app.route("/task/<task_id>", methods=["GET"])
    def get_task(task_id):
        """Get task details."""
        try:
            task = storage.get_task_by_id(task_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 409
        if not task:
            return jsonify({"error": "Task not found"}), 404

        return jsonify({
            "id": task.id,
            "name": task.name,
            "description": task.description,
            "priority": task.priority.value,
            "tags": task.tags,
            "start_time": task.start_time.isoformat() if task.start_time else None,
            "end_time": task.end_time.isoformat() if task.end_time else None,
            "reminder": task.reminder,
        })

    @app.route("/task/<task_id>/time", methods=["PUT"])
    def update_task_time(task_id):
        """Update task start and end time."""
        try:
            task = storage.get_task_by_id(task_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 409
        if not task:
            return jsonify({"error": "Task not found"}), 404

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Update start_time if provided
        if "start_time" in data:
            if data["start_time"]:
                try:
                    task.start_time = datetime.fromisoformat(data["start_time"])
                except ValueError:
                    return jsonify({"error": "Invalid start_time format"}), 400
            else:
                task.start_time = None

        # Update end_time if provided
        if "end_time" in data:
            if data["end_time"]:
                try:
                    task.end_time = datetime.fromisoformat(data["end_time"])
                except ValueError:
                    return jsonify({"error": "Invalid end_time format"}), 400
            else:
                task.end_time = None

        if task.start_time and task.end_time and task.end_time <= task.start_time:
            return jsonify({"error": "结束时间必须晚于开始时间"}), 400

        storage.update_task(task)

        return jsonify({
            "success": True,
            "start_time": task.start_time.isoformat() if task.start_time else None,
            "end_time": task.end_time.isoformat() if task.end_time else None,
            "time_window": format_time_window(task.start_time, task.end_time),
        })

    return app


if __name__ == "__main__":
    app = create_app()
    # 关闭 reloader：避免父子两个进程重复初始化应用（含提醒线程）
    app.run(debug=True, port=5000, use_reloader=False)
