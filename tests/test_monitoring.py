import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main as bds


class MonitoringAndScheduleTests(unittest.TestCase):
    def _manager(self, root: Path):
        manager = bds.Manager.__new__(bds.Manager)
        manager.root = root
        manager.backup_dir = root.parent / "backups"
        manager.backup_dir.mkdir(exist_ok=True)
        manager.proc = None
        manager.process_lock = threading.RLock()
        manager.metrics_lock = threading.Lock()
        manager.metrics_samples = __import__("collections").deque(maxlen=180)
        manager._metrics_previous = {}
        manager._metrics_current = {"available": False, "sampled_at": None}
        manager.jobs_lock = threading.RLock()
        manager.scheduled_jobs = []
        manager.job_history = []
        manager._job_running = set()
        manager.log_id = 0
        manager.log_lock = threading.Lock()
        manager.logs = __import__("collections").deque(maxlen=100)
        manager._append_log = MagicMock()
        return manager

    def test_proc_readers_return_safe_shapes_or_none(self):
        cpu = bds._read_proc_cpu()
        memory = bds._read_proc_memory()
        network = bds._read_proc_network()
        if cpu is not None:
            self.assertEqual(len(cpu), 2)
            self.assertGreaterEqual(cpu[0], cpu[1])
        if memory is not None:
            self.assertGreater(memory["total"], 0)
            self.assertGreaterEqual(memory["used"], 0)
            self.assertLessEqual(memory["used"], memory["total"])
        if network is not None:
            self.assertGreaterEqual(network["rx_bytes"], 0)
            self.assertGreaterEqual(network["tx_bytes"], 0)

    def test_process_metrics_does_not_fail_for_missing_pid(self):
        self.assertIsNone(bds._read_process_metrics(None))
        self.assertIsNone(bds._read_process_metrics(999999999))

    def test_metric_sampler_computes_bounded_cpu_and_network_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "server"
            backup = Path(tmp) / "backups"
            root.mkdir(); backup.mkdir()
            manager = self._manager(root)
            manager.backup_dir = backup
            manager._metrics_previous = {
                "time": 100.0,
                "cpu": (1000, 500),
                "network": {"rx_bytes": 1000, "tx_bytes": 2000},
                "process": None,
            }
            with patch.object(bds.time, "time", return_value=105.0), \
                 patch.object(bds, "_read_proc_cpu", return_value=(1500, 750)), \
                 patch.object(bds, "_read_proc_memory", return_value={"total": 1000, "available": 400, "used": 600}), \
                 patch.object(bds, "_read_proc_network", return_value={"rx_bytes": 6000, "tx_bytes": 7000}), \
                 patch.object(bds, "_read_process_metrics", return_value=None):
                sample = manager._sample_metrics()
            self.assertEqual(sample["cpu_percent"], 50.0)
            self.assertEqual(sample["network"]["rx_bytes_per_sec"], 1000.0)
            self.assertEqual(sample["network"]["tx_bytes_per_sec"], 1000.0)
            self.assertIn("disk", sample)
            self.assertTrue(sample["available"])

    def test_metric_history_is_copied_not_exposed_mutably(self):
        manager = bds.Manager.__new__(bds.Manager)
        manager.metrics_lock = threading.Lock()
        manager._metrics_current = {"cpu_percent": 12.5}
        manager.metrics_samples = __import__("collections").deque([{"cpu_percent": 10}], maxlen=180)
        payload = manager.metrics()
        payload["current"]["cpu_percent"] = 90
        payload["history"].clear()
        self.assertEqual(manager._metrics_current["cpu_percent"], 12.5)
        self.assertEqual(len(manager.metrics_samples), 1)

    def test_health_check_reports_missing_files_and_stopped_as_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "server"
            root.mkdir()
            manager = self._manager(root)
            manager.bds_executable = lambda: root / "bedrock_server"
            manager.metrics = lambda: {"current": {"disk": {"free": 4 * 1024**3}, "sampled_at": "now"}}
            manager._read_props = lambda: {}
            manager.list_backups = lambda: []
            result = manager.health()
            by_id = {c["id"]: c for c in result["checks"]}
            self.assertEqual(by_id["process"]["status"], "info")
            self.assertEqual(by_id["disk"]["status"], "ok")
            self.assertEqual(by_id["executable"]["status"], "warning")

    def test_scheduled_job_persists_valid_daily_world_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(Path(tmp))
            schedule_file = Path(tmp) / "jobs.json"
            with patch.object(bds, "SCHEDULE_FILE", schedule_file):
                result = manager.upsert_scheduled_job({
                    "name": "Daily world backup", "kind": "world_backup", "time": "02:30",
                    "days": list(range(7)), "enabled": True,
                })
                self.assertTrue(result["ok"])
                stored = json.loads(schedule_file.read_text())
            self.assertEqual(stored["jobs"][0]["name"], "Daily world backup")
            self.assertEqual(stored["jobs"][0]["time"], "02:30")
            self.assertEqual(len(stored["jobs"][0]["id"]), 12)

    def test_scheduled_job_validation_rejects_invalid_time_days_and_announcement(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(Path(tmp))
            base = {"name": "Task", "kind": "world_backup", "time": "02:30", "days": [0], "enabled": True}
            for change, message in [
                ({"time": "24:00"}, "HH:MM"),
                ({"days": []}, "weekday"),
                ({"days": [7]}, "weekday"),
                ({"enabled": "yes"}, "true or false"),
            ]:
                payload = {**base, **change}
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                    manager.upsert_scheduled_job(payload)
            with self.assertRaisesRegex(ValueError, "single line"):
                manager.upsert_scheduled_job({**base, "kind": "announcement", "message": "hello\nstop"})

    def test_schedule_job_update_preserves_id_and_last_run_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(Path(tmp))
            with patch.object(bds, "SCHEDULE_FILE", Path(tmp) / "jobs.json"):
                first = manager.upsert_scheduled_job({"name": "World", "kind": "world_backup", "time": "01:00", "days": [0]})["job"]
                manager.scheduled_jobs[0]["last_run_key"] = "2026-11-02@01:00"
                manager.scheduled_jobs[0]["last_status"] = "success"
                updated = manager.upsert_scheduled_job({**first, "name": "World backup", "time": "02:00"})["job"]
            self.assertEqual(first["id"], updated["id"])
            self.assertEqual(updated["last_run_key"], "2026-11-02@01:00")
            self.assertEqual(updated["last_status"], "success")

    def test_delete_job_requires_known_safe_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(Path(tmp))
            with patch.object(bds, "SCHEDULE_FILE", Path(tmp) / "jobs.json"):
                job = manager.upsert_scheduled_job({"name": "World", "kind": "world_backup", "time": "01:00", "days": [0]})["job"]
                with self.assertRaisesRegex(ValueError, "Invalid scheduled task ID"):
                    manager.delete_scheduled_job("../../bad")
                self.assertTrue(manager.delete_scheduled_job(job["id"])["ok"])
                self.assertEqual(manager.scheduled_jobs, [])

    def test_scheduled_backup_executes_and_records_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(Path(tmp))
            manager.scheduled_jobs = [{"id": "a1b2c3d4e5f6", "name": "Nightly", "kind": "world_backup", "last_status": "running"}]
            manager.backup = MagicMock(return_value={"message": "World backup complete"})
            with patch.object(bds, "SCHEDULE_FILE", Path(tmp) / "jobs.json"):
                manager._run_scheduled_job(dict(manager.scheduled_jobs[0]))
            manager.backup.assert_called_once_with("world")
            self.assertEqual(manager.scheduled_jobs[0]["last_status"], "success")
            self.assertEqual(manager.job_history[-1]["status"], "success")

    def test_scheduled_announcement_uses_bedrock_command_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(Path(tmp))
            manager.command = MagicMock(return_value={"message": "Sent announcement"})
            job = {"id": "a1b2c3d4e5f6", "name": "Notice", "kind": "announcement", "message": "Maintenance soon"}
            with patch.object(bds, "SCHEDULE_FILE", Path(tmp) / "jobs.json"):
                manager._run_scheduled_job(job)
            manager.command.assert_called_once_with('say "Maintenance soon"')
            self.assertEqual(manager.job_history[-1]["status"], "success")

    def test_scheduled_task_failure_is_recorded_and_does_not_kill_scheduler(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(Path(tmp))
            manager.backup = MagicMock(side_effect=ValueError("not running"))
            job = {"id": "a1b2c3d4e5f6", "name": "Nightly", "kind": "full_backup"}
            with patch.object(bds, "SCHEDULE_FILE", Path(tmp) / "jobs.json"):
                manager._run_scheduled_job(job)
            self.assertEqual(manager.job_history[-1]["status"], "failed")
            self.assertIn("not running", manager.job_history[-1]["message"])
            self.assertNotIn(job["id"], manager._job_running)

    def test_scheduled_state_contains_local_timezone_and_history(self):
        manager = bds.Manager.__new__(bds.Manager)
        manager.jobs_lock = threading.RLock()
        manager.scheduled_jobs = [{"id": "a1b2c3d4e5f6", "name": "Test"}]
        manager.job_history = [{"status": "success"}]
        state = manager.scheduled_jobs_state()
        self.assertEqual(len(state["jobs"]), 1)
        self.assertEqual(len(state["history"]), 1)
        self.assertTrue(state["timezone"])
        self.assertTrue(state["local_time"])


if __name__ == "__main__":
    unittest.main()

class MonitoringHttpApiTests(unittest.TestCase):
    def setUp(self):
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        self.urllib_error = urllib.error
        self.urllib_request = urllib.request
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "server"
        root.mkdir()
        self.manager = MonitoringAndScheduleTests()._manager(root)
        self.manager.metrics_lock = threading.Lock()
        self.manager.metrics_samples = __import__("collections").deque([{"cpu_percent": 12}], maxlen=180)
        self.manager._metrics_current = {"available": True, "cpu_percent": 12}
        self.manager.metrics = lambda: {"current": dict(self.manager._metrics_current), "history": list(self.manager.metrics_samples)}
        self.manager.health = lambda: {"status": "ok", "checks": []}
        self.patches = [
            patch.object(bds, "MANAGER", self.manager),
            patch.object(bds, "TOKEN", "monitoring-test-token"),
            patch.object(bds, "SCHEDULE_FILE", Path(self.tmp.name) / "scheduled_jobs.json"),
        ]
        for item in self.patches:
            item.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), bds.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def request(self, path, method="GET", payload=None, auth=True):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {}
        if auth:
            headers["X-BDS-Token"] = "monitoring-test-token"
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = self.urllib_request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.urllib_request.urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read().decode())
        except self.urllib_error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_metrics_health_and_schedules_api_require_authentication(self):
        for path in ("/api/metrics", "/api/health", "/api/schedules"):
            with self.subTest(path=path):
                code, _ = self.request(path, auth=False)
                self.assertEqual(code, 403)
        code, metrics = self.request("/api/metrics")
        self.assertEqual(code, 200)
        self.assertEqual(metrics["current"]["cpu_percent"], 12)
        code, health = self.request("/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(health["status"], "ok")

    def test_schedule_create_update_and_delete_over_http(self):
        code, result = self.request("/api/schedules", "POST", {"job": {
            "name": "Daily backup", "kind": "world_backup", "time": "02:30",
            "days": [0, 1, 2, 3, 4, 5, 6], "enabled": True,
        }})
        self.assertEqual(code, 200)
        job = result["job"]
        self.assertEqual(job["kind"], "world_backup")
        code, state = self.request("/api/schedules")
        self.assertEqual(code, 200)
        self.assertEqual(state["jobs"][0]["id"], job["id"])
        code, updated = self.request("/api/schedules", "POST", {"job": {**job, "enabled": False}})
        self.assertEqual(code, 200)
        self.assertFalse(updated["job"]["enabled"])
        code, deleted = self.request("/api/schedules/delete", "POST", {"id": job["id"]})
        self.assertEqual(code, 200)
        self.assertTrue(deleted["ok"])
        code, state = self.request("/api/schedules")
        self.assertEqual(state["jobs"], [])

    def test_schedule_api_rejects_invalid_task_without_persisting_it(self):
        code, result = self.request("/api/schedules", "POST", {"job": {
            "name": "Bad task", "kind": "shell", "time": "25:99", "days": [8], "enabled": True,
        }})
        self.assertEqual(code, 400)
        self.assertEqual(self.manager.scheduled_jobs, [])


if __name__ == "__main__":
    unittest.main()

class MonitoringSchedulerLifecycleTests(unittest.TestCase):
    def _scheduler_manager(self, jobs):
        manager = bds.Manager.__new__(bds.Manager)
        manager.jobs_lock = threading.RLock()
        manager.scheduled_jobs = jobs
        manager.job_history = []
        manager._job_running = set()
        manager._append_log = MagicMock()
        manager._scheduler_stop = MagicMock()
        manager._scheduler_stop.wait.side_effect = [False, True]
        manager._persist_scheduled_jobs = MagicMock()
        return manager

    def test_scheduler_starts_matching_local_weekday_task_once(self):
        from datetime import datetime, timezone
        job = {"id": "a1b2c3d4e5f6", "name": "Friday backup", "kind": "world_backup",
               "time": "02:30", "days": [4], "enabled": True, "last_run_key": ""}
        manager = self._scheduler_manager([job])
        created = []

        class FakeDateTime:
            @staticmethod
            def now():
                return datetime(2026, 11, 6, 2, 30, 20, tzinfo=timezone.utc)

        class FakeThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.started = False
                created.append(self)
            def start(self):
                self.started = True

        with patch.object(bds, "datetime", FakeDateTime), patch.object(bds.threading, "Thread", FakeThread):
            manager._scheduled_job_loop()

        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].started)
        self.assertEqual(manager.scheduled_jobs[0]["last_status"], "running")
        self.assertIn("a1b2c3d4e5f6", manager._job_running)
        self.assertTrue(manager._persist_scheduled_jobs.called)

    def test_scheduler_does_not_replay_a_missed_schedule_window(self):
        from datetime import datetime, timezone
        job = {"id": "a1b2c3d4e5f6", "name": "Missed backup", "kind": "world_backup",
               "time": "02:30", "days": [4], "enabled": True, "last_run_key": ""}
        manager = self._scheduler_manager([job])
        created = []

        class FakeDateTime:
            @staticmethod
            def now():
                return datetime(2026, 11, 6, 2, 33, 10, tzinfo=timezone.utc)

        class FakeThread:
            def __init__(self, **kwargs):
                created.append(self)
            def start(self):
                self.started = True

        with patch.object(bds, "datetime", FakeDateTime), patch.object(bds.threading, "Thread", FakeThread):
            manager._scheduled_job_loop()

        self.assertEqual(created, [])
        self.assertEqual(manager.scheduled_jobs[0].get("last_status"), None)
        self.assertEqual(manager._job_running, set())

    def test_shutdown_waits_for_active_job_to_release_before_returning(self):
        import time
        manager = bds.Manager.__new__(bds.Manager)
        manager.jobs_lock = threading.RLock()
        manager._job_running = {"a1b2c3d4e5f6"}
        manager._scheduler_stop = threading.Event()
        manager._metrics_stop = threading.Event()
        manager._scheduler_thread = None
        manager._auto_thread = None
        manager._metrics_thread = None

        def finish_job():
            time.sleep(0.12)
            with manager.jobs_lock:
                manager._job_running.discard("a1b2c3d4e5f6")

        worker = threading.Thread(target=finish_job)
        worker.start()
        manager.shutdown()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(manager._job_running, set())
        self.assertTrue(manager._scheduler_stop.is_set())
        self.assertTrue(manager._metrics_stop.is_set())

    def test_scheduler_worker_start_failure_is_recorded_and_releases_job(self):
        from datetime import datetime, timezone
        job = {"id": "a1b2c3d4e5f6", "name": "Friday backup", "kind": "world_backup",
               "time": "02:30", "days": [4], "enabled": True, "last_run_key": ""}
        manager = MonitoringSchedulerLifecycleTests()._scheduler_manager([job])

        class FakeDateTime:
            @staticmethod
            def now():
                return datetime(2026, 11, 6, 2, 30, 20, tzinfo=timezone.utc)

        class BrokenThread:
            def __init__(self, **kwargs):
                pass
            def start(self):
                raise RuntimeError("thread limit reached")

        with patch.object(bds, "datetime", FakeDateTime), patch.object(bds.threading, "Thread", BrokenThread):
            manager._scheduled_job_loop()

        self.assertEqual(manager._job_running, set())
        self.assertEqual(manager.scheduled_jobs[0]["last_status"], "failed")
        self.assertIn("thread limit reached", manager.scheduled_jobs[0]["last_message"])
        self.assertEqual(manager.job_history[-1]["status"], "failed")

    def test_scheduler_ignores_corrupt_persisted_task_id(self):
        from datetime import datetime, timezone
        job = {"id": "../../unsafe", "name": "Corrupt", "kind": "world_backup",
               "time": "02:30", "days": [4], "enabled": True, "last_run_key": ""}
        manager = MonitoringSchedulerLifecycleTests()._scheduler_manager([job])
        created = []

        class FakeDateTime:
            @staticmethod
            def now():
                return datetime(2026, 11, 6, 2, 30, 20, tzinfo=timezone.utc)

        class FakeThread:
            def __init__(self, **kwargs): created.append(self)
            def start(self): pass

        with patch.object(bds, "datetime", FakeDateTime), patch.object(bds.threading, "Thread", FakeThread):
            manager._scheduled_job_loop()
        self.assertEqual(created, [])
        self.assertEqual(manager._job_running, set())

    def test_health_check_explains_external_bds_without_claiming_managed_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "server"
            root.mkdir()
            manager = MonitoringAndScheduleTests()._manager(root)
            manager.bds_executable = lambda: root / "bedrock_server"
            manager.metrics = lambda: {"current": {"disk": {"free": 4 * 1024**3}, "sampled_at": "now"}}
            manager._unmanaged_bds = lambda: [{"pid": 4321, "command": "bedrock_server"}]
            result = manager.health()
            process = next(check for check in result["checks"] if check["id"] == "process")
            self.assertEqual(process["status"], "info")
            self.assertIn("External BDS process detected (PID 4321)", process["detail"])
            self.assertIn("metrics are unavailable", process["detail"])
