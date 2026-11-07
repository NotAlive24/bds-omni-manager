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


BP_A = "11111111-1111-4111-8111-111111111111"
BP_A_MOD = "11111111-1111-4111-8111-111111111112"
BP_B = "22222222-2222-4222-8222-222222222221"
BP_B_MOD = "22222222-2222-4222-8222-222222222222"
RP_A = "33333333-3333-4333-8333-333333333331"
RP_A_MOD = "33333333-3333-4333-8333-333333333332"
RP_B = "44444444-4444-4444-8444-444444444441"
RP_B_MOD = "44444444-4444-4444-8444-444444444442"


def pack_manifest(kind, pack_uuid, module_uuid, name, dependencies=None, min_engine=(1, 20, 0)):
    module_type = "data" if kind == "behavior" else "resources"
    return {
        "format_version": 2,
        "header": {
            "name": name,
            "description": f"{name} fixture",
            "uuid": pack_uuid,
            "version": [1, 0, 0],
            "min_engine_version": list(min_engine) if min_engine else None,
        },
        "modules": [{"type": module_type, "uuid": module_uuid, "version": [1, 0, 0]}],
        "dependencies": dependencies or [],
    }


def write_pack(root, kind, pack_uuid, module_uuid, name, dependencies=None, files=None, scope="shared", world="MainWorld", min_engine=(1, 20, 0)):
    folder_name = "behavior_packs" if kind == "behavior" else "resource_packs"
    parent = root / folder_name if scope == "shared" else root / "worlds" / world / folder_name
    path = parent / name.replace(" ", "")
    path.mkdir(parents=True, exist_ok=True)
    payload = pack_manifest(kind, pack_uuid, module_uuid, name, dependencies, min_engine)
    if min_engine is None:
        payload["header"].pop("min_engine_version", None)
    (path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    for relative, content in (files or {}).items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return path


def write_world(root, name):
    path = root / "worlds" / name
    (path / "db").mkdir(parents=True, exist_ok=True)
    (path / "db" / "CURRENT").write_text("MANIFEST-000001\n", encoding="utf-8")
    (path / "db" / "000001.ldb").write_bytes(b"world data")
    (path / "level.dat").write_bytes(b"level data")
    (path / "levelname.txt").write_text(name + "\n", encoding="utf-8")
    return path


def set_stack(world, kind, records):
    filename = "world_behavior_packs.json" if kind == "behavior" else "world_resource_packs.json"
    (world / filename).write_text(json.dumps([{"pack_id": pack_id, "version": [1, 0, 0]} for pack_id in records], indent=2), encoding="utf-8")


def write_backup(path, world_name, files):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr(".bds-omni-backup.json", json.dumps({"mode": "world", "world_name": world_name, "created_at": "2026-11-07T09:00:00+05:30"}))
        for name, data in files.items():
            archive.writestr(name, data)


class ContentIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "bedrock-server"
        self.root.mkdir()
        self.backups = base / "backups"
        self.backups.mkdir()
        (self.root / "server.properties").write_text("# test server\nlevel-name=MainWorld\nserver-port=19132\n", encoding="utf-8")
        self.world = write_world(self.root, "MainWorld")
        self.other_world = write_world(self.root, "OtherWorld")
        self.manager = bds.Manager.__new__(bds.Manager)
        self.manager.root = self.root
        self.manager.backup_dir = self.backups
        self.manager.proc = None
        self.manager.process_lock = threading.RLock()
        self.manager._unmanaged_bds = lambda: []
        self.manager._append_log = lambda *args, **kwargs: None
        self.manager.server_version = "1.26.70.20"
        self.manager.stack_profiles = []
        self.manager.stack_profile_lock = threading.RLock()
        self.manager.stack_profile_path = base / "state" / "world_pack_profiles.json"

    def tearDown(self):
        self.temp.cleanup()

    def install_basic_stack(self, world=None):
        world = world or self.world
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Alpha Behavior")
        write_pack(self.root, "resource", RP_A, RP_A_MOD, "Alpha Resources")
        set_stack(world, "behavior", [BP_A])
        set_stack(world, "resource", [RP_A])

    def test_version_tuple_accepts_bedrock_and_semver_prefixes(self):
        self.assertEqual(self.manager._version_tuple([1, 26, 70]), (1, 26, 70))
        self.assertEqual(self.manager._version_tuple("1.26.70.20"), (1, 26, 70, 20))
        self.assertEqual(self.manager._version_tuple("1.26.70-preview.1"), (1, 26, 70))
        self.assertIsNone(self.manager._version_tuple([True, 26, 70]))
        self.assertIsNone(self.manager._version_tuple("unknown"))

    def test_content_lab_reports_dependency_graph_and_engine_compatibility_warning(self):
        write_pack(self.root, "resource", RP_A, RP_A_MOD, "Core Resources", min_engine=(1, 20, 0))
        write_pack(
            self.root, "behavior", BP_A, BP_A_MOD, "Gameplay Pack",
            dependencies=[{"uuid": RP_A, "version": [1, 0, 0]}], min_engine=(1, 30, 0),
        )
        set_stack(self.world, "behavior", [BP_A])
        set_stack(self.world, "resource", [RP_A])
        report = self.manager.content_lab("MainWorld")
        self.assertEqual(report["summary"]["available_packs"], 2)
        self.assertEqual(report["summary"]["active_behavior"], 1)
        self.assertEqual(report["summary"]["active_resource"], 1)
        self.assertEqual(report["summary"]["dependency_edges"], 1)
        self.assertEqual(len(report["nodes"]), 2)
        self.assertEqual(len(report["edges"]), 1)
        self.assertTrue(report["edges"][0]["resolved"])
        self.assertTrue(any(item["code"] == "engine-version" for item in report["findings"]))
        self.assertTrue(report["mutations_allowed"])

    def test_content_lab_detects_dependency_cycles(self):
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Pack Alpha", dependencies=[{"uuid": BP_B, "version": [1, 0, 0]}])
        write_pack(self.root, "behavior", BP_B, BP_B_MOD, "Pack Beta", dependencies=[{"uuid": BP_A, "version": [1, 0, 0]}])
        set_stack(self.world, "behavior", [BP_A, BP_B])
        report = self.manager.content_lab("MainWorld")
        self.assertEqual(report["summary"]["cycles"], 1)
        self.assertTrue(any(item["code"] == "dependency-cycle" for item in report["findings"]))

    def test_content_lab_iterative_cycle_walk_handles_deep_dependency_chain(self):
        count = 1500
        records = []
        for index in range(count):
            location_id = f"behavior:shared::{index}"
            dependency = []
            if index + 1 < count:
                dependency = [{"uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pack-{index + 1}")), "resolved": True, "location_id": f"behavior:shared::{index + 1}", "detail": "fixture"}]
            records.append({
                "location_id": location_id, "uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pack-{index}")),
                "name": f"Pack {index}", "kind": "behavior", "scope": "shared", "world_name": "",
                "version": "1.0.0", "valid": True, "active_worlds": [], "module_types": ["data"],
                "min_engine_version": "", "dependencies": dependency, "errors": [],
            })
        self.manager._pack_records = lambda: records
        self.manager.world_detail = lambda _name: {"world": "MainWorld"}
        self.manager._world_pack_refs = lambda _path, _kind: ([], [])
        report = self.manager.content_lab("MainWorld")
        self.assertEqual(len(report["nodes"]), count)
        self.assertEqual(len(report["edges"]), count - 1)
        self.assertEqual(report["summary"]["cycles"], 0)

    def test_content_lab_finds_different_content_at_same_resource_path(self):
        write_pack(self.root, "resource", RP_A, RP_A_MOD, "Resource Alpha", files={"textures/blocks/stone.png": b"first image"})
        write_pack(self.root, "resource", RP_B, RP_B_MOD, "Resource Beta", files={"textures/blocks/stone.png": b"second image"})
        set_stack(self.world, "resource", [RP_A, RP_B])
        report = self.manager.content_lab("MainWorld")
        self.assertEqual(report["summary"]["asset_overlaps"], 1)
        self.assertEqual(report["conflicts"][0]["classification"], "different-content")
        self.assertTrue(any(item["code"] == "asset-overlap" and item["severity"] == "warning" for item in report["findings"]))

    def test_content_lab_marks_identical_asset_overlap_as_informational(self):
        shared = b"identical texture"
        write_pack(self.root, "resource", RP_A, RP_A_MOD, "Resource Alpha", files={"textures/blocks/stone.png": shared})
        write_pack(self.root, "resource", RP_B, RP_B_MOD, "Resource Beta", files={"textures/blocks/stone.png": shared})
        set_stack(self.world, "resource", [RP_A, RP_B])
        report = self.manager.content_lab("MainWorld")
        self.assertEqual(report["conflicts"][0]["classification"], "same-or-unverified")
        finding = next(item for item in report["findings"] if item["code"] == "asset-overlap")
        self.assertEqual(finding["severity"], "info")

    def test_content_lab_does_not_claim_compatibility_when_server_version_unknown(self):
        self.manager.server_version = "Unknown"
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Needs New Engine", min_engine=(99, 0, 0))
        report = self.manager.content_lab("MainWorld")
        self.assertTrue(any(item["code"] == "engine-unknown" for item in report["findings"]))
        self.assertFalse(any(item["code"] == "engine-version" for item in report["findings"]))

    def test_content_lab_does_not_resolve_dependency_from_another_world_local_pack(self):
        write_pack(self.root, "resource", RP_A, RP_A_MOD, "Other World Resources", scope="world", world="OtherWorld")
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Main World Behavior", dependencies=[{"uuid": RP_A, "version": [1, 0, 0]}])
        set_stack(self.world, "behavior", [BP_A])
        report = self.manager.content_lab("MainWorld")
        self.assertTrue(any(item["code"] == "pack-validation" and "visible scope" in item["message"] for item in report["findings"]))
        self.assertTrue(any(not item["resolved"] for item in report["edges"]))

    def test_content_lab_blocks_stack_mutation_when_bds_is_managed_or_external(self):
        class RunningProcess:
            def poll(self):
                return None
        self.install_basic_stack()
        self.manager.proc = RunningProcess()
        self.assertFalse(self.manager.content_lab("MainWorld")["mutations_allowed"])
        self.manager.proc = None
        self.manager._unmanaged_bds = lambda: [{"pid": 1234}]
        self.assertFalse(self.manager.content_lab("MainWorld")["mutations_allowed"])

    def test_stack_profile_create_persists_and_list_returns_sanitized_metadata(self):
        self.install_basic_stack()
        result = self.manager.create_stack_profile("MainWorld", "Survival Core")
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"]["name"], "Survival Core")
        stored = json.loads(self.manager.stack_profile_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["schema_version"], 1)
        self.assertEqual(len(stored["profiles"]), 1)
        listed = self.manager.list_stack_profiles()
        self.assertEqual(listed[0]["behavior_count"], 1)
        self.assertEqual(listed[0]["resource_count"], 1)
        self.assertNotIn("behavior", listed[0])

    def test_stack_profile_rejects_duplicate_name_for_same_world_and_invalid_names(self):
        self.install_basic_stack()
        self.manager.create_stack_profile("MainWorld", "Default")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.manager.create_stack_profile("MainWorld", " default ")
        for bad in ("", "../escape", "bad/name", "x" * 65, "bad\nname"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.manager.create_stack_profile("MainWorld", bad)

    def test_stack_profiles_allow_same_name_for_different_worlds(self):
        self.install_basic_stack()
        self.manager.create_stack_profile("MainWorld", "Default")
        self.manager.create_stack_profile("OtherWorld", "Default")
        self.assertEqual(len(self.manager.list_stack_profiles()), 2)

    def test_stack_profile_preview_resolves_shared_packs_for_another_world(self):
        self.install_basic_stack()
        profile = self.manager.create_stack_profile("MainWorld", "Survival")
        preview = self.manager.preview_stack_profile(profile["profile"]["id"], "OtherWorld")
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["target_world"], "OtherWorld")
        self.assertEqual(preview["diff"]["behavior"]["added"], [BP_A])
        self.assertEqual(preview["diff"]["resource"]["added"], [RP_A])

    def test_stack_profile_preview_reports_missing_pack_dependency_in_profile(self):
        write_pack(self.root, "resource", RP_A, RP_A_MOD, "Dependency Resources")
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Dependent Behavior", dependencies=[{"uuid": RP_A, "version": [1, 0, 0]}])
        # The resource dependency is installed and valid but intentionally not active.
        set_stack(self.world, "behavior", [BP_A])
        profile = self.manager.create_stack_profile("MainWorld", "Incomplete")
        preview = self.manager.preview_stack_profile(profile["profile"]["id"], "OtherWorld")
        self.assertFalse(preview["ready"])
        self.assertTrue(any(item["code"] == "profile-missing-dependency" for item in preview["issues"]))

    def test_stack_profile_preview_blocks_dependency_cycles(self):
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Cycle Alpha", dependencies=[{"uuid": BP_B, "version": [1, 0, 0]}])
        write_pack(self.root, "behavior", BP_B, BP_B_MOD, "Cycle Beta", dependencies=[{"uuid": BP_A, "version": [1, 0, 0]}])
        set_stack(self.world, "behavior", [BP_A, BP_B])
        profile = self.manager.create_stack_profile("MainWorld", "Cyclic stack")
        preview = self.manager.preview_stack_profile(profile["profile"]["id"], "OtherWorld")
        self.assertFalse(preview["ready"])
        self.assertTrue(any(item["code"] == "profile-dependency-cycle" for item in preview["issues"]))

    def test_stack_profile_preview_rejects_missing_or_ambiguous_target_packs(self):
        self.install_basic_stack()
        profile = self.manager.create_stack_profile("MainWorld", "Default")
        # A second visible copy creates UUID ambiguity for the destination world.
        write_pack(self.root, "behavior", BP_A, "55555555-5555-4555-8555-555555555555", "Duplicate Alpha")
        preview = self.manager.preview_stack_profile(profile["profile"]["id"], "OtherWorld")
        self.assertFalse(preview["ready"])
        self.assertTrue(any(item["code"] == "unresolved-pack" for item in preview["issues"]))

    def test_stack_profile_apply_creates_recovery_archive_and_changes_both_stacks(self):
        self.install_basic_stack()
        profile = self.manager.create_stack_profile("MainWorld", "Deployment")
        # Clear the destination stacks to make the applied change observable.
        set_stack(self.other_world, "behavior", [])
        set_stack(self.other_world, "resource", [])
        result = self.manager.apply_stack_profile(profile["profile"]["id"], "OtherWorld")
        self.assertTrue(result["ok"])
        self.assertTrue((self.other_world / "world_behavior_packs.json").is_file())
        self.assertTrue((self.other_world / "world_resource_packs.json").is_file())
        self.assertEqual(json.loads((self.other_world / "world_behavior_packs.json").read_text())[0]["pack_id"], BP_A)
        self.assertEqual(json.loads((self.other_world / "world_resource_packs.json").read_text())[0]["pack_id"], RP_A)
        recovery = self.backups / "pack-stack-profiles" / result["archive"]
        self.assertTrue(recovery.is_file())
        with zipfile.ZipFile(recovery) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn(".bds-omni-stack.json", archive.namelist())

    def test_stack_profile_apply_is_blocked_when_server_running(self):
        self.install_basic_stack()
        profile = self.manager.create_stack_profile("MainWorld", "Default")
        class RunningProcess:
            def poll(self):
                return None
        self.manager.proc = RunningProcess()
        with self.assertRaisesRegex(ValueError, "Stop the managed BDS server"):
            self.manager.apply_stack_profile(profile["profile"]["id"], "OtherWorld")

    def test_stack_profile_apply_rolls_back_first_file_if_second_write_fails(self):
        self.install_basic_stack()
        profile = self.manager.create_stack_profile("MainWorld", "Rollback")
        set_stack(self.other_world, "behavior", [])
        set_stack(self.other_world, "resource", [])
        behavior_path = self.other_world / "world_behavior_packs.json"
        resource_path = self.other_world / "world_resource_packs.json"
        original_behavior = behavior_path.read_bytes()
        original_resource = resource_path.read_bytes()
        original_writer = self.manager._save_stack_file
        def flaky_writer(world_path, kind, entries):
            if kind == "resource":
                raise OSError("simulated second-file failure")
            return original_writer(world_path, kind, entries)
        with patch.object(self.manager, "_save_stack_file", side_effect=flaky_writer):
            with self.assertRaisesRegex(OSError, "second-file failure"):
                self.manager.apply_stack_profile(profile["profile"]["id"], "OtherWorld")
        self.assertEqual(behavior_path.read_bytes(), original_behavior)
        self.assertEqual(resource_path.read_bytes(), original_resource)

    def test_stack_profile_apply_does_not_mutate_target_when_preview_has_issues(self):
        write_pack(self.root, "resource", RP_A, RP_A_MOD, "Dependency Resources")
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Dependent Behavior", dependencies=[{"uuid": RP_A, "version": [1, 0, 0]}])
        set_stack(self.world, "behavior", [BP_A])
        profile = self.manager.create_stack_profile("MainWorld", "Incomplete")
        set_stack(self.other_world, "behavior", [BP_B])
        before = (self.other_world / "world_behavior_packs.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "cannot be applied"):
            self.manager.apply_stack_profile(profile["profile"]["id"], "OtherWorld")
        self.assertEqual((self.other_world / "world_behavior_packs.json").read_bytes(), before)

    def test_delete_profile_persists_and_unknown_id_is_rejected(self):
        self.install_basic_stack()
        profile = self.manager.create_stack_profile("MainWorld", "Delete me")
        profile_id = profile["profile"]["id"]
        self.assertTrue(self.manager.delete_stack_profile(profile_id)["ok"])
        self.assertEqual(self.manager.list_stack_profiles(), [])
        with self.assertRaisesRegex(ValueError, "not found"):
            self.manager.delete_stack_profile(profile_id)
        with self.assertRaisesRegex(ValueError, "Invalid profile"):
            self.manager.delete_stack_profile("../bad")

    def test_profile_persistence_failure_restores_in_memory_state(self):
        self.install_basic_stack()
        with patch.object(self.manager, "_persist_stack_profiles", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.manager.create_stack_profile("MainWorld", "Not saved")
        self.assertEqual(self.manager._profile_store(), [])

    def test_reorder_pack_stack_preserves_every_reference_and_archives_original(self):
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Alpha")
        write_pack(self.root, "behavior", BP_B, BP_B_MOD, "Beta")
        set_stack(self.world, "behavior", [BP_A, BP_B])
        result = self.manager.reorder_world_pack_stack("MainWorld", "behavior", [BP_B, BP_A])
        actual = json.loads((self.world / "world_behavior_packs.json").read_text())
        self.assertEqual([item["pack_id"] for item in actual], [BP_B, BP_A])
        self.assertTrue(result["changed"])
        archive = self.backups / "pack-stack-profiles" / result["archive"]
        with zipfile.ZipFile(archive) as zipped:
            self.assertIsNone(zipped.testzip())
            previous = json.loads(zipped.read("world_behavior_packs.json"))
        self.assertEqual([item["pack_id"] for item in previous], [BP_A, BP_B])

    def test_reorder_requires_exact_permutation_and_rejects_duplicates(self):
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Alpha")
        write_pack(self.root, "behavior", BP_B, BP_B_MOD, "Beta")
        set_stack(self.world, "behavior", [BP_A, BP_B])
        original = (self.world / "world_behavior_packs.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "every currently active"):
            self.manager.reorder_world_pack_stack("MainWorld", "behavior", [BP_A])
        with self.assertRaisesRegex(ValueError, "every currently active"):
            self.manager.reorder_world_pack_stack("MainWorld", "behavior", [BP_A, BP_A])
        self.assertEqual((self.world / "world_behavior_packs.json").read_bytes(), original)

    def test_reorder_is_blocked_when_server_is_running_or_detected_external(self):
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Alpha")
        set_stack(self.world, "behavior", [BP_A])
        class RunningProcess:
            def poll(self):
                return None
        self.manager.proc = RunningProcess()
        with self.assertRaisesRegex(ValueError, "Stop the managed BDS server"):
            self.manager.reorder_world_pack_stack("MainWorld", "behavior", [BP_A])
        self.manager.proc = None
        self.manager._unmanaged_bds = lambda: [{"pid": 123}]
        with self.assertRaisesRegex(ValueError, "external BDS process"):
            self.manager.reorder_world_pack_stack("MainWorld", "behavior", [BP_A])

    def test_stack_recovery_refuses_symlinked_recovery_directory(self):
        outside = Path(self.temp.name) / "outside-recovery"
        outside.mkdir()
        target = self.backups / "pack-stack-profiles"
        try:
            target.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink unavailable")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.manager._archive_stack_state("MainWorld", {"behavior": b"[]"})
        self.assertEqual(list(outside.iterdir()), [])

    def test_reorder_symlink_stack_file_is_rejected_without_replacing_link(self):
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Alpha")
        target = Path(self.temp.name) / "outside.json"
        target.write_text(json.dumps([{"pack_id": BP_A, "version": [1, 0, 0]}]), encoding="utf-8")
        stack = self.world / "world_behavior_packs.json"
        try:
            stack.symlink_to(target)
        except OSError:
            self.skipTest("symlink unavailable")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.manager.reorder_world_pack_stack("MainWorld", "behavior", [])
        self.assertTrue(stack.is_symlink())

    def test_backup_comparison_reports_added_removed_changed_and_unchanged(self):
        first = self.backups / "BDS-first.zip"
        second = self.backups / "BDS-second.zip"
        write_backup(first, "MainWorld", {
            "server.properties": b"level-name=MainWorld\n",
            "worlds/MainWorld/level.dat": b"level data old",
            "worlds/MainWorld/db/old.ldb": b"removed",
            "worlds/MainWorld/levelname.txt": b"MainWorld\n",
        })
        write_backup(second, "MainWorld", {
            "server.properties": b"level-name=MainWorld\n",
            "worlds/MainWorld/level.dat": b"level data new",
            "worlds/MainWorld/db/new.ldb": b"added",
            "worlds/MainWorld/levelname.txt": b"MainWorld\n",
        })
        result = self.manager.compare_backups(first.name, second.name)
        self.assertEqual(result["counts"]["added"], 1)
        self.assertEqual(result["counts"]["removed"], 1)
        self.assertEqual(result["counts"]["changed"], 1)
        self.assertEqual(result["counts"]["unchanged"], 2)
        self.assertEqual(result["added"], ["worlds/MainWorld/db/new.ldb"])
        self.assertEqual(result["removed"], ["worlds/MainWorld/db/old.ldb"])
        self.assertEqual(result["changed"][0]["path"], "worlds/MainWorld/level.dat")
        self.assertIn("does not inspect LevelDB semantics", result["comparison"])

    def test_backup_comparison_rejects_same_name_invalid_name_and_corrupt_archive(self):
        archive = self.backups / "BDS-valid.zip"
        write_backup(archive, "MainWorld", {"server.properties": b"x", "worlds/MainWorld/level.dat": b"y"})
        with self.assertRaisesRegex(ValueError, "different backups"):
            self.manager.compare_backups(archive.name, archive.name)
        with self.assertRaisesRegex(ValueError, "Invalid backup filename"):
            self.manager.compare_backups("../BDS-valid.zip", archive.name)
        broken = self.backups / "BDS-broken.zip"
        broken.write_bytes(b"not a zip")
        with self.assertRaisesRegex(ValueError, "valid ZIP"):
            self.manager.compare_backups(archive.name, broken.name)


class ContentIntelligenceHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "bedrock-server"
        self.root.mkdir()
        self.backups = base / "backups"
        self.backups.mkdir()
        (self.root / "server.properties").write_text("level-name=MainWorld\n", encoding="utf-8")
        self.world = write_world(self.root, "MainWorld")
        self.manager = bds.Manager.__new__(bds.Manager)
        self.manager.root = self.root
        self.manager.backup_dir = self.backups
        self.manager.proc = None
        self.manager.process_lock = threading.RLock()
        self.manager._unmanaged_bds = lambda: []
        self.manager._append_log = lambda *args, **kwargs: None
        self.manager.server_version = "1.26.70.20"
        self.manager.stack_profiles = []
        self.manager.stack_profile_lock = threading.RLock()
        self.manager.stack_profile_path = base / "state" / "world_pack_profiles.json"
        self.token = "content-lab-test-token"
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
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"X-BDS-Token": self.token} if authenticated else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_content_lab_and_profiles_require_authentication(self):
        status, body = self.request("/api/content-lab?world=MainWorld", authenticated=False)
        self.assertEqual(status, 403)
        status, body = self.request("/api/content-profiles", authenticated=False)
        self.assertEqual(status, 403)
        status, body = self.request("/api/content-lab?world=MainWorld")
        self.assertEqual(status, 200)
        self.assertEqual(body["world"], "MainWorld")
        status, body = self.request("/api/content-profiles")
        self.assertEqual(status, 200)
        self.assertEqual(body, [])

    def test_profile_create_preview_apply_and_delete_http_workflow(self):
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Alpha")
        write_pack(self.root, "resource", RP_A, RP_A_MOD, "Resources")
        set_stack(self.world, "behavior", [BP_A])
        set_stack(self.world, "resource", [RP_A])
        status, created = self.request("/api/content-profiles", method="POST", payload={"action": "create", "world": "MainWorld", "name": "HTTP Deployment"})
        self.assertEqual(status, 200)
        profile_id = created["profile"]["id"]
        status, preview = self.request("/api/content-profiles", method="POST", payload={"action": "preview", "id": profile_id, "target_world": "MainWorld"})
        self.assertEqual(status, 200)
        self.assertTrue(preview["ready"])
        status, applied = self.request("/api/content-profiles", method="POST", payload={"action": "apply", "id": profile_id, "target_world": "MainWorld"})
        self.assertEqual(status, 200)
        self.assertTrue(applied["ok"])
        status, deleted = self.request("/api/content-profiles", method="POST", payload={"action": "delete", "id": profile_id})
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])

    def test_reorder_http_route_uses_server_stopped_guard(self):
        write_pack(self.root, "behavior", BP_A, BP_A_MOD, "Alpha")
        set_stack(self.world, "behavior", [BP_A])
        status, result = self.request("/api/content-lab/reorder", method="POST", payload={"world": "MainWorld", "kind": "behavior", "ordered_ids": [BP_A]})
        self.assertEqual(status, 200)
        self.assertFalse(result["changed"])
        class RunningProcess:
            def poll(self):
                return None
        self.manager.proc = RunningProcess()
        status, result = self.request("/api/content-lab/reorder", method="POST", payload={"world": "MainWorld", "kind": "behavior", "ordered_ids": [BP_A]})
        self.assertEqual(status, 400)
        self.assertIn("Stop the managed BDS server", result["error"])

    def test_backup_compare_http_route_only_returns_verified_comparison(self):
        first, second = self.backups / "BDS-http-a.zip", self.backups / "BDS-http-b.zip"
        write_backup(first, "MainWorld", {"server.properties": b"x", "worlds/MainWorld/level.dat": b"old"})
        write_backup(second, "MainWorld", {"server.properties": b"x", "worlds/MainWorld/level.dat": b"new"})
        status, result = self.request("/api/backups/compare", method="POST", payload={"first": first.name, "second": second.name})
        self.assertEqual(status, 200)
        self.assertEqual(result["counts"]["changed"], 1)


if __name__ == "__main__":
    unittest.main()
