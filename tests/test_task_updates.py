from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Thread
import unittest
from unittest.mock import patch

from todo.cli import cmd_add, cmd_edit, create_parser
from todo.models import Task, TaskStatus
from todo.reminder import ReminderService
from todo.storage import TaskStorage, TaskStorageError
from web.app import create_app


class TaskUpdateTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "tasks.json"
        self.storage = TaskStorage(self.path)

    def client(self):
        return create_app(self.storage, start_reminder=False).test_client()

    def test_lock_failure_does_not_strand_other_threads(self):
        with (
            patch("todo.storage._os_lock", side_effect=OSError("lock timeout")),
            patch("todo.storage._os_unlock") as unlock,
            self.assertRaisesRegex(OSError, "lock timeout"),
        ):
            self.storage.get_all_tasks()
        unlock.assert_not_called()

        results = []
        reader = Thread(target=lambda: results.append(self.storage.get_all_tasks()), daemon=True)
        reader.start()
        reader.join(3)
        self.assertEqual(results, [[]])

    def test_failed_commit_after_recovery_keeps_valid_backup(self):
        first = self.storage.add_task(Task("first"))
        self.storage.add_task(Task("second"))
        self.path.write_text('{"wrong": "shape"}', encoding="utf-8")
        replace = os.replace
        main_replacements = 0

        def interrupt_second_main_replace(source, destination):
            nonlocal main_replacements
            if destination == self.path:
                main_replacements += 1
                if main_replacements == 2:
                    raise OSError("commit interrupted")
            return replace(source, destination)

        with patch("todo.storage.os.replace", side_effect=interrupt_second_main_replace):
            with self.assertRaisesRegex(OSError, "commit interrupted"):
                self.storage.add_task(Task("third"))

        self.assertEqual([task.id for task in self.storage.get_all_tasks()], [first.id])
        backup = self.path.with_suffix(".json.bak")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))[0]["id"], first.id)
        self.assertEqual(len(list(self.path.parent.glob("tasks.corrupt-*.json"))), 1)

    def test_missing_primary_recovers_and_two_damaged_files_block_write(self):
        first = self.storage.add_task(Task("first"))
        self.storage.add_task(Task("second"))
        self.path.unlink()
        reopened = TaskStorage(self.path)
        self.assertEqual([task.id for task in reopened.get_all_tasks()], [first.id])
        backup = self.path.with_suffix(".json.bak")
        self.path.write_text("[", encoding="utf-8")
        backup.write_text("null", encoding="utf-8")

        with self.assertRaises(TaskStorageError):
            reopened.add_task(Task("third"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "[")
        self.assertEqual(backup.read_text(encoding="utf-8"), "null")
        self.assertEqual(self.client().get("/tasks").status_code, 503)

    def test_web_edit_and_concurrent_status_update_both_survive(self):
        task = self.storage.add_task(Task("original"))
        other = TaskStorage(self.path)
        ready = Barrier(2, timeout=3)

        def complete():
            ready.wait()
            other.mark_done(task.id)
            other.mark_reminded(task.id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = pool.submit(complete)
            ready.wait()
            response = self.client().put(
                f"/task/{task.id}/edit", json={"name": "renamed", "expected": {"name": "original"}}
            )
            result.result(timeout=5)

        self.assertEqual(response.status_code, 200)
        stored = self.storage.get_task_by_id(task.id)
        self.assertEqual(stored.name, "renamed")
        self.assertEqual(stored.status, TaskStatus.DONE)
        self.assertTrue(stored.reminded)

    def test_conflicting_edit_cannot_replace_a_newer_field(self):
        task = self.storage.add_task(Task("original"))
        self.storage.update_task_fields(task.id, {"name": "newer"})
        response = self.client().put(
            f"/task/{task.id}/edit", json={"name": "stale", "expected": {"name": "original"}}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.storage.get_task_by_id(task.id).name, "newer")

    def test_retries_id_collision(self):
        self.storage.add_task(Task("first", id="same"))
        with patch("todo.storage.generate_short_id", side_effect=["same", "next"]):
            task = self.storage.add_task(Task("second", id="same"))
        self.assertEqual(task.id, "next")

    def test_offset_is_normalized_and_midnight_is_exclusive(self):
        client = self.client()
        source = datetime.fromisoformat("2025-04-01T13:00:00+08:00")
        added = client.post("/add", json={"name": "offset", "start_time": source.isoformat()})
        self.assertEqual(added.status_code, 200)
        task = self.storage.get_task_by_id(added.get_json()["id"])
        self.assertEqual(task.start_time, source.astimezone().replace(tzinfo=None))

        response = client.put(f"/task/{task.id}/time", json={
            "start_time": "2025-04-01T23:00:00",
            "end_time": "2025-04-02T00:00:00",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["task"]["end_time_iso"], "2025-04-02T00:00:00")
        self.assertEqual(len(self.storage.get_tasks_for_date(date(2025, 4, 1))), 1)
        self.assertEqual(client.get("/api/tasks/2025-04-02").get_json(), [])

    def test_cli_preserves_explicit_dates_and_infers_bare_end(self):
        parser = create_parser()
        cmd_add(parser.parse_args([
            "add", "historic", "-s", "2025-04-01 23:03", "-e", "2025-04-02 01:07"
        ]), self.storage)
        task, = self.storage.get_all_tasks()
        self.assertEqual(task.start_time, datetime(2025, 4, 1, 23, 3))

        cmd_edit(parser.parse_args(["edit", task.id, "-e", "02:07"]), self.storage)
        self.assertEqual(self.storage.get_task_by_id(task.id).end_time, datetime(2025, 4, 2, 2, 7))

    def test_reschedule_during_delivery_leaves_new_reminder_due(self):
        new_start = datetime.now() - timedelta(minutes=1)
        task = self.storage.add_task(Task("due", start_time=new_start - timedelta(minutes=2), reminder=True))
        service = ReminderService(self.storage)
        other = TaskStorage(self.path)

        def reschedule(_task):
            other.update_task_fields(task.id, {"start_time": new_start})

        with patch.object(service, "_send_interactive_toast", side_effect=reschedule):
            service.run_once()

        self.assertFalse(self.storage.get_task_by_id(task.id).reminded)
        with patch.object(service, "_send_interactive_toast") as send:
            service.run_once()
        send.assert_called_once()
        self.assertTrue(self.storage.get_task_by_id(task.id).reminded)
        response = self.client().put(f"/task/{task.id}/time", json={
            "start_time": (new_start + timedelta(seconds=10)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.storage.get_task_by_id(task.id).reminded)

    def test_single_run_cannot_duplicate_an_active_scan_and_can_take_over(self):
        started = Event()
        release = Event()
        taken_over = Event()
        task = self.storage.add_task(Task(
            "due", start_time=datetime.now() - timedelta(minutes=1), reminder=True
        ))
        daemon = ReminderService(self.storage)
        backup = ReminderService(TaskStorage(self.path), check_interval=0.05)

        def hold_scan(_task):
            started.set()
            release.wait(5)

        with patch.object(daemon, "_send_interactive_toast", side_effect=hold_scan), patch.object(
            backup, "_send_interactive_toast", side_effect=lambda _task: taken_over.set()
        ) as send:
            daemon.start()
            try:
                self.assertTrue(started.wait(3))
                backup.run_once()
                send.assert_not_called()
                backup.start()
                self.assertTrue(backup.is_running())
                release.set()
                daemon.stop()
                self.assertTrue(self.storage.get_task_by_id(task.id).reminded)
                self.storage.update_task_fields(task.id, {"start_time": datetime.now() - timedelta(seconds=10)})
                self.assertTrue(taken_over.wait(3))
            finally:
                release.set()
                daemon.stop()
                backup.stop()
            self.assertTrue(self.storage.get_task_by_id(task.id).reminded)
            send.assert_called_once()
