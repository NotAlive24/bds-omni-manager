import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import main as bds


class ServerUpdateTests(unittest.TestCase):
    def make_server(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        (root / "bedrock_server").write_bytes(b"OLD-BDS-EXECUTABLE" + b"o" * 2048)
        (root / "bedrock_server").chmod(0o755)
        (root / "server.properties").write_text(
            "server-name=My Existing Server\nlevel-name=SurvivalWorld\nserver-port=19132\ntransport=nethernet\ncustom-option=keep-me\n",
            encoding="utf-8",
        )
        (root / "allowlist.json").write_text('[{"name":"AllowedPlayer","ignoresPlayerLimit":false}]\n', encoding="utf-8")
        (root / "permissions.json").write_text('[{"permission":"operator","xuid":"123"}]\n', encoding="utf-8")
        (root / "packetlimitconfig.json").write_text('{"packet-limit":123}\n', encoding="utf-8")
        (root / "server-icon.png").write_bytes(b"CUSTOM-SERVER-ICON")
        world = root / "worlds" / "SurvivalWorld"
        (world / "db").mkdir(parents=True)
        (world / "db" / "CURRENT").write_text("MANIFEST-000001\n", encoding="utf-8")
        (world / "level.dat").write_bytes(b"BEDROCK-WORLD")
        (world / "world_behavior_packs.json").write_text('[{"pack_id":"custom-behavior","version":[1,0,0]}]\n', encoding="utf-8")
        (world / "behavior_packs" / "WorldLocal").mkdir(parents=True)
        (world / "behavior_packs" / "WorldLocal" / "keep.txt").write_text("world-local-pack", encoding="utf-8")
        custom = root / "behavior_packs" / "CustomBehavior"
        custom.mkdir(parents=True)
        (custom / "manifest.json").write_text(json.dumps({"format_version": 2, "header": {"uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "version": [1, 0, 0]}}), encoding="utf-8")
        (custom / "behavior.js").write_text("custom behavior", encoding="utf-8")
        (root / "behavior_packs" / "custom-readme.txt").write_text("custom pack sidecar", encoding="utf-8")
        bundled = root / "behavior_packs" / "vanilla"
        bundled.mkdir(parents=True)
        (bundled / "manifest.json").write_text(json.dumps({"format_version": 2, "header": {"uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "version": [1, 0, 0]}}), encoding="utf-8")
        (bundled / "vanilla.txt").write_text("old vanilla", encoding="utf-8")
        resource = root / "resource_packs" / "CustomResources"
        resource.mkdir(parents=True)
        (resource / "manifest.json").write_text(json.dumps({"format_version": 2, "header": {"uuid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "version": [1, 0, 0]}}), encoding="utf-8")
        (resource / "textures.txt").write_text("custom resources", encoding="utf-8")
        (root / "keys").mkdir()
        (root / "keys" / "private.key").write_text("private-key-placeholder", encoding="utf-8")
        (root / "config").mkdir()
        (root / "config" / "custom.json").write_text('{"custom":true}\n', encoding="utf-8")
        (root / "world_templates").mkdir()
        (root / "world_templates" / "MyTemplate").mkdir()
        (root / "world_templates" / "MyTemplate" / "template.txt").write_text("user template", encoding="utf-8")
        (root / "world_templates" / "BundledTemplate" / "template.txt").parent.mkdir(parents=True)
        (root / "world_templates" / "BundledTemplate" / "template.txt").write_text("old built-in template", encoding="utf-8")

    def make_update_zip(self, path: Path, *, wrapped=False, add_traversal=False):
        prefix = "BDS-Release/" if wrapped else ""
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(prefix + "bedrock_server", b"NEW-BDS-EXECUTABLE" + b"n" * 4096)
            zf.writestr(prefix + "server.properties", "server-name=Fresh Defaults\nlevel-name=FreshWorld\n")
            zf.writestr(prefix + "server-icon.png", b"NEW-DEFAULT-ICON")
            zf.writestr(prefix + "bedrock_server_how_to.html", "new release docs")
            vanilla = prefix + "behavior_packs/vanilla/"
            zf.writestr(vanilla + "manifest.json", json.dumps({"format_version": 2, "header": {"uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "version": [2, 0, 0]}}))
            zf.writestr(vanilla + "vanilla.txt", "new vanilla")
            zf.writestr(prefix + "world_templates/BundledTemplate/template.txt", "new built-in template")
            if add_traversal:
                zf.writestr(prefix + "../escape.txt", "bad")

    def stub_manager(self, root: Path, state_dir: Path):
        manager = bds.Manager.__new__(bds.Manager)
        manager.root = root
        manager.proc = None
        manager.started_at = None
        manager.process_lock = threading.RLock()
        manager.players_lock = threading.RLock()
        manager.online_players = {}
        manager._players_tracking_active = False
        manager._server_update_lock = threading.RLock()
        manager.server_update_tickets = {}
        manager.server_version = "1.0.0"
        manager._append_log = lambda *_args, **_kwargs: None
        manager._unmanaged_bds = lambda: []
        manager.backup = lambda mode: {"ok": True, "message": "Full backup created", "backup": {"name": "BDS-full-test.zip", "size": 123}}
        manager.stop = lambda: {"ok": True, "message": "BDS has stopped."}
        manager._restore_cached_server_version = lambda: None
        self.state_dir_patch = patch.object(bds, "STATE_DIR", state_dir)
        self.state_dir_patch.start()
        self.addCleanup(self.state_dir_patch.stop)
        state_dir.mkdir(parents=True, exist_ok=True)
        return manager

    def create_preview(self, manager, archive: Path, filename="bds-linux.zip"):
        return manager.prepare_server_update_archive(archive, filename)

    def test_preview_accepts_official_zip_at_root_and_one_enclosing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            archive = base / "release.zip"
            self.make_update_zip(archive, wrapped=True)
            preview = self.create_preview(manager, archive)
            self.assertTrue(preview["ok"])
            self.assertEqual(preview["archive_root"], "BDS-Release")
            self.assertGreater(preview["file_count"], 2)
            self.assertTrue(Path(manager.server_update_tickets[preview["ticket"]]["path"]).is_file())

    def test_preview_rejects_traversal_and_leaves_current_server_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            archive = base / "bad.zip"
            self.make_update_zip(archive, add_traversal=True)
            with self.assertRaisesRegex(ValueError, "unsafe path|traversal"):
                self.create_preview(manager, archive)
            self.assertTrue((root / "worlds" / "SurvivalWorld" / "level.dat").is_file())

    def test_preview_rejects_non_bds_and_windows_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            archive = base / "wrong.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("bedrock_server.exe", b"x" * 2048)
                zf.writestr("server.properties", "level-name=World\n")
            with self.assertRaisesRegex(ValueError, "bedrock_server"):
                self.create_preview(manager, archive)

    def test_update_preserves_user_data_and_merges_custom_packs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            manager.config = {"server_dir": str(root), "custom_manager_setting": "untouched"}
            archive = base / "release.zip"
            self.make_update_zip(archive)
            preview = self.create_preview(manager, archive)
            events = []
            manager.backup = lambda mode: events.append("backup") or {"ok": True, "backup": {"name": "BDS-full-test.zip", "size": 123}}
            result = manager.apply_server_update(preview["ticket"], before_stop=lambda: events.append("before-stop"))
            self.assertEqual(events, ["backup", "before-stop"])
            self.assertTrue(result["ok"])
            self.assertTrue(result["server_stopped"])
            self.assertEqual((root / "server.properties").read_text(encoding="utf-8"), "server-name=My Existing Server\nlevel-name=SurvivalWorld\nserver-port=19132\ntransport=nethernet\ncustom-option=keep-me\n")
            self.assertEqual((root / "server-icon.png").read_bytes(), b"CUSTOM-SERVER-ICON")
            self.assertIn("AllowedPlayer", (root / "allowlist.json").read_text(encoding="utf-8"))
            self.assertIn('"123"', (root / "permissions.json").read_text(encoding="utf-8"))
            self.assertEqual((root / "bedrock_server").read_bytes()[:18], b"NEW-BDS-EXECUTABLE")
            self.assertTrue(os.access(root / "bedrock_server", os.X_OK))
            self.assertEqual((root / "worlds" / "SurvivalWorld" / "level.dat").read_bytes(), b"BEDROCK-WORLD")
            self.assertEqual((root / "worlds" / "SurvivalWorld" / "world_behavior_packs.json").read_text(encoding="utf-8").strip(), '[{"pack_id":"custom-behavior","version":[1,0,0]}]')
            self.assertEqual((root / "worlds" / "SurvivalWorld" / "behavior_packs" / "WorldLocal" / "keep.txt").read_text(encoding="utf-8"), "world-local-pack")
            self.assertEqual((root / "behavior_packs" / "CustomBehavior" / "behavior.js").read_text(encoding="utf-8"), "custom behavior")
            self.assertEqual((root / "behavior_packs" / "custom-readme.txt").read_text(encoding="utf-8"), "custom pack sidecar")
            self.assertEqual((root / "resource_packs" / "CustomResources" / "textures.txt").read_text(encoding="utf-8"), "custom resources")
            self.assertEqual((root / "behavior_packs" / "vanilla" / "vanilla.txt").read_text(encoding="utf-8"), "new vanilla")
            self.assertEqual((root / "keys" / "private.key").read_text(encoding="utf-8"), "private-key-placeholder")
            self.assertEqual((root / "config" / "custom.json").read_text(encoding="utf-8"), '{"custom":true}\n')
            self.assertEqual((root / "world_templates" / "MyTemplate" / "template.txt").read_text(encoding="utf-8"), "user template")
            self.assertEqual((root / "world_templates" / "BundledTemplate" / "template.txt").read_text(encoding="utf-8"), "new built-in template")
            self.assertEqual(manager.config["custom_manager_setting"], "untouched")
            rollback = Path(result["rollback_folder"])
            self.assertTrue(rollback.is_dir())
            self.assertTrue((rollback / "bedrock_server").read_bytes().startswith(b"OLD-BDS-EXECUTABLE"))
            self.assertNotIn(preview["ticket"], manager.server_update_tickets)

    def test_live_update_creates_backup_before_tunnel_callback_and_stopping_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            class RunningProcess:
                def poll(self): return None
            proc = RunningProcess()
            manager.proc = proc
            events = []
            manager.backup = lambda mode: events.append("backup") or {"ok": True, "backup": {"name": "full.zip"}}
            def stop():
                events.append("stop")
                manager.proc = None
                return {"ok": True}
            manager.stop = stop
            archive = base / "release.zip"
            self.make_update_zip(archive)
            preview = self.create_preview(manager, archive)
            result = manager.apply_server_update(preview["ticket"], before_stop=lambda: events.append("tunnel-stop"))
            self.assertEqual(events, ["backup", "tunnel-stop", "stop"])
            self.assertTrue(result["ok"])

    def test_external_server_blocks_update_before_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            manager._unmanaged_bds = lambda: [{"pid": 999, "command": "bedrock_server"}]
            events = []
            manager.backup = lambda mode: events.append("backup") or {"ok": True}
            archive = base / "release.zip"
            self.make_update_zip(archive)
            preview = self.create_preview(manager, archive)
            with self.assertRaisesRegex(ValueError, "outside this manager"):
                manager.apply_server_update(preview["ticket"])
            self.assertEqual(events, [])
            self.assertTrue((root / "bedrock_server").read_bytes().startswith(b"OLD-BDS-EXECUTABLE"))

    def test_bad_zip_replaced_after_preview_fails_before_backup_or_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            events = []
            manager.backup = lambda mode: events.append("backup") or {"ok": True}
            manager.stop = lambda: events.append("stop") or {"ok": True}
            archive = base / "release.zip"
            self.make_update_zip(archive)
            preview = self.create_preview(manager, archive)
            staged_zip = manager.server_update_tickets[preview["ticket"]]["path"]
            staged_zip.write_bytes(b"not a zip anymore")
            with self.assertRaises(ValueError):
                manager.apply_server_update(preview["ticket"])
            self.assertEqual(events, [])
            self.assertTrue((root / "bedrock_server").read_bytes().startswith(b"OLD-BDS-EXECUTABLE"))

    def test_preserved_symlink_is_rejected_before_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            (root / "worlds" / "SurvivalWorld" / "link").symlink_to(root / "server.properties")
            manager = self.stub_manager(root, base / "state")
            events = []
            manager.backup = lambda mode: events.append("backup") or {"ok": True}
            archive = base / "release.zip"
            self.make_update_zip(archive)
            preview = self.create_preview(manager, archive)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                manager.apply_server_update(preview["ticket"])
            self.assertEqual(events, [])
            self.assertTrue((root / "bedrock_server").read_bytes().startswith(b"OLD-BDS-EXECUTABLE"))

    def test_swap_failure_restores_previous_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            manager.backup = lambda mode: {"ok": True, "backup": {"name": "full.zip"}}
            archive = base / "release.zip"
            self.make_update_zip(archive)
            preview = self.create_preview(manager, archive)
            real_replace = os.replace
            failed = {"done": False}
            def flaky_replace(src, dst):
                src_path, dst_path = Path(src), Path(dst)
                if dst_path == root and src_path.name == "new-server" and not failed["done"]:
                    failed["done"] = True
                    raise OSError("simulated atomic swap failure")
                return real_replace(src, dst)
            with patch.object(bds.os, "replace", side_effect=flaky_replace):
                with self.assertRaisesRegex(ValueError, "simulated atomic swap failure"):
                    manager.apply_server_update(preview["ticket"])
            self.assertTrue(failed["done"])
            self.assertTrue(root.is_dir())
            self.assertTrue((root / "bedrock_server").read_bytes().startswith(b"OLD-BDS-EXECUTABLE"))
            self.assertEqual((root / "server.properties").read_text(encoding="utf-8").splitlines()[0], "server-name=My Existing Server")


    def test_preview_rejects_symlinked_server_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            alias = base / "server-alias"
            alias.symlink_to(root, target_is_directory=True)
            manager = self.stub_manager(alias, base / "state")
            archive = base / "release.zip"
            self.make_update_zip(archive)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                self.create_preview(manager, archive)
            self.assertTrue((root / "bedrock_server").is_file())

    def test_failed_full_backup_never_stops_or_replaces_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            events = []
            manager.backup = lambda mode: events.append("backup") or {"ok": False, "message": "simulated backup disk failure"}
            manager.stop = lambda: events.append("stop") or {"ok": True}
            archive = base / "release.zip"
            self.make_update_zip(archive)
            preview = self.create_preview(manager, archive)
            with self.assertRaisesRegex(ValueError, "backup disk failure"):
                manager.apply_server_update(preview["ticket"], before_stop=lambda: events.append("tunnel-stop"))
            self.assertEqual(events, ["backup"])
            self.assertTrue((root / "bedrock_server").read_bytes().startswith(b"OLD-BDS-EXECUTABLE"))
            self.assertEqual((root / "server.properties").read_text(encoding="utf-8").splitlines()[0], "server-name=My Existing Server")

    def test_raw_upload_handler_validates_and_stores_the_uploaded_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            state_dir = base / "state"
            manager = self.stub_manager(root, state_dir)
            archive = base / "release.zip"
            self.make_update_zip(archive)
            payload = archive.read_bytes()
            handler = bds.Handler.__new__(bds.Handler)
            handler.headers = {
                "Content-Length": str(len(payload)),
                "X-Upload-Filename": "bedrock-server-linux.zip",
            }
            handler.rfile = io.BytesIO(payload)
            with patch.object(bds, "MANAGER", manager):
                result = handler._receive_server_update_upload()
            self.assertTrue(result["ok"])
            staged = manager.server_update_tickets[result["ticket"]]["path"]
            self.assertTrue(staged.is_file())
            self.assertEqual(staged.read_bytes(), payload)
            self.assertEqual(list(state_dir.glob("bds-omni-server-update-upload-*.zip")), [])

    def test_raw_upload_handler_rejects_truncated_body_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            state_dir = base / "state"
            manager = self.stub_manager(root, state_dir)
            handler = bds.Handler.__new__(bds.Handler)
            handler.headers = {"Content-Length": "20", "X-Upload-Filename": "bedrock-server.zip"}
            handler.rfile = io.BytesIO(b"short")
            with patch.object(bds, "MANAGER", manager):
                with self.assertRaisesRegex(ValueError, "ended before all declared bytes"):
                    handler._receive_server_update_upload()
            self.assertEqual(list(state_dir.glob("bds-omni-server-update-upload-*.zip")), [])

    def test_discard_removes_pending_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "server"
            self.make_server(root)
            manager = self.stub_manager(root, base / "state")
            archive = base / "release.zip"
            self.make_update_zip(archive)
            preview = self.create_preview(manager, archive)
            staged = manager.server_update_tickets[preview["ticket"]]["path"]
            result = manager.discard_server_update_ticket(preview["ticket"])
            self.assertTrue(result["ok"])
            self.assertFalse(staged.exists())
            self.assertNotIn(preview["ticket"], manager.server_update_tickets)


if __name__ == "__main__":
    unittest.main()
