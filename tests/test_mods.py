import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as bds


BP_UUID = "71d7f4a1-9e64-4d2a-ae01-100000000001"
BP_MODULE = "71d7f4a1-9e64-4d2a-ae01-100000000002"
RP_UUID = "71d7f4a1-9e64-4d2a-ae01-100000000003"
RP_MODULE = "71d7f4a1-9e64-4d2a-ae01-100000000004"
SCRIPT_UUID = "71d7f4a1-9e64-4d2a-ae01-100000000005"
SCRIPT_MODULE = "71d7f4a1-9e64-4d2a-ae01-100000000006"


def pack_manifest(kind="behavior", pack_uuid=None, module_uuid=None, dependencies=None, module_type=None):
    behavior = kind == "behavior"
    pack_uuid = pack_uuid or (BP_UUID if behavior else RP_UUID)
    module_uuid = module_uuid or (BP_MODULE if behavior else RP_MODULE)
    module_type = module_type or ("data" if behavior else "resources")
    return {
        "format_version": 2,
        "header": {
            "name": "Bundle Behavior Pack" if behavior else "Bundle Resource Pack",
            "description": "Bedrock add-on import test fixture",
            "uuid": pack_uuid,
            "version": [1, 0, 0],
            "min_engine_version": [1, 20, 0],
        },
        "modules": [{
            "type": module_type,
            "uuid": module_uuid,
            "version": [1, 0, 0],
            **({"entry": "scripts/main.js"} if module_type == "script" else {}),
        }],
        "dependencies": dependencies or [],
        "capabilities": ["script_eval"] if module_type == "script" else [],
        "metadata": {"authors": ["Test Author"], "license": "MIT"},
    }


def pack_bytes(kind="behavior", pack_uuid=None, module_uuid=None, dependencies=None, module_type=None):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(pack_manifest(kind, pack_uuid, module_uuid, dependencies, module_type)))
        zf.writestr("content.json", '{"ok":true}')
        if module_type == "script":
            zf.writestr("scripts/main.js", "console.warn('test script');\n")
    return content.getvalue()


def write_mcpack(path: Path, kind="behavior", **kwargs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pack_bytes(kind, **kwargs))
    return path


