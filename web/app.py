"""Flask web application for TodoList."""

from flask import Flask, render_template, request, jsonify
from datetime import datetime, date
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from todo.models import Task, Priority, TaskStatus
from todo.storage import TaskConflict, TaskNotFound, TaskStorage, TaskStorageError
from todo.reminder import ReminderService
from todo.utils import parse_time, parse_iso_time, format_datetime, format_time_window, get_relative_time


def _format_task(task: Task) -> dict:
    """Format a task object into a dict for JSON response."""
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
        "status_icon": task.status.icon,
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

    start_reminder=True starts a background thread. A cross-process
    lock serializes its scans with the standalone reminder commands.
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

    @app.errorhandler(TaskStorageError)
    def storage_unavailable(error):
        return jsonify({"error": str(error)}), 503

    @app.route("/")
    def index():
        """Main page showing today's tasks."""
        target_date = date.today()
        initial_task = None
        task_id = request.args.get("task")
        try:
            date_str = request.args.get("date")
            if date_str:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = date.today()

        if task_id:
            initial_task = storage.get_task_by_id(task_id, exact=True)
            if initial_task is None:
                return jsonify({"error": "未找到对应任务"}), 404
            target_date = (initial_task.start_time or initial_task.end_time or initial_task.created_at).date()

        # Filter and format tasks for the target date
        filtered = storage.get_tasks_for_date(target_date)
        filtered = storage.sort_tasks(filtered)
        formatted_tasks = [_format_task(t) for t in filtered]

        return render_template(
            "index.html", tasks=formatted_tasks, current_date=target_date.isoformat(),
            initial_task_id=initial_task.id if initial_task else None,
        )

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

        task = Task(
            name=name,
            description=data.get("description", ""),
            priority=priority,
            tags=tags,
            start_time=start_time,
            end_time=end_time,
            reminder=data.get("reminder", False),
        )

        try:
            storage.add_task(task)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        return jsonify({"success": True, "id": task.id})

    def parse_task_patch(data, fields, *, original=False):
        changes = {key: data[key] for key in fields if key in data}
        if "name" in changes and not original:
            changes["name"] = str(changes["name"] or "").strip()
            if not changes["name"]:
                raise ValueError("任务名称不能为空")
        if "priority" in changes:
            if not isinstance(changes["priority"], str):
                raise ValueError("Invalid priority")
            changes["priority"] = Priority.from_string(changes["priority"])
        if "tags" in changes:
            if isinstance(changes["tags"], str):
                changes["tags"] = [tag.strip() for tag in changes["tags"].split(",") if tag.strip()]
            if not isinstance(changes["tags"], list):
                raise ValueError("tags 必须是数组或逗号分隔字符串")
        for field in ("start_time", "end_time"):
            if field in changes:
                value = changes[field]
                changes[field] = None if value in (None, "") else parse_iso_time(value)
        return changes

    def apply_task_patch(task_id, fields, *, require_changes=False):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Invalid JSON body")
        changes = parse_task_patch(data, fields)
        if require_changes and not changes:
            raise ValueError("No time fields provided")
        original = data.get("expected", {})
        if not isinstance(original, dict) or original.keys() - changes.keys():
            raise ValueError("expected 必须只包含正在修改的字段")
        expected = parse_task_patch(original, fields, original=True)
        return storage.update_task_fields(task_id, changes, expected=expected)

    @app.route("/task/<task_id>/edit", methods=["PUT"])
    def edit_task(task_id):
        try:
            task = apply_task_patch(task_id, ("name", "priority", "tags", "reminder", "start_time", "end_time"))
        except TaskNotFound as e:
            return jsonify({"error": str(e)}), 404
        except TaskConflict as e:
            return jsonify({"error": str(e)}), 409
        except (ValueError, TypeError, OverflowError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"success": True, "task": _format_task(task)})

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
            task = storage.mark_doing(task_id, allow_completed=False)
            return jsonify({"success": True, "task": task.name})
        except TaskConflict as e:
            return jsonify({"error": str(e)}), 409
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

    @app.route("/task/<task_id>", methods=["DELETE"])
    def delete_task(task_id):
        """Delete a task."""
        try:
            deleted = storage.delete_task(task_id, exact=True)
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
            task = apply_task_patch(task_id, ("start_time", "end_time"), require_changes=True)
        except TaskNotFound as e:
            return jsonify({"error": str(e)}), 404
        except TaskConflict as e:
            return jsonify({"error": str(e)}), 409
        except (ValueError, TypeError, OverflowError) as e:
            return jsonify({"error": str(e)}), 400

        return jsonify({
            "success": True,
            "task": _format_task(task),
            "start_time": task.start_time.isoformat() if task.start_time else None,
            "end_time": task.end_time.isoformat() if task.end_time else None,
            "time_window": format_time_window(task.start_time, task.end_time),
        })

    return app


if __name__ == "__main__":
    app = create_app()
    # 关闭 reloader：避免父子两个进程重复初始化应用（含提醒线程）
    app.run(debug=True, port=5000, use_reloader=False)
