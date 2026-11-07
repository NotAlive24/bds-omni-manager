import json
import os
import stat
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


BEHAVIOR_UUID = "8a4b4f52-cc15-4e5d-a3e6-5b11f5b89a01"
BEHAVIOR_MODULE_UUID = "b4e1f3c1-59ca-4c16-8a3c-f6f17bb09d02"
RESOURCE_UUID = "9b4b4f52-cc15-4e5d-a3e6-5b11f5b89a02"
RESOURCE_MODULE_UUID = "c4e1f3c1-59ca-4c16-8a3c-f6f17bb09d03"


def manifest(kind="behavior", pack_uuid=None, module_uuid=None, dependencies=None, version=None, format_version=2):
    if kind == "behavior":
        pack_uuid = pack_uuid or BEHAVIOR_UUID
        module_uuid = module_uuid or BEHAVIOR_MODULE_UUID
        module_type = "data"
        name = "Test Behavior Pack"
    else:
        pack_uuid = pack_uuid or RESOURCE_UUID
        module_uuid = module_uuid or RESOURCE_MODULE_UUID
        module_type = "resources"
        name = "Test Resource Pack"
    return {
        "format_version": format_version,
        "header": {
            "name": name,
            "description": "Content-management test fixture",
            "uuid": pack_uuid,
            "version": version if version is not None else [1, 0, 0],
            "min_engine_version": [1, 20, 0],
        },
        "modules": [{"type": module_type, "uuid": module_uuid, "version": [1, 0, 0]}],
        "dependencies": dependencies or [],
    }


def write_world(root: Path, name="MainWorld", data=b"world-db") -> Path:
    world = root / "worlds" / name
    (world / "db").mkdir(parents=True, exist_ok=True)
    (world / "level.dat").write_bytes(b"level-data")
    (world / "db" / "CURRENT").write_text("MANIFEST-000001\n", encoding="utf-8")
    (world / "db" / "000001.ldb").write_bytes(data)
    (world / "levelname.txt").write_text(name + "\n", encoding="utf-8")
    return world


def write_pack(root: Path, kind="behavior", scope="shared", world_name="", pack_manifest=None, folder_name=None) -> Path:
    parent_name = "behavior_packs" if kind == "behavior" else "resource_packs"
    parent = root / parent_name if scope == "shared" else root / "worlds" / world_name / parent_name
    parent.mkdir(parents=True, exist_ok=True)
    pack_manifest = pack_manifest or manifest(kind)
    folder_name = folder_name or pack_manifest["header"]["name"].replace(" ", "")
    folder = parent / folder_name
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps(pack_manifest, indent=2), encoding="utf-8")
    (folder / "content.txt").write_text("pack-content\n", encoding="utf-8")
    return folder


def write_world_archive(path: Path, name="ImportedWorld", data=b"imported-db") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("level.dat", b"level-data")
        zf.writestr("levelname.txt", name + "\n")
        zf.writestr("db/CURRENT", "MANIFEST-000001\n")
        zf.writestr("db/000001.ldb", data)
    return path


def write_pack_archive(path: Path, kind="behavior", pack_manifest=None) -> Path:
    pack_manifest = pack_manifest or manifest(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(pack_manifest))
        zf.writestr("content.json", '{"ok":true}')
    return path


class ContentManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "bedrock-server"
        self.root.mkdir()
        self.backups = base / "backups"
        self.backups.mkdir()
        (self.root / "server.properties").write_text("# main server\nlevel-name=MainWorld\nserver-port=19132\n", encoding="utf-8")
        write_world(self.root, "MainWorld")
        self.manager = bds.Manager.__new__(bds.Manager)
        self.manager.root = self.root
        self.manager.backup_dir = self.backups
        self.manager.proc = None
        self.manager.process_lock = threading.RLock()
        self.manager._unmanaged_bds = lambda: []
        self.manager._append_log = lambda *args, **kwargs: None

    def tearDown(self):
        self.temp.cleanup()

    def test_world_library_reports_active_world_size_and_integrity(self):
        worlds = self.manager.worlds()
        self.assertEqual(len(worlds), 1)
        self.assertEqual(worlds[0]["name"], "MainWorld")
        self.assertTrue(worlds[0]["active"])
        self.assertTrue(worlds[0]["has_level_dat"])
        self.assertTrue(worlds[0]["has_db"])
        self.assertGreater(worlds[0]["size"], 0)
        self.assertEqual(worlds[0]["issues"], [])

    def test_world_library_flags_missing_level_dat_and_empty_database(self):
        broken = self.root / "worlds" / "Broken"
        (broken / "db").mkdir(parents=True)
        result = next(w for w in self.manager.worlds() if w["name"] == "Broken")
        self.assertTrue(any(issue.startswith("level.dat is missing") for issue in result["issues"]))
        self.assertIn("db directory has no regular files", result["issues"])

    def test_world_library_does_not_follow_symlinked_world_or_db_entries(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.ldb").write_text("not a world", encoding="utf-8")
        linked = self.root / "worlds" / "LinkedWorld"
        linked.mkdir()
        try:
            (linked / "db").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks are unavailable")
        result = next(w for w in self.manager.worlds() if w["name"] == "LinkedWorld")
        self.assertTrue(result["contains_symlinks"])
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.manager._preflight_world_tree(linked)

    def test_world_library_surfaces_symlinked_level_files_as_integrity_issues(self):
        world = self.root / "worlds" / "MainWorld"
        outside = Path(self.temp.name) / "outside-level.dat"
        outside.write_bytes(b"external")
        original = world / "level.dat"
        original.unlink()
        try:
            original.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks are unavailable")
        summary = next(item for item in self.manager.worlds() if item["name"] == "MainWorld")
        self.assertTrue(summary["contains_symlinks"])
        self.assertIn("level.dat is missing or is a symbolic link", summary["issues"])
        self.assertTrue(any("symbolic links" in issue for issue in summary["issues"]))

    def test_world_names_reject_traversal_reserved_and_hidden_names(self):
        for value in ("../escape", "..", ".hidden", "name/child", "name\\child", "trailing.", "bad:name"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bds.Manager._validate_world_name(value)
        self.assertEqual(bds.Manager._validate_world_name("My World"), "My World")

    def test_mutations_are_blocked_for_managed_and_external_bds(self):
        class Running:
            def poll(self):
                return None
        self.manager.proc = Running()
        with self.assertRaisesRegex(ValueError, "Stop the managed BDS"):
            self.manager.world_action("create", {"name": "SecondWorld"})
        self.manager.proc = None
        self.manager._unmanaged_bds = lambda: [{"pid": 321, "command": "bedrock_server"}]
        with self.assertRaisesRegex(ValueError, "external BDS process"):
            self.manager.world_action("create", {"name": "SecondWorld"})

    def test_import_world_archive_installs_valid_world_at_archive_root(self):
        archive = write_world_archive(Path(self.temp.name) / "Incoming.mcworld", "Incoming")
        result = self.manager.import_world_archive(archive)
        self.assertEqual(result["world"], "Incoming")
        self.assertTrue((self.root / "worlds" / "Incoming" / "level.dat").is_file())
        self.assertFalse(any(self.root.glob("worlds/.bds-omni-world-import-*")))

    def test_import_world_archive_supports_one_enclosing_folder(self):
        archive = Path(self.temp.name) / "folder.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Exported/level.dat", b"level")
            zf.writestr("Exported/levelname.txt", "Foldered\n")
            zf.writestr("Exported/db/CURRENT", "MANIFEST-000001\n")
        result = self.manager.import_world_archive(archive, "FolderedWorld")
        self.assertEqual(result["world"], "FolderedWorld")
        self.assertTrue((self.root / "worlds" / "FolderedWorld" / "db" / "CURRENT").is_file())

    def test_import_world_archive_rejects_existing_name_without_overwriting(self):
        archive = write_world_archive(Path(self.temp.name) / "MainWorld.mcworld")
        existing = (self.root / "worlds" / "MainWorld" / "db" / "000001.ldb").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.manager.import_world_archive(archive, "MainWorld")
        self.assertEqual((self.root / "worlds" / "MainWorld" / "db" / "000001.ldb").read_bytes(), existing)

    def test_import_world_archive_rejects_zip_slip_and_special_entries(self):
        traversal = Path(self.temp.name) / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as zf:
            zf.writestr("../escaped.txt", "bad")
            zf.writestr("level.dat", b"level")
            zf.writestr("db/file.ldb", b"db")
        with self.assertRaisesRegex(ValueError, "unsafe path|path traversal"):
            self.manager.import_world_archive(traversal, "BadArchive")
        self.assertFalse((Path(self.temp.name) / "escaped.txt").exists())

        symlink_archive = Path(self.temp.name) / "symlink.zip"
        with zipfile.ZipFile(symlink_archive, "w") as zf:
            info = zipfile.ZipInfo("level.dat")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "../../outside")
            zf.writestr("db/file.ldb", b"db")
        with self.assertRaisesRegex(ValueError, "symbolic link or special file"):
            self.manager.import_world_archive(symlink_archive, "BadSymlink")

    def test_import_world_archive_rejects_empty_database_and_bad_crc(self):
        empty_db = Path(self.temp.name) / "empty.zip"
        with zipfile.ZipFile(empty_db, "w") as zf:
            zf.writestr("level.dat", b"level")
            zf.writestr("db/", "")
        with self.assertRaisesRegex(ValueError, "database folder is empty"):
            self.manager.import_world_archive(empty_db, "EmptyDb")
        corrupt = Path(self.temp.name) / "corrupt.zip"
        with zipfile.ZipFile(corrupt, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("level.dat", b"level-data")
            zf.writestr("levelname.txt", "Corrupt\n")
            zf.writestr("db/CURRENT", "MANIFEST-000001\n")
            zf.writestr("db/000001.ldb", b"database-data")
        raw = bytearray(corrupt.read_bytes())
        # Corrupt a stored member payload without changing central metadata.
        member = b"level-data"
        offset = raw.find(member)
        self.assertGreaterEqual(offset, 0)
        raw[offset] ^= 0x01
        corrupt.write_bytes(raw)
        with self.assertRaises(Exception):
            self.manager.import_world_archive(corrupt, "Corrupt")

    def test_export_world_archive_has_mcworld_root_entries_and_valid_crc(self):
        output = self.manager.export_world_archive("MainWorld")
        self.assertEqual(output.suffix, ".mcworld")
        self.assertTrue(output.is_relative_to(self.backups))
        with zipfile.ZipFile(output) as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("level.dat", zf.namelist())
            self.assertIn("db/000001.ldb", zf.namelist())
            self.assertNotIn("MainWorld/level.dat", zf.namelist())

    def test_world_create_selects_new_name_without_fabricating_database(self):
        result = self.manager.world_action("create", {"name": "FreshWorld"})
        self.assertTrue(result["ok"])
        self.assertEqual(bds.parse_properties((self.root / "server.properties").read_text())["level-name"], "FreshWorld")
        self.assertFalse((self.root / "worlds" / "FreshWorld").exists())

    def test_world_duplicate_copies_world_and_sets_display_name(self):
        result = self.manager.world_action("duplicate", {"name": "MainWorld", "new_name": "CopyWorld"})
        self.assertEqual(result["world"], "CopyWorld")
        self.assertEqual((self.root / "worlds" / "CopyWorld" / "db" / "000001.ldb").read_bytes(), b"world-db")
        self.assertEqual((self.root / "worlds" / "CopyWorld" / "levelname.txt").read_text(), "CopyWorld\n")
        self.assertEqual(bds.parse_properties((self.root / "server.properties").read_text())["level-name"], "MainWorld")

    def test_world_rename_updates_active_property_and_rolls_back_on_property_failure(self):
        original = (self.root / "worlds" / "MainWorld" / "levelname.txt").read_bytes()
        result = self.manager.world_action("rename", {"name": "MainWorld", "new_name": "RenamedWorld"})
        self.assertEqual(result["world"], "RenamedWorld")
        self.assertEqual(bds.parse_properties((self.root / "server.properties").read_text())["level-name"], "RenamedWorld")
        self.assertEqual((self.root / "worlds" / "RenamedWorld" / "levelname.txt").read_text(), "RenamedWorld\n")

        self.manager.save_properties = lambda *_: (_ for _ in ()).throw(OSError("simulated config write failure"))
        with self.assertRaisesRegex(OSError, "simulated config write failure"):
            self.manager.world_action("rename", {"name": "RenamedWorld", "new_name": "FailedRename"})
        self.assertTrue((self.root / "worlds" / "RenamedWorld").is_dir())
        self.assertFalse((self.root / "worlds" / "FailedRename").exists())

    def test_active_world_cannot_be_deleted(self):
        write_world(self.root, "OtherWorld")
        with self.assertRaisesRegex(ValueError, "active world cannot be deleted"):
            self.manager.world_action("delete", {"name": "MainWorld"})

    def test_delete_inactive_world_creates_verified_recovery_archive(self):
        write_world(self.root, "OtherWorld")
        result = self.manager.world_action("delete", {"name": "OtherWorld"})
        self.assertFalse((self.root / "worlds" / "OtherWorld").exists())
        archive = self.backups / "deleted-worlds" / result["archive"]
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("level.dat", zf.namelist())

    def test_pack_scanner_reads_behavior_resource_scope_and_dependencies(self):
        write_world(self.root, "MainWorld")
        rp = write_pack(self.root, "resource")
        bp_manifest = manifest("behavior", dependencies=[{"uuid": RESOURCE_UUID, "version": [1, 0, 0]}])
        bp = write_pack(self.root, "behavior", pack_manifest=bp_manifest)
        records = self.manager._pack_records()
        by_name = {record["name"]: record for record in records}
        self.assertTrue(by_name["Test Resource Pack"]["valid"])
        self.assertTrue(by_name["Test Behavior Pack"]["valid"])
        self.assertTrue(by_name["Test Behavior Pack"]["dependencies"][0]["resolved"])
        self.assertEqual(by_name["Test Behavior Pack"]["scope"], "shared")
        self.assertTrue(rp.is_dir() and bp.is_dir())

    def test_world_detail_shows_available_and_active_packs(self):
        write_pack(self.root, "resource")
        write_pack(self.root, "behavior")
        world = self.root / "worlds" / "MainWorld"
        (world / "world_behavior_packs.json").write_text(json.dumps([{"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}]), encoding="utf-8")
        detail = self.manager.world_detail("MainWorld")
        self.assertEqual(len(detail["available_packs"]["behavior"]), 1)
        self.assertTrue(detail["available_packs"]["behavior"][0]["enabled"])
        self.assertEqual(detail["references"]["behavior"][0]["name"], "Test Behavior Pack")
        self.assertEqual(detail["issues"], [])

    def test_world_pack_json_rejects_invalid_uuid_duplicate_and_invalid_version(self):
        world = self.root / "worlds" / "MainWorld"
        path = world / "world_behavior_packs.json"
        for payload in (
            [{"pack_id": "not-a-uuid", "version": [1, 0, 0]}],
            [{"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}, {"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}],
            [{"pack_id": BEHAVIOR_UUID, "version": [True, 0, 0]}],
        ):
            path.write_text(json.dumps(payload), encoding="utf-8")
            _, errors = self.manager._world_pack_refs(world, "behavior")
            self.assertTrue(errors)

    def test_pack_scanner_marks_symbolic_link_content_invalid(self):
        folder = write_pack(self.root, "behavior")
        outside = Path(self.temp.name) / "outside-content.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            (folder / "external.txt").symlink_to(outside)
        except OSError:
            self.skipTest("symlinks are unavailable")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        self.assertFalse(record["valid"])
        self.assertTrue(any("symbolic-link file" in error for error in record["errors"]))

    def test_invalid_active_pack_can_be_disabled_but_cannot_be_enabled(self):
        broken = manifest("behavior", dependencies=[{"uuid": RESOURCE_UUID, "version": [1, 0, 0]}])
        write_pack(self.root, "behavior", pack_manifest=broken)
        world = self.root / "worlds" / "MainWorld"
        (world / "world_behavior_packs.json").write_text(json.dumps([
            {"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}
        ]), encoding="utf-8")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        self.assertFalse(record["valid"])
        result = self.manager.set_world_pack("MainWorld", "behavior", record["location_id"], False)
        self.assertIn("disabled", result["message"])
        refs, errors = self.manager._world_pack_refs(world, "behavior")
        self.assertFalse(errors)
        self.assertEqual(refs, [])
        with self.assertRaisesRegex(ValueError, "cannot be activated"):
            self.manager.set_world_pack("MainWorld", "behavior", record["location_id"], True)

    def test_duplicate_pack_uuid_blocks_world_activation_and_deactivation(self):
        write_pack(self.root, "behavior", folder_name="DuplicateOne")
        write_pack(self.root, "behavior", folder_name="DuplicateTwo")
        world = self.root / "worlds" / "MainWorld"
        (world / "world_behavior_packs.json").write_text(json.dumps([{"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}]), encoding="utf-8")
        records = self.manager._pack_records()
        self.assertEqual(len(records), 2)
        for record in records:
            with self.subTest(location=record["location_id"]), self.assertRaisesRegex(ValueError, "multiple installed locations"):
                self.manager.set_world_pack("MainWorld", "behavior", record["location_id"], False)
                self.manager.set_world_pack("MainWorld", "behavior", record["location_id"], True)
        refs, errors = self.manager._world_pack_refs(world, "behavior")
        self.assertFalse(errors)
        self.assertEqual(refs[0]["pack_id"], BEHAVIOR_UUID)

    def test_exports_reject_inside_server_destination_without_creating_it(self):
        world_destination = self.root / "unexpected-world-export"
        with self.assertRaisesRegex(ValueError, "outside the BDS server directory"):
            self.manager.export_world_archive("MainWorld", world_destination)
        self.assertFalse(world_destination.exists())
        write_pack(self.root, "behavior")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        pack_destination = self.root / "unexpected-pack-export"
        with self.assertRaisesRegex(ValueError, "outside the BDS server directory"):
            self.manager._export_pack_path(self.manager._pack_path_for_record(record), record, pack_destination)
        self.assertFalse(pack_destination.exists())

    def test_pack_scanner_marks_missing_dependencies_duplicate_header_uuids_and_invalid_manifests(self):
        missing_dep = manifest("behavior", dependencies=[{"uuid": "6e4b4f52-cc15-4e5d-a3e6-5b11f5b89a03", "version": [1, 0, 0]}])
        write_pack(self.root, "behavior", pack_manifest=missing_dep, folder_name="MissingDependency")
        duplicate = manifest("resource", pack_uuid=BEHAVIOR_UUID, module_uuid=RESOURCE_MODULE_UUID)
        write_pack(self.root, "resource", pack_manifest=duplicate, folder_name="DuplicateUuid")
        invalid = manifest("resource", pack_uuid="bad-uuid", module_uuid=RESOURCE_MODULE_UUID)
        write_pack(self.root, "resource", pack_manifest=invalid, folder_name="InvalidManifest")
        records = self.manager._pack_records()
        by_folder = {record["folder"]: record for record in records}
        self.assertFalse(by_folder["MissingDependency"]["valid"])
        self.assertTrue(any("Required pack is not installed" in error for error in by_folder["MissingDependency"]["errors"]))
        self.assertFalse(by_folder["DuplicateUuid"]["valid"])
        self.assertTrue(any("duplicated across installed locations" in error for error in by_folder["DuplicateUuid"]["errors"]))
        self.assertFalse(by_folder["InvalidManifest"]["valid"])

    def test_pack_dependency_cannot_resolve_to_another_worlds_local_pack(self):
        write_world(self.root, "WorldA")
        write_world(self.root, "WorldB")
        local_dep = manifest("resource", pack_uuid=RESOURCE_UUID, module_uuid=RESOURCE_MODULE_UUID)
        write_pack(self.root, "resource", "world", "WorldB", local_dep)
        depends_on_local = manifest("behavior", dependencies=[{"uuid": RESOURCE_UUID, "version": [1, 0, 0]}])
        write_pack(self.root, "behavior", "world", "WorldA", depends_on_local)
        records = self.manager._pack_records()
        owner = next(record for record in records if record["world_name"] == "WorldA")
        self.assertFalse(owner["valid"])
        self.assertIn("visible scope", owner["errors"][-1])

    def test_pack_import_installs_behavior_or_resource_pack(self):
        archive = write_pack_archive(Path(self.temp.name) / "behavior.mcpack", "behavior")
        result = self.manager.import_pack_archive(archive, "shared")
        self.assertEqual(result["kind"], "behavior")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        self.assertTrue(record["valid"])

    def test_pack_import_can_install_world_specific_pack(self):
        archive = write_pack_archive(Path(self.temp.name) / "resource.mcpack", "resource")
        result = self.manager.import_pack_archive(archive, "world", "MainWorld")
        self.assertEqual(result["scope"], "world")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == RESOURCE_UUID)
        self.assertEqual(record["world_name"], "MainWorld")

    def test_pack_import_rejects_invalid_manifest_format_and_module_uuid(self):
        bad_format = manifest("behavior", format_version="2")
        with self.assertRaisesRegex(ValueError, "format_version"):
            self.manager.import_pack_archive(write_pack_archive(Path(self.temp.name) / "bad-format.mcpack", pack_manifest=bad_format))
        bad_uuid = manifest("behavior", module_uuid=BEHAVIOR_UUID)
        with self.assertRaisesRegex(ValueError, "module UUID"):
            self.manager.import_pack_archive(write_pack_archive(Path(self.temp.name) / "bad-module.mcpack", pack_manifest=bad_uuid))

    def test_pack_import_rejects_preview_v3_manifest_on_stable_flow(self):
        # Import currently allows staging but the scanner blocks activation; the
        # user should get a clear incompatibility result rather than a silent install.
        archive = write_pack_archive(Path(self.temp.name) / "preview-v3.mcpack", pack_manifest=manifest("behavior", format_version=3))
        result = self.manager.import_pack_archive(archive)
        self.assertEqual(result["kind"], "behavior")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        self.assertFalse(record["valid"])
        self.assertTrue(any("preview-only" in error for error in record["errors"]))

    def test_pack_import_rejects_archive_traversal_and_duplicate_uuid(self):
        traversal = Path(self.temp.name) / "pack-traversal.zip"
        with zipfile.ZipFile(traversal, "w") as zf:
            zf.writestr("../outside", "bad")
            zf.writestr("manifest.json", json.dumps(manifest("behavior")))
        with self.assertRaisesRegex(ValueError, "unsafe path|path traversal"):
            self.manager.import_pack_archive(traversal)
        existing = write_pack(self.root, "behavior")
        archive = write_pack_archive(Path(self.temp.name) / "same.mcpack", "behavior")
        with self.assertRaisesRegex(ValueError, "already installed"):
            self.manager.import_pack_archive(archive)
        self.assertTrue(existing.is_dir())

    def test_pack_export_is_a_valid_root_level_mcpack(self):
        pack_path = write_pack(self.root, "behavior")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        output = self.manager.export_pack_archive(record["location_id"])
        self.assertEqual(output.suffix, ".mcpack")
        with zipfile.ZipFile(output) as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("manifest.json", zf.namelist())
            self.assertNotIn(pack_path.name + "/manifest.json", zf.namelist())

    def test_enabling_behavior_pack_automatically_activates_resource_dependency(self):
        write_pack(self.root, "resource")
        behavior_manifest = manifest("behavior", dependencies=[{"uuid": RESOURCE_UUID, "version": [1, 0, 0]}])
        write_pack(self.root, "behavior", pack_manifest=behavior_manifest)
        records = self.manager._pack_records()
        behavior = next(item for item in records if item["kind"] == "behavior")
        result = self.manager.set_world_pack("MainWorld", "behavior", behavior["location_id"], True)
        self.assertTrue(result["ok"])
        behavior_refs, errors = self.manager._world_pack_refs(self.root / "worlds" / "MainWorld", "behavior")
        resource_refs, resource_errors = self.manager._world_pack_refs(self.root / "worlds" / "MainWorld", "resource")
        self.assertFalse(errors or resource_errors)
        self.assertEqual(behavior_refs[0]["pack_id"], BEHAVIOR_UUID)
        self.assertEqual(resource_refs[0]["pack_id"], RESOURCE_UUID)

    def test_dependency_cannot_be_disabled_while_dependent_pack_is_active(self):
        write_pack(self.root, "resource")
        behavior_manifest = manifest("behavior", dependencies=[{"uuid": RESOURCE_UUID, "version": [1, 0, 0]}])
        write_pack(self.root, "behavior", pack_manifest=behavior_manifest)
        records = self.manager._pack_records()
        behavior = next(item for item in records if item["kind"] == "behavior")
        resource = next(item for item in records if item["kind"] == "resource")
        self.manager.set_world_pack("MainWorld", "behavior", behavior["location_id"], True)
        with self.assertRaisesRegex(ValueError, "Disable dependent pack"):
            self.manager.set_world_pack("MainWorld", "resource", resource["location_id"], False)

    def test_pack_removal_archives_pack_and_removes_world_references(self):
        write_pack(self.root, "behavior")
        record = next(item for item in self.manager._pack_records() if item["kind"] == "behavior")
        world = self.root / "worlds" / "MainWorld"
        (world / "world_behavior_packs.json").write_text(json.dumps([{"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}]), encoding="utf-8")
        result = self.manager.pack_action("remove", {"location_id": record["location_id"]})
        self.assertTrue(result["ok"])
        self.assertEqual(self.manager._pack_records(), [])
        refs, errors = self.manager._world_pack_refs(world, "behavior")
        self.assertFalse(errors)
        self.assertEqual(refs, [])
        archive = self.backups / "removed-packs" / result["archive"]
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("manifest.json", zf.namelist())

    def test_pack_removal_restores_original_folder_and_references_when_update_fails(self):
        pack_path = write_pack(self.root, "behavior")
        record = next(item for item in self.manager._pack_records() if item["kind"] == "behavior")
        world = self.root / "worlds" / "MainWorld"
        refs_path = world / "world_behavior_packs.json"
        original_refs = json.dumps([{"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}])
        refs_path.write_text(original_refs, encoding="utf-8")
        real_atomic_write = bds.atomic_write
        fail_once = {"value": True}

        def fail_first_ref_write(path, data):
            if path == refs_path and fail_once["value"]:
                fail_once["value"] = False
                raise OSError("simulated reference write failure")
            return real_atomic_write(path, data)

        with patch.object(bds, "atomic_write", side_effect=fail_first_ref_write):
            with self.assertRaisesRegex(OSError, "simulated reference write failure"):
                self.manager.pack_action("remove", {"location_id": record["location_id"]})
        self.assertTrue(pack_path.is_dir())
        self.assertEqual(refs_path.read_text(encoding="utf-8"), original_refs)
        self.assertFalse(any(self.root.rglob(".bds-omni-pack-remove-*")))

    def test_pack_removal_cleanup_failure_keeps_hidden_quarantine_and_verified_archive(self):
        write_pack(self.root, "behavior")
        record = next(item for item in self.manager._pack_records() if item["kind"] == "behavior")
        world = self.root / "worlds" / "MainWorld"
        refs_path = world / "world_behavior_packs.json"
        refs_path.write_text(json.dumps([{"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}]), encoding="utf-8")
        with patch.object(bds.shutil, "rmtree", side_effect=OSError("simulated quarantine cleanup failure")):
            result = self.manager.pack_action("remove", {"location_id": record["location_id"]})
        self.assertIn("cleanup folder remains", result["message"])
        self.assertTrue(result["cleanup_warning"])
        self.assertEqual(self.manager._pack_records(), [])
        quarantine = list((self.root / "behavior_packs").glob(".bds-omni-pack-remove-*"))
        self.assertEqual(len(quarantine), 1)
        archive = self.backups / "removed-packs" / result["archive"]
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("manifest.json", zf.namelist())
        refs, errors = self.manager._world_pack_refs(world, "behavior")
        self.assertFalse(errors)
        self.assertEqual(refs, [])

    def test_pack_removal_is_blocked_when_manifest_uuid_is_duplicated(self):
        write_pack(self.root, "behavior", folder_name="PackOne")
        write_pack(self.root, "behavior", folder_name="PackTwo")
        records = self.manager._pack_records()
        self.assertTrue(all(item.get("duplicate_uuid") for item in records))
        for record in records:
            with self.subTest(location=record["location_id"]), self.assertRaisesRegex(ValueError, "installed in multiple locations"):
                self.manager.pack_action("remove", {"location_id": record["location_id"]})
        self.assertTrue((self.root / "behavior_packs" / "PackOne").is_dir())
        self.assertTrue((self.root / "behavior_packs" / "PackTwo").is_dir())

    def test_pack_removal_blocks_when_an_active_pack_depends_on_it(self):
        write_pack(self.root, "resource")
        write_pack(self.root, "behavior", pack_manifest=manifest("behavior", dependencies=[{"uuid": RESOURCE_UUID, "version": [1, 0, 0]}]))
        records = self.manager._pack_records()
        behavior = next(item for item in records if item["kind"] == "behavior")
        resource = next(item for item in records if item["kind"] == "resource")
        self.manager.set_world_pack("MainWorld", "behavior", behavior["location_id"], True)
        with self.assertRaisesRegex(ValueError, "required by active pack"):
            self.manager.pack_action("remove", {"location_id": resource["location_id"]})

    def test_download_tickets_are_single_use_and_expire(self):
        archive = self.manager.export_world_archive("MainWorld")
        ticket = self.manager.issue_download_ticket(archive, archive.name, "application/x-minecraft-world")
        parsed = ticket["url"].removeprefix("/download/")
        entry = self.manager.consume_download_ticket(parsed)
        self.assertIsNotNone(entry)
        self.assertTrue(entry[0].is_file())
        self.assertIsNone(self.manager.consume_download_ticket(parsed))
        ticket2 = self.manager.issue_download_ticket(archive, archive.name, "application/x-minecraft-world")
        ticket_key = ticket2["url"].removeprefix("/download/")
        self.manager.download_tickets[ticket_key] = (archive, archive.name, "application/x-minecraft-world", 0)
        self.assertIsNone(self.manager.consume_download_ticket(ticket_key))

    def test_duplicate_manifest_versions_do_not_accept_booleans(self):
        self.assertEqual(bds.Manager._version_text([1, 2, 3]), "1.2.3")
        self.assertEqual(bds.Manager._version_text("1.2.3-beta.1"), "1.2.3-beta.1")
        self.assertEqual(bds.Manager._version_text([True, 0, 0]), "")
        self.assertEqual(bds.Manager._version_text([1, 2.0, 3]), "")


    def test_world_detail_does_not_treat_duplicate_invalid_pack_uuid_as_installed(self):
        write_pack(self.root, "behavior", pack_manifest=manifest("behavior"), folder_name="PackOne")
        write_pack(self.root, "behavior", pack_manifest=manifest("behavior"), folder_name="PackTwo")
        world = self.root / "worlds" / "MainWorld"
        (world / "world_behavior_packs.json").write_text(json.dumps([
            {"pack_id": BEHAVIOR_UUID, "version": [1, 0, 0]}
        ]), encoding="utf-8")
        detail = self.manager.world_detail("MainWorld")
        self.assertFalse(detail["references"]["behavior"][0]["installed"])
        self.assertEqual(detail["references"]["behavior"][0]["name"], "Missing pack")
        self.assertTrue(any("Missing behavior pack" in issue for issue in detail["issues"]))

    def test_world_delete_restores_complete_snapshot_if_quarantine_cleanup_fails(self):
        (self.root / "server.properties").write_text("# main server\nlevel-name=OtherWorld\nserver-port=19132\n", encoding="utf-8")
        world = self.root / "worlds" / "MainWorld"
        original = world / "db" / "000001.ldb"
        self.assertEqual(original.read_bytes(), b"world-db")
        real_rmtree = bds.shutil.rmtree
        def fail_after_partial_quarantine_removal(path, *args, **kwargs):
            path = Path(path)
            if path.name.startswith(".bds-omni-world-delete-"):
                (path / "db" / "000001.ldb").unlink(missing_ok=True)
                raise OSError("simulated partial cleanup failure")
            return real_rmtree(path, *args, **kwargs)
        with patch.object(bds.shutil, "rmtree", side_effect=fail_after_partial_quarantine_removal):
            with self.assertRaisesRegex(ValueError, "world has been restored"):
                self.manager.world_action("delete", {"name": "MainWorld"})
        restored = self.root / "worlds" / "MainWorld" / "db" / "000001.ldb"
        self.assertTrue(restored.is_file())
        self.assertEqual(restored.read_bytes(), b"world-db")
        self.assertTrue(any((self.root / "worlds").glob(".bds-omni-world-delete-*")))
        self.assertTrue(any((self.backups / "deleted-worlds").glob("*.mcworld")))

    def test_world_export_refuses_excessive_entry_count_before_creating_archive(self):
        with patch.object(bds, "MAX_CONTENT_ARCHIVE_ENTRIES", 2):
            with self.assertRaisesRegex(ValueError, "entry limit"):
                self.manager.export_world_archive("MainWorld")
        self.assertFalse(any((self.backups / "world-exports").glob("*.mcworld")))

    def test_pack_export_refuses_excessive_entry_count_before_creating_archive(self):
        folder = write_pack(self.root, "behavior")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        with patch.object(bds, "MAX_CONTENT_ARCHIVE_ENTRIES", 1):
            with self.assertRaisesRegex(ValueError, "entry limit"):
                self.manager.export_pack_archive(record["location_id"])
        self.assertFalse(any((self.backups / "pack-exports").glob("*.mcpack")))

    def test_repeated_exports_do_not_overwrite_previous_world_or_pack_archives(self):
        world_one = self.manager.export_world_archive("MainWorld")
        world_two = self.manager.export_world_archive("MainWorld")
        self.assertNotEqual(world_one, world_two)
        self.assertTrue(world_one.exists() and world_two.exists())
        write_pack(self.root, "behavior")
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        pack_one = self.manager.export_pack_archive(record["location_id"])
        pack_two = self.manager.export_pack_archive(record["location_id"])
        self.assertNotEqual(pack_one, pack_two)
        self.assertTrue(pack_one.exists() and pack_two.exists())


class ContentHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        root = base / "bedrock-server"
        root.mkdir()
        backup_dir = base / "backups"
        backup_dir.mkdir()
        (root / "server.properties").write_text("level-name=MainWorld\n", encoding="utf-8")
        write_world(root, "MainWorld")
        self.manager = bds.Manager.__new__(bds.Manager)
        self.manager.root = root
        self.manager.backup_dir = backup_dir
        self.manager.proc = None
        self.manager.process_lock = threading.RLock()
        self.manager._unmanaged_bds = lambda: []
        self.manager._append_log = lambda *args, **kwargs: None
        self.token = "content-test-token"
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

    def request_json(self, path, method="GET", payload=None, authenticated=True, extra_headers=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = dict(extra_headers or {})
        if authenticated:
            headers["X-BDS-Token"] = self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))

    def request_upload(self, path, archive: Path, filename: str, extra_headers=None, authenticated=True):
        raw = archive.read_bytes()
        headers = {"Content-Length": str(len(raw)), "X-Upload-Filename": filename}
        headers.update(extra_headers or {})
        if authenticated:
            headers["X-BDS-Token"] = self.token
        request = urllib.request.Request(self.base_url + path, data=raw, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_world_and_pack_endpoints_require_authentication(self):
        status, body = self.request_json("/api/worlds", authenticated=False)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "Forbidden")
        status, body = self.request_json("/api/packs", authenticated=False)
        self.assertEqual(status, 403)

    def test_world_and_pack_read_apis_return_structured_data(self):
        status, worlds = self.request_json("/api/worlds")
        self.assertEqual(status, 200)
        self.assertEqual(worlds[0]["name"], "MainWorld")
        write_pack(self.manager.root, "behavior")
        status, packs = self.request_json("/api/packs")
        self.assertEqual(status, 200)
        self.assertEqual(packs["behavior"][0]["name"], "Test Behavior Pack")
        status, detail = self.request_json("/api/worlds/detail?name=MainWorld")
        self.assertEqual(status, 200)
        self.assertEqual(detail["world"]["name"], "MainWorld")

    def test_world_import_upload_requires_auth_and_installs_archive(self):
        archive = write_world_archive(Path(self.temp.name) / "Imported.mcworld", "Imported")
        status, body = self.request_upload("/api/worlds/import", archive, archive.name, authenticated=False)
        self.assertEqual(status, 403)
        status, body = self.request_upload("/api/worlds/import", archive, archive.name, {"X-World-Name": "Imported"})
        self.assertEqual(status, 200)
        self.assertEqual(body["world"], "Imported")
        self.assertTrue((self.manager.root / "worlds" / "Imported" / "level.dat").is_file())

    def test_pack_import_upload_supports_world_scope_and_rejects_bad_extension(self):
        archive = write_pack_archive(Path(self.temp.name) / "Test.mcpack", "behavior")
        status, body = self.request_upload("/api/packs/import", archive, archive.name, {"X-Pack-Scope": "world", "X-Pack-World": "MainWorld"})
        self.assertEqual(status, 200)
        record = next(item for item in self.manager._pack_records() if item["uuid"] == BEHAVIOR_UUID)
        self.assertEqual(record["scope"], "world")
        status, body = self.request_upload("/api/packs/import", archive, "bad.exe")
        self.assertEqual(status, 400)
        self.assertIn("Unsupported archive extension", body["error"])

    def test_export_api_returns_single_use_local_download_ticket(self):
        status, response = self.request_json("/api/worlds/export", method="POST", payload={"name": "MainWorld"})
        self.assertEqual(status, 200)
        self.assertIn("/download/", response["url"])
        with urllib.request.urlopen(self.base_url + response["url"], timeout=5) as downloaded:
            self.assertEqual(downloaded.headers.get("Content-Type"), "application/x-minecraft-world")
            archive = downloaded.read()
        import io
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("level.dat", zf.namelist())
        status, body = self.request_json(response["url"], authenticated=False)
        # The download URL is not an API route and a consumed ticket is one-use.
        self.assertEqual(status, 404)
        self.assertIn("expired or already used", body["error"])


if __name__ == "__main__":
    unittest.main()
