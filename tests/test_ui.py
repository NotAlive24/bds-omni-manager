import re
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


class SidebarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.classes = {}
        self.parent_of = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        element_id = attrs.get("id")
        key = element_id or f"anon-{len(self.parent_of)}"
        self.classes[key] = classes
        self.parent_of[key] = self.stack[-1] if self.stack else None
        if tag not in {"input", "img", "br", "hr", "meta", "link"}:
            self.stack.append(key)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()


class UiRegressionTests(unittest.TestCase):
    def test_config_refresh_never_overwrites_dirty_path_drafts(self):
        self.assertIn("const configDrafts=new Map()", HTML)
        self.assertIn("function setConfigDraft(id,value)", HTML)
        self.assertIn("if(!el||dirtyConfigInputs.has(id))return", HTML)
        self.assertIn("el.addEventListener('input',()=>setConfigDraft(id,el.value))", HTML)
        self.assertIn("syncConfigInput('serverDirInput',s.server_dir)", HTML)
        self.assertIn("syncConfigInput('backupDir',s.backup_dir)", HTML)
        self.assertIn("function resetConfigInput(id)", HTML)
        self.assertIn("function pickDirectory(inputId)", HTML)

    def test_path_inputs_support_paste_browse_and_reset(self):
        self.assertIn('id="serverDirInput" type="text" spellcheck="false" autocomplete="off"', HTML)
        self.assertIn('id="backupDir" type="text" spellcheck="false" autocomplete="off"', HTML)
        self.assertIn("pickDirectory('serverDirInput')", HTML)
        self.assertIn("pickDirectory('backupDir')", HTML)
        self.assertIn("resetConfigInput('serverDirInput')", HTML)
        self.assertIn("resetConfigInput('backupDir')", HTML)
        self.assertIn("Folder selected. Save to apply the new path.", HTML)

    def test_settings_are_pinned_and_navigation_is_compact(self):
        parser = SidebarParser()
        parser.feed(HTML)
        sidebar_scroll = parser.classes["sidebarScroll"]
        self.assertIn("sidebar-scroll", sidebar_scroll)
        self.assertIn('data-page="settings"', HTML)
        self.assertIn(".sidebar-app{padding:9px 0 8px", HTML)
        self.assertIn(".nav button{min-height:35px", HTML)
        self.assertIn("overflow-y:auto", HTML)

    def test_refresh_poll_is_visibility_aware_and_non_overlapping(self):
        self.assertIn("let stateRefreshRunning=false", HTML)
        self.assertIn("if(stateRefreshRunning||document.visibilityState==='hidden')return", HTML)
        self.assertIn("setInterval(safeRefreshState,3500)", HTML)

    def test_ui_has_consistent_dark_visual_system(self):
        self.assertIn("--bg:#000", HTML)
        self.assertIn("--radius:14px", HTML)
        self.assertIn(".page-head h1{font-size:25px", HTML)
        self.assertIn("prefers-reduced-motion:reduce", HTML)
        self.assertIn(":focus-visible", HTML)

    def test_command_studio_has_preview_before_execution(self):
        self.assertIn('id="guidedCommandSelect"', HTML)
        self.assertIn('id="guidedPreviewPanel"', HTML)
        self.assertIn("/api/command/preview", HTML)
        self.assertIn("async function runGuidedCommand()", HTML)
        self.assertIn("Preview does not execute a command.", HTML)
        self.assertIn("function invalidateGuidedPreview()", HTML)
        self.assertIn("guidedRequestId", HTML)
        self.assertIn("guidedValidationPending", HTML)


    def test_command_studio_styles_every_generated_control_type(self):
        self.assertIn(".guided-picker select{", HTML)
        self.assertIn(".guided-field input,.guided-field select,.guided-field textarea{", HTML)
        self.assertIn(".guided-field select option,.guided-field select optgroup{", HTML)
        self.assertIn(".guided-field textarea{min-height:82px", HTML)
        self.assertIn(".guided-field input[type=number]", HTML)
        self.assertIn(".guided-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))", HTML)
        self.assertIn("@media(max-width:560px){.guided-studio", HTML)

    def test_guided_studio_has_accessible_labels_and_empty_state(self):
        self.assertIn('aria-labelledby="guidedStudioTitle"', HTML)
        self.assertIn('for="guidedCommandSelect"', HTML)
        self.assertIn('id="guidedSelectedTitle"', HTML)
        self.assertIn('id="guidedFieldMeta"', HTML)
        self.assertIn('class="guided-empty"', HTML)
        self.assertIn('aria-live="polite"', HTML)

    def test_guided_preview_is_invalidated_when_arguments_change(self):
        self.assertIn("$('guidedFields').addEventListener('input',invalidateGuidedPreview)", HTML)
        self.assertIn("$('guidedFields').addEventListener('change',invalidateGuidedPreview)", HTML)
        self.assertIn("Arguments changed. Preview the command again before sending.", HTML)
        self.assertIn("JSON.stringify(currentValues)!==JSON.stringify(guidedPreview.values)", HTML)

    def test_application_version_is_updated(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION = "2.0.0"', main_source)

    def test_content_intelligence_workspace_and_navigation_exist(self):
        for value in (
            'data-page="contentlab"', 'id="page-contentlab"', 'id="labWorldSelect"',
            'id="labGraph"', 'id="labFindings"', 'id="labBehaviorStack"',
            'id="labResourceStack"', 'id="labProfiles"', 'id="labBackupDiff"',
            'id="labCompareButton"',
        ):
            self.assertIn(value, HTML)
        self.assertIn("contentlab:'Content Intelligence'", HTML)
        self.assertIn("if(page==='contentlab')loadContentLab()", HTML)
        self.assertIn("/api/content-lab", HTML)
        self.assertIn("/api/content-profiles", HTML)
        self.assertIn("/api/backups/compare", HTML)

    def test_content_intelligence_elements_have_explicit_oled_styles(self):
        for selector in (
            ".lab-stat-grid", ".lab-stat", ".lab-panel", ".lab-graph-nodes",
            ".lab-graph-node", ".lab-edge", ".lab-finding", ".lab-stack-row",
            ".lab-order-actions .btn", ".lab-profile-row", ".lab-profile-preview",
            ".lab-diff-summary", ".lab-diff-group", ".lab-empty", ".lab-note",
            ".lab-form-grid input", ".lab-form-grid select",
            ".lab-profile-actions .btn:disabled", ".lab-order-actions .btn:disabled",
        ):
            self.assertIn(selector, HTML)
        self.assertIn("color-scheme:dark", HTML)
        self.assertIn("@media(max-width:760px)", HTML)
        self.assertIn("@media(max-width:440px)", HTML)

    def test_content_intelligence_has_safe_preview_apply_and_profile_actions(self):
        self.assertIn("async function moveContentPack", HTML)
        self.assertIn("async function saveContentProfile", HTML)
        self.assertIn("async function handleContentProfile", HTML)
        self.assertIn("async function compareContentBackups", HTML)
        self.assertIn("preview_stack_profile", (ROOT / "main.py").read_text(encoding="utf-8"))
        self.assertIn("Apply profile", HTML)
        self.assertIn("restart BDS", HTML)

    def test_properties_have_search_filter(self):
        self.assertIn('id="propertySearch"', HTML)
        self.assertIn("function filterProperties()", HTML)
        self.assertIn("filterProperties()", HTML)

    def test_udp_mapping_property_uses_a_styled_multiline_editor(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"key": "server-udp-ports"', main_source)
        self.assertIn('"type": "multiline"', main_source)
        self.assertIn("p.type==='multiline'", HTML)
        self.assertIn("<textarea data-prop=", HTML)
        self.assertIn(".prop-item input,.prop-item select,.prop-item textarea", HTML)
        self.assertIn(".prop-item textarea{", HTML)
        self.assertIn("one mapping per line", main_source)

    def test_weather_duration_is_labelled_in_ticks(self):
        self.assertIn("Weather duration (ticks, optional)", HTML)
        self.assertIn("20 game ticks = about 1 second.", HTML)


    def test_monitoring_page_has_metrics_health_and_task_controls(self):
        for value in (
            'data-page="monitor"', 'id="page-monitor"', 'id="metricCpu"',
            'id="metricMemory"', 'id="metricBdsMemory"', 'id="metricDisk"',
            'id="healthChecks"', 'id="scheduledJobList"', 'id="scheduledJobHistory"',
            'id="scheduleTime"', 'id="scheduleKind"', 'id="scheduleDays"',
        ):
            self.assertIn(value, HTML)
        self.assertIn("/api/metrics", HTML)
        self.assertIn("/api/health", HTML)
        self.assertIn("/api/schedules", HTML)

    def test_monitoring_generated_controls_have_explicit_oled_styles(self):
        for selector in (".schedule-form-grid .field input", ".schedule-form-grid .field select", ".schedule-form-grid .field textarea", ".weekday-picker label", ".metric-chart", ".health-row", ".scheduled-job"):
            self.assertIn(selector, HTML)
        self.assertIn("color-scheme:dark", HTML)
        self.assertIn("function drawMetricChart", HTML)
        self.assertIn("function renderMetrics", HTML)

    def test_monitoring_poll_is_visibility_aware(self):
        self.assertIn("$('page-monitor').classList.contains('active'))loadMonitoring()", HTML)
        self.assertIn("async function loadMonitoring()", HTML)

    def test_world_and_content_management_controls_have_explicit_oled_styles(self):
        for element_id in (
            "worldImportFile", "worldImportName", "worldCreateName", "packImportFile",
            "packInstallScope", "packInstallWorld", "worldList", "worldActionEditor",
            "worldDetailPanel", "worldBehaviorPackList", "worldResourcePackList",
            "behaviorPacks", "resourcePacks",
        ):
            self.assertIn(f'id="{element_id}"', HTML)
        for selector in (
            ".content-panel .field input:not([type=file])", ".content-panel .field select",
            ".content-panel .field textarea", ".content-file-input::file-selector-button",
            ".world-card", ".content-pack-card", ".world-pack-toggle.invalid",
            ".world-pack-error", ".content-pack-actions .btn:disabled",
        ):
            self.assertIn(selector, HTML)
        self.assertIn("const duplicate=!!p.duplicate_uuid;const blocked=duplicate||(!p.valid&&!p.enabled)", HTML)
        self.assertIn("Disable this active reference to keep the world from loading it.", HTML)
        self.assertIn("Resolve duplicate UUID or manifest issues before changing this pack reference", HTML)
        self.assertIn("function renderWorldPackToggles", HTML)


    def test_dedicated_mods_page_and_navigation_exist(self):
        for value in (
            'data-page="mods"', 'id="page-mods"', 'id="modsSummary"',
            'id="modImportFile"', 'id="modInstallScope"', 'id="modInstallWorld"',
            'id="modsSearch"', 'id="modsTypeFilter"', 'id="modsHealthFilter"',
            'id="modsTargetWorld"', 'id="modsList"',
        ):
            self.assertIn(value, HTML)
        self.assertIn("mods:'Mods & Add-ons'", HTML)
        self.assertIn("if(page==='mods')loadMods()", HTML)
        self.assertIn("/api/mods", HTML)
        self.assertIn("/api/mods/import", HTML)

    def test_mods_ui_has_explicit_oled_styles_and_bedrock_format_guidance(self):
        for selector in (
            ".mods-panel .field input:not([type=file])", ".mods-panel .field select",
            ".mods-panel .mods-file-input::file-selector-button", ".mods-stat",
            ".mod-card", ".mod-icon", ".mod-status", ".mod-meta-strip",
            ".mod-details summary", ".mod-action-buttons .btn:disabled",
            ".mods-empty", ".mods-warning",
        ):
            self.assertIn(selector, HTML)
        self.assertIn('accept=".mcaddon,.mcpack,.zip,application/zip"', HTML)
        self.assertIn('Java <code>.jar</code> mods and Fabric/Forge loaders do not run on Bedrock Dedicated Server.', HTML)
        self.assertIn("function importModFile()", HTML)
        self.assertIn("function renderMods()", HTML)
        self.assertIn("function handleModAction(action,locationId)", HTML)
        self.assertIn("function syncModScope()", HTML)
        self.assertIn("function contentMutationsAllowed(){return !!appState", HTML)
        self.assertIn("canDisable=mutable&&!!selectedWorld&&canTarget&&enabled&&!duplicate", HTML)
        self.assertIn("const duplicate=!!p.duplicate_uuid", HTML)

    def test_inline_javascript_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        scripts = re.findall(r"<script>(.*?)</script>", HTML, flags=re.S)
        self.assertTrue(scripts)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as tmp:
            tmp.write("\n".join(scripts))
            tmp.flush()
            result = subprocess.run([node, "--check", tmp.name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_nethernet_tunnel_panel_has_complete_navigation_and_controls(self):
        self.assertIn('data-page="tunnel"', HTML)
        self.assertIn('id="page-tunnel"', HTML)
        for element_id in (
            "tunnelHealthBadge", "tunnelClientVersion", "tunnelServiceState",
            "tunnelTcpTarget", "tunnelDiagnostics", "tunnelLogs",
            "tunnelStartWithBds", "tunnelStopWithBds", "tunnelBdsUdpValue", "tunnelBdsBind",
        ):
            self.assertIn(f'id="{element_id}"', HTML)
        external_count_decl = HTML.index("const externalClientCount=")
        external_count_use = HTML.index("if(externalClientCount)")
        self.assertLess(external_count_decl, external_count_use)
        for action in ("start", "stop", "restart", "enable", "disable"):
            self.assertIn(f"tunnelAction('{action}')", HTML)
        self.assertIn("/api/tunnel/state", (ROOT / "main.py").read_text(encoding="utf-8"))
        self.assertIn("/api/tunnel/action", (ROOT / "main.py").read_text(encoding="utf-8"))


    def test_bedrock_node_dashboard_config_import_has_preview_confirmation_and_styles(self):
        for element_id in ("tunnelConfigText", "tunnelConfigPreview", "tunnelConfigPreviewBtn", "tunnelConfigApplyBtn"):
            self.assertIn(f'id="{element_id}"', HTML)
        self.assertIn("/api/tunnel/config/preview", HTML)
        self.assertIn("/api/tunnel/config/import", HTML)
        self.assertIn("tunnelConfigRequestId", HTML)
        self.assertIn("function renderTunnelConfigPreview(d)", HTML)
        self.assertIn("async function applyTunnelConfig()", HTML)
        self.assertIn(".tunnel-config-input:focus", HTML)
        self.assertIn(".tunnel-config-preview table", HTML)
        self.assertIn("Only networking-related keys are accepted", HTML)

    def test_tunnel_controls_have_explicit_oled_styles_and_responsive_layout(self):
        for selector in (
            ".tunnel-action-grid .btn", ".tunnel-toggle input[type=checkbox]",
            ".tunnel-log", ".tunnel-diagnostic", ".tunnel-network-grid",
            ".tunnel-links a", ".tunnel-stat-value",
        ):
            self.assertIn(selector, HTML)
        self.assertIn("@media(max-width:1050px){.tunnel-layout", HTML)
        self.assertIn("@media(max-width:560px){.tunnel-stats", HTML)
        self.assertIn("BedrockNodeTunnel systemd user service", HTML)

    def test_tunnel_ui_links_to_upstream_project_and_dashboard(self):
        self.assertIn('href="https://github.com/NotAlive24/BedrockNodeTunnel"', HTML)
        self.assertIn('href="https://bedrocknode.net/"', HTML)
        self.assertIn("The manager never downloads or replaces the tunnel binary", HTML)


if __name__ == "__main__":
    unittest.main()