def write_mcaddon(path: Path, packs: list[tuple[str, bytes]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, raw in packs:
            zf.writestr(name, raw)
    return path


class ModsManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "bedrock-server"
        self.root.mkdir()
        self.backups = base / "backups"
        self.backups.mkdir()
        (self.root / "server.properties").write_text("# test\nlevel-name=MainWorld\n", encoding="utf-8")
        world = self.root / "worlds" / "MainWorld"
        (world / "db").mkdir(parents=True)
        (world / "level.dat").write_bytes(b"level-data")
        (world / "db" / "CURRENT").write_text("MANIFEST-000001\n", encoding="utf-8")
        self.manager = bds.Manager.__new__(bds.Manager)
        self.manager.root = self.root
        self.manager.backup_dir = self.backups
        self.manager.proc = None
        self.manager.process_lock = threading.RLock()
        self.manager._unmanaged_bds = lambda: []
        self.manager._append_log = lambda *args, **kwargs: None

    def tearDown(self):
        self.temp.cleanup()

    def installed(self):
        return self.manager._pack_records()

    def test_import_single_mcpack_through_mod_manager(self):
        archive = write_mcpack(Path(self.temp.name) / "one.mcpack")
        result = self.manager.import_mod_archive(archive, archive.name)
        self.assertEqual(result["installed_count"], 1)
        self.assertEqual(result["kind"], "behavior")
        self.assertEqual(len(self.installed()), 1)

    def test_mcaddon_import_installs_behavior_resource_bundle_atomically(self):
        behavior = pack_bytes("behavior", dependencies=[{"uuid": RP_UUID, "version": [1, 0, 0]}])
        resource = pack_bytes("resource")
        archive = write_mcaddon(Path(self.temp.name) / "complete-addon.mcaddon", [("Demo_BP.mcpack", behavior), ("Demo_RP.mcpack", resource)])
        result = self.manager.import_mod_archive(archive, archive.name)
        self.assertEqual(result["mode"], "bundle")
        self.assertEqual(result["installed_count"], 2)
        records = self.installed()
        self.assertEqual({item["kind"] for item in records}, {"behavior", "resource"})
        self.assertTrue(all(item["valid"] for item in records))
        self.assertTrue((self.root / "behavior_packs").is_dir())
        self.assertTrue((self.root / "resource_packs").is_dir())

    def test_bundle_import_is_world_specific_when_requested(self):
        archive = write_mcaddon(Path(self.temp.name) / "world.mcaddon", [("only_BP.mcpack", pack_bytes("behavior"))])
        result = self.manager.import_mod_archive(archive, archive.name, "world", "MainWorld")
        self.assertEqual(result["scope"], "world")
        record = self.installed()[0]
        self.assertEqual(record["world_name"], "MainWorld")
        self.assertTrue(record["folder"] in {p.name for p in (self.root / "worlds" / "MainWorld" / "behavior_packs").iterdir()})

    def test_bundle_duplicate_uuid_rejects_every_pack_before_commit(self):
        duplicate = pack_bytes("resource", pack_uuid=BP_UUID, module_uuid=RP_MODULE)
        archive = write_mcaddon(Path(self.temp.name) / "duplicate.mcaddon", [("a.mcpack", pack_bytes("behavior")), ("b.mcpack", duplicate)])
        with self.assertRaisesRegex(ValueError, "duplicate pack header UUIDs"):
            self.manager.import_mod_archive(archive, archive.name)
        self.assertEqual(self.installed(), [])
        self.assertFalse((self.root / "behavior_packs").exists())
        self.assertFalse((self.root / "resource_packs").exists())

    def test_bundle_existing_uuid_conflict_rejects_every_pack(self):
        existing = write_mcpack(Path(self.temp.name) / "existing.mcpack")
        self.manager.import_pack_archive(existing)
        extra = pack_bytes("resource")
        bundle = write_mcaddon(Path(self.temp.name) / "conflict.mcaddon", [("new.mcpack", extra), ("same.mcpack", pack_bytes("behavior"))])
        with self.assertRaisesRegex(ValueError, "already installed"):
            self.manager.import_mod_archive(bundle, bundle.name)
        self.assertEqual(len(self.installed()), 1)
        self.assertEqual(self.installed()[0]["uuid"], BP_UUID)

    def test_invalid_second_pack_leaves_no_partial_bundle_install(self):
        valid = pack_bytes("behavior")
        invalid = b"not a zip archive"
        archive = write_mcaddon(Path(self.temp.name) / "broken.mcaddon", [("valid.mcpack", valid), ("broken.mcpack", invalid)])
        with self.assertRaises(ValueError):
            self.manager.import_mod_archive(archive, archive.name)
        self.assertEqual(self.installed(), [])
        staging = self.root / ".bds-omni-pack-staging"
        self.assertFalse(staging.exists() and any(staging.iterdir()))

    def test_bundle_enforces_combined_unpacked_size_limit(self):
        def large_pack(kind, pack_uuid, module_uuid):
            content = io.BytesIO()
            with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(pack_manifest(kind, pack_uuid, module_uuid)))
                zf.writestr("payload.bin", b"A" * 100_000)
            return content.getvalue()

        archive = Path(self.temp.name) / "oversized-expanded.mcaddon"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("one.mcpack", large_pack("behavior", BP_UUID, BP_MODULE))
            zf.writestr("two.mcpack", large_pack("resource", RP_UUID, RP_MODULE))
        with patch.object(bds, "MAX_CONTENT_EXTRACTED_BYTES", 150_000):
            with self.assertRaisesRegex(ValueError, "Combined add-on contents exceed"):
                self.manager.import_mod_archive(archive, archive.name)
        self.assertEqual(self.installed(), [])

    def test_single_zip_with_enclosing_pack_folder_remains_supported(self):
        archive = Path(self.temp.name) / "wrapped.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("OuterPack/manifest.json", json.dumps(pack_manifest("behavior")))
            zf.writestr("OuterPack/content.json", "{}")
        result = self.manager.import_mod_archive(archive, archive.name)
        self.assertEqual(result["installed_count"], 1)
        self.assertEqual(len(self.installed()), 1)

    def test_bundle_with_world_archive_is_rejected_without_partial_pack_install(self):
        archive = Path(self.temp.name) / "mixed-content.mcaddon"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("behavior.mcpack", pack_bytes("behavior"))
            zf.writestr("world.mcworld", b"world archive must be imported separately")
        with self.assertRaisesRegex(ValueError, "contains a .mcworld archive"):
            self.manager.import_mod_archive(archive, archive.name)
        self.assertEqual(self.installed(), [])

    def test_bundle_rejects_traversal_and_bad_crc_without_installing(self):
        archive = Path(self.temp.name) / "unsafe.mcaddon"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("../outside.mcpack", pack_bytes("behavior"))
        with self.assertRaisesRegex(ValueError, "unsafe path|path traversal"):
            self.manager.import_mod_archive(archive, archive.name)
        self.assertEqual(self.installed(), [])

    def test_java_jar_is_rejected_with_bedrock_specific_message(self):
        archive = Path(self.temp.name) / "java-mod.jar"
        archive.write_bytes(b"not used")
        with self.assertRaisesRegex(ValueError, "Java .jar mods are not compatible with BDS"):
            self.manager.import_mod_archive(archive, archive.name)

    def test_mod_inventory_classifies_script_modules_and_summarizes_health(self):
        script_manifest = pack_manifest("behavior", SCRIPT_UUID, SCRIPT_MODULE, module_type="script")
        folder = self.root / "behavior_packs" / "ScriptMod"
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps(script_manifest), encoding="utf-8")
        (folder / "scripts").mkdir()
        (folder / "scripts" / "main.js").write_text("console.warn('hi')", encoding="utf-8")
        self.manager._unmanaged_bds = lambda: []
        result = self.manager.mods()
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["script"], 1)
        self.assertEqual(result["items"][0]["mod_type"], "Script behavior pack")
        self.assertTrue(result["items"][0]["is_script"])
        self.assertEqual(result["items"][0]["authors"], ["Test Author"])

    def test_managed_server_running_blocks_mod_import(self):
        class Running:
            def poll(self):
                return None
        self.manager.proc = Running()
        archive = write_mcpack(Path(self.temp.name) / "running.mcpack")
        with self.assertRaisesRegex(ValueError, "Stop the managed BDS"):
            self.manager.import_mod_archive(archive, archive.name)


class ModsHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        root = base / "bedrock-server"
        root.mkdir()
        backups = base / "backups"
        backups.mkdir()
        (root / "server.properties").write_text("level-name=MainWorld\n", encoding="utf-8")
        world = root / "worlds" / "MainWorld"
        (world / "db").mkdir(parents=True)
        (world / "level.dat").write_bytes(b"level-data")
        (world / "db" / "CURRENT").write_text("MANIFEST-000001\n", encoding="utf-8")
        self.manager = bds.Manager.__new__(bds.Manager)
        self.manager.root = root
        self.manager.backup_dir = backups
        self.manager.proc = None
        self.manager.process_lock = threading.RLock()
        self.manager._unmanaged_bds = lambda: []
        self.manager._append_log = lambda *args, **kwargs: None
        self.token = "mods-test-token"
        self.patches = [patch.object(bds, "MANAGER", self.manager), patch.object(bds, "TOKEN", self.token)]
        for item in self.patches:
            item.start()
        self.server = bds.ThreadingHTTPServer(("127.0.0.1", 0), bds.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None, authenticated=True):
        raw = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"X-BDS-Token": self.token} if authenticated else {}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=raw, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def upload(self, path, archive: Path, filename: str, authenticated=True):
        raw = archive.read_bytes()
        headers = {"Content-Length": str(len(raw)), "X-Upload-Filename": filename}
        if authenticated:
            headers["X-BDS-Token"] = self.token
        req = urllib.request.Request(self.base_url + path, data=raw, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_mod_inventory_and_upload_require_authentication(self):
        status, body = self.request("/api/mods", authenticated=False)
        self.assertEqual(status, 403)
        status, body = self.request("/api/mods")
        self.assertEqual(status, 200)
        self.assertEqual(body["summary"]["total"], 0)

    def test_mcaddon_upload_installs_both_packs_over_http(self):
        archive = write_mcaddon(Path(self.temp.name) / "http.mcaddon", [("bp.mcpack", pack_bytes("behavior")), ("rp.mcpack", pack_bytes("resource"))])
        status, body = self.upload("/api/mods/import", archive, archive.name)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["installed_count"], 2)
        status, inventory = self.request("/api/mods")
        self.assertEqual(status, 200)
        self.assertEqual(inventory["summary"]["total"], 2)

    def test_mod_upload_rejects_jar_extension_before_processing(self):
        archive = Path(self.temp.name) / "nope.jar"
        archive.write_bytes(b"x")
        status, body = self.upload("/api/mods/import", archive, archive.name)
        self.assertEqual(status, 400)
        self.assertIn("Java .jar", body["error"])


if __name__ == "__main__":
    unittest.main()
