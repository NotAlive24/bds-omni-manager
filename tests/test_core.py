import os
import io
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as bds


class CoreTests(unittest.TestCase):
    def _player_manager(self):
        manager = bds.Manager.__new__(bds.Manager)
        manager.players_lock = threading.RLock()
        manager.online_players = {}
        manager._player_session_generation = 7
        manager._players_tracking_active = True
        return manager

    def test_player_disconnect_with_xuid_and_pfid_removes_online_player(self):
        manager = self._player_manager()
        generation = manager._player_session_generation
        manager._track_player(
            "[2026-11-07 10:00:00:000 INFO] Player connected: ExamplePlayer, xuid: 2535469490621656",
            player_generation=generation,
        )
        self.assertEqual(manager._online_player_snapshot(), [
            {"name": "ExamplePlayer", "xuid": "2535469490621656"}
        ])

        manager._track_player(
            "[2026-11-07 10:05:00:000 INFO] Player disconnected: ExamplePlayer, xuid: 2535469490621656, pfid: 5bfef7ceb772454",
            player_generation=generation,
        )
        self.assertEqual(manager._online_player_snapshot(), [])

    def test_player_disconnect_supports_blank_xuid_and_pfid_only(self):
        manager = self._player_manager()
        generation = manager._player_session_generation
        manager._track_player("Player connected: LocalPlayer, xuid: ", player_generation=generation)
        manager._track_player(
            "Player disconnected: LocalPlayer, xuid: , pfid: ",
            player_generation=generation,
        )
        self.assertEqual(manager._online_player_snapshot(), [])

        manager._track_player("Player connected: OfflinePlayer", player_generation=generation)
        manager._track_player(
            "Player disconnected: OfflinePlayer, pfid: device-session",
            player_generation=generation,
        )
        self.assertEqual(manager._online_player_snapshot(), [])

    def test_player_disconnect_falls_back_to_xuid_if_name_differs(self):
        manager = self._player_manager()
        generation = manager._player_session_generation
        manager._track_player("Player connected: OldName, xuid: 42", player_generation=generation)
        manager._track_player(
            "Player disconnected: NewName, xuid: 42, pfid: session-1",
            player_generation=generation,
        )
        self.assertEqual(manager._online_player_snapshot(), [])

    def test_late_player_events_from_previous_process_are_ignored(self):
        manager = self._player_manager()
        manager._player_session_generation = 8
        manager.online_players["currentplayer"] = {"name": "CurrentPlayer", "xuid": "84"}
        manager._track_player(
            "Player disconnected: CurrentPlayer, xuid: 84, pfid: old-session",
            player_generation=7,
        )
        manager._track_player("Player connected: StalePlayer, xuid: 99", player_generation=7)
        self.assertEqual(manager._online_player_snapshot(), [
            {"name": "CurrentPlayer", "xuid": "84"}
        ])

    def test_historical_log_replay_does_not_rebuild_live_player_list(self):
        manager = self._player_manager()
        manager._players_tracking_active = False
        manager.log_id = 0
        manager.logs = []
        manager.log_lock = threading.Lock()
        manager.save_capture_active = False
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "manager.log"
            log_path.write_text(
                "[2026-11-06T10:00:00+00:00] [INFO] Player connected: OldPlayer, xuid: 1\n",
                encoding="utf-8",
            )
            with patch.object(bds, "LOG_FILE", log_path):
                manager._load_saved_log_tail()
        self.assertEqual(manager._online_player_snapshot(), [])
        self.assertEqual(len(manager.logs), 1)

    def test_process_exit_clears_player_session(self):
        class ExitedProcess:
            pid = 123
            def wait(self):
                return 1

        manager = self._player_manager()
        process = ExitedProcess()
        manager.proc = process
        manager.started_at = time.time()
        manager.process_lock = threading.RLock()
        manager._append_log = lambda *_args, **_kwargs: None
        manager._watch_process(process, manager._player_session_generation, None)
        self.assertIsNone(manager.proc)
        self.assertIsNone(manager.started_at)
        self.assertFalse(manager._players_tracking_active)
        self.assertEqual(manager._online_player_snapshot(), [])

    def test_old_process_watcher_cannot_clear_newer_player_session(self):
        class ExitedProcess:
            def wait(self):
                return 0

        manager = self._player_manager()
        old_process = ExitedProcess()
        current_process = object()
        manager.proc = current_process
        manager.started_at = 123.0
        manager.process_lock = threading.RLock()
        manager._player_session_generation = 8
        manager.online_players["currentplayer"] = {"name": "CurrentPlayer", "xuid": "84"}
        manager._append_log = lambda *_args, **_kwargs: None
        manager._watch_process(old_process, 7, None)
        self.assertIs(manager.proc, current_process)
        self.assertEqual(manager.started_at, 123.0)
        self.assertTrue(manager._players_tracking_active)
        self.assertEqual(manager._online_player_snapshot(), [
            {"name": "CurrentPlayer", "xuid": "84"}
        ])

    def test_online_player_snapshot_is_detached_from_live_state(self):
        manager = self._player_manager()
        manager.online_players["player"] = {"name": "Player", "xuid": "1"}
        snapshot = manager._online_player_snapshot()
        snapshot[0]["name"] = "Changed"
        self.assertEqual(manager.online_players["player"]["name"], "Player")

    def _status_manager(self, root, process):
        manager = self._player_manager()
        manager.process_lock = threading.RLock()
        manager.proc = process
        manager.started_at = time.time() if process.poll() is None else None
        manager.root = root
        manager.backup_dir = root / "backups"
        manager.server_version = "1.26.0"
        manager.server_version_source = "test fixture"
        manager.server_version_detected_at = None
        manager._version_signature = None
        manager.auto_backup_hours = 0
        manager.backup_keep = 3
        manager.tunnel_start_with_bds = False
        manager.tunnel_stop_with_bds = False
        manager._executable_signature = lambda: None
        manager._read_props = lambda: {"server-name": "Test server", "level-name": "TestWorld"}
        manager.list_backups = lambda: []
        manager._unmanaged_bds = lambda: []
        return manager

    def test_status_returns_player_list_and_count_from_one_snapshot(self):
        class RunningProcess:
            pid = 123
            @staticmethod
            def poll():
                return None

        with tempfile.TemporaryDirectory() as tmp:
            manager = self._status_manager(Path(tmp), RunningProcess())
            manager.online_players["player"] = {"name": "Player", "xuid": "1"}
            status = manager.status()
        self.assertEqual(status["players"], [{"name": "Player", "xuid": "1"}])
        self.assertEqual(status["player_count"], len(status["players"]))
        self.assertEqual(status["player_count"], 1)

    def test_status_reconciles_exited_process_before_watcher_runs(self):
        class ExitedProcess:
            pid = 123
            @staticmethod
            def poll():
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            manager = self._status_manager(Path(tmp), ExitedProcess())
            manager.online_players["player"] = {"name": "Player", "xuid": "1"}
            status = manager.status()
        self.assertFalse(status["running"])
        self.assertEqual(status["players"], [])
        self.assertEqual(status["player_count"], 0)
        self.assertIsNone(manager.proc)
        self.assertFalse(manager._players_tracking_active)

    def test_directory_picker_returns_selected_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp)
            completed = bds.subprocess.CompletedProcess(["zenity"], 0, stdout=str(selected) + "\n", stderr="")
            with patch.dict(bds.os.environ, {"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}, clear=True):
                with patch.object(bds.shutil, "which", side_effect=lambda name: "/usr/bin/zenity" if name == "zenity" else None):
                    with patch.object(bds.subprocess, "run", return_value=completed) as run:
                        result = bds.choose_directory_dialog(selected, "Choose folder")
            self.assertEqual(result, selected.resolve())
            self.assertIn("--directory", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["timeout"], 180)

    def test_directory_picker_cancel_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = bds.subprocess.CompletedProcess(["kdialog"], 1, stdout="", stderr="")
            with patch.dict(bds.os.environ, {"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}, clear=True):
                with patch.object(bds.shutil, "which", side_effect=lambda name: "/usr/bin/kdialog" if name == "kdialog" else None):
                    with patch.object(bds.subprocess, "run", return_value=completed):
                        result = bds.choose_directory_dialog(Path(tmp), "Choose folder")
            self.assertIsNone(result)

    def test_directory_picker_explains_missing_gui_picker(self):
        with patch.dict(bds.os.environ, {"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}, clear=True):
            with patch.object(bds.shutil, "which", return_value=None):
                with self.assertRaisesRegex(ValueError, "Install zenity, kdialog, or yad"):
                    bds.choose_directory_dialog(Path.home(), "Choose folder")

    def test_property_parser_ignores_comments_and_blank_lines(self):
        parsed = bds.parse_properties("# comment\n\nserver-name=Test World\nallow-cheats=true\n")
        self.assertEqual(parsed, {"server-name": "Test World", "allow-cheats": "true"})

    def test_property_merge_keeps_comments_and_adds_new_keys(self):
        original = "# keep this comment\nserver-name=Old\ncustom-key=old\n"
        merged = bds.merge_properties(original, {"server-name": "New", "custom-key": "keep", "new-key": "42"})
        self.assertIn("# keep this comment", merged)
        self.assertIn("server-name=New", merged)
        self.assertIn("custom-key=keep", merged)
        self.assertIn("new-key=42", merged)

    def test_property_parser_preserves_repeated_nethernet_udp_mappings(self):
        parsed = bds.parse_properties(
            "# public port mappings\n"
            "server-udp-ports=203.0.113.10:48000:48000\n"
            "# second mapping\n"
            "server-udp-ports=203.0.113.10:48001:48001\n"
            "server-port=19132\n"
        )
        self.assertEqual(parsed["server-udp-ports"], "203.0.113.10:48000:48000\n203.0.113.10:48001:48001")
        self.assertEqual(parsed["server-port"], "19132")

    def test_property_merge_rewrites_repeated_udp_mappings_without_collapsing_them(self):
        original = (
            "# preserve header\n"
            "server-udp-ports=old-a\n"
            "# keep this comment too\n"
            "server-udp-ports=old-b\n"
            "server-port=19132\n"
        )
        merged = bds.merge_properties(original, {
            "server-udp-ports": "new-a\nnew-b\nnew-c",
            "server-port": "19133",
        })
        self.assertEqual(merged.count("server-udp-ports="), 3)
        self.assertIn("server-udp-ports=new-a\nserver-udp-ports=new-b\nserver-udp-ports=new-c", merged)
        self.assertIn("# preserve header", merged)
        self.assertIn("# keep this comment too", merged)
        self.assertIn("server-port=19133", merged)

    def test_property_validation_allows_bounded_udp_lines_only_for_udp_property(self):
        manager = bds.Manager.__new__(bds.Manager)
        validated = manager.validate_property_updates({"server-udp-ports": "map-a\nmap-b"})
        self.assertEqual(validated["server-udp-ports"], "map-a\nmap-b")
        with self.assertRaisesRegex(ValueError, "must be a single line"):
            manager.validate_property_updates({"server-name": "two\nlines"})
        with self.assertRaisesRegex(ValueError, "At most 32"):
            manager.validate_property_updates({"server-udp-ports": "\n".join(f"map-{i}" for i in range(33))})

    def test_save_properties_preserves_multiple_udp_rows_and_original_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = "# keep comment\ntransport=nethernet\nserver-udp-ports=old-a\nserver-udp-ports=old-b\nserver-port=19132\n"
            path = root / "server.properties"
            path.write_text(original, encoding="utf-8")
            manager = bds.Manager.__new__(bds.Manager)
            manager.root = root
            manager.proc = None
            result = manager.save_properties({
                "server-udp-ports": "new-a\nnew-b\nnew-c",
                "server-port": "19133",
            })
            saved = path.read_text(encoding="utf-8")
            self.assertTrue(result["ok"])
            self.assertEqual((root / "server.properties.before-bds-omni.bak").read_text(encoding="utf-8"), original)
            self.assertEqual(saved.count("server-udp-ports="), 3)
            self.assertIn("server-udp-ports=new-a\nserver-udp-ports=new-b\nserver-udp-ports=new-c", saved)
            self.assertIn("# keep comment", saved)
            self.assertEqual(bds.parse_properties(saved)["server-udp-ports"], "new-a\nnew-b\nnew-c")

    def test_live_backup_copy_truncates_to_manifest_length(self):
        manager = bds.Manager()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.ldb"
            source.write_bytes(b"abcdefg")
            archive = root / "test.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                manager._copy_exact_to_zip(zf, source, "worlds/test/db/file.ldb", 4)
            with zipfile.ZipFile(archive) as zf:
                self.assertEqual(zf.read("worlds/test/db/file.ldb"), b"abcd")


    def test_http_response_ignores_a_client_that_closed_the_connection(self):
        class DisconnectedWriter:
            def write(self, _data):
                raise BrokenPipeError("client closed the socket")

        handler = bds.Handler.__new__(bds.Handler)
        handler.wfile = DisconnectedWriter()
        handler.close_connection = False
        handler.send_response = lambda *_args, **_kwargs: None
        handler.send_header = lambda *_args, **_kwargs: None
        handler.end_headers = lambda: None

        self.assertFalse(handler._send(200, {"ok": True}))
        self.assertTrue(handler.close_connection)

    def test_http_response_ignores_connection_reset_during_headers(self):
        handler = bds.Handler.__new__(bds.Handler)
        handler.wfile = object()
        handler.close_connection = False
        handler.send_response = lambda *_args, **_kwargs: None
        handler.send_header = lambda *_args, **_kwargs: None
        def reset_during_headers():
            raise ConnectionResetError("browser reset the socket")
        handler.end_headers = reset_during_headers

        self.assertFalse(handler._send(200, "page", "text/html; charset=utf-8"))
        self.assertTrue(handler.close_connection)

    def test_command_index_contains_tickingarea_and_save(self):
        names = {name for group in bds.COMMANDS.values() for name in group}
        self.assertIn("tickingarea", names)
        self.assertIn("save", names)
        self.assertIn("allowlist", names)

    def test_guided_command_forms_are_available_for_common_operations(self):
        ids = {form["id"] for form in bds.COMMAND_FORMS}
        self.assertEqual(len(ids), 27)
        for command in ("gamemode", "give", "effect", "fill", "clone", "clearspawnpoint", "tickingarea", "playsound"):
            if command != "tickingarea":
                self.assertIn(command, ids)

    def test_every_guided_form_can_build_a_valid_default_or_sample_preview(self):
        fallback = {
            "target": "@s", "identifier": "minecraft:stone", "coords": "0 64 0",
            "text": "dodaylightcycle", "textarea": "Test message", "number": "1",
            "select": "", "bool": "true",
        }
        for spec in bds.COMMAND_FORMS:
            values = {}
            for field in spec["fields"]:
                if "default" in field:
                    values[field["id"]] = field["default"]
                elif field.get("required"):
                    values[field["id"]] = fallback.get(field["type"], "test")
            with self.subTest(form=spec["id"]):
                result = bds.build_guided_command(spec["id"], values)
                self.assertTrue(result["command"])

    def test_guided_gamemode_preview_does_not_execute_command(self):
        result = bds.build_guided_command("gamemode", {"mode": "creative", "target": "@a"})
        self.assertEqual(result["command"], "gamemode creative @a")
        self.assertFalse(result["confirm"])

    def test_guided_message_is_quoted_and_newlines_are_rejected(self):
        result = bds.build_guided_command("say", {"message": "Hello everyone"})
        self.assertEqual(result["command"], 'say "Hello everyone"')
        with self.assertRaisesRegex(ValueError, "printable characters"):
            bds.build_guided_command("say", {"message": "hello\nstop"})

    def test_guided_fill_preview_is_marked_for_confirmation(self):
        result = bds.build_guided_command("fill", {
            "from": "~-2 ~ ~-2", "to": "~2 ~3 ~2", "block": "minecraft:glass", "mode": "hollow"
        })
        self.assertEqual(result["command"], "fill ~-2 ~ ~-2 ~2 ~3 ~2 minecraft:glass hollow")
        self.assertTrue(result["confirm"])

    def test_guided_target_rejects_command_injection(self):
        with self.assertRaisesRegex(ValueError, "player name or target selector"):
            bds.build_guided_command("kick", {"target": "@a;stop"})

    def test_guided_coordinates_require_exactly_three_safe_tokens(self):
        with self.assertRaisesRegex(ValueError, "exactly three coordinates"):
            bds.build_guided_command("teleport", {"target": "@s", "position": "0 64 0; stop", "check": "false"})

    def test_guided_xp_uses_bedrock_points_syntax_not_java_level_suffix(self):
        result = bds.build_guided_command("xp", {"mode": "remove", "amount": "5", "target": "@s"})
        self.assertEqual(result["command"], "xp -5 @s")
        self.assertNotIn("L", result["command"])

    def test_guided_clear_requires_item_when_max_count_is_set(self):
        with self.assertRaisesRegex(ValueError, "Choose an item"):
            bds.build_guided_command("clear", {"target": "@s", "item": "", "count": "3"})

    def test_guided_clear_places_bedrock_data_placeholder_before_max_count(self):
        result = bds.build_guided_command("clear", {"target": "@s", "item": "minecraft:dirt", "count": "3"})
        self.assertEqual(result["command"], "clear @s minecraft:dirt -1 3")

    def test_guided_clone_marks_move_operation_for_confirmation(self):
        result = bds.build_guided_command("clone", {
            "begin": "0 64 0", "end": "10 70 10", "destination": "20 64 0",
            "mask": "masked", "clone_mode": "move"
        })
        self.assertEqual(result["command"], "clone 0 64 0 10 70 10 20 64 0 masked move")
        self.assertTrue(result["confirm"])

    def test_guided_unknown_form_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown guided command"):
            bds.build_guided_command("unknown", {})

    def _bare_manager(self, root: Path, backup_dir: Path):
        manager = bds.Manager.__new__(bds.Manager)
        manager.root = root
        manager.backup_dir = backup_dir
        manager.proc = None
        manager._unmanaged_bds = lambda: []
        manager._append_log = lambda *args, **kwargs: None
        return manager

    def _write_world_backup(self, archive: Path, world_name="TestWorld"):
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(".bds-omni-backup.json", __import__("json").dumps({
                "format": 1, "mode": "world", "world_name": world_name, "live_snapshot": False
            }))
            zf.writestr("server.properties", "level-name=TestWorld\n")
            zf.writestr("allowlist.json", "[]\n")
            zf.writestr(f"worlds/{world_name}/db/level.dat", b"new-world-data")

    def test_bds_version_parser_only_accepts_version_banner(self):
        self.assertEqual(bds.parse_bds_version("[INFO] Version: 1.26.70.20"), "1.26.70.20")
        self.assertEqual(bds.parse_bds_version("Bedrock Dedicated Server version: 1.26.52.3"), "1.26.52.3")
        self.assertIsNone(bds.parse_bds_version("[INFO] Protocol version: 123"))
        self.assertIsNone(bds.parse_bds_version("old log line without a version"))

    def test_live_process_banner_updates_server_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "bedrock_server"
            exe.write_text("#!/bin/sh\necho '[INFO] Version: 1.26.70.20'\nwhile IFS= read -r line; do [ \"$line\" = stop ] && exit 0; done\n")
            exe.chmod(0o755)
            (root / "server.properties").write_text("level-name=TestWorld\n")
            manager = bds.Manager.__new__(bds.Manager)
            manager.root = root
            manager.config = {"bds_version_cache": {}}
            manager.server_version = "Unknown"
            manager.server_version_source = "Not detected"
            manager.server_version_detected_at = None
            manager._version_signature = None
            manager._version_captured_this_run = False
            manager.proc = None
            manager.started_at = None
            manager.process_lock = threading.RLock()
            manager.players_lock = threading.RLock()
            manager.online_players = {}
            manager._player_session_generation = 0
            manager._players_tracking_active = False
            manager._unmanaged_bds = lambda: []
            manager._persist_config = lambda: None
            manager._append_log = lambda *args, **kwargs: None
            result = manager.start()
            self.assertTrue(result["ok"])
            deadline = time.monotonic() + 3
            while manager.server_version == "Unknown" and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(manager.server_version, "1.26.70.20")
            self.assertIn("startup log", manager.server_version_source)
            manager.stop(timeout=3)

    def test_version_cache_is_invalidated_when_executable_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "bedrock_server"
            exe.write_bytes(b"BDS build one")
            manager = bds.Manager.__new__(bds.Manager)
            manager.root = root
            manager.config = {"bds_version_cache": {}}
            signature = manager._executable_signature()
            manager.config["bds_version_cache"][str(exe.resolve())] = {
                "signature": signature,
                "version": "1.26.52.3",
                "detected_at": "2026-11-06T12:00:00+00:00",
            }
            manager._restore_cached_server_version()
            self.assertEqual(manager.server_version, "1.26.52.3")
            self.assertIn("Cached", manager.server_version_source)
            exe.write_bytes(b"BDS build two, changed contents")
            manager._restore_cached_server_version()
            self.assertEqual(manager.server_version, "Unknown")

    def test_backup_verification_checks_crc_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            backup_dir.mkdir()
            archive = backup_dir / "BDS-test-world.zip"
            self._write_world_backup(archive)
            manager = self._bare_manager(root, backup_dir)
            result = manager.verify_backup(archive.name)
            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "world")
            self.assertEqual(result["world_name"], "TestWorld")
            self.assertEqual(result["file_count"], 3)

    def test_restore_replaces_world_and_restores_shared_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            backup_dir.mkdir()
            archive = backup_dir / "BDS-test-world.zip"
            self._write_world_backup(archive)
            (root / "server.properties").write_text("level-name=OldWorld\n")
            (root / "allowlist.json").write_text("[\n  {\"name\":\"old\"}\n]\n")
            (root / "unrelated.txt").write_text("keep me")
            old_db = root / "worlds" / "TestWorld" / "db" / "level.dat"
            old_db.parent.mkdir(parents=True)
            old_db.write_bytes(b"old-world-data")
            manager = self._bare_manager(root, backup_dir)
            result = manager.restore(archive.name)
            self.assertTrue(result["ok"])
            self.assertEqual(old_db.read_bytes(), b"new-world-data")
            self.assertEqual((root / "server.properties").read_text(), "level-name=TestWorld\n")
            self.assertEqual((root / "allowlist.json").read_text(), "[]\n")
            self.assertEqual((root / "unrelated.txt").read_text(), "keep me")
            self.assertFalse(any(root.glob(".bds-omni-restore-*")))

    def test_restore_rolls_back_world_and_files_on_apply_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            backup_dir.mkdir()
            archive = backup_dir / "BDS-test-world.zip"
            self._write_world_backup(archive)
            (root / "server.properties").write_text("level-name=OldWorld\n")
            (root / "allowlist.json").write_text("old-allowlist\n")
            world_file = root / "worlds" / "TestWorld" / "db" / "level.dat"
            world_file.parent.mkdir(parents=True)
            world_file.write_bytes(b"old-world-data")
            manager = self._bare_manager(root, backup_dir)
            real_replace = bds.os.replace

            def fail_on_allowlist(src, dst):
                if Path(dst) == root / "allowlist.json" and "payload" in str(src):
                    raise OSError("simulated write failure")
                return real_replace(src, dst)

            with patch.object(bds.os, "replace", side_effect=fail_on_allowlist):
                with self.assertRaises(OSError):
                    manager.restore(archive.name)
            self.assertEqual(world_file.read_bytes(), b"old-world-data")
            self.assertEqual((root / "server.properties").read_text(), "level-name=OldWorld\n")
            self.assertEqual((root / "allowlist.json").read_text(), "old-allowlist\n")
            self.assertFalse(any(root.glob(".bds-omni-restore-*")))


class NethernetTunnelTests(unittest.TestCase):
    def _manager(self):
        manager = bds.Manager.__new__(bds.Manager)
        manager.tunnel_start_with_bds = False
        manager.tunnel_stop_with_bds = False
        manager.config = {}
        manager._read_props = lambda: {
            "transport": "nethernet",
            "server-port": "19132",
            "server-udp-ports": "203.0.113.10:48000-48009:48000-48009",
            "max-players": "10",
        }
        manager._append_log = lambda *_args, **_kwargs: None
        return manager

    def _service_properties(self, binary="/usr/bin/nethernet-client", active="inactive", extra=""):
        return {
            "LoadState": "loaded",
            "ActiveState": active,
            "MainPID": "4321" if active == "active" else "0",
            "WorkingDirectory": str(Path.home()),
            "FragmentPath": "/usr/lib/systemd/user/bedrocknode-tunnel.service",
            "ExecStart": f"{{ path={binary} ; argv[]={binary} {extra}; }}",
        }

    def test_systemd_user_dropin_respects_absolute_xdg_config_home(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            custom = Path(tmp) / "custom-config"
            home.mkdir()
            custom.mkdir()
            with patch.object(bds, "HOME", home), patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": str(custom)}):
                self.assertEqual(
                    tunnel._override_path(),
                    custom / "systemd/user/bedrocknode-tunnel.service.d/90-bds-omni-manager.conf",
                )
            with patch.object(bds, "HOME", home), patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": "relative-config"}):
                self.assertEqual(tunnel._override_path(), home / tunnel.OVERRIDE_RELATIVE)

    def test_client_parameter_defaults_match_upstream_help(self):
        self.assertEqual(bds.NethernetTunnelManager.validate_client_args({}), {
            "web": "https://bedrocknode.net",
            "control": "51.222.142.179:7000",
            "local_http": "127.0.0.1:19132",
            "local_udp_ip": "",
            "local_udp_port": 47334,
        })

    def test_client_parameter_validation_accepts_custom_bedrock_node_arguments(self):
        args = bds.NethernetTunnelManager.validate_client_args({
            "web": "https://my-world.bedrocknode.net/",
            "control": "203.0.113.20:7000",
            "local_http": "127.0.0.1:19132",
            "local_udp_ip": "192.168.1.50",
            "local_udp_port": "48000",
        })
        self.assertEqual(args["web"], "https://my-world.bedrocknode.net/")
        self.assertEqual(args["control"], "203.0.113.20:7000")
        self.assertEqual(args["local_udp_ip"], "192.168.1.50")
        self.assertEqual(args["local_udp_port"], 48000)

    def test_client_parameter_validation_requires_bracketed_ipv6_and_valid_ranges(self):
        with self.assertRaisesRegex(ValueError, r"\[IPv6\]:port"):
            bds.NethernetTunnelManager.validate_client_args({"local_http": "::1:19132"})
        valid = bds.NethernetTunnelManager.validate_client_args({"local_http": "[::1]:19132", "local_udp_ip": "2001:db8::5"})
        self.assertEqual(valid["local_http"], "[::1]:19132")
        self.assertEqual(valid["local_udp_ip"], "2001:db8::5")
        for payload in (
            {"web": "http://remote.example"},
            {"web": "https://user:pass@example.com"},
            {"web": "https://<subdomain>.bedrocknode.net/"},
            {"control": "remote.example:70000"},
            {"local_http": "127.0.0.1:0"},
            {"local_udp_ip": "localhost"},
            {"local_udp_port": 0},
            {"local_udp_port": 65536},
            {"local_udp_port": True},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                bds.NethernetTunnelManager.validate_client_args(payload)

    def test_client_settings_write_allowed_flags_to_private_user_dropin(self):
        manager = self._manager()
        manager._persist_config = lambda: None
        log_lines = []
        manager._append_log = log_lines.append
        tunnel = bds.NethernetTunnelManager(manager)
        props = self._service_properties()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            token_path = home / ".config/bds-omni-manager/nethernet-client.env"
            payload = {
                "web": "https://my-world.bedrocknode.net/",
                "control": "203.0.113.20:7000",
                "local_http": "127.0.0.1:19133",
                "local_udp_ip": "192.168.1.50",
                "local_udp_port": 48000,
                "token": "PRIVATE_TOKEN_VALUE",
            }
            with patch.object(bds, "HOME", home), patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}), patch.object(bds, "TUNNEL_CLIENT_ENV_FILE", token_path):
                with patch.object(tunnel, "_unit_properties", return_value=(props, None)):
                    with patch.object(tunnel, "_other_clients", return_value=[]):
                        with patch.object(tunnel, "_service_binary", return_value="/usr/bin/nethernet-client"):
                            with patch.object(tunnel, "_run", return_value=(0, "", "")) as run:
                                result = tunnel.set_client_settings(payload)
                override = home / tunnel.OVERRIDE_RELATIVE
                text = override.read_text(encoding="utf-8")
                self.assertTrue(result["ok"])
                self.assertIn("-web=https://my-world.bedrocknode.net/", text)
                self.assertIn("-control=203.0.113.20:7000", text)
                self.assertIn("-local-http=127.0.0.1:19133", text)
                self.assertIn("-local-udp-ip=192.168.1.50", text)
                self.assertIn("-local-udp-port=48000", text)
                self.assertIn("-token=${BDS_OMNI_NETHERNET_TOKEN}", text)
                self.assertNotIn("PRIVATE_TOKEN_VALUE", text)
                self.assertEqual(token_path.read_text(encoding="ascii"), 'BDS_OMNI_NETHERNET_TOKEN="PRIVATE_TOKEN_VALUE"\n')
                self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(token_path.parent.stat().st_mode & 0o777, 0o700)
                self.assertNotIn("PRIVATE_TOKEN_VALUE", repr(result))
                self.assertNotIn("PRIVATE_TOKEN_VALUE", repr(manager.config))
                self.assertNotIn("PRIVATE_TOKEN_VALUE", "\n".join(log_lines))
                self.assertNotIn("/usr/lib/systemd/user", str(override))
                self.assertEqual(run.call_args.args[0], ["systemctl", "--user", "daemon-reload"])

    def test_client_settings_preserve_existing_token_and_can_explicitly_clear_it(self):
        manager = self._manager()
        manager._persist_config = lambda: None
        tunnel = bds.NethernetTunnelManager(manager)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            token_path = home / ".config/bds-omni-manager/nethernet-client.env"
            with patch.object(bds, "HOME", home), patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}), patch.object(bds, "TUNNEL_CLIENT_ENV_FILE", token_path):
                token_path.parent.mkdir(parents=True)
                token_path.write_text('BDS_OMNI_NETHERNET_TOKEN="KEEP_ME"\n', encoding="ascii")
                props = self._service_properties()
                with patch.object(tunnel, "_unit_properties", return_value=(props, None)), patch.object(tunnel, "_other_clients", return_value=[]), patch.object(tunnel, "_service_binary", return_value="/usr/bin/nethernet-client"), patch.object(tunnel, "_run", return_value=(0, "", "")):
                    tunnel.set_client_settings({})
                    self.assertIn("KEEP_ME", token_path.read_text(encoding="ascii"))
                    tunnel.set_client_settings({"clear_token": True})
                self.assertFalse(token_path.exists())
                override = (home / tunnel.OVERRIDE_RELATIVE).read_text(encoding="utf-8")
                self.assertNotIn("-token=", override)

    def test_client_settings_migrate_legacy_token_without_exposing_it(self):
        manager = self._manager()
        manager._persist_config = lambda: None
        manager._append_log = lambda *_args: None
        tunnel = bds.NethernetTunnelManager(manager)
        secret = "LEGACY_SECRET_123"
        props = self._service_properties(extra=f"-token={secret}")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            token_path = home / ".config/bds-omni-manager/nethernet-client.env"
            with patch.object(bds, "HOME", home), patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}), patch.object(bds, "TUNNEL_CLIENT_ENV_FILE", token_path):
                with patch.object(tunnel, "_unit_properties", return_value=(props, None)), patch.object(tunnel, "_other_clients", return_value=[]), patch.object(tunnel, "_service_binary", return_value="/usr/bin/nethernet-client"), patch.object(tunnel, "_run", return_value=(0, "", "")):
                    result = tunnel.set_client_settings({})
                override = (home / tunnel.OVERRIDE_RELATIVE).read_text(encoding="utf-8")
                self.assertIn(secret, token_path.read_text(encoding="ascii"))
                self.assertNotIn(secret, override)
                self.assertNotIn(secret, repr(result))
                self.assertNotIn(secret, repr(manager.config))

    def test_active_client_settings_restart_service_after_dropin_reload(self):
        manager = self._manager()
        manager._persist_config = lambda: None
        tunnel = bds.NethernetTunnelManager(manager)
        props = self._service_properties(active="active")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            with patch.object(bds, "HOME", home), patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}), patch.object(bds, "TUNNEL_CLIENT_ENV_FILE", home / ".config/bds-omni-manager/nethernet-client.env"):
                with patch.object(tunnel, "_unit_properties", return_value=(props, None)), patch.object(tunnel, "_other_clients", return_value=[]), patch.object(tunnel, "_service_binary", return_value="/usr/bin/nethernet-client"), patch.object(tunnel, "_run", return_value=(0, "", "")) as run:
                    result = tunnel.set_client_settings({"control": "node.example:7000"})
                self.assertIn("restarted", result["message"])
                self.assertEqual([call.args[0] for call in run.call_args_list], [
                    ["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "restart", tunnel.UNIT],
                ])

    def test_reset_token_runs_only_explicit_flag_and_restarts_previous_active_service(self):
        manager = self._manager()
        manager.config = {"nethernet_client_args": dict(bds.NethernetTunnelManager.CLIENT_DEFAULTS)}
        manager._append_log = lambda *_args: None
        tunnel = bds.NethernetTunnelManager(manager)
        props = self._service_properties(active="active")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            token_path = home / ".config/bds-omni-manager/nethernet-client.env"
            token_path.parent.mkdir(parents=True)
            token_path.write_text('BDS_OMNI_NETHERNET_TOKEN="SECRET"\n', encoding="ascii")
            with patch.object(bds, "HOME", home), patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}), patch.object(bds, "TUNNEL_CLIENT_ENV_FILE", token_path):
                with patch.object(tunnel, "_unit_properties", return_value=(props, None)), patch.object(tunnel, "_other_clients", return_value=[]), patch.object(tunnel, "_service_binary", return_value="/usr/bin/nethernet-client"), patch.object(tunnel, "_run", return_value=(0, "", "")) as systemd_run:
                    completed = bds.subprocess.CompletedProcess(["/usr/bin/nethernet-client", "-reset-token"], 0, stdout="claim token secret", stderr="")
                    with patch.object(bds.subprocess, "run", return_value=completed) as reset_run:
                        result = tunnel.reset_token()
                self.assertTrue(result["ok"])
                self.assertEqual(reset_run.call_args.args[0], ["/usr/bin/nethernet-client", "-reset-token"])
                self.assertFalse(token_path.exists())
                self.assertNotIn("claim token secret", repr(result))
                systemd_args = [call.args[0] for call in systemd_run.call_args_list]
                self.assertEqual(systemd_args[0], ["systemctl", "--user", "stop", tunnel.UNIT])
                self.assertEqual(systemd_args[1], ["systemctl", "--user", "daemon-reload"])
                self.assertEqual(systemd_args[2], ["systemctl", "--user", "start", tunnel.UNIT])
                override = (home / tunnel.OVERRIDE_RELATIVE).read_text(encoding="utf-8")
                self.assertNotIn("-token=", override)

    def test_reset_token_restarts_active_service_even_if_dropin_update_fails(self):
        manager = self._manager()
        manager.config = {"nethernet_client_args": dict(bds.NethernetTunnelManager.CLIENT_DEFAULTS)}
        manager._append_log = lambda *_args: None
        tunnel = bds.NethernetTunnelManager(manager)
        props = self._service_properties(active="active")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            token_path = home / ".config/bds-omni-manager/nethernet-client.env"
            token_path.parent.mkdir(parents=True)
            token_path.write_text('BDS_OMNI_NETHERNET_TOKEN="SECRET"\n', encoding="ascii")
            with patch.object(bds, "HOME", home), patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}), patch.object(bds, "TUNNEL_CLIENT_ENV_FILE", token_path):
                with patch.object(tunnel, "_unit_properties", return_value=(props, None)), patch.object(tunnel, "_other_clients", return_value=[]), patch.object(tunnel, "_service_binary", return_value="/usr/bin/nethernet-client"), patch.object(tunnel, "_run", return_value=(0, "", "")) as systemd_run:
                    completed = bds.subprocess.CompletedProcess(["/usr/bin/nethernet-client", "-reset-token"], 0, stdout="", stderr="")
                    with patch.object(bds.subprocess, "run", return_value=completed), patch.object(tunnel, "_private_write", side_effect=OSError("disk full")):
                        with self.assertRaisesRegex(ValueError, "could not finish updating"):
                            tunnel.reset_token()
                systemd_args = [call.args[0] for call in systemd_run.call_args_list]
                self.assertEqual(systemd_args[-1], ["systemctl", "--user", "start", tunnel.UNIT])


    def test_dashboard_property_import_parses_network_settings_without_guessing_values(self):
        snippet = "transport=nethernet\nserver-port=19132\nserver-portv6=19133\nserver-udp-ports=20001-20004\n"
        parsed = bds.parse_bedrock_node_property_lines(snippet)
        self.assertEqual(parsed, {
            "transport": "nethernet",
            "server-port": "19132",
            "server-portv6": "19133",
            "server-udp-ports": "20001-20004",
        })

    def test_dashboard_property_import_rejects_unrelated_duplicate_and_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "not a recognized network property"):
            bds.parse_bedrock_node_property_lines("level-name=MyWorld\n")
        with self.assertRaisesRegex(ValueError, "Duplicate property"):
            bds.parse_bedrock_node_property_lines("server-port=19132\nserver-port=19133\n")
        with self.assertRaisesRegex(ValueError, "requires transport=nethernet"):
            bds.parse_bedrock_node_property_lines("transport=raknet\n")
        with self.assertRaisesRegex(ValueError, "must use key=value"):
            bds.parse_bedrock_node_property_lines("not a property\n")

    def test_dashboard_property_import_accepts_empty_or_literal_server_ip(self):
        parsed = bds.parse_bedrock_node_property_lines(
            "transport=nethernet\nserver-ip=\nserver-port=19132\nserver-udp-ports=48000-48009\n"
        )
        self.assertEqual(parsed["server-ip"], "")
        parsed = bds.parse_bedrock_node_property_lines("server-ip=192.0.2.10\n")
        self.assertEqual(parsed["server-ip"], "192.0.2.10")
        parsed = bds.parse_bedrock_node_property_lines("server-ip=2001:db8::10\n")
        self.assertEqual(parsed["server-ip"], "2001:db8::10")
        with self.assertRaisesRegex(ValueError, "IPv4/IPv6 literal"):
            bds.parse_bedrock_node_property_lines("server-ip=example.com\n")

    def test_dashboard_import_keeps_repeated_udp_mapping_lines(self):
        parsed = bds.parse_bedrock_node_property_lines(
            "transport=nethernet\n"
            "server-port=19132\n"
            "server-udp-ports=203.0.113.10:48000:48000\n"
            "server-udp-ports=203.0.113.10:48001:48001\n"
        )
        self.assertEqual(
            parsed["server-udp-ports"],
            "203.0.113.10:48000:48000\n203.0.113.10:48001:48001",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate property"):
            bds.parse_bedrock_node_property_lines("server-port=19132\nserver-port=19133\n")

    def test_dashboard_property_import_validates_tcp_ports(self):
        for value in ("0", "65536", "not-a-port"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "server-port must"):
                    bds.parse_bedrock_node_property_lines(f"server-port={value}\n")

    def test_target_parser_supports_ipv4_hostnames_and_ipv6(self):
        parse = bds.NethernetTunnelManager._split_host_port
        self.assertEqual(parse("127.0.0.1:19132"), ("127.0.0.1", 19132))
        self.assertEqual(parse("localhost:19132"), ("localhost", 19132))
        self.assertEqual(parse("[::1]:19132"), ("::1", 19132))
        self.assertIsNone(parse("127.0.0.1:notaport"))
        self.assertIsNone(parse("127.0.0.1:70000"))

    def test_extract_flag_reads_only_explicit_safe_client_flags(self):
        extract = bds.NethernetTunnelManager._extract_flag
        command = "{ path=/usr/bin/nethernet-client ; argv[]=/usr/bin/nethernet-client -local-http=127.0.0.1:19132 -local-udp-port=47334 -token=SECRET_VALUE ; }"
        self.assertEqual(extract(command, "local-http", "fallback"), "127.0.0.1:19132")
        self.assertEqual(extract(command, "local-udp-port", "1"), "47334")
        self.assertEqual(extract(command, "local-udp-ip", "Auto-detected"), "Auto-detected")

    def test_tunnel_state_reports_authenticated_connection_without_exposing_token(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        show = """LoadState=loaded
ActiveState=active
SubState=running
UnitFileState=enabled
FragmentPath=/usr/lib/systemd/user/bedrocknode-tunnel.service
MainPID=4321
ActiveEnterTimestamp=Fri 2026-11-06 19:00:00 IST
InvocationID=0123456789abcdef0123456789abcdef
WorkingDirectory=/home/testuser
Result=success
NRestarts=1
ExecStart={ path=/usr/bin/nethernet-client ; argv[]=/usr/bin/nethernet-client -local-http=127.0.0.1:19132 -local-udp-port=47334 -token=SECRET_VALUE ; }
"""
        completed = lambda args, **kwargs: bds.subprocess.CompletedProcess(
            args, 0,
            stdout=("1.0\n" if "-version" in args else show if "show" in args else "2026-11-06 INFO Authenticated tunnel established!\n"),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            token_dir = Path(tmp) / "bedrocknode-tunnel"
            token_dir.mkdir()
            token_file = token_dir / "bedrocknode-token.txt"
            token_file.write_text("SECRET_VALUE")
            token_file.chmod(0o600)
            with patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
                with patch.object(bds.shutil, "which", side_effect=lambda name: {
                    "systemctl": "/usr/bin/systemctl",
                    "journalctl": "/usr/bin/journalctl",
                    "nethernet-client": "/usr/bin/nethernet-client",
                }.get(Path(name).name)):
                    with patch.object(bds.subprocess, "run", side_effect=completed):
                        with patch.object(bds.socket, "create_connection") as connect:
                            cm = MagicMock()
                            connect.return_value.__enter__.return_value = cm
                            state = tunnel.state()
        self.assertEqual(state["health"], "connected")
        self.assertTrue(state["invocation_logs_scoped"])
        self.assertTrue(state["enabled"])
        self.assertTrue(state["token"]["configured"])
        self.assertTrue(state["token"]["permissions_ok"])
        self.assertEqual(state["target"]["tcp"], "127.0.0.1:19132")
        self.assertTrue(state["target"]["tcp_reachable"])
        self.assertEqual(state["client"]["version"], "1.0")
        self.assertNotIn("SECRET_VALUE", repr(state))

    def test_tunnel_state_marks_missing_unit_without_claiming_connected(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with patch.object(tunnel, "_unit_properties", return_value=({"LoadState": "not-found"}, None)):
            with patch.object(tunnel, "_client_info", return_value={"installed": True, "path": "/usr/bin/nethernet-client", "version": "1.0"}):
                with patch.object(tunnel, "_journal", return_value=[]):
                    state = tunnel.state()
        self.assertFalse(state["service_installed"])
        self.assertFalse(state["active"])
        self.assertEqual(state["health"], "not_installed")
        self.assertTrue(any(x["title"] == "User service not installed" for x in state["diagnostics"]))


    def test_journal_is_scoped_to_a_valid_systemd_invocation(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with patch.object(bds.shutil, "which", return_value="/usr/bin/journalctl"):
            with patch.object(tunnel, "_run", return_value=(0, "line one\nline two\n", "")) as run:
                lines = tunnel._journal(25, "0123456789abcdef0123456789abcdef")
        self.assertEqual(lines, ["line one", "line two"])
        args = run.call_args.args[0]
        self.assertIn("_SYSTEMD_INVOCATION_ID=0123456789abcdef0123456789abcdef", args)

    def test_invalid_invocation_id_is_never_used_as_a_journal_match(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with patch.object(bds.shutil, "which", return_value="/usr/bin/journalctl"):
            with patch.object(tunnel, "_run", return_value=(0, "old log\n", "")) as run:
                lines = tunnel._journal(5, "not-a-systemd-invocation")
        self.assertEqual(lines, ["old log"])
        self.assertFalse(any(str(arg).startswith("_SYSTEMD_INVOCATION_ID=") for arg in run.call_args.args[0]))

    def test_active_service_without_current_invocation_id_never_claims_connected_from_old_logs(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        props = {"LoadState": "loaded", "ActiveState": "active", "ExecStart": "", "InvocationID": ""}
        with patch.object(tunnel, "_unit_properties", return_value=(props, None)):
            with patch.object(tunnel, "_client_info", return_value={"installed": True, "path": "/usr/bin/nethernet-client", "version": "1.0"}):
                with patch.object(tunnel, "_journal") as journal:
                    with patch.object(tunnel, "_probe_tcp", return_value=None):
                        state = tunnel.state()
        self.assertEqual(state["health"], "connecting")
        self.assertFalse(state["invocation_logs_scoped"])
        journal.assert_not_called()
        self.assertTrue(any(x["title"] == "Current-session log verification unavailable" for x in state["diagnostics"]))

    def test_empty_xdg_config_home_falls_back_to_home_config_dir(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with tempfile.TemporaryDirectory() as tmp:
            token_dir = Path(tmp) / ".config" / "bedrocknode-tunnel"
            token_dir.mkdir(parents=True)
            token_file = token_dir / "bedrocknode-token.txt"
            token_file.write_text("TOKEN_VALUE")
            token_file.chmod(0o600)
            props = {"LoadState": "not-found"}
            with patch.object(bds, "HOME", Path(tmp)):
                with patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": ""}, clear=False):
                    with patch.object(tunnel, "_unit_properties", return_value=(props, None)):
                        with patch.object(tunnel, "_client_info", return_value={"installed": False, "path": "", "version": "Not installed"}):
                            with patch.object(tunnel, "_probe_tcp", return_value=None):
                                state = tunnel.state()
        self.assertTrue(state["token"]["configured"])
        self.assertTrue(state["token"]["permissions_ok"])

    def test_home_fallback_token_is_detected_when_service_working_directory_is_unset(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "bedrocknode-token.txt"
            token_file.write_text("TOKEN_VALUE")
            token_file.chmod(0o600)
            with patch.object(bds, "HOME", Path(tmp)):
                with patch.dict(bds.os.environ, {"XDG_CONFIG_HOME": ""}, clear=False):
                    with patch.object(tunnel, "_unit_properties", return_value=({"LoadState": "not-found", "WorkingDirectory": ""}, None)):
                        with patch.object(tunnel, "_client_info", return_value={"installed": False, "path": "", "version": "Not installed"}):
                            with patch.object(tunnel, "_probe_tcp", return_value=None):
                                state = tunnel.state()
        self.assertTrue(state["token"]["configured"])
        self.assertTrue(state["token"]["permissions_ok"])

    def test_invalid_auth_event_is_classified_as_claim_required(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        props = {"LoadState": "loaded", "ActiveState": "active", "InvocationID": "0123456789abcdef0123456789abcdef", "ExecStart": ""}
        with patch.object(tunnel, "_unit_properties", return_value=(props, None)):
            with patch.object(tunnel, "_client_info", return_value={"installed": True, "path": "/usr/bin/nethernet-client", "version": "1.0"}):
                with patch.object(tunnel, "_journal", return_value=["[Tunnel Client] Auth error (invalid token) - deleting saved token and re-claiming..."]):
                    with patch.object(tunnel, "_probe_tcp", return_value=None):
                        state = tunnel.state()
        self.assertEqual(state["health"], "claim_required")

    def test_service_executable_path_is_used_for_client_version_when_valid(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "nethernet-client"
            binary.write_text("#!/bin/sh\necho 9.9.9\n")
            binary.chmod(0o755)
            exec_start = f"{{ path={binary} ; argv[]={binary} -local-http=127.0.0.1:19132 ; }}"
            with patch.object(bds.shutil, "which", side_effect=lambda name: "/usr/bin/systemctl" if name == "systemctl" else None):
                with patch.object(tunnel, "_run", return_value=(0, "9.9.9\n", "")) as run:
                    info = tunnel._client_info(exec_start)
        self.assertTrue(info["installed"])
        self.assertEqual(info["version"], "9.9.9")
        self.assertEqual(info["version_source"], "service-executable")
        self.assertEqual(run.call_args.args[0], [str(binary), "-version"])

    def test_exec_path_rejects_wrappers_and_non_client_binary_names(self):
        extract = bds.NethernetTunnelManager._extract_exec_path
        self.assertIsNone(extract("{ path=/usr/bin/bash ; argv[]=/usr/bin/bash -c nethernet-client ; }"))

    def test_other_client_scan_excludes_service_pid_and_never_returns_arguments(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            for pid, argv0 in ((123, "/usr/bin/nethernet-client"), (456, "/opt/bin/nethernet-client")):
                proc_dir = proc_root / str(pid)
                proc_dir.mkdir()
                (proc_dir / "cmdline").write_bytes((argv0 + "\x00-token\x00VERY_SECRET\x00").encode())
                (proc_dir / "comm").write_text("nethernet-clien\n")
            with patch.object(tunnel, "PROC_ROOT", proc_root):
                found = tunnel._other_clients("123")
        self.assertEqual(found, [{"pid": 456, "process": "nethernet-client (outside systemd unit)"}])
        self.assertNotIn("VERY_SECRET", repr(found))

    def test_start_and_restart_are_blocked_when_foreground_client_is_running(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with tempfile.TemporaryDirectory() as tmp:
            proc_dir = Path(tmp) / "789"
            proc_dir.mkdir()
            (proc_dir / "cmdline").write_bytes(b"/usr/bin/nethernet-client\x00")
            (proc_dir / "comm").write_text("nethernet-clien\n")
            with patch.object(tunnel, "PROC_ROOT", Path(tmp)):
                with patch.object(tunnel, "_unit_properties", return_value=({"LoadState": "loaded", "MainPID": "123"}, None)):
                    with patch.object(tunnel, "_run") as run:
                        with self.assertRaisesRegex(ValueError, "foreground nethernet-client"):
                            tunnel.action("start")
                        with self.assertRaisesRegex(ValueError, "foreground nethernet-client"):
                            tunnel.action("restart")
        run.assert_not_called()

    def test_tunnel_actions_use_fixed_systemctl_arguments_and_allowlist(self):
        tunnel = bds.NethernetTunnelManager(self._manager())
        with patch.object(tunnel, "_unit_properties", return_value=({"LoadState": "loaded"}, None)):
            with patch.object(tunnel, "_run", return_value=(0, "", "")) as run:
                result = tunnel.action("restart")
        self.assertTrue(result["ok"])
        self.assertEqual(run.call_args.args[0], ["systemctl", "--user", "restart", "bedrocknode-tunnel.service"])
        with self.assertRaisesRegex(ValueError, "Unsupported tunnel action"):
            tunnel.action("restart; rm -rf /")

    def test_tunnel_lifecycle_settings_require_booleans_and_persist(self):
        manager = self._manager()
        manager._persist_config = lambda: None
        result = manager.set_tunnel_settings({"start_with_bds": True, "stop_with_bds": False})
        self.assertTrue(result["ok"])
        self.assertTrue(manager.tunnel_start_with_bds)
        self.assertFalse(manager.tunnel_stop_with_bds)
        with self.assertRaisesRegex(ValueError, "true or false"):
            manager.set_tunnel_settings({"start_with_bds": "yes", "stop_with_bds": False})


class TunnelHttpIntegrationTests(unittest.TestCase):
    class FakeManager:
        def __init__(self):
            self.proc = None
            self.tunnel_start_with_bds = False
            self.tunnel_stop_with_bds = False
            self.actions = []
            self.logs = []

        def _append_log(self, line):
            self.logs.append(line)

        def start(self):
            return {"ok": True, "message": "BDS start requested."}

        def stop(self):
            return {"ok": True, "message": "BDS stop requested."}

        def restart(self):
            return {"ok": True, "message": "BDS restart requested."}

        def set_tunnel_settings(self, data):
            self.tunnel_start_with_bds = data.get("start_with_bds", self.tunnel_start_with_bds)
            self.tunnel_stop_with_bds = data.get("stop_with_bds", self.tunnel_stop_with_bds)
            return {"ok": True, "message": "Settings saved."}

        def _read_props(self):
            return {"transport": "raknet", "server-port": "19132", "server-udp-ports": "old-range"}

        def validate_property_updates(self, updates):
            for key, value in updates.items():
                if key in {"server-port", "server-portv6"} and not 1 <= int(value) <= 65535:
                    raise ValueError(f"{key} must be a valid port.")
            return {str(key): str(value) for key, value in updates.items()}

        def save_properties(self, updates):
            self.saved_properties = self.validate_property_updates(updates)
            return {"ok": True, "message": "server.properties saved.", "restart_required": False}

    class FakeTunnel:
        def __init__(self):
            self.actions = []
            self.fail_actions = set()

        def state(self):
            return {"health": "connected", "token": {"configured": True}}

        def logs(self):
            return {"unit": "bedrocknode-tunnel.service", "lines": ["Authenticated tunnel established!"]}

        def action(self, action):
            self.actions.append(action)
            if action in self.fail_actions:
                raise ValueError(f"simulated {action} failure")
            return {"ok": True, "message": f"{action} requested."}

        def set_client_settings(self, data):
            self.client_settings = dict(data)
            return {"ok": True, "message": "Client settings saved."}

        def reset_token(self):
            self.token_reset_called = True
            return {"ok": True, "message": "Token reset."}

    def setUp(self):
        self.manager = self.FakeManager()
        self.tunnel = self.FakeTunnel()
        self.patches = [
            patch.object(bds, "MANAGER", self.manager),
            patch.object(bds, "TUNNEL_MANAGER", self.tunnel),
            patch.object(bds, "TOKEN", "integration-test-token"),
        ]
        for item in self.patches:
            item.start()
        self.server = bds.ThreadingHTTPServer(("127.0.0.1", 0), bds.Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()

    def _request(self, path, method="GET", payload=None, authenticated=True):
        data = None if payload is None else __import__("json").dumps(payload).encode()
        headers = {}
        if authenticated:
            headers["X-BDS-Token"] = "integration-test-token"
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, __import__("json").loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, __import__("json").loads(exc.read().decode())

    def test_nethernet_client_parameter_and_token_reset_routes_require_authentication(self):
        code, _ = self._request("/api/tunnel/client-settings", "POST", {"control": "node.example:7000"}, authenticated=False)
        self.assertEqual(code, 403)
        code, result = self._request("/api/tunnel/client-settings", "POST", {"control": "node.example:7000"})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(self.tunnel.client_settings["control"], "node.example:7000")
        code, _ = self._request("/api/tunnel/token/reset", "POST", {}, authenticated=False)
        self.assertEqual(code, 403)
        code, result = self._request("/api/tunnel/token/reset", "POST", {})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertTrue(self.tunnel.token_reset_called)

    def test_tunnel_state_and_logs_api_require_authentication(self):
        code, _ = self._request("/api/tunnel/state", authenticated=False)
        self.assertEqual(code, 403)
        code, state = self._request("/api/tunnel/state")
        self.assertEqual(code, 200)
        self.assertEqual(state["health"], "connected")
        self.assertTrue(state["token"]["configured"])
        code, logs = self._request("/api/tunnel/logs")
        self.assertEqual(code, 200)
        self.assertIn("Authenticated tunnel established!", logs["lines"][0])

    def test_tunnel_action_and_settings_routes_call_only_expected_handlers(self):
        code, action = self._request("/api/tunnel/action", "POST", {"action": "restart"})
        self.assertEqual(code, 200)
        self.assertEqual(action["ok"], True)
        self.assertEqual(self.tunnel.actions, ["restart"])
        code, settings = self._request("/api/tunnel/settings", "POST", {
            "start_with_bds": True, "stop_with_bds": False
        })
        self.assertEqual(code, 200)
        self.assertTrue(settings["ok"])
        self.assertTrue(self.manager.tunnel_start_with_bds)
        self.assertFalse(self.manager.tunnel_stop_with_bds)

    def test_stop_route_does_not_stop_tunnel_without_a_managed_bds_process(self):
        self.manager.tunnel_stop_with_bds = True
        code, result = self._request("/api/stop", "POST", {})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(self.tunnel.actions, [])

    def test_stop_route_stops_tunnel_before_a_managed_bds_process(self):
        class RunningProcess:
            def poll(self):
                return None
        self.manager.proc = RunningProcess()
        self.manager.tunnel_stop_with_bds = True
        code, result = self._request("/api/stop", "POST", {})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(self.tunnel.actions, ["stop"])

    def test_stop_route_reports_tunnel_failure_but_still_stops_bds(self):
        class RunningProcess:
            def poll(self):
                return None
        self.manager.proc = RunningProcess()
        self.manager.tunnel_stop_with_bds = True
        self.tunnel.fail_actions.add("stop")
        code, result = self._request("/api/stop", "POST", {})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertIn("Tunnel stop failed", result.get("warning", ""))
        self.assertEqual(self.tunnel.actions, ["stop"])
        self.assertTrue(any("Automatic Tunnel stop failed" in line for line in self.manager.logs))

    def test_restart_route_reports_both_tunnel_lifecycle_failures(self):
        class RunningProcess:
            def poll(self):
                return None
        self.manager.proc = RunningProcess()
        self.manager.tunnel_stop_with_bds = True
        self.manager.tunnel_start_with_bds = True
        self.tunnel.fail_actions.update({"stop", "start"})
        code, result = self._request("/api/restart", "POST", {})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        warning = result.get("warning", "")
        self.assertIn("Tunnel stop failed", warning)
        self.assertIn("Tunnel start failed", warning)
        self.assertEqual(self.tunnel.actions, ["stop", "start"])

    def test_start_route_starts_tunnel_only_when_lifecycle_option_is_enabled(self):
        self.manager.tunnel_start_with_bds = True
        code, result = self._request("/api/start", "POST", {})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(self.tunnel.actions, ["start"])

    def test_dashboard_property_preview_returns_exact_diff_without_writing(self):
        text = "transport=nethernet\nserver-port=20000\nserver-udp-ports=30000-30003\n"
        code, result = self._request("/api/tunnel/config/preview", "POST", {"text": text})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(result["values"]["server-port"], "20000")
        self.assertEqual(len(result["changes"]), 3)
        self.assertTrue(all(change["changed"] for change in result["changes"]))
        self.assertFalse(hasattr(self.manager, "saved_properties"))

    def test_dashboard_property_import_applies_only_after_explicit_post(self):
        text = "transport=nethernet\nserver-port=20000\nserver-udp-ports=30000-30003\n"
        code, result = self._request("/api/tunnel/config/import", "POST", {"text": text})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(self.manager.saved_properties, {
            "transport": "nethernet",
            "server-port": "20000",
            "server-udp-ports": "30000-30003",
        })

    def test_dashboard_property_import_preserves_repeated_udp_rows_through_http(self):
        text = (
            "transport=nethernet\nserver-port=20000\n"
            "server-udp-ports=203.0.113.10:48000:48000\n"
            "server-udp-ports=203.0.113.10:48001:48001\n"
        )
        code, result = self._request("/api/tunnel/config/import", "POST", {"text": text})
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.manager.saved_properties["server-udp-ports"],
            "203.0.113.10:48000:48000\n203.0.113.10:48001:48001",
        )

    def test_dashboard_property_import_route_rejects_non_network_keys(self):
        code, result = self._request("/api/tunnel/config/preview", "POST", {"text": "level-name=not-a-network-setting"})
        self.assertEqual(code, 400)
        self.assertIn("not a recognized network property", result["error"])

    def test_server_port_property_has_unambiguous_nethernet_label(self):
        fields = [field for group in bds.PROPERTY_GROUPS.values() for field in group]
        port = next(field for field in fields if field["key"] == "server-port")
        self.assertEqual(port["label"], "TCP signaling port")

    def test_server_ip_property_is_available_and_validated(self):
        fields = [field for group in bds.PROPERTY_GROUPS.values() for field in group]
        server_ip = next(field for field in fields if field["key"] == "server-ip")
        self.assertEqual(server_ip["default"], "")
        self.assertEqual(server_ip["type"], "text")
        manager = bds.Manager.__new__(bds.Manager)
        self.assertEqual(manager.validate_property_updates({"server-ip": ""}), {"server-ip": ""})
        self.assertEqual(manager.validate_property_updates({"server-ip": "::1"}), {"server-ip": "::1"})
        with self.assertRaisesRegex(ValueError, "IPv4/IPv6 literal"):
            manager.validate_property_updates({"server-ip": "not-an-ip"})


class MainLifecycleTests(unittest.TestCase):
    class FakeProcess:
        @staticmethod
        def poll():
            return None

    class FakeHttpServer:
        def __init__(self, events):
            self.events = events

        def serve_forever(self, poll_interval=0.5):
            self.events.append("serve")
            raise KeyboardInterrupt

        def server_close(self):
            self.events.append("http-close")

    class FakeTimer:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    class FakeManager:
        def __init__(self, events, backup_error=None, shutdown_error=None):
            self.events = events
            self.root = Path("/tmp/fake-bds")
            self.proc = MainLifecycleTests.FakeProcess()
            self.tunnel_stop_with_bds = False
            self.backup_error = backup_error
            self.shutdown_error = shutdown_error

        def shutdown(self):
            self.events.append("shutdown")
            if self.shutdown_error:
                raise RuntimeError(self.shutdown_error)

        def backup(self, mode):
            self.events.append(f"backup:{mode}")
            if self.backup_error:
                raise ValueError(self.backup_error)
            return {"ok": True, "backup": {"name": "BDS-final-world.zip"}}

        def stop(self):
            self.events.append("stop")
            self.proc = None
            return {"ok": True}

        def _append_log(self, line):
            self.events.append("log")

    def _run_main(self, manager):
        events = manager.events
        server = self.FakeHttpServer(events)
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(bds, "MANAGER", manager), \
             patch.object(bds, "ThreadingHTTPServer", return_value=server), \
             patch.object(bds.threading, "Timer", self.FakeTimer), \
             patch("webbrowser.open", return_value=True), \
             redirect_stdout(output), redirect_stderr(errors):
            bds.main()
        return events, output.getvalue(), errors.getvalue()

    def test_ctrl_c_backs_up_world_before_stopping_bds_and_closes_http_server(self):
        events = []
        manager = self.FakeManager(events)
        events, output, errors = self._run_main(manager)
        self.assertLess(events.index("shutdown"), events.index("backup:world"))
        self.assertLess(events.index("backup:world"), events.index("stop"))
        self.assertLess(events.index("stop"), events.index("http-close"))
        self.assertIn("BDS-final-world.zip", output)
        self.assertNotIn("WARNING", errors)

    def test_ctrl_c_backup_failure_still_stops_bds_and_closes_http_server(self):
        events = []
        manager = self.FakeManager(events, backup_error="simulated backup failure")
        events, output, errors = self._run_main(manager)
        self.assertIn("backup:world", events)
        self.assertLess(events.index("backup:world"), events.index("stop"))
        self.assertLess(events.index("stop"), events.index("http-close"))
        self.assertIn("simulated backup failure", errors)
        self.assertIn("stop", events)

    def test_ctrl_c_background_shutdown_failure_does_not_skip_final_backup(self):
        events = []
        manager = self.FakeManager(events, shutdown_error="simulated shutdown failure")
        events, output, errors = self._run_main(manager)
        self.assertIn("backup:world", events)
        self.assertLess(events.index("backup:world"), events.index("stop"))
        self.assertIn("background shutdown reported an issue", errors)
        self.assertIn("BDS-final-world.zip", output)


if __name__ == "__main__":
    unittest.main()
