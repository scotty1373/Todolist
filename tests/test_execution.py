from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from todo.models import ExecutionEnd, ExecutionPeriod, ExecutionState, Task, TaskStatus
from todo.storage import TaskConflict, TaskStorage
from web.app import create_app


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "tasks.json"
        self.store = TaskStorage(self.path)
        self.a = self.store.add_task(Task("A", id="taska", start_time=datetime(2026, 12, 8, 9)))
        self.b = self.store.add_task(Task("B", id="taskb"))

    def test_switch_resume_and_complete_after_reopening(self):
        self.store.mark_doing(self.a.id)
        self.store.mark_doing(self.b.id)
        self.store.mark_doing(self.b.id)
        reopened = TaskStorage(self.path)
        state = reopened.get_execution_state()
        self.assertEqual(state.current_task.id, self.b.id)
        self.assertEqual(reopened.get_task_by_id(self.a.id).status, TaskStatus.INTERRUPTED)
        self.assertEqual(len(state.periods), 2)
        self.assertEqual(state.periods[0].end_reason, ExecutionEnd.SWITCHED)
        self.assertEqual(state.periods[0].ended_at, state.periods[1].started_at)
        self.assertEqual(state.active_period.started_at.tzinfo, timezone.utc)
        self.assertEqual(reopened.get_task_by_id(self.a.id).start_time, self.a.start_time)

        reopened.mark_doing(self.a.id)
        reopened.mark_done(self.a.id)
        state = self.store.get_execution_state()
        self.assertIsNone(state.current_task)
        self.assertEqual([p.task_id for p in state.periods], [self.a.id, self.b.id, self.a.id])
        self.assertEqual(state.periods[-1].end_reason, ExecutionEnd.COMPLETED)

    def test_interruption_or_deletion_closes_only_the_relevant_period(self):
        self.store.mark_doing(self.a.id)
        self.store.interrupt_task(self.a.id)
        self.assertEqual(self.store.get_execution_state().periods[-1].end_reason, ExecutionEnd.INTERRUPTED)
        self.store.mark_doing(self.b.id)
        with self.assertRaises(TaskConflict):
            self.store.interrupt_task(self.a.id)
        self.assertEqual(self.store.get_execution_state().current_task.id, self.b.id)
        self.store.delete_task(self.b.id)
        state = self.store.get_execution_state()
        self.assertIsNone(state.current_task)
        self.assertEqual(state.periods[-1].end_reason, ExecutionEnd.DELETED)
        self.assertEqual(state.periods[-1].task_name, self.b.name)

    def test_daily_records_split_overnight_execution_and_show_end_status_only_on_completion_day(self):
        zone = timezone(timedelta(hours=8))
        started = datetime(2026, 11, 22, 23, 40, tzinfo=zone)
        ended = datetime(2026, 11, 23, 0, 15, tzinfo=zone)
        now = datetime(2026, 11, 24, 9, tzinfo=zone)
        period = ExecutionPeriod(self.a.id, self.a.name, started, ended, ExecutionEnd.COMPLETED)
        state = ExecutionState((self.a,), (period,), None)

        first, = state.records_for_day(date(2026, 11, 22), now, zone=zone)
        self.assertEqual(first.elapsed_seconds(now), 20 * 60)
        self.assertIsNone(first.end_reason)
        second, = state.records_for_day(date(2026, 11, 23), now, zone=zone)
        self.assertEqual(second.elapsed_seconds(now), 15 * 60)
        self.assertEqual(second.end_reason, ExecutionEnd.COMPLETED)
        self.assertEqual(state.records_for_day(date(2026, 11, 24), now, zone=zone), ())

    def test_daily_records_handle_exact_midnight_and_live_period(self):
        zone = timezone(timedelta(hours=8))
        started = datetime(2026, 11, 22, 23, 30, tzinfo=zone)
        midnight = datetime(2026, 11, 23, tzinfo=zone)
        now = midnight + timedelta(minutes=10)
        state = ExecutionState(
            (self.a, self.b),
            (
                ExecutionPeriod(self.a.id, self.a.name, started, midnight, ExecutionEnd.COMPLETED),
                ExecutionPeriod(self.b.id, self.b.name, midnight),
            ),
            self.b,
        )
        previous, = state.records_for_day(date(2026, 11, 22), now, zone=zone)
        self.assertEqual(previous.elapsed_seconds(now), 30 * 60)
        self.assertIsNone(previous.end_reason)
        completed, running = state.records_for_day(date(2026, 11, 23), now, zone=zone)
        self.assertEqual(completed.elapsed_seconds(now), 0)
        self.assertEqual(completed.end_reason, ExecutionEnd.COMPLETED)
        self.assertIsNone(running.ended_at)
        self.assertEqual(running.elapsed_seconds(now), 10 * 60)

    def test_completed_insertions_leave_previous_tasks_in_resume_order(self):
        c = self.store.add_task(Task("C", id="taskc"))
        for task in (self.a, self.b, c):
            self.store.mark_doing(task.id)
        self.store.mark_done(c.id)

        reopened = TaskStorage(self.path)
        state = reopened.get_execution_state()
        self.assertIsNone(state.current_task)
        self.assertEqual([task.id for task in state.pending_resume], [self.b.id, self.a.id])
        reopened.mark_doing(state.pending_resume[0].id)
        self.assertEqual(reopened.get_execution_state().current_task.id, self.b.id)
        reopened.mark_done(self.b.id)
        self.assertEqual([task.id for task in reopened.get_execution_state().pending_resume], [self.a.id])

        reopened.mark_doing(self.a.id)
        reopened.interrupt_task(self.a.id)
        state = reopened.get_execution_state()
        self.assertEqual([task.id for task in state.pending_resume], [self.a.id])
        self.assertEqual(sum(period.task_id == self.a.id for period in state.periods), 2)
        reopened.delete_task(self.a.id)
        self.assertEqual(reopened.get_execution_state().pending_resume, ())

    def test_add_task_only_changes_focus_when_requested(self):
        self.store.mark_doing(self.a.id)
        self.store.add_task(Task("Waiting", id="waiting"))
        self.assertEqual(self.store.get_execution_state().current_task.id, self.a.id)
        self.assertEqual(len(self.store.get_execution_state().periods), 1)

        added = self.store.add_task(Task("Inserted", id="inserted"), activate=True)
        state = self.store.get_execution_state()
        self.assertEqual(state.current_task.id, added.id)
        self.assertEqual(state.pending_resume[0].id, self.a.id)
        self.assertEqual(state.periods[0].ended_at, state.periods[1].started_at)
        self.assertEqual(state.activity[0].task, "A → Inserted")

    def test_progress_and_execution_history_survive_task_deletion(self):
        self.store.mark_doing(self.a.id)
        with self.assertRaises(TaskConflict):
            self.store.add_progress(self.b.id, "incorrect task")
        with self.assertRaises(ValueError):
            self.store.add_progress(self.a.id, "  ")
        self.store.add_progress(self.a.id, "  Finished the first draft.  ")
        self.assertFalse(self.store.delete_task("task", exact=True))
        self.assertTrue(self.store.delete_task(self.a.id, exact=True))

        reopened = TaskStorage(self.path).get_execution_state()
        self.assertIsNone(reopened.current_task)
        self.assertEqual(len(reopened.notes), 1)
        self.assertEqual(reopened.notes[0].text, "Finished the first draft.")
        self.assertEqual(reopened.notes[0].task_id, self.a.id)
        self.assertEqual([entry.title for entry in reopened.activity], ["任务删除", "补充进展", "开始执行"])

    def test_failed_switch_preserves_status_and_history(self):
        self.store.mark_doing(self.a.id)
        before = self.store.get_execution_state()
        original_replace = self.store._replace_json

        def interrupted_commit(path, data):
            if path == self.path:
                raise OSError("commit interrupted")
            original_replace(path, data)

        with patch.object(self.store, "_replace_json", side_effect=interrupted_commit):
            with self.assertRaisesRegex(OSError, "commit interrupted"):
                self.store.mark_doing(self.b.id)
        after = self.store.get_execution_state()
        self.assertEqual(after.periods, before.periods)
        self.assertEqual(after.current_task.id, self.a.id)
        self.assertEqual(self.store.get_task_by_id(self.b.id).status, TaskStatus.PENDING)

    def test_concurrent_switches_leave_one_running_task(self):
        other = TaskStorage(self.path)
        ready = Barrier(2, timeout=3)

        def start(storage, task_id):
            ready.wait()
            storage.mark_doing(task_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(start, store, task.id) for store, task in ((self.store, self.a), (other, self.b))]
            for result in results:
                result.result(timeout=5)
        state = self.store.get_execution_state()
        statuses = {task.status for task in state.tasks}
        self.assertEqual(statuses, {TaskStatus.DOING, TaskStatus.INTERRUPTED})
        self.assertEqual(len(state.periods), 2)
        self.assertEqual(state.periods[0].ended_at, state.periods[1].started_at)
        self.assertEqual(state.current_task.id, state.periods[1].task_id)

    def test_import_merges_legacy_tasks_by_id(self):
        source = self.path.with_name("previous.json")
        legacy = Task("旧任务", id="legacy", status=TaskStatus.DOING)
        source.write_text(json.dumps([self.a.to_dict(), legacy.to_dict()]), encoding="utf-8")
        original = source.read_bytes()
        self.store.mark_doing(self.a.id)
        self.assertEqual(self.store.import_tasks(source), (1, 1))
        self.assertEqual(self.store.import_tasks(source), (0, 2))
        self.assertEqual(self.store.get_execution_state().current_task.id, self.a.id)
        self.assertEqual(self.store.get_task_by_id(legacy.id).name, legacy.name)
        self.assertEqual(self.store.get_task_by_id(legacy.id).status, TaskStatus.INTERRUPTED)
        self.assertEqual(source.read_bytes(), original)

    def test_web_start_switches_execution_and_links_to_task_date(self):
        client = create_app(self.store, start_reminder=False).test_client()
        for task in (self.a, self.b):
            self.assertEqual(client.post(f"/task/{task.id}/start").status_code, 200)
        self.assertEqual(self.store.get_task_by_id(self.a.id).status, TaskStatus.INTERRUPTED)
        self.assertEqual(self.store.get_execution_state().current_task.id, self.b.id)
        page = client.get(f"/?task={self.a.id}&date=2027-01-01")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("let currentDate = '2026-12-08'", html)
        self.assertIn('const initialTaskId = "taska"', html)
        self.assertEqual(client.get("/?task=missing").status_code, 404)
