#!/usr/bin/env python3
"""BDS Omni Manager - local-only web GUI for Minecraft Bedrock Dedicated Server.

Python standard-library only. Intended for Linux (Arch Linux tested by design).
"""
from __future__ import annotations

import json
import os
import hashlib
import ipaddress
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import tempfile
import zipfile
import uuid as uuidlib
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse, parse_qs, unquote, quote

APP_NAME = "BDS Omni Manager"
APP_VERSION = "2.0.1"
HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "bds-omni-manager"
STATE_DIR = HOME / ".local" / "state" / "bds-omni-manager"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = STATE_DIR / "manager.log"
SCHEDULE_FILE = STATE_DIR / "scheduled_jobs.json"
STACK_PROFILE_FILE = STATE_DIR / "world_pack_profiles.json"
DEFAULT_BACKUPS = HOME / "BDS-Backups"
MAX_CONTENT_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024
MAX_CONTENT_EXTRACTED_BYTES = 32 * 1024 * 1024 * 1024
MAX_CONTENT_ARCHIVE_ENTRIES = 150_000

# Command names and descriptions follow the official Bedrock command index.
COMMANDS: dict[str, dict[str, str]] = {
    "World & environment": {
        "daylock": "Locks or unlocks the day/night cycle.",
        "difficulty": "Changes world difficulty.",
        "gamerule": "Queries or changes a world game rule.",
        "mobevent": "Enables or disables a world mob event.",
        "time": "Queries or changes world game time.",
        "toggledownfall": "Toggles weather.",
        "weather": "Sets clear, rain, or thunder weather.",
        "tickingarea": "Adds, removes, lists, or preloads ticking areas.",
        "setworldspawn": "Sets the world spawn location.",
        "spawnpoint": "Sets a player's spawn point.",
        "clearspawnpoint": "Removes a player's spawn point.",
        "locate": "Finds a nearby biome or structure.",
        "setblock": "Changes a block at a coordinate.",
        "fill": "Fills a region with blocks.",
        "clone": "Copies a block region to another location.",
        "spreadplayers": "Spreads entities across positions.",
        "place": "Places a feature, feature rule, or jigsaw structure.",
        "structure": "Saves, loads, or deletes structures.",
    },
    "Players & entities": {
        "aimassist": "Configures aim assist for a player.",
        "camera": "Changes a player's camera perspective.",
        "camerashake": "Adds or stops camera shake.",
        "clear": "Clears items from player inventory.",
        "damage": "Applies damage to selected entities.",
        "effect": "Adds or clears status effects.",
        "enchant": "Enchants the selected item.",
        "gamemode": "Changes a player's game mode.",
        "give": "Gives an item to a player.",
        "hud": "Configures player HUD elements.",
        "inputpermission": "Enables or disables player input types.",
        "kick": "Removes a player from the server.",
        "kill": "Removes selected entities.",
        "op": "Grants operator permission.",
        "deop": "Revokes operator permission.",
        "permission": "Reloads and applies dedicated-server permissions.",
        "replaceitem": "Replaces an inventory or entity slot item.",
        "ride": "Manages riding relationships between entities.",
        "teleport": "Teleports players or entities; alias: tp.",
        "tag": "Adds, removes, or lists entity tags.",
        "xp": "Adds or removes player experience.",
        "recipe": "Unlocks or locks recipes for a player.",
        "summon": "Summons an entity.",
        "event": "Triggers an entity event.",
        "playanimation": "Plays an animation on selected entities.",
        "testfor": "Counts entities matching a selector.",
        "testforblock": "Tests a block at a coordinate.",
        "testforblocks": "Compares two block regions.",
    },
    "Server & access": {
        "allowlist": "Manages the dedicated-server allowlist.",
        "changesetting": "Changes a supported server setting at runtime.",
        "help": "Shows command help or syntax.",
        "list": "Lists connected players.",
        "packstack": "Prints active client/server pack stacks.",
        "reload": "Reloads function and script files from behavior packs.",
        "reloadconfig": "Reloads server configuration files.",
        "reloadpacketlimitconfig": "Reloads packet-limit configuration.",
        "save": "Queries, holds, or resumes world saving.",
        "setmaxplayers": "Changes the session player limit.",
        "stop": "Stops the dedicated server.",
        "transfer": "Transfers a player to another server.",
        "wsserver": "Connects to a WebSocket server.",
        "sendshowstoreoffer": "Opens a Marketplace offer for a player.",
    },
    "Chat & communication": {
        "dialogue": "Opens NPC dialogue for a player.",
        "me": "Sends an action-style message.",
        "say": "Broadcasts a server message.",
        "tell": "Sends a private message.",
        "tellraw": "Sends a raw JSON message.",
        "title": "Displays a title, subtitle, or action bar.",
        "titleraw": "Displays a raw JSON title message.",
    },
    "Logic, functions & scripting": {
        "execute": "Runs a command with a modified executor or context.",
        "function": "Runs a function from a behavior pack.",
        "schedule": "Schedules a function to run later or when an area loads.",
        "scoreboard": "Manages objectives and player scores.",
        "gametest": "Manages GameTest tests (requires the relevant experiment/API).",
        "script": "Runs script debugging commands.",
        "scriptevent": "Fires a script event with a message ID and payload.",
        "project": "Provides Editor-mode project tools.",
    },
    "Audio & visuals": {
        "fog": "Adds, removes, or lists player fog settings.",
        "music": "Controls in-game music playback.",
        "particle": "Creates a particle emitter.",
        "playsound": "Plays a sound for selected players.",
        "stopsound": "Stops sounds for selected players.",
        "loot": "Generates loot into an inventory or into the world.",
        "controlscheme": "Sets or clears a control scheme.",
    },
}


# Guided command forms are deliberately a subset of the full BDS command
# surface. Every form is validated server-side before it becomes a command;
# the raw console remains available for commands and syntax added by newer BDS
# builds or behavior packs.
COMMAND_FORMS: list[dict[str, Any]] = [
    {"id": "gamemode", "label": "Game mode", "group": "Players", "description": "Change one or more players' game mode.", "cheats": True, "confirm": False, "fields": [
        {"id": "mode", "label": "Mode", "type": "select", "options": ["survival", "creative", "adventure", "spectator"], "default": "survival"},
        {"id": "target", "label": "Player / selector", "type": "target", "default": "@a", "required": True},
    ]},
    {"id": "difficulty", "label": "Difficulty", "group": "World", "description": "Set world difficulty.", "cheats": True, "confirm": False, "fields": [
        {"id": "difficulty", "label": "Difficulty", "type": "select", "options": ["peaceful", "easy", "normal", "hard"], "default": "normal"},
    ]},
    {"id": "time", "label": "Time", "group": "World", "description": "Set, add or query world time.", "cheats": True, "confirm": False, "fields": [
        {"id": "action", "label": "Action", "type": "select", "options": ["set", "add", "query"], "default": "set"},
        {"id": "value", "label": "Value / query type", "type": "text", "default": "day", "help": "Set: day, noon, sunset, night, midnight, sunrise or ticks. Add: ticks. Query: daytime, day or gametime."},
    ]},
    {"id": "weather", "label": "Weather", "group": "World", "description": "Change weather with an optional duration in ticks.", "cheats": True, "confirm": False, "fields": [
        {"id": "weather", "label": "Weather", "type": "select", "options": ["clear", "rain", "thunder"], "default": "clear"},
        {"id": "duration", "label": "Duration (ticks, optional)", "type": "number", "min": 1, "max": 1000000, "placeholder": "600"},
    ]},
    {"id": "gamerule", "label": "Game rule", "group": "World", "description": "Query a rule or set its value.", "cheats": True, "confirm": False, "fields": [
        {"id": "rule", "label": "Rule name", "type": "text", "required": True, "placeholder": "doDaylightCycle"},
        {"id": "value", "label": "Value (leave blank to query)", "type": "text", "placeholder": "true / false / number"},
    ]},
    {"id": "say", "label": "Broadcast message", "group": "Communication", "description": "Send a message to everyone.", "cheats": False, "confirm": False, "fields": [
        {"id": "message", "label": "Message", "type": "textarea", "required": True, "placeholder": "Server announcement"},
    ]},
    {"id": "tell", "label": "Private message", "group": "Communication", "description": "Send a message to one player.", "cheats": False, "confirm": False, "fields": [
        {"id": "target", "label": "Player", "type": "target", "required": True, "placeholder": "Gamertag"},
        {"id": "message", "label": "Message", "type": "textarea", "required": True},
    ]},
    {"id": "title", "label": "Title / action bar", "group": "Communication", "description": "Display a title, subtitle or action-bar message.", "cheats": True, "confirm": False, "fields": [
        {"id": "target", "label": "Player / selector", "type": "target", "required": True, "default": "@a"},
        {"id": "kind", "label": "Display type", "type": "select", "options": ["title", "subtitle", "actionbar"], "default": "title"},
        {"id": "message", "label": "Text", "type": "textarea", "required": True},
    ]},
    {"id": "teleport", "label": "Teleport", "group": "Players", "description": "Teleport a target to coordinates.", "cheats": True, "confirm": True, "fields": [
        {"id": "target", "label": "Player / selector", "type": "target", "required": True, "default": "@s"},
        {"id": "position", "label": "Destination X Y Z", "type": "coords", "required": True, "default": "0 64 0"},
        {"id": "check", "label": "Check destination for blocks", "type": "bool", "default": "false"},
    ]},
    {"id": "give", "label": "Give item", "group": "Players", "description": "Give an item stack to a player.", "cheats": True, "confirm": False, "fields": [
        {"id": "target", "label": "Player / selector", "type": "target", "required": True, "default": "@s"},
        {"id": "item", "label": "Item identifier", "type": "identifier", "required": True, "placeholder": "minecraft:diamond"},
        {"id": "amount", "label": "Amount", "type": "number", "default": "1", "min": 1, "max": 32767},
    ]},
    {"id": "effect", "label": "Status effect", "group": "Players", "description": "Apply or clear a player effect.", "cheats": True, "confirm": False, "fields": [
        {"id": "target", "label": "Player / selector", "type": "target", "required": True, "default": "@s"},
        {"id": "action", "label": "Action", "type": "select", "options": ["give", "clear"], "default": "give"},
        {"id": "effect", "label": "Effect identifier", "type": "identifier", "default": "speed", "placeholder": "speed"},
        {"id": "duration", "label": "Duration (seconds)", "type": "number", "default": "60", "min": 1, "max": 1000000},
        {"id": "amplifier", "label": "Amplifier (0 = level I)", "type": "number", "default": "0", "min": 0, "max": 255},
        {"id": "hide", "label": "Hide particles", "type": "bool", "default": "false"},
    ]},
    {"id": "xp", "label": "Experience", "group": "Players", "description": "Give a player experience points or levels.", "cheats": True, "confirm": False, "fields": [
        {"id": "target", "label": "Player / selector", "type": "target", "required": True, "default": "@s"},
        {"id": "mode", "label": "Action", "type": "select", "options": ["add", "remove"], "default": "add"},
        {"id": "amount", "label": "Amount", "type": "number", "required": True, "default": "1", "min": 1, "max": 1000000},
    ]},
    {"id": "summon", "label": "Summon entity", "group": "World editing", "description": "Summon an entity at a position.", "cheats": True, "confirm": True, "fields": [
        {"id": "entity", "label": "Entity identifier", "type": "identifier", "required": True, "placeholder": "minecraft:chicken"},
        {"id": "position", "label": "Position X Y Z (optional)", "type": "coords", "placeholder": "~ ~ ~"},
    ]},
    {"id": "setblock", "label": "Set block", "group": "World editing", "description": "Change one block at a position.", "cheats": True, "confirm": True, "fields": [
        {"id": "position", "label": "Position X Y Z", "type": "coords", "required": True, "default": "~ ~-1 ~"},
        {"id": "block", "label": "Block identifier", "type": "identifier", "required": True, "placeholder": "minecraft:stone"},
        {"id": "mode", "label": "Existing block handling", "type": "select", "options": ["replace", "destroy", "keep"], "default": "replace"},
    ]},
    {"id": "fill", "label": "Fill region", "group": "World editing", "description": "Fill a cuboid region with a block. Review coordinates before sending.", "cheats": True, "confirm": True, "fields": [
        {"id": "from", "label": "From X Y Z", "type": "coords", "required": True, "default": "~ ~ ~"},
        {"id": "to", "label": "To X Y Z", "type": "coords", "required": True, "default": "~5 ~5 ~5"},
        {"id": "block", "label": "Block identifier", "type": "identifier", "required": True, "placeholder": "minecraft:stone"},
        {"id": "mode", "label": "Fill mode", "type": "select", "options": ["replace", "destroy", "hollow", "keep", "outline"], "default": "replace"},
    ]},
    {"id": "clone", "label": "Clone region", "group": "World editing", "description": "Copy a cuboid region to a destination. Move mode clears the original region.", "cheats": True, "confirm": True, "fields": [
        {"id": "begin", "label": "Begin X Y Z", "type": "coords", "required": True, "default": "0 64 0"},
        {"id": "end", "label": "End X Y Z", "type": "coords", "required": True, "default": "10 70 10"},
        {"id": "destination", "label": "Destination X Y Z", "type": "coords", "required": True, "default": "20 64 0"},
        {"id": "mask", "label": "Mask mode", "type": "select", "options": ["replace", "masked"], "default": "replace"},
        {"id": "clone_mode", "label": "Clone mode", "type": "select", "options": ["normal", "force", "move"], "default": "normal"},
    ]},
    {"id": "clearspawnpoint", "label": "Clear player spawn", "group": "Players", "description": "Remove a player's individual spawn point.", "cheats": True, "confirm": False, "fields": [
        {"id": "target", "label": "Player / selector", "type": "target", "required": True, "default": "@s"},
    ]},
    {"id": "tag", "label": "Entity tags", "group": "Players & entities", "description": "Add, remove or list tags on an entity selector.", "cheats": True, "confirm": False, "fields": [
        {"id": "target", "label": "Target selector", "type": "target", "required": True, "default": "@s"},
        {"id": "action", "label": "Action", "type": "select", "options": ["add", "remove", "list"], "default": "add"},
        {"id": "tag", "label": "Tag (not needed for list)", "type": "text", "default": "managed", "placeholder": "my_tag", "help": "Used for add/remove; ignored by list."},
    ]},
    {"id": "kick", "label": "Kick player", "group": "Players", "description": "Disconnect a player with an optional reason.", "cheats": False, "confirm": True, "fields": [
        {"id": "target", "label": "Player", "type": "target", "required": True},
        {"id": "reason", "label": "Reason (optional)", "type": "textarea", "placeholder": "Server maintenance"},
    ]},
    {"id": "op", "label": "Grant operator", "group": "Players & access", "description": "Grant operator permission to a player.", "cheats": False, "confirm": True, "fields": [
        {"id": "target", "label": "Player gamertag", "type": "target", "required": True},
    ]},
    {"id": "deop", "label": "Remove operator", "group": "Players & access", "description": "Remove operator permission from a player.", "cheats": False, "confirm": True, "fields": [
        {"id": "target", "label": "Player gamertag", "type": "target", "required": True},
    ]},
    {"id": "setworldspawn", "label": "Set world spawn", "group": "World", "description": "Set the world's default spawn point.", "cheats": True, "confirm": False, "fields": [
        {"id": "position", "label": "Position X Y Z", "type": "coords", "required": True, "default": "0 64 0"},
    ]},
    {"id": "spawnpoint", "label": "Set player spawn", "group": "World", "description": "Set an individual player's spawn point.", "cheats": True, "confirm": False, "fields": [
        {"id": "target", "label": "Player / selector", "type": "target", "required": True, "default": "@s"},
        {"id": "position", "label": "Position X Y Z", "type": "coords", "required": True, "default": "~ ~ ~"},
    ]},
    {"id": "mobevent", "label": "Mob event", "group": "World", "description": "Enable or disable a world mob event.", "cheats": True, "confirm": False, "fields": [
        {"id": "event", "label": "Event identifier", "type": "identifier", "required": True, "default": "minecraft:pillager_patrols_event"},
        {"id": "enabled", "label": "Enabled", "type": "bool", "default": "true"},
    ]},
    {"id": "clear", "label": "Clear inventory", "group": "Players", "description": "Clear items from a player's inventory.", "cheats": True, "confirm": True, "fields": [
        {"id": "target", "label": "Player / selector", "type": "target", "required": True, "default": "@s"},
        {"id": "item", "label": "Item identifier (optional)", "type": "identifier", "placeholder": "minecraft:dirt"},
        {"id": "data", "label": "Item data (optional; -1 = any)", "type": "number", "min": -1, "max": 32767, "default": "-1"},
        {"id": "count", "label": "Maximum count (requires item)", "type": "number", "min": 1, "max": 32767},
    ]},
    {"id": "kill", "label": "Remove entities", "group": "Players & entities", "description": "Run the kill command on a target selector. Check the selector carefully.", "cheats": True, "confirm": True, "fields": [
        {"id": "target", "label": "Target selector", "type": "target", "required": True, "default": "@e[type=minecraft:item]"},
    ]},
    {"id": "playsound", "label": "Play sound", "group": "Audio & visuals", "description": "Play a sound for selected players.", "cheats": True, "confirm": False, "fields": [
        {"id": "sound", "label": "Sound identifier", "type": "identifier", "required": True, "placeholder": "random.pop"},
        {"id": "target", "label": "Player / selector", "type": "target", "default": "@a"},
        {"id": "position", "label": "Position X Y Z", "type": "coords", "default": "~ ~ ~", "required": True},
        {"id": "volume", "label": "Volume (0–1)", "type": "number", "default": "1", "min": 0, "max": 1},
        {"id": "pitch", "label": "Pitch (0–256)", "type": "number", "default": "1", "min": 0, "max": 256},
    ]},
]

# BDS properties documented by Microsoft Learn. Unknown/newer properties are
# still loaded and editable by the generic key/value editor.
PROPERTY_GROUPS: dict[str, list[dict[str, Any]]] = {
    "Identity & gameplay": [
        {"key": "server-name", "label": "Server name", "type": "text", "default": "Dedicated Server", "help": "Name displayed to players."},
        {"key": "gamemode", "label": "Default game mode", "type": "select", "options": ["survival", "creative", "adventure"], "default": "survival"},
        {"key": "force-gamemode", "label": "Force game mode", "type": "bool", "default": "false", "help": "Send the configured mode even when the saved world mode differs."},
        {"key": "difficulty", "label": "Difficulty", "type": "select", "options": ["peaceful", "easy", "normal", "hard"], "default": "easy"},
        {"key": "allow-cheats", "label": "Allow cheats", "type": "bool", "default": "false", "help": "Enables commands that require cheats. Changes normally need a restart."},
        {"key": "default-player-permission-level", "label": "Default player permission", "type": "select", "options": ["visitor", "member", "operator"], "default": "member"},
        {"key": "max-players", "label": "Maximum players", "type": "number", "default": "10", "min": 1},
        {"key": "player-idle-timeout", "label": "Idle timeout (minutes)", "type": "number", "default": "30", "min": 0, "help": "0 disables idle kicking."},
        {"key": "level-name", "label": "World folder name", "type": "text", "default": "Bedrock level", "help": "Must match a folder under worlds/."},
        {"key": "level-seed", "label": "World seed", "type": "text", "default": ""},
    ],
    "Network & privacy": [
        {"key": "transport", "label": "Bedrock transport", "type": "select", "options": ["nethernet"], "default": "nethernet", "help": "NetherNet is the transport used by current Bedrock clients and Bedrock Node Tunnel. Restart BDS after changing this setting."},
        {"key": "online-mode", "label": "Xbox Live authentication", "type": "bool", "default": "true", "help": "Keep enabled for internet-facing servers."},
        {"key": "allow-list", "label": "Enable allowlist", "type": "bool", "default": "false"},
        {"key": "server-ip", "label": "NetherNet bind address", "type": "text", "default": "", "help": "An IPv4 or IPv6 literal. Leave empty to bind all available interfaces. Verify this setting against the how-to file bundled with your BDS version."},
        {"key": "server-port", "label": "TCP signaling port", "type": "number", "default": "19132", "min": 1, "max": 65535},
        {"key": "server-portv6", "label": "IPv6 port (legacy transport)", "type": "number", "default": "19133", "min": 1, "max": 65535, "help": "Current NetherNet BDS builds use server-port for TCP signaling; server-portv6 may be ignored."},
        {"key": "server-udp-ports", "label": "NetherNet UDP gameplay port mapping", "type": "multiline", "default": "", "help": "Paste one UDP mapping per line. Some Bedrock builds/workarounds use repeated server-udp-ports entries; keep each dashboard-provided mapping on its own line. Do not guess public or internal ports."},
        {"key": "enable-lan-visibility", "label": "LAN discovery", "type": "bool", "default": "true"},
        {"key": "chat-restriction", "label": "Chat restriction", "type": "select", "options": ["None", "Dropped", "Disabled"], "default": "None"},
        {"key": "disable-custom-skins", "label": "Disable custom skins", "type": "bool", "default": "false"},
        {"key": "disable-player-interaction", "label": "Disable player interaction", "type": "bool", "default": "false"},
    ],
    "Performance & networking": [
        {"key": "view-distance", "label": "View distance (chunks)", "type": "number", "default": "32", "min": 5, "help": "Higher values increase memory and CPU use."},
        {"key": "tick-distance", "label": "Tick distance (chunks)", "type": "number", "default": "4", "min": 4, "max": 12},
        {"key": "max-threads", "label": "Maximum threads", "type": "number", "default": "8", "min": 0},
        {"key": "compression-threshold", "label": "Compression threshold", "type": "number", "default": "1", "min": 0, "max": 65535},
        {"key": "compression-algorithm", "label": "Compression algorithm", "type": "select", "options": ["zlib", "snappy"], "default": "zlib"},
        {"key": "client-side-chunk-generation-enabled", "label": "Client-side chunk generation", "type": "bool", "default": "true"},
        {"key": "server-build-radius-ratio", "label": "Server build radius ratio", "type": "text", "default": "Disabled", "help": "Disabled or a value from 0.0 to 1.0."},
        {"key": "block-network-ids-are-hashes", "label": "Hashed block network IDs", "type": "bool", "default": "true"},
        {"key": "texturepack-required", "label": "Require texture packs", "type": "bool", "default": "false"},
        {"key": "content-log-file-enabled", "label": "Content log file", "type": "bool", "default": "false"},
    ],
    "Movement & authoritative simulation": [
        {"key": "server-authoritative-movement-strict", "label": "Strict authoritative movement", "type": "bool", "default": "false"},
        {"key": "server-authoritative-dismount-strict", "label": "Strict authoritative dismount", "type": "bool", "default": "false"},
        {"key": "server-authoritative-entity-interactions-strict", "label": "Strict entity interactions", "type": "bool", "default": "false"},
        {"key": "player-position-acceptance-threshold", "label": "Position acceptance threshold", "type": "text", "default": "0.5"},
        {"key": "player-movement-action-direction-threshold", "label": "Movement direction threshold", "type": "text", "default": "0.85"},
        {"key": "server-authoritative-block-breaking", "label": "Authoritative block breaking", "type": "bool", "default": "false"},
        {"key": "server-authoritative-block-breaking-range-scalar", "label": "Block breaking range scalar", "type": "text", "default": "1.5"},
    ],
    "Scripting & diagnostics": [
        {"key": "allow-outbound-script-debugging", "label": "Allow outbound script debugging", "type": "bool", "default": "false"},
        {"key": "allow-inbound-script-debugging", "label": "Allow inbound script debugging", "type": "bool", "default": "false"},
        {"key": "force-inbound-debug-port", "label": "Inbound debug port", "type": "number", "default": "19144", "min": 1, "max": 65535},
        {"key": "script-debugger-auto-attach", "label": "Script debugger auto-attach", "type": "select", "options": ["disabled", "connect", "listen"], "default": "disabled"},
        {"key": "script-debugger-auto-attach-connect-address", "label": "Debugger connect address", "type": "text", "default": "localhost:19144"},
        {"key": "script-debugger-auto-attach-timeout", "label": "Debugger attach timeout", "type": "number", "default": "0", "min": 0},
        {"key": "script-debugger-passcode", "label": "Script debugger passcode", "type": "password", "default": "", "help": "Optional passcode requested by the script debugger when connecting."},
        {"key": "script-watchdog-enable", "label": "Script watchdog", "type": "bool", "default": "true"},
        {"key": "script-watchdog-enable-exception-handling", "label": "Watchdog exception handling", "type": "bool", "default": "true"},
        {"key": "script-watchdog-enable-shutdown", "label": "Watchdog shutdown on unhandled exception", "type": "bool", "default": "true"},
        {"key": "script-watchdog-hang-exception", "label": "Watchdog hang exception", "type": "bool", "default": "true"},
        {"key": "script-watchdog-hang-threshold", "label": "Watchdog hang threshold", "type": "number", "default": "10000", "min": 0},
        {"key": "script-watchdog-spike-threshold", "label": "Watchdog spike threshold", "type": "number", "default": "100", "min": 0},
        {"key": "script-watchdog-slow-threshold", "label": "Watchdog slow threshold", "type": "number", "default": "10", "min": 0},
        {"key": "script-watchdog-memory-warning", "label": "Watchdog memory warning (MB)", "type": "number", "default": "100", "min": 0, "max": 2000},
        {"key": "script-watchdog-memory-limit", "label": "Watchdog memory limit (MB)", "type": "number", "default": "250", "min": 0, "max": 2000},
        {"key": "diagnostics-capture-auto-start", "label": "Auto-start diagnostics capture", "type": "bool", "default": "false"},
        {"key": "diagnostics-capture-max-files", "label": "Maximum diagnostic files", "type": "number", "default": "5", "min": 0},
        {"key": "diagnostics-capture-max-file-size", "label": "Maximum diagnostic file size (bytes)", "type": "number", "default": "2097152", "min": 1},
        {"key": "disable-client-vibrant-visuals", "label": "Disable Vibrant Visuals on clients", "type": "bool", "default": "true"},
    ],
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_json_read(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        original_mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        original_mode = None
    with temp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if original_mode is not None:
        os.chmod(temp, original_mode)
    os.replace(temp, path)


def parse_bds_version(line: str) -> str | None:
    """Extract the version banner emitted by the official BDS process.

    Do not parse arbitrary persisted manager log history: old log lines can
    belong to a different server directory or an older binary.
    """
    patterns = (
        r"\bVersion\s*[:=]\s*v?(\d+(?:\.\d+){1,3})\b",
        r"\bBedrock Dedicated Server(?:\s+version)?\s*[:=]?\s*v?(\d+(?:\.\d+){1,3})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def parse_properties(text: str) -> dict[str, str]:
    """Parse BDS key/value lines while preserving repeated UDP mapping entries.

    Most server.properties keys are scalar. NetherNet's server-udp-ports key
    may be repeated on affected builds/workarounds, so its entries are exposed
    to the editor as newline-separated values instead of silently collapsing
    them to only the last mapping.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if key == "server-udp-ports" and key in result:
            result[key] += "\n" + value
        else:
            result[key] = value
    return result


def parse_bedrock_node_property_lines(text: str) -> dict[str, str]:
    """Parse the network-only properties copied from the Bedrock Node dashboard.

    The dashboard supplies four server.properties lines. Their exact values
    are account-specific, so this parser deliberately imports the user's
    pasted lines instead of guessing public ports or mappings.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Paste the four networking lines from your Bedrock Node dashboard first.")
    if len(text.encode("utf-8")) > 16_384:
        raise ValueError("The pasted configuration is too large (16 KB maximum).")
    values: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if any(ord(char) < 32 for char in line):
            raise ValueError(f"Line {line_number} contains a control character.")
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key or (not value and key != "server-ip"):
            raise ValueError(f"Line {line_number} must use key=value. Only server-ip may be empty (empty means bind all interfaces).")
        if not re.fullmatch(r"[a-z0-9._-]{1,100}", key):
            raise ValueError(f"Line {line_number} has an invalid property key: {key!r}.")
        is_network_key = (
            key in {"transport", "server-ip", "server-port", "server-portv6"}
            or key.startswith("server-udp-")
            or key.startswith("nethernet-")
        )
        if not is_network_key:
            raise ValueError(f"{key} is not a recognized network property. Only transport, server port, UDP, and nethernet settings can be imported here.")
        if key in values and key != "server-udp-ports":
            raise ValueError(f"Duplicate property on line {line_number}: {key}.")
        if any(ord(char) < 32 for char in value) or "\n" in value or "\r" in value:
            raise ValueError(f"Value for {key} contains an invalid control character.")
        if key == "transport" and value.lower() != "nethernet":
            raise ValueError("Bedrock Node Tunnel requires transport=nethernet. Check the value copied from the dashboard.")
        if key in {"server-port", "server-portv6"}:
            try:
                port = int(value)
            except ValueError:
                raise ValueError(f"{key} must be a whole-number TCP port.")
            if not 1 <= port <= 65535:
                raise ValueError(f"{key} must be between 1 and 65535.")
        if key == "server-ip" and value:
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise ValueError("server-ip must be an IPv4/IPv6 literal or empty to bind all interfaces.")
        if key == "server-udp-ports" and key in values:
            udp_entries = values[key].splitlines() + [value]
            if len(udp_entries) > 32:
                raise ValueError("At most 32 separate server-udp-ports mappings can be imported at once.")
            values[key] = "\n".join(udp_entries)
        else:
            values[key] = value
        if len(values) > 8:
            raise ValueError("The dashboard import contains more than 8 distinct properties. Check that you pasted only its networking settings.")
    if not values:
        raise ValueError("No networking properties were found in the pasted text.")
    return values


def merge_properties(original: str, updates: dict[str, str]) -> str:
    """Merge validated values while preserving comments and UDP mapping rows."""
    original_keys = parse_properties(original)
    seen: set[str] = set()
    out: list[str] = []

    def render_updates(key: str, value: str) -> list[str]:
        if key == "server-udp-ports":
            entries = [item.strip() for item in str(value).splitlines() if item.strip()]
            return [f"{key}={entry}" for entry in entries] or [f"{key}="]
        return [f"{key}={value}"]

    for line in original.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                if key == "server-udp-ports":
                    if key not in seen:
                        out.extend(render_updates(key, updates[key]))
                        seen.add(key)
                    # Replace all original entries with the reviewed list once.
                    continue
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen and key not in original_keys:
            out.extend(render_updates(key, value))
    return "\n".join(out).rstrip() + "\n"


def choose_directory_dialog(initial: str | Path, title: str) -> Path | None:
    """Open an optional native Linux directory chooser (zenity, kdialog, or yad).

    Returns None when the user cancels. No shell is used, and the selected path
    is returned only if it names an existing directory.
    """
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise ValueError("No graphical desktop session was detected. You can still paste or type the folder path.")

    initial_path = Path(initial).expanduser()
    if not initial_path.is_dir():
        initial_path = HOME

    command: list[str] | None = None
    zenity = shutil.which("zenity")
    kdialog = shutil.which("kdialog")
    yad = shutil.which("yad")
    if zenity:
        command = [zenity, "--file-selection", "--directory", "--title", title, "--filename", str(initial_path) + "/"]
    elif kdialog:
        command = [kdialog, "--getexistingdirectory", str(initial_path), title]
    elif yad:
        command = [yad, "--file-selection", "--directory", "--title", title, "--filename", str(initial_path) + "/"]
    if not command:
        raise ValueError("No supported folder picker was found. Install zenity, kdialog, or yad; you can also paste or type the path.")

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("The folder picker did not respond. Close it and try again.") from exc
    if result.returncode != 0:
        # All supported pickers return a non-zero code when the user cancels.
        if result.returncode in (1, 255) or not (result.stdout or "").strip():
            return None
        raise ValueError((result.stderr or "Folder picker failed.").strip())
    selected = (result.stdout or "").strip()
    if not selected:
        return None
    path = Path(selected).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("The selected path is not an existing directory.")
    return path



def _form_value(values: dict[str, Any], key: str, default: str = "") -> str:
    value = values.get(key, default)
    if value is None:
        return ""
    return str(value).strip()


def _form_target(value: str, field: str = "target") -> str:
    value = value.strip()
    if not value or len(value) > 128 or any(ord(c) < 32 for c in value) or any(c in value for c in "\r\n;\\"):
        raise ValueError(f"{field} must be a player name or target selector.")
    selector = r"@(?:a|e|p|r|s)(?:\[[A-Za-z0-9_.,=!:+\-]*\])?"
    if re.fullmatch(selector, value):
        return value
    if re.fullmatch(r"[A-Za-z0-9_ .-]{1,64}", value):
        return json.dumps(value, ensure_ascii=False) if " " in value else value
    raise ValueError(f"{field} must be a gamertag or a simple selector such as @a or @e[type=zombie].")


def _form_identifier(value: str, field: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError(f"{field} must be a valid identifier (letters, numbers, underscore, dot, colon or hyphen).")
    return value


def _form_coords(value: str, field: str = "Position") -> str:
    parts = value.split()
    coord = r"(?:~(?:-?(?:\d+(?:\.\d*)?|\.\d+))?|\^(?:-?(?:\d+(?:\.\d*)?|\.\d+))?|-?(?:\d+(?:\.\d*)?|\.\d+))"
    if len(parts) != 3 or any(not re.fullmatch(coord, item) for item in parts):
        raise ValueError(f"{field} must contain exactly three coordinates, for example: 0 64 0 or ~ ~-1 ~.")
    return " ".join(parts)


def _form_message(value: str, field: str = "Message", limit: int = 256) -> str:
    value = value.strip()
    if not value or len(value) > limit or any(ord(c) < 32 and c not in "\t" for c in value):
        raise ValueError(f"{field} must contain 1–{limit} printable characters on one line.")
    return json.dumps(value, ensure_ascii=False)


def _form_int(value: str, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a whole number.")
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}.")
    return number


def build_guided_command(form_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """Validate a typed command form and return a preview, never execute it."""
    if not isinstance(values, dict):
        raise ValueError("Command form values must be an object.")
    spec = next((item for item in COMMAND_FORMS if item["id"] == form_id), None)
    if spec is None:
        raise ValueError("Unknown guided command form.")
    get = lambda key, default="": _form_value(values, key, default)
    target = lambda key="target": _form_target(get(key), key)
    ident = lambda key, label=None: _form_identifier(get(key), label or key)
    pos = lambda key="position", label="Position": _form_coords(get(key), label)
    command = ""
    if form_id == "gamemode":
        mode = get("mode", "survival")
        if mode not in ("survival", "creative", "adventure", "spectator"):
            raise ValueError("Choose a supported game mode.")
        command = f"gamemode {mode} {target()}"
    elif form_id == "difficulty":
        difficulty = get("difficulty", "normal")
        if difficulty not in ("peaceful", "easy", "normal", "hard"):
            raise ValueError("Choose a supported difficulty.")
        command = f"difficulty {difficulty}"
    elif form_id == "time":
        action, value = get("action", "set"), get("value", "day")
        if action == "query":
            if value not in ("daytime", "day", "gametime"):
                raise ValueError("Time query must be daytime, day or gametime.")
            command = f"time query {value}"
        elif action == "add":
            command = f"time add {_form_int(value, 'Ticks', 0, 2147483647)}"
        elif action == "set":
            if value not in ("day", "noon", "sunset", "night", "midnight", "sunrise") and not re.fullmatch(r"\d{1,10}", value):
                raise ValueError("Time set must be a named preset or a whole number of ticks.")
            command = f"time set {value}"
        else:
            raise ValueError("Choose set, add or query.")
    elif form_id == "weather":
        weather = get("weather", "clear")
        if weather not in ("clear", "rain", "thunder"):
            raise ValueError("Choose clear, rain or thunder.")
        duration = get("duration")
        command = f"weather {weather}" + (f" {_form_int(duration, 'Duration', 1, 1000000)}" if duration else "")
    elif form_id == "gamerule":
        rule = get("rule")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", rule):
            raise ValueError("Enter a valid gamerule name.")
        value = get("value")
        if value and not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", value):
            raise ValueError("Gamerule value must be a simple boolean or number.")
        command = f"gamerule {rule}" + (f" {value}" if value else "")
    elif form_id == "say":
        command = f"say {_form_message(get('message'))}"
    elif form_id == "tell":
        command = f"tell {target()} {_form_message(get('message'))}"
    elif form_id == "title":
        kind = get("kind", "title")
        if kind not in ("title", "subtitle", "actionbar"):
            raise ValueError("Choose title, subtitle or actionbar.")
        command = f"title {target()} {kind} {_form_message(get('message'))}"
    elif form_id == "teleport":
        check = get("check", "false").lower()
        if check not in ("true", "false"):
            raise ValueError("Check for blocks must be true or false.")
        command = f"tp {target()} {pos()}" + (" true" if check == "true" else "")
    elif form_id == "give":
        amount = _form_int(get("amount", "1"), "Amount", 1, 32767)
        command = f"give {target()} {ident('item', 'Item identifier')} {amount}"
    elif form_id == "effect":
        action = get("action", "give")
        command = f"effect {target()} "
        if action == "clear":
            effect = get("effect")
            command += "clear" + (f" {ident('effect', 'Effect identifier')}" if effect else "")
        elif action == "give":
            duration = _form_int(get("duration", "60"), "Duration", 1, 1000000)
            amplifier = _form_int(get("amplifier", "0"), "Amplifier", 0, 255)
            hide = get("hide", "false").lower()
            if hide not in ("true", "false"):
                raise ValueError("Hide particles must be true or false.")
            command += f"{ident('effect', 'Effect identifier')} {duration} {amplifier} {hide}"
        else:
            raise ValueError("Choose give or clear.")
    elif form_id == "xp":
        amount = _form_int(get("amount", "1"), "Amount", 1, 1000000)
        mode = get("mode", "add")
        if mode not in ("add", "remove"):
            raise ValueError("Choose add or remove.")
        signed_amount = amount if mode == "add" else -amount
        command = f"xp {signed_amount} {target()}"
    elif form_id == "summon":
        command = f"summon {ident('entity', 'Entity identifier')}"
        if get("position"):
            command += " " + pos()
    elif form_id == "setblock":
        mode = get("mode", "replace")
        if mode not in ("replace", "destroy", "keep"):
            raise ValueError("Choose replace, destroy or keep.")
        command = f"setblock {pos()} {ident('block', 'Block identifier')} {mode}"
    elif form_id == "fill":
        mode = get("mode", "replace")
        if mode not in ("replace", "destroy", "hollow", "keep", "outline"):
            raise ValueError("Choose a valid fill mode.")
        command = f"fill {pos('from', 'From position')} {pos('to', 'To position')} {ident('block', 'Block identifier')} {mode}"
    elif form_id == "clone":
        mask = get("mask", "replace")
        clone_mode = get("clone_mode", "normal")
        if mask not in ("replace", "masked") or clone_mode not in ("normal", "force", "move"):
            raise ValueError("Choose valid clone mask and clone modes.")
        command = f"clone {pos('begin', 'Begin position')} {pos('end', 'End position')} {pos('destination', 'Destination')} {mask} {clone_mode}"
    elif form_id == "clearspawnpoint":
        command = f"clearspawnpoint {target()}"
    elif form_id == "tag":
        action = get("action", "add")
        if action not in ("add", "remove", "list"):
            raise ValueError("Choose add, remove or list.")
        command = f"tag {target()} {action}"
        if action != "list":
            tag = get("tag")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", tag):
                raise ValueError("Enter a tag containing letters, numbers, underscore, dot, colon or hyphen.")
            command += f" {tag}"
    elif form_id == "kick":
        reason = get("reason")
        command = f"kick {target()}" + (f" {_form_message(reason, 'Reason')}" if reason else "")
    elif form_id in ("op", "deop"):
        command = f"{form_id} {target()}"
    elif form_id == "setworldspawn":
        command = f"setworldspawn {pos()}"
    elif form_id == "spawnpoint":
        command = f"spawnpoint {target()} {pos()}"
    elif form_id == "mobevent":
        enabled = get("enabled", "true").lower()
        if enabled not in ("true", "false"):
            raise ValueError("Enabled must be true or false.")
        command = f"mobevent {ident('event', 'Event identifier')} {enabled}"
    elif form_id == "clear":
        item, count, raw_data = get("item"), get("count"), get("data", "-1") or "-1"
        command = f"clear {target()}"
        if (count or raw_data != "-1") and not item:
            raise ValueError("Choose an item before setting item data or a maximum count.")
        if item:
            command += " " + _form_identifier(item, "Item identifier")
            if count or raw_data != "-1":
                data_value = _form_int(raw_data, "Item data", -1, 32767)
                command += f" {data_value}"
            if count:
                command += " " + str(_form_int(count, "Maximum count", 1, 32767))
    elif form_id == "kill":
        command = f"kill {target()}"
    elif form_id == "playsound":
        sound = ident("sound", "Sound identifier")
        tgt = _form_target(get("target", "@a"), "target")
        volume = get("volume", "1") or "1"
        pitch = get("pitch", "1") or "1"
        try:
            vol, pit = float(volume), float(pitch)
        except ValueError:
            raise ValueError("Volume and pitch must be numbers.")
        if not 0 <= vol <= 1 or not 0 <= pit <= 256:
            raise ValueError("Volume must be 0–1 and pitch must be 0–256.")
        command = f"playsound {sound} {tgt} " + (pos() if get("position") else "~ ~ ~")
        command += f" {vol:g} {pit:g}"
    else:
        raise ValueError("This guided command is not implemented.")
    if len(command) > 4000:
        raise ValueError("Generated command is too long.")
    return {"ok": True, "command": command, "label": spec["label"], "confirm": bool(spec.get("confirm")), "cheats": bool(spec.get("cheats")), "group": spec["group"]}



def _read_proc_cpu() -> tuple[int, int] | None:
    """Return aggregate CPU ticks (total, idle+ iowait), or None off Linux."""
    try:
        first = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0]
        values = [int(x) for x in first.split()[1:]]
        if len(values) < 4:
            return None
        # Linux guest and guest_nice counters are already included in user/nice.
        # Exclude those trailing values to avoid double-counting CPU time.
        total = sum(values[:8])
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return total, idle
    except (OSError, ValueError, IndexError):
        return None


def _read_proc_memory() -> dict[str, int] | None:
    """Return total/available memory in bytes from /proc/meminfo."""
    try:
        fields: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.strip().split()
            if parts and parts[0].isdigit():
                fields[key] = int(parts[0]) * (1024 if len(parts) > 1 and parts[1].lower() == "kb" else 1)
        total = fields.get("MemTotal", 0)
        available = fields.get("MemAvailable", fields.get("MemFree", 0) + fields.get("Buffers", 0) + fields.get("Cached", 0))
        if total <= 0:
            return None
        return {"total": total, "available": min(total, available), "used": max(0, total - available)}
    except (OSError, ValueError):
        return None


def _read_proc_network() -> dict[str, int] | None:
    """Aggregate non-loopback receive/transmit bytes from /proc/net/dev."""
    try:
        rx = tx = 0
        lines = Path("/proc/net/dev").read_text(encoding="ascii").splitlines()[2:]
        found = False
        for line in lines:
            iface, sep, counters = line.partition(":")
            if not sep or iface.strip() == "lo":
                continue
            values = counters.split()
            if len(values) < 9:
                continue
            rx += int(values[0])
            tx += int(values[8])
            found = True
        return {"rx_bytes": rx, "tx_bytes": tx} if found else None
    except (OSError, ValueError, IndexError):
        return None


def _read_process_metrics(pid: int | None) -> dict[str, Any] | None:
    """Read safe resource metrics for one process from procfs."""
    if not pid or pid <= 0:
        return None
    base = Path("/proc") / str(pid)
    try:
        status: dict[str, str] = {}
        for line in (base / "status").read_text(encoding="ascii", errors="replace").splitlines():
            key, sep, value = line.partition(":")
            if sep:
                status[key] = value.strip()
        rss_kb = int((status.get("VmRSS", "0 kB").split() or ["0"])[0])
        vm_kb = int((status.get("VmSize", "0 kB").split() or ["0"])[0])
        threads = int(status.get("Threads", "0"))
        stat_line = (base / "stat").read_text(encoding="ascii", errors="replace")
        close = stat_line.rfind(")")
        fields = stat_line[close + 1:].split() if close >= 0 else []
        # After comm: state is field 3; utime/stime are fields 14/15.
        cpu_ticks = int(fields[11]) + int(fields[12]) if len(fields) >= 13 else 0
        try:
            fd_count = len(list((base / "fd").iterdir()))
        except OSError:
            fd_count = None
        return {
            "pid": pid,
            "rss_bytes": rss_kb * 1024,
            "vms_bytes": vm_kb * 1024,
            "threads": threads,
            "fd_count": fd_count,
            "cpu_ticks": cpu_ticks,
        }
    except (OSError, ValueError, IndexError):
        return None


def default_root() -> str:
    candidates = [
        HOME / "bedrock-server",
        HOME / "minecraft-bedrock-server",
        HOME / "bds",
        Path("/opt/minecraft-bedrock-server"),
        Path("/opt/minecraft-bedrock-server-backup"),
    ]
    for candidate in candidates:
        if (candidate / "bedrock_server").is_file():
            return str(candidate)
    return str(HOME / "bedrock-server")


class Manager:
    def __init__(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.config = safe_json_read(CONFIG_FILE, {})
        self.root = Path(self.config.get("server_dir") or default_root()).expanduser()
        self.server_version = "Unknown"
        self.server_version_source = "Not detected for this BDS executable"
        self.server_version_detected_at: str | None = None
        self._version_signature: str | None = None
        self._version_captured_this_run = False
        self._restore_cached_server_version()
        self.backup_dir = Path(self.config.get("backup_dir") or DEFAULT_BACKUPS).expanduser()
        self.auto_backup_hours = int(self.config.get("auto_backup_hours", 0) or 0)
        if self.auto_backup_hours not in (0, 1, 3, 6, 12, 24, 48):
            self.auto_backup_hours = 0
        self.backup_keep = max(1, min(100, int(self.config.get("backup_keep", 10) or 10)))
        self.tunnel_start_with_bds = self.config.get("tunnel_start_with_bds") is True
        self.tunnel_stop_with_bds = self.config.get("tunnel_stop_with_bds") is True
        if self.auto_backup_hours and not self.config.get("last_auto_backup"):
            # Start the first scheduled backup after the configured interval,
            # not immediately on the first manager launch.
            self.config["last_auto_backup"] = time.time()
        self.proc: subprocess.Popen[str] | None = None
        self.output_thread: threading.Thread | None = None
        self.started_at: float | None = None
        self.process_lock = threading.RLock()
        self.backup_lock = threading.Lock()
        self.backup_meta_lock = threading.Lock()
        self.download_lock = threading.Lock()
        self.download_tickets: dict[str, tuple[Path, str, str, float]] = {}
        self._backup_meta_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
        self.log_lock = threading.Lock()
        self.log_id = 0
        self.logs: deque[dict[str, Any]] = deque(maxlen=1600)
        # Player connection state is per managed BDS process, never rebuilt
        # from historical logs. The generation prevents late output from an
        # exited process from mutating a newer server session.
        self.players_lock = threading.RLock()
        self.online_players: dict[str, dict[str, str]] = {}
        self._player_session_generation = 0
        self._players_tracking_active = False
        self.metrics_lock = threading.Lock()
        self.metrics_samples: deque[dict[str, Any]] = deque(maxlen=180)
        self._metrics_previous: dict[str, Any] = {}
        self._metrics_current: dict[str, Any] = {"available": False, "sampled_at": None}
        self._metrics_stop = threading.Event()
        self.jobs_lock = threading.RLock()
        self.stack_profile_lock = threading.RLock()
        stored_profiles = safe_json_read(STACK_PROFILE_FILE, {"profiles": []})
        self.stack_profiles = [item for item in stored_profiles.get("profiles", []) if isinstance(item, dict)] if isinstance(stored_profiles, dict) and isinstance(stored_profiles.get("profiles", []), list) else []
        self.stack_profile_path = STACK_PROFILE_FILE
        loaded_jobs = safe_json_read(SCHEDULE_FILE, {"jobs": [], "history": []})
        if isinstance(loaded_jobs, list):
            loaded_jobs = {"jobs": loaded_jobs, "history": []}
        if not isinstance(loaded_jobs, dict):
            loaded_jobs = {"jobs": [], "history": []}
        self.scheduled_jobs = [j for j in loaded_jobs.get("jobs", []) if isinstance(j, dict)] if isinstance(loaded_jobs.get("jobs", []), list) else []
        self.job_history = [h for h in loaded_jobs.get("history", []) if isinstance(h, dict)][-50:] if isinstance(loaded_jobs.get("history", []), list) else []
        # A task marked running at the previous shutdown cannot still be running.
        for job in self.scheduled_jobs:
            if job.get("last_status") == "running":
                job["last_status"] = "interrupted"
                job["last_message"] = "Manager stopped while this task was running; inspect the backup folder before retrying."
        self._scheduler_stop = threading.Event()
        self._job_running: set[str] = set()
        self.save_ready_event = threading.Event()
        self.save_capture_active = False
        self.save_capture_lock = threading.Lock()
        self.save_capture_lines: list[str] = []
        self._load_saved_log_tail()
        self._auto_thread = threading.Thread(target=self._auto_backup_loop, name="bds-auto-backup", daemon=True)
        self._metrics_thread = threading.Thread(target=self._metrics_loop, name="bds-metrics", daemon=True)
        self._scheduler_thread = threading.Thread(target=self._scheduled_job_loop, name="bds-scheduler", daemon=True)
        self._auto_thread.start()
        self._metrics_thread.start()
        self._scheduler_thread.start()

    def _sample_metrics(self) -> dict[str, Any]:
        now = time.time()
        cpu = _read_proc_cpu()
        memory = _read_proc_memory()
        network = _read_proc_network()
        with self.process_lock:
            proc = self.proc
            pid = proc.pid if proc and proc.poll() is None else None
        process = _read_process_metrics(pid)
        previous = self._metrics_previous
        cpu_percent: float | None = None
        if cpu and previous.get("cpu"):
            total_delta = cpu[0] - previous["cpu"][0]
            idle_delta = cpu[1] - previous["cpu"][1]
            if total_delta > 0:
                cpu_percent = round(max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta)), 1)
        rx_rate: float | None = None
        tx_rate: float | None = None
        if network and previous.get("network") and now > previous.get("time", now):
            elapsed = now - previous["time"]
            rx_rate = max(0.0, (network["rx_bytes"] - previous["network"]["rx_bytes"]) / elapsed)
            tx_rate = max(0.0, (network["tx_bytes"] - previous["network"]["tx_bytes"]) / elapsed)
        process_cpu: float | None = None
        if process and previous.get("process") and previous["process"].get("pid") == process["pid"] and now > previous.get("time", now):
            elapsed = now - previous["time"]
            try:
                ticks = max(0, process["cpu_ticks"] - previous["process"]["cpu_ticks"])
                process_cpu = round((ticks / float(os.sysconf("SC_CLK_TCK"))) / elapsed / max(1, os.cpu_count() or 1) * 100.0, 1)
            except (ValueError, OSError, ZeroDivisionError):
                process_cpu = None
        disk = None
        backup_disk = None
        try:
            usage = shutil.disk_usage(self.root)
            disk = {"total": usage.total, "used": usage.used, "free": usage.free, "percent": round(usage.used / usage.total * 100, 1) if usage.total else None}
        except OSError:
            pass
        try:
            usage = shutil.disk_usage(self.backup_dir)
            backup_disk = {"total": usage.total, "used": usage.used, "free": usage.free, "percent": round(usage.used / usage.total * 100, 1) if usage.total else None}
        except OSError:
            pass
        sample = {
            "available": bool(cpu or memory or disk),
            "sampled_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "timestamp": int(now),
            "cpu_percent": cpu_percent,
            "memory": memory,
            "disk": disk,
            "backup_disk": backup_disk,
            "network": {"rx_bytes": network["rx_bytes"], "tx_bytes": network["tx_bytes"], "rx_bytes_per_sec": round(rx_rate, 1) if rx_rate is not None else None, "tx_bytes_per_sec": round(tx_rate, 1) if tx_rate is not None else None} if network else None,
            "bds_process": {k: v for k, v in process.items() if k != "cpu_ticks"} | {"cpu_percent": process_cpu} if process else None,
        }
        self._metrics_previous = {"time": now, "cpu": cpu, "network": network, "process": process}
        return sample

    def _metrics_loop(self) -> None:
        while not self._metrics_stop.is_set():
            try:
                sample = self._sample_metrics()
                with self.metrics_lock:
                    self._metrics_current = sample
                    self.metrics_samples.append(sample)
            except Exception as exc:
                self._append_log(f"[manager] Metrics sampler warning: {exc}")
            self._metrics_stop.wait(5)

    def metrics(self) -> dict[str, Any]:
        with self.metrics_lock:
            return {"current": dict(self._metrics_current), "history": list(self.metrics_samples)}

    def health(self) -> dict[str, Any]:
        metrics = self.metrics().get("current", {})
        with self.process_lock:
            running = bool(self.proc and self.proc.poll() is None)
        external_bds: list[dict[str, Any]] = []
        if not running:
            try:
                external_bds = self._unmanaged_bds()
            except OSError:
                external_bds = []
        checks: list[dict[str, Any]] = []
        exe = self.bds_executable()
        checks.append({"id": "executable", "label": "BDS executable", "status": "ok" if exe.is_file() and os.access(exe, os.X_OK) else "warning", "detail": "Found and executable" if exe.is_file() and os.access(exe, os.X_OK) else "Missing or not executable"})
        props = self.root / "server.properties"
        checks.append({"id": "properties", "label": "server.properties", "status": "ok" if props.is_file() and not props.is_symlink() else "warning", "detail": "Configuration file found" if props.is_file() and not props.is_symlink() else "Configuration file missing or unsafe"})
        if running and self.proc:
            process_status, process_detail = "ok", f"Managed process PID {self.proc.pid}"
        elif external_bds:
            pids = ", ".join(str(item.get("pid")) for item in external_bds)
            process_status = "info"
            process_detail = f"External BDS process detected (PID {pids}); process metrics are unavailable because it is not managed here."
        else:
            process_status, process_detail = "info", "Stopped (not an error)"
        checks.append({"id": "process", "label": "BDS process", "status": process_status, "detail": process_detail})
        free = (metrics.get("disk") or {}).get("free")
        if isinstance(free, (int, float)):
            status = "critical" if free < 512 * 1024 * 1024 else "warning" if free < 2 * 1024 * 1024 * 1024 else "ok"
            detail = f"{free / (1024 ** 3):.2f} GiB free"
        else:
            status, detail = "warning", "Disk availability could not be read"
        checks.append({"id": "disk", "label": "Server disk space", "status": status, "detail": detail})
        checks.append({"id": "backups", "label": "Backup destination", "status": "ok" if self.backup_dir.is_dir() and os.access(self.backup_dir, os.W_OK) else "warning", "detail": str(self.backup_dir) if self.backup_dir.is_dir() and os.access(self.backup_dir, os.W_OK) else "Backup folder is unavailable or not writable"})
        recent = self.log_since(max(0, self.log_id - 300))
        error_count = sum(1 for item in recent if re.search(r"\b(error|fatal|critical|crash)\b", item.get("line", ""), re.I))
        checks.append({"id": "logs", "label": "Recent log errors", "status": "warning" if error_count else "ok", "detail": f"{error_count} error-like lines in the latest {len(recent)} log entries"})
        rank = {"critical": 3, "warning": 2, "info": 1, "ok": 0}
        overall = max(checks, key=lambda c: rank.get(c["status"], 0))["status"]
        if overall == "info":
            overall = "ok"
        return {"status": overall, "checks": checks, "sampled_at": metrics.get("sampled_at")}

    def _persist_scheduled_jobs(self) -> None:
        with self.jobs_lock:
            payload = {"jobs": self.scheduled_jobs, "history": self.job_history[-50:]}
            atomic_write(SCHEDULE_FILE, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    def scheduled_jobs_state(self) -> dict[str, Any]:
        with self.jobs_lock:
            jobs = [dict(job) for job in self.scheduled_jobs]
            history = [dict(item) for item in self.job_history[-30:]]
        return {"jobs": jobs, "history": history, "timezone": datetime.now().astimezone().tzname() or "Local time", "local_time": datetime.now().astimezone().isoformat(timespec="seconds")}

    def upsert_scheduled_job(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("Scheduled task must be an object.")
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 48 or any(ord(ch) < 32 for ch in name):
            raise ValueError("Task name is required and must be 48 characters or fewer.")
        kind = str(data.get("kind", ""))
        if kind not in {"world_backup", "full_backup", "announcement"}:
            raise ValueError("Choose a supported task type.")
        time_text = str(data.get("time", ""))
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_text):
            raise ValueError("Time must use 24-hour HH:MM format.")
        days = data.get("days", list(range(7)))
        if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
            raise ValueError("Select at least one valid weekday.")
        days = sorted(set(days))
        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Enabled must be true or false.")
        message = str(data.get("message", "")).strip()
        if kind == "announcement":
            if not message or len(message) > 256 or any(ch in message for ch in "\r\n\x00"):
                raise ValueError("Announcement must be 1–256 characters on a single line.")
            # Reuse the exact Bedrock message validator and quoting behavior.
            build_guided_command("say", {"message": message})
        job_id = str(data.get("id", ""))
        with self.jobs_lock:
            existing = next((j for j in self.scheduled_jobs if j.get("id") == job_id), None) if job_id else None
            if not existing and len(self.scheduled_jobs) >= 40:
                raise ValueError("A maximum of 40 scheduled tasks is supported.")
            if not existing:
                job_id = secrets.token_hex(6)
                existing = {"id": job_id, "created_at": now_iso(), "last_run_key": "", "last_status": "never", "last_message": "Never run"}
                self.scheduled_jobs.append(existing)
            existing.update({"name": name, "kind": kind, "time": time_text, "days": days, "enabled": enabled, "message": message if kind == "announcement" else ""})
            self._persist_scheduled_jobs()
        self._append_log(f"[manager] Scheduled task saved: {name} ({kind}, {time_text}).")
        return {"ok": True, "message": "Scheduled task saved.", "job": dict(existing)}

    def delete_scheduled_job(self, job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{12}", job_id or ""):
            raise ValueError("Invalid scheduled task ID.")
        with self.jobs_lock:
            before = len(self.scheduled_jobs)
            self.scheduled_jobs = [j for j in self.scheduled_jobs if j.get("id") != job_id]
            if len(self.scheduled_jobs) == before:
                raise ValueError("Scheduled task not found.")
            self._persist_scheduled_jobs()
        return {"ok": True, "message": "Scheduled task deleted."}

    def _scheduled_job_loop(self) -> None:
        while not self._scheduler_stop.wait(10):
            now = datetime.now().astimezone()
            minute_of_day = now.hour * 60 + now.minute
            due: list[dict[str, Any]] = []
            with self.jobs_lock:
                for job in self.scheduled_jobs:
                    job_id = job.get("id")
                    if (job.get("enabled") is not True
                            or not isinstance(job_id, str)
                            or not re.fullmatch(r"[a-f0-9]{12}", job_id)
                            or job_id in self._job_running):
                        continue
                    try:
                        hour, minute = (int(x) for x in str(job.get("time", "")).split(":"))
                        target_minute = hour * 60 + minute
                        days = job.get("days", [])
                        if not isinstance(days, list) or any(type(day) is not int or not 0 <= day <= 6 for day in days):
                            continue
                    except (ValueError, TypeError):
                        continue
                    # Run only within 150 seconds of the configured local time.
                    # Missed tasks are deliberately not replayed after sleep/reboot.
                    delta = (minute_of_day - target_minute) * 60 + now.second
                    run_key = f"{now.date().isoformat()}@{job.get('time')}"
                    if now.weekday() in days and 0 <= delta <= 150 and job.get("last_run_key") != run_key:
                        job["last_run_key"] = run_key
                        job["last_status"] = "running"
                        job["last_message"] = "Running"
                        self._job_running.add(str(job.get("id")))
                        due.append(dict(job))
                if due:
                    self._persist_scheduled_jobs()
            for job in due:
                try:
                    worker = threading.Thread(target=self._run_scheduled_job, args=(job,), name=f"bds-job-{job['id']}", daemon=True)
                    worker.start()
                except Exception as exc:
                    # Do not leave a task marked running if the OS refuses to
                    # create a worker; otherwise shutdown could wait forever.
                    finished = now_iso()
                    message = f"Could not start scheduled task worker: {str(exc)[:400]}"
                    with self.jobs_lock:
                        existing = next((j for j in self.scheduled_jobs if j.get("id") == job.get("id")), None)
                        if existing:
                            existing["last_status"] = "failed"
                            existing["last_message"] = message
                            existing["last_run_at"] = finished
                        self.job_history.append({"id": job.get("id"), "name": job.get("name", "Scheduled task"), "kind": job.get("kind"), "status": "failed", "message": message, "started_at": finished, "finished_at": finished})
                        self.job_history = self.job_history[-50:]
                        self._job_running.discard(str(job.get("id")))
                        try:
                            self._persist_scheduled_jobs()
                        except Exception as persist_exc:
                            self._append_log(f"[manager] Could not persist task launch failure: {persist_exc}")
                    self._append_log(f"[manager] {message}")

    def _run_scheduled_job(self, job: dict[str, Any]) -> None:
        started = now_iso()
        status = "success"
        message = ""
        try:
            kind = job.get("kind")
            if kind == "world_backup":
                result = self.backup("world")
                message = result.get("message", "World backup completed.")
            elif kind == "full_backup":
                result = self.backup("full")
                message = result.get("message", "Full backup completed.")
            elif kind == "announcement":
                built = build_guided_command("say", {"message": job.get("message", "")})
                result = self.command(built["command"])
                message = result.get("message", "Announcement sent.")
            else:
                raise ValueError("Unsupported scheduled task type.")
        except Exception as exc:
            status = "failed"
            message = str(exc)[:500]
        finished = now_iso()
        with self.jobs_lock:
            existing = next((j for j in self.scheduled_jobs if j.get("id") == job.get("id")), None)
            if existing:
                existing["last_status"] = status
                existing["last_message"] = message
                existing["last_run_at"] = finished
            self.job_history.append({"id": job.get("id"), "name": job.get("name"), "kind": job.get("kind"), "status": status, "message": message, "started_at": started, "finished_at": finished})
            self.job_history = self.job_history[-50:]
            self._job_running.discard(str(job.get("id")))
            self._persist_scheduled_jobs()
        self._append_log(f"[manager] Scheduled task {job.get('name')}: {status} — {message}")

    def shutdown(self) -> None:
        """Stop background scheduling and let active backups finish before BDS exits."""
        self._scheduler_stop.set()
        scheduler = getattr(self, "_scheduler_thread", None)
        auto_backup = getattr(self, "_auto_thread", None)
        for thread in (scheduler, auto_backup):
            if thread and thread is not threading.current_thread():
                thread.join()
        # A scheduled backup may have issued `save hold`; wait for it to finish
        # (and execute its `save resume` cleanup) before main() stops BDS.
        jobs_lock = getattr(self, "jobs_lock", None)
        if jobs_lock is not None and hasattr(self, "_job_running"):
            while True:
                with jobs_lock:
                    active = bool(self._job_running)
                if not active:
                    break
                time.sleep(0.1)
        self._metrics_stop.set()
        metrics_thread = getattr(self, "_metrics_thread", None)
        if metrics_thread and metrics_thread is not threading.current_thread():
            metrics_thread.join(timeout=2)

    def _load_saved_log_tail(self) -> None:
        try:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
        except OSError:
            return
        for line in lines:
            # Historical logs are displayed for context only. Replaying old
            # join/leave entries must not recreate a live player session.
            self._append_log(line, persist=False, track_player=False)

    def _persist_config(self) -> None:
        self.config.update({
            "server_dir": str(self.root),
            "backup_dir": str(self.backup_dir),
            "auto_backup_hours": self.auto_backup_hours,
            "backup_keep": self.backup_keep,
            "tunnel_start_with_bds": self.tunnel_start_with_bds,
            "tunnel_stop_with_bds": self.tunnel_stop_with_bds,
        })
        atomic_write(CONFIG_FILE, json.dumps(self.config, indent=2) + "\n")

    def _append_log(
        self,
        line: str,
        persist: bool = True,
        track_player: bool = True,
        player_generation: int | None = None,
    ) -> None:
        stamp = now_iso()
        clean = line.rstrip("\r\n")
        with self.log_lock:
            self.log_id += 1
            self.logs.append({"id": self.log_id, "time": stamp, "line": clean})
            if persist:
                try:
                    with LOG_FILE.open("a", encoding="utf-8") as f:
                        f.write(f"[{stamp}] {clean}\n")
                except OSError:
                    pass
        if self.save_capture_active:
            with self.save_capture_lock:
                self.save_capture_lines.append(clean)
            if "files are now ready to be copied" in clean.lower():
                self.save_ready_event.set()
        if track_player:
            self._track_player(clean, player_generation=player_generation)

    def _track_player(self, line: str, player_generation: int | None = None) -> None:
        """Apply one live BDS join/leave event to the current session."""
        event = re.search(r"\bPlayer\s+(connected|disconnected):\s*(.*)$", line, re.I)
        if not event:
            return

        payload = event.group(2).strip()
        # A disconnect normally includes both `xuid` and `pfid`; a connect
        # normally includes only `xuid`. Strip either metadata field from the
        # displayed name, then read XUID independently of metadata ordering.
        metadata = re.search(r",\s*(?:xuid|pfid)\s*:", payload, re.I)
        if metadata:
            name = payload[:metadata.start()].strip()
            metadata_text = payload[metadata.start():]
        else:
            name = payload
            metadata_text = ""
        xuid_match = re.search(r"(?:^|,)\s*xuid\s*:\s*([^,\s]*)", metadata_text, re.I)
        xuid = xuid_match.group(1).strip() if xuid_match else ""
        key = name.casefold()

        with self.players_lock:
            if not self._players_tracking_active:
                return
            if player_generation is not None and player_generation != self._player_session_generation:
                return

            if event.group(1).casefold() == "connected":
                if not name:
                    return
                # Prefer one entry per authenticated account when the server
                # provides an XUID, even if the visible name changed.
                if xuid:
                    for existing_key, player in list(self.online_players.items()):
                        if player.get("xuid") == xuid and existing_key != key:
                            self.online_players.pop(existing_key, None)
                self.online_players[key] = {"name": name, "xuid": xuid}
                return

            # Names are the primary key because offline/split-screen entries
            # may have a blank XUID. Fall back to XUID when the leave event's
            # visible name differs from the original connection event.
            if name:
                self.online_players.pop(key, None)
            if xuid:
                for existing_key, player in list(self.online_players.items()):
                    if player.get("xuid") == xuid:
                        self.online_players.pop(existing_key, None)

    def _online_player_snapshot(self) -> list[dict[str, str]]:
        """Return a consistent, detached snapshot for the HTTP status API."""
        with self.players_lock:
            return [dict(player) for player in self.online_players.values()]

    def log_since(self, since: int = 0) -> list[dict[str, Any]]:
        with self.log_lock:
            return [item for item in self.logs if item["id"] > since]

    def bds_executable(self) -> Path:
        return self.root / "bedrock_server"

    def _executable_signature(self) -> str | None:
        try:
            exe = self.bds_executable().resolve(strict=True)
            st = exe.stat()
            return f"{exe}|{st.st_dev}|{st.st_ino}|{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            return None

    def _restore_cached_server_version(self) -> None:
        signature = self._executable_signature()
        self._version_signature = signature
        self.server_version = "Unknown"
        self.server_version_source = "Not detected for this BDS executable"
        self.server_version_detected_at = None
        if not signature:
            return
        cache = self.config.get("bds_version_cache", {})
        if not isinstance(cache, dict):
            return
        key = str(self.bds_executable().resolve())
        entry = cache.get(key)
        if isinstance(entry, dict) and entry.get("signature") == signature:
            version = str(entry.get("version", ""))
            if re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
                self.server_version = version
                self.server_version_source = "Cached from a previous start of this unchanged executable"
                self.server_version_detected_at = str(entry.get("detected_at") or "") or None

    def _remember_server_version(self, version: str) -> None:
        signature = self._executable_signature()
        if not signature:
            return
        key = str(self.bds_executable().resolve())
        cache = self.config.get("bds_version_cache", {})
        if not isinstance(cache, dict):
            cache = {}
        detected_at = now_iso()
        cache[key] = {"signature": signature, "version": version, "detected_at": detected_at}
        # Keep the cache compact when several server folders have been used.
        if len(cache) > 20:
            cache = dict(list(cache.items())[-20:])
        self.config["bds_version_cache"] = cache
        self.server_version = version
        self.server_version_source = "Detected from this BDS process startup log"
        self.server_version_detected_at = detected_at
        self._version_signature = signature
        self._persist_config()

    def _capture_server_version(self, line: str) -> None:
        if self._version_captured_this_run:
            return
        version = parse_bds_version(line)
        if version:
            self._version_captured_this_run = True
            self._remember_server_version(version)
            self._append_log(f"[manager] Detected BDS server version {version} from the current process.")

    def _read_props(self) -> dict[str, str]:
        path = self.root / "server.properties"
        try:
            return parse_properties(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return {}

    def _unmanaged_bds(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        proc_root = Path("/proc")
        if not proc_root.exists():
            return found
        managed_pid = self.proc.pid if self.proc and self.proc.poll() is None else None
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == managed_pid:
                continue
            try:
                raw_args = (entry / "cmdline").read_bytes().split(b"\x00")
                args = [x.decode("utf-8", "replace") for x in raw_args if x]
                cmdline = " ".join(args).strip()
                comm = (entry / "comm").read_text().strip()
            except OSError:
                continue
            argv0 = Path(args[0]).name if args else ""
            if comm in ("bedrock_server", "bedrock-server") or argv0 in ("bedrock_server", "bedrock-server"):
                found.append({"pid": pid, "command": cmdline[:180]})
        return found

    def status(self) -> dict[str, Any]:
        with self.process_lock:
            proc = self.proc
            running = bool(proc and proc.poll() is None)
            if proc and not running:
                # The status endpoint can observe process exit before the
                # watcher thread runs. Reconcile here so the dashboard never
                # reports players for an already-dead managed process.
                with self.players_lock:
                    if self._players_tracking_active:
                        self._players_tracking_active = False
                        self.online_players.clear()
                if self.proc is proc:
                    self.proc = None
                    self.started_at = None
            pid = proc.pid if running and proc else None
            uptime = int(time.time() - self.started_at) if running and self.started_at else 0
        current_signature = self._executable_signature()
        # If the executable is replaced while its process is running, keep the
        # version captured from that live process until it exits. Revalidate the
        # cache against the new file before the next start.
        if not running and current_signature != self._version_signature:
            self._restore_cached_server_version()
        props = self._read_props()
        worlds_dir = self.root / "worlds"
        worlds = sorted([p.name for p in worlds_dir.iterdir() if p.is_dir()]) if worlds_dir.is_dir() else []
        backups = self.list_backups()
        unmanaged = self._unmanaged_bds() if not running else []
        players = self._online_player_snapshot()
        return {
            "app_version": APP_VERSION,
            "server_dir": str(self.root),
            "backup_dir": str(self.backup_dir),
            "executable": str(self.bds_executable()),
            "executable_exists": self.bds_executable().is_file(),
            "executable_runnable": self.bds_executable().is_file() and os.access(self.bds_executable(), os.X_OK),
            "running": running,
            "pid": pid,
            "uptime": uptime,
            "unmanaged": unmanaged,
            "server_name": props.get("server-name", "Dedicated Server"),
            "world_name": props.get("level-name", "Bedrock level"),
            "worlds": worlds,
            "port": props.get("server-port", "19132"),
            "max_players": props.get("max-players", "10"),
            "version": self.server_version,
            "version_source": self.server_version_source,
            "version_detected_at": self.server_version_detected_at,
            "players": players,
            "player_count": len(players),
            "properties": props,
            "backups": backups[:8],
            "auto_backup_hours": self.auto_backup_hours,
            "backup_keep": self.backup_keep,
            "tunnel_start_with_bds": self.tunnel_start_with_bds,
            "tunnel_stop_with_bds": self.tunnel_stop_with_bds,
        }

    def set_tunnel_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("Tunnel settings must be an object.")
        start_with = data.get("start_with_bds", self.tunnel_start_with_bds)
        stop_with = data.get("stop_with_bds", self.tunnel_stop_with_bds)
        if not isinstance(start_with, bool) or not isinstance(stop_with, bool):
            raise ValueError("Tunnel lifecycle options must be true or false.")
        self.tunnel_start_with_bds = start_with
        self.tunnel_stop_with_bds = stop_with
        self._persist_config()
        return {"ok": True, "message": "Bedrock Node Tunnel lifecycle settings saved."}

    def start(self) -> dict[str, Any]:
        with self.process_lock:
            if self.proc and self.proc.poll() is None:
                return {"ok": True, "message": "BDS is already running under this manager."}
            unmanaged = self._unmanaged_bds()
            if unmanaged:
                pids = ", ".join(str(x["pid"]) for x in unmanaged)
                raise ValueError(f"A BDS process is already running outside this manager (PID {pids}). Stop that instance first to avoid opening the same world twice.")
            exe = self.bds_executable()
            if not exe.is_file():
                raise ValueError(f"Cannot find bedrock_server at {exe}. Set the correct BDS folder in Settings.")
            if not os.access(exe, os.X_OK):
                raise ValueError(f"{exe} is not executable. Run: chmod +x '{exe}'")
            if not (self.root / "server.properties").is_file():
                raise ValueError("server.properties is missing from the selected BDS folder.")
            # Refresh the per-binary version cache immediately before launch;
            # this catches updates made while the manager was left open.
            self._restore_cached_server_version()
            self._version_captured_this_run = False
            env = os.environ.copy()
            env["LD_LIBRARY_PATH"] = str(self.root) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
            self._append_log(f"[manager] Starting BDS from {self.root}")
            try:
                self.proc = subprocess.Popen(
                    [str(exe)], cwd=str(self.root), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    env=env, start_new_session=True,
                )
            except OSError as exc:
                self.proc = None
                raise ValueError(f"Could not start BDS: {exc}") from exc
            self.started_at = time.time()
            with self.players_lock:
                self._player_session_generation += 1
                player_generation = self._player_session_generation
                self._players_tracking_active = True
                self.online_players.clear()
            self.output_thread = threading.Thread(
                target=self._read_process_output,
                args=(self.proc, player_generation),
                name=f"bds-output-{self.proc.pid}",
                daemon=True,
            )
            self.output_thread.start()
            threading.Thread(
                target=self._watch_process,
                args=(self.proc, player_generation, self.output_thread),
                name=f"bds-watch-{self.proc.pid}",
                daemon=True,
            ).start()
            return {"ok": True, "message": f"BDS started (PID {self.proc.pid})."}

    def _read_process_output(self, proc: subprocess.Popen[str], player_generation: int) -> None:
        try:
            assert proc.stdout
            for line in proc.stdout:
                self._append_log(line, player_generation=player_generation)
                self._capture_server_version(line)
        except (OSError, ValueError):
            pass

    def _watch_process(
        self,
        proc: subprocess.Popen[str],
        player_generation: int,
        reader: threading.Thread | None,
    ) -> None:
        code = proc.wait()
        self._append_log(f"[manager] BDS process exited with code {code}.", track_player=False)
        # Drain final log output before ending this session. A generation check
        # also prevents a delayed reader from changing a subsequent session.
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2)
        with self.process_lock:
            if self.proc is proc:
                with self.players_lock:
                    if self._player_session_generation == player_generation:
                        self._players_tracking_active = False
                        self.online_players.clear()
                self.proc = None
                self.started_at = None

    def command(self, command: str) -> dict[str, Any]:
        raw = command.strip()
        if raw.startswith("/"):
            raw = raw[1:]
        if not raw:
            raise ValueError("Enter a command first.")
        if any(c in raw for c in "\r\n\x00"):
            raise ValueError("Commands must be a single line.")
        if len(raw) > 4000:
            raise ValueError("Command is too long (limit: 4000 characters).")
        if raw.split(maxsplit=1)[0].lower() == "stop":
            raise ValueError("Use the Stop button so the manager can track shutdown safely.")
        with self.process_lock:
            proc = self.proc
            if not proc or proc.poll() is not None or not proc.stdin:
                raise ValueError("The server is not running under this manager. Start it here first; an external process cannot receive console commands.")
            try:
                proc.stdin.write(raw + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ValueError(f"Could not send command: {exc}") from exc
        self._append_log(f"> {raw}")
        return {"ok": True, "message": f"Sent: {raw}"}

    def stop(self, timeout: int = 30) -> dict[str, Any]:
        with self.process_lock:
            proc = self.proc
            if not proc:
                return {"ok": True, "message": "BDS is not running under this manager."}
            if proc.poll() is not None:
                with self.players_lock:
                    self._players_tracking_active = False
                    self.online_players.clear()
                self.proc = None
                self.started_at = None
                return {"ok": True, "message": "BDS is not running under this manager."}
            reader = self.output_thread
            player_generation = self._player_session_generation
            self._append_log("[manager] Requesting graceful shutdown (stop).")
            try:
                if proc.stdin:
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._append_log("[manager] Graceful stop timed out; sending SIGTERM.")
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._append_log("[manager] SIGTERM timed out; sending SIGKILL.")
                proc.kill()
                proc.wait(timeout=5)
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2)
        # The watcher performs the same cleanup for crashes/unexpected exits.
        # This synchronous path keeps the Stop action's state deterministic.
        with self.process_lock:
            if self.proc is proc:
                with self.players_lock:
                    if self._player_session_generation == player_generation:
                        self._players_tracking_active = False
                        self.online_players.clear()
                self.proc = None
                self.started_at = None
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        return {"ok": True, "message": "BDS has stopped."}

    def restart(self) -> dict[str, Any]:
        self.stop()
        time.sleep(1)
        return self.start()

    def set_server_dir(self, value: str) -> dict[str, Any]:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("That server directory does not exist or is not a directory.")
        if not (path / "bedrock_server").is_file():
            raise ValueError("That folder does not contain a bedrock_server executable.")
        if not (path / "server.properties").is_file():
            raise ValueError("That folder does not contain server.properties.")
        with self.process_lock:
            if self.proc and self.proc.poll() is None:
                raise ValueError("Stop the managed server before changing its directory.")
        self.root = path
        self._restore_cached_server_version()
        self._persist_config()
        return {"ok": True, "message": f"Server folder set to {path}"}

    def set_backup_settings(self, value: dict[str, Any]) -> dict[str, Any]:
        new_dir = Path(str(value.get("backup_dir", self.backup_dir))).expanduser().resolve()
        hours = int(value.get("auto_backup_hours", self.auto_backup_hours))
        keep = int(value.get("backup_keep", self.backup_keep))
        if hours not in (0, 1, 3, 6, 12, 24, 48):
            raise ValueError("Auto-backup interval must be Off, 1, 3, 6, 12, 24, or 48 hours.")
        if not 1 <= keep <= 100:
            raise ValueError("Backup retention must be between 1 and 100 archives.")
        try:
            new_dir.relative_to(self.root.resolve())
            inside_server = True
        except ValueError:
            inside_server = False
        if inside_server:
            raise ValueError("Choose a backup folder outside the BDS server folder. This avoids recursive backups and protects world data.")
        new_dir.mkdir(parents=True, exist_ok=True)
        if hours != self.auto_backup_hours:
            self.config["last_auto_backup"] = time.time()
        self.backup_dir, self.auto_backup_hours, self.backup_keep = new_dir, hours, keep
        self._persist_config()
        return {"ok": True, "message": "Backup settings saved."}

    def properties(self) -> dict[str, Any]:
        path = self.root / "server.properties"
        if not path.is_file():
            raise ValueError("server.properties was not found in the selected BDS folder.")
        return {"text": path.read_text(encoding="utf-8", errors="replace"), "values": self._read_props(), "groups": PROPERTY_GROUPS}

    def validate_property_updates(self, updates: dict[str, Any]) -> dict[str, str]:
        if not isinstance(updates, dict) or len(updates) > 300:
            raise ValueError("Invalid property update.")
        clean: dict[str, str] = {}
        known = {item["key"]: item for group in PROPERTY_GROUPS.values() for item in group}
        for key, val in updates.items():
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", str(key)):
                raise ValueError(f"Invalid property name: {key}")
            value = str(val)
            if key == "server-udp-ports":
                if "\r" in value:
                    raise ValueError("server-udp-ports entries must use one mapping per line without carriage returns.")
                entries = [entry.strip() for entry in value.splitlines() if entry.strip()]
                if len(entries) > 32:
                    raise ValueError("At most 32 separate server-udp-ports mappings can be saved at once.")
                if any(len(entry) > 512 or any(ord(char) < 32 for char in entry) for entry in entries):
                    raise ValueError("Each server-udp-ports mapping must be 512 characters or fewer and contain no control characters.")
                value = "\n".join(entries)
            elif "\n" in value or "\r" in value:
                raise ValueError(f"Property {key} must be a single line.")
            meta = known.get(key)
            if key == "server-name" and ";" in value:
                raise ValueError("server-name cannot contain a semicolon.")
            if key == "server-ip" and value:
                try:
                    ipaddress.ip_address(value)
                except ValueError:
                    raise ValueError("server-ip must be an IPv4/IPv6 literal or empty to bind all interfaces.")
            if key == "level-name" and (not value or value in (".", "..") or "/" in value or "\\" in value):
                raise ValueError("level-name must be a single world-folder name, not a path.")
            if key == "server-build-radius-ratio" and value.lower() != "disabled":
                try:
                    ratio = float(value)
                except ValueError:
                    raise ValueError("server-build-radius-ratio must be Disabled or a number from 0.0 to 1.0.")
                if not 0.0 <= ratio <= 1.0:
                    raise ValueError("server-build-radius-ratio must be from 0.0 to 1.0.")
            if meta:
                if meta["type"] == "bool" and value.lower() not in ("true", "false"):
                    raise ValueError(f"{key} must be true or false.")
                if meta["type"] == "select" and value not in meta["options"]:
                    raise ValueError(f"{key} must be one of: {', '.join(meta['options'])}.")
                if meta["type"] == "number":
                    try:
                        n = int(value)
                    except ValueError:
                        raise ValueError(f"{key} must be a whole number.")
                    if "min" in meta and n < meta["min"] or "max" in meta and n > meta["max"]:
                        raise ValueError(f"{key} must be between {meta.get('min', '-∞')} and {meta.get('max', '∞')}.")
            clean[str(key)] = value
        return clean

    def save_properties(self, updates: dict[str, Any]) -> dict[str, Any]:
        path = self.root / "server.properties"
        if not path.is_file():
            raise ValueError("server.properties was not found.")
        clean = self.validate_property_updates(updates)
        original = path.read_text(encoding="utf-8", errors="replace")
        backup = path.with_name("server.properties.before-bds-omni.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
        atomic_write(path, merge_properties(original, clean))
        running = bool(self.proc and self.proc.poll() is None)
        return {"ok": True, "message": "server.properties saved. Restart BDS to apply startup-only settings." if running else "server.properties saved.", "restart_required": running}

    def access_files(self) -> dict[str, Any]:
        allow = safe_json_read(self.root / "allowlist.json", [])
        perms = safe_json_read(self.root / "permissions.json", [])
        if not isinstance(allow, list): allow = []
        if not isinstance(perms, list): perms = []
        return {"allowlist": allow, "permissions": perms}

    def save_access_files(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("Access-file update must be an object.")
        allow = data.get("allowlist", [])
        perms = data.get("permissions", [])
        if not isinstance(allow, list) or not isinstance(perms, list) or len(allow) > 5000 or len(perms) > 5000:
            raise ValueError("Allowlist and permissions must be arrays.")
        clean_allow = []
        allow_names: set[str] = set()
        for item in allow:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                raise ValueError("Each allowlist entry needs a player name.")
            name = str(item["name"]).strip()
            xuid = str(item.get("xuid", "")).strip()
            if len(name) > 64 or any(ord(c) < 32 for c in name):
                raise ValueError("Gamertags must be 64 characters or fewer and cannot contain control characters.")
            if len(xuid) > 32 or any(ord(c) < 32 for c in xuid):
                raise ValueError("XUID values must be 32 characters or fewer and cannot contain control characters.")
            if name.casefold() in allow_names:
                raise ValueError(f"Duplicate allowlist entry: {name}")
            allow_names.add(name.casefold())
            ignores = item.get("ignoresPlayerLimit", False)
            if not isinstance(ignores, bool):
                raise ValueError("ignoresPlayerLimit must be true or false.")
            clean_allow.append({
                "name": name,
                "ignoresPlayerLimit": ignores,
                "xuid": xuid,
            })
        clean_perms = []
        permission_xuids: set[str] = set()
        for item in perms:
            if not isinstance(item, dict) or not str(item.get("xuid", "")).strip():
                raise ValueError("Each permissions entry needs an XUID. Use /op in the console if you only know the gamertag.")
            perm = str(item.get("permission", "member"))
            if perm not in ("visitor", "member", "operator"):
                raise ValueError("Permission must be visitor, member, or operator.")
            xuid = str(item["xuid"]).strip()
            if len(xuid) > 32 or any(ord(c) < 32 for c in xuid):
                raise ValueError("XUID values must be 32 characters or fewer and cannot contain control characters.")
            if xuid in permission_xuids:
                raise ValueError(f"Duplicate permission entry for XUID {xuid}.")
            permission_xuids.add(xuid)
            clean_perms.append({"permission": perm, "xuid": xuid})
        allow_path = self.root / "allowlist.json"
        perms_path = self.root / "permissions.json"
        for path in (allow_path, perms_path):
            backup = path.with_name(path.name + ".before-bds-omni.bak")
            if path.is_file() and not backup.exists():
                shutil.copy2(path, backup)
        atomic_write(allow_path, json.dumps(clean_allow, indent=2) + "\n")
        atomic_write(perms_path, json.dumps(clean_perms, indent=2) + "\n")
        running = bool(self.proc and self.proc.poll() is None)
        if running:
            try:
                self.command("allowlist reload")
            except ValueError:
                pass
            try:
                self.command("permission reload")
            except ValueError:
                pass
        return {"ok": True, "message": "Access files saved" + ("; reload commands were sent where supported." if running else ". Restart or reload the relevant server permissions if needed.")}

    @staticmethod
    def _validate_world_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name or len(name) > 64 or name in (".", "..") or name.startswith("."):
            raise ValueError("World names must be 1–64 characters and cannot start with a dot.")
        if any((not char.isprintable()) or char in '/\\<>:"|?*' for char in name):
            raise ValueError("World names cannot contain path separators, control characters or reserved filename characters.")
        if name.endswith((".", " ")):
            raise ValueError("World names cannot end with a dot or space.")
        return name

    def _require_content_server_stopped(self) -> None:
        with self.process_lock if hasattr(self, "process_lock") else threading.RLock():
            proc = getattr(self, "proc", None)
            if proc and proc.poll() is None:
                raise ValueError("Stop the managed BDS server before changing worlds or packs. This prevents LevelDB and pack files from changing during the operation.")
        detector = getattr(self, "_unmanaged_bds", None)
        if callable(detector):
            external = detector()
            if external:
                pids = ", ".join(str(item.get("pid", "?")) for item in external)
                raise ValueError(f"An external BDS process is running (PID {pids}). Stop it through its existing launcher before changing worlds or packs.")

    def _worlds_root(self, create: bool = False) -> Path:
        root = self.root.resolve()
        path = self.root / "worlds"
        if path.is_symlink():
            raise ValueError("The BDS worlds directory is a symbolic link. Content management is disabled for safety.")
        if path.exists() and not path.is_dir():
            raise ValueError("The BDS worlds path is not a directory.")
        if create:
            path.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve() if path.exists() else path
        if path.exists() and root not in (resolved, *resolved.parents):
            raise ValueError("The BDS worlds directory resolves outside the selected server folder.")
        return path

    def _world_path(self, name: str) -> Path:
        clean = self._validate_world_name(name)
        base = self._worlds_root()
        path = base / clean
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError("The selected world is not a normal directory.")
        if path.exists() and base.resolve() not in path.resolve().parents:
            raise ValueError("The selected world resolves outside the BDS worlds directory.")
        return path

    @staticmethod
    def _version_text(value: Any) -> str:
        # bool is a subclass of int in Python; explicitly reject it so malformed
        # manifest values like [true, 0, 0] cannot pass version validation.
        if isinstance(value, list) and len(value) == 3 and all(type(x) is int and x >= 0 for x in value):
            return ".".join(str(x) for x in value)
        if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", value):
            return value
        return ""

    def _pack_records(self) -> list[dict[str, Any]]:
        locations: list[tuple[str, str, str, Path]] = []
        for kind, folder in (("behavior", "behavior_packs"), ("resource", "resource_packs")):
            shared = self.root / folder
            if shared.is_symlink():
                continue
            if shared.is_dir():
                locations.extend((kind, "shared", "", child) for child in sorted(shared.iterdir(), key=lambda item: item.name.lower()) if child.is_dir() and not child.is_symlink() and not child.name.startswith(".bds-omni-"))
            worlds = self._worlds_root()
            if worlds.is_dir():
                for world in sorted(worlds.iterdir(), key=lambda item: item.name.lower()):
                    if not world.is_dir() or world.is_symlink():
                        continue
                    local = world / folder
                    if local.is_dir() and not local.is_symlink():
                        locations.extend((kind, "world", world.name, child) for child in sorted(local.iterdir(), key=lambda item: item.name.lower()) if child.is_dir() and not child.is_symlink() and not child.name.startswith(".bds-omni-"))
        records: list[dict[str, Any]] = []
        for kind, scope, world_name, pack_path in locations:
            manifest_path = pack_path / "manifest.json"
            errors: list[str] = []
            manifest: dict[str, Any] = {}
            if manifest_path.is_symlink() or not manifest_path.is_file():
                errors.append("manifest.json is missing or is a symbolic link")
            else:
                try:
                    if manifest_path.stat().st_size > 2_000_000:
                        raise ValueError("manifest.json exceeds the 2 MB inspection limit")
                    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(loaded, dict):
                        raise ValueError("manifest root must be a JSON object")
                    manifest = loaded
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    errors.append(f"Invalid manifest: {exc}")
            format_version = manifest.get("format_version") if isinstance(manifest, dict) else None
            if type(format_version) is not int or format_version not in (1, 2, 3):
                errors.append("format_version must be 1, 2, or 3")
            header = manifest.get("header", {}) if isinstance(manifest, dict) else {}
            if not isinstance(header, dict):
                header = {}
                errors.append("manifest header must be an object")
            pack_uuid = str(header.get("uuid", ""))
            try:
                pack_uuid = str(uuidlib.UUID(pack_uuid))
            except (ValueError, AttributeError):
                errors.append("header.uuid is missing or invalid")
                pack_uuid = ""
            version_raw = header.get("version")
            version = self._version_text(version_raw)
            if not version:
                errors.append("header.version must be a three-number array or semantic version string")
            min_engine_raw = header.get("min_engine_version")
            min_engine = self._version_text(min_engine_raw) if min_engine_raw is not None else ""
            if min_engine_raw is not None and not min_engine:
                errors.append("header.min_engine_version must be a three-number array or semantic version string")
            if format_version == 3:
                # Manifest v3 is documented as preview-only. Inspect it, but do
                # not allow it to be activated on a stable server by accident.
                errors.append("manifest format_version 3 is preview-only; inspect this pack on a compatible preview build before activation")
            pack_name = str(header.get("name") or pack_path.name)
            if not str(header.get("name") or "").strip():
                errors.append("header.name is missing")
            modules = manifest.get("modules", []) if isinstance(manifest, dict) else []
            if not isinstance(modules, list) or not modules:
                errors.append("manifest modules must be a non-empty array")
                modules = []
            module_types: set[str] = set()
            module_uuids: set[str] = set()
            for module in modules:
                if not isinstance(module, dict):
                    errors.append("manifest contains an invalid module entry")
                    continue
                module_type = str(module.get("type", ""))
                module_types.add(module_type)
                try:
                    module_uuid = str(uuidlib.UUID(str(module.get("uuid", ""))))
                    if module_uuid == pack_uuid:
                        errors.append("a module UUID must differ from the header UUID")
                    if module_uuid in module_uuids:
                        errors.append("module UUIDs must be unique within a manifest")
                    module_uuids.add(module_uuid)
                except (ValueError, AttributeError):
                    errors.append("a module UUID is missing or invalid")
                if not self._version_text(module.get("version")):
                    errors.append("a module version is missing or invalid")
            # client_data is used by older Microsoft examples; accept it as a
            # behavior module while still checking that the pack matches its folder.
            expected = {"data", "script", "client_data"} if kind == "behavior" else {"resources"}
            if module_types and not (module_types & expected):
                errors.append(f"module types {', '.join(sorted(module_types))} do not match the {kind} pack folder")
            dependencies = manifest.get("dependencies", []) if isinstance(manifest, dict) else []
            if not isinstance(dependencies, list):
                errors.append("manifest dependencies must be an array")
                dependencies = []
            size = 0
            try:
                for current, dirs, files in os.walk(pack_path, followlinks=False):
                    current_path = Path(current)
                    safe_dirs = []
                    for dirname in dirs:
                        child = current_path / dirname
                        if child.is_symlink():
                            errors.append(f"Pack contains a symbolic-link directory: {child.relative_to(pack_path)}")
                        else:
                            safe_dirs.append(dirname)
                    dirs[:] = safe_dirs
                    for filename in files:
                        item = current_path / filename
                        try:
                            item_stat = item.lstat()
                            if stat.S_ISLNK(item_stat.st_mode):
                                errors.append(f"Pack contains a symbolic-link file: {item.relative_to(pack_path)}")
                            elif not stat.S_ISREG(item_stat.st_mode):
                                errors.append(f"Pack contains a non-regular file: {item.relative_to(pack_path)}")
                            else:
                                size += item_stat.st_size
                        except OSError as exc:
                            errors.append(f"Could not inspect pack file {item.relative_to(pack_path)}: {exc}")
            except OSError as exc:
                errors.append(f"Could not scan pack contents: {exc}")
            location_id = f"{kind}:{scope}:{world_name}:{pack_path.name}"
            records.append({
                "kind": kind, "scope": scope, "world_name": world_name,
                "folder": pack_path.name, "location_id": location_id,
                "name": pack_name, "uuid": pack_uuid,
                "version": version, "manifest_version": version_raw, "description": str(header.get("description", "")),
                "min_engine_version": min_engine,
                "format_version": format_version,
                "module_types": sorted(module_types),
                "module_summaries": [
                    {"type": str(module.get("type", "")), "uuid": str(module.get("uuid", "")),
                     "version": self._version_text(module.get("version")), "entry": str(module.get("entry", ""))[:512]}
                    for module in modules if isinstance(module, dict)
                ],
                "capabilities": [str(value)[:120] for value in manifest.get("capabilities", []) if isinstance(value, str)] if isinstance(manifest.get("capabilities", []), list) else [],
                "authors": [str(value)[:120] for value in (manifest.get("metadata", {}).get("authors", []) if isinstance(manifest.get("metadata", {}), dict) else []) if isinstance(value, str)][:20],
                "license": str(manifest.get("metadata", {}).get("license", ""))[:200] if isinstance(manifest.get("metadata", {}), dict) else "",
                "pack_scope": str(header.get("pack_scope", ""))[:40],
                "dependencies_raw": dependencies,
                "dependencies": [], "valid": not errors, "errors": errors,
                "size": size, "manifest": manifest,
            })
        for record in records:
            resolved: list[dict[str, Any]] = []
            for dependency in record.pop("dependencies_raw", []):
                if not isinstance(dependency, dict):
                    resolved.append({"resolved": False, "detail": "Invalid dependency entry"})
                    continue
                module_name = str(dependency.get("module_name", "")).strip()
                dependency_uuid = str(dependency.get("uuid", ""))
                expected_version = self._version_text(dependency.get("version"))
                if module_name:
                    if not re.fullmatch(r"@[A-Za-z0-9_./-]{1,120}", module_name) or not expected_version:
                        resolved.append({"module_name": module_name, "resolved": False, "detail": "Built-in module dependency has an invalid name or version"})
                    else:
                        resolved.append({"module_name": module_name, "version": expected_version, "resolved": True, "detail": "Built-in script module; runtime compatibility depends on the installed BDS build"})
                    continue
                try:
                    dependency_uuid = str(uuidlib.UUID(dependency_uuid))
                except (ValueError, AttributeError):
                    resolved.append({"uuid": dependency_uuid, "resolved": False, "detail": "Dependency UUID is invalid"})
                    continue
                candidates = [item for item in records if item.get("uuid", "").lower() == dependency_uuid.lower()]
                owner = record
                visible_candidates = [item for item in candidates if item["scope"] == "shared" or (owner["scope"] == "world" and item["scope"] == "world" and item["world_name"] == owner["world_name"])]
                target = visible_candidates[0] if len(visible_candidates) == 1 else None
                if not expected_version:
                    resolved.append({"uuid": dependency_uuid, "version": "", "resolved": False, "detail": "Dependency version is missing or invalid"})
                elif len(visible_candidates) > 1:
                    resolved.append({"uuid": dependency_uuid, "version": expected_version, "resolved": False, "detail": "Dependency UUID is ambiguous in this scope"})
                elif not target:
                    resolved.append({"uuid": dependency_uuid, "version": expected_version, "resolved": False, "detail": "Required pack is not installed in this pack's visible scope"})
                else:
                    version_ok = target["version"] == expected_version
                    resolved.append({"uuid": dependency_uuid, "version": expected_version, "resolved": version_ok, "detail": f"Found {target['name']} {target['version']}" if version_ok else f"Installed version is {target['version']}, expected {expected_version}", "location_id": target["location_id"]})
            record["dependencies"] = resolved
            missing = [dep for dep in resolved if not dep.get("resolved")]
            if missing:
                record["errors"].extend(dep.get("detail", "Unresolved dependency") for dep in missing)
                record["valid"] = False
        uuid_locations: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            if record.get("uuid"):
                uuid_locations.setdefault(record["uuid"].lower(), []).append(record)
        for duplicates in uuid_locations.values():
            if len(duplicates) > 1:
                locations_text = ", ".join(item["location_id"] for item in duplicates)
                for record in duplicates:
                    record["duplicate_uuid"] = True
                    record["errors"].append(f"Pack UUID is duplicated across installed locations: {locations_text}")
                    record["valid"] = False
        worlds = self._worlds_root()
        usage: dict[str, list[str]] = {}
        if worlds.is_dir():
            for world in worlds.iterdir():
                if not world.is_dir() or world.is_symlink():
                    continue
                for kind, filename in (("behavior", "world_behavior_packs.json"), ("resource", "world_resource_packs.json")):
                    data = safe_json_read(world / filename, [])
                    if isinstance(data, list):
                        for entry in data:
                            if isinstance(entry, dict) and entry.get("pack_id"):
                                usage.setdefault(str(entry["pack_id"]).lower(), []).append(world.name)
        for record in records:
            record["active_worlds"] = sorted(set(usage.get(record["uuid"].lower(), []))) if record["uuid"] else []
            record.pop("manifest", None)
        return records

    def packs(self) -> dict[str, Any]:
        result: dict[str, list[dict[str, Any]]] = {"behavior": [], "resource": []}
        for record in self._pack_records():
            result[record["kind"]].append(record)
        return result

    @staticmethod
    def _world_pack_refs(world_path: Path, kind: str) -> tuple[list[dict[str, Any]], list[str]]:
        filename = "world_behavior_packs.json" if kind == "behavior" else "world_resource_packs.json"
        path = world_path / filename
        if path.is_symlink():
            return [], [f"{filename} is a symbolic link"]
        if not path.exists():
            return [], []
        try:
            if path.stat().st_size > 2_000_000:
                raise ValueError("file exceeds the 2 MB safety limit")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("root must be an array")
            cleaned = []
            seen: set[str] = set()
            for item in raw:
                if not isinstance(item, dict) or not item.get("pack_id") or "version" not in item:
                    raise ValueError("each pack entry must include pack_id and version")
                try:
                    pack_id = str(uuidlib.UUID(str(item["pack_id"])))
                except (ValueError, AttributeError) as exc:
                    raise ValueError("each pack_id must be a valid UUID") from exc
                if pack_id.lower() in seen:
                    raise ValueError(f"duplicate pack reference: {pack_id}")
                seen.add(pack_id.lower())
                if not Manager._version_text(item.get("version")):
                    raise ValueError(f"pack reference {pack_id} has an invalid version")
                clone = dict(item)
                clone["pack_id"] = pack_id
                cleaned.append(clone)
            return cleaned, []
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return [], [f"{filename} cannot be read: {exc}"]

    def world_detail(self, name: str) -> dict[str, Any]:
        path = self._world_path(name)
        if not path.is_dir():
            raise ValueError("World not found.")
        all_packs = self._pack_records()
        refs: dict[str, list[dict[str, Any]]] = {}
        issues: list[str] = []
        for kind in ("behavior", "resource"):
            entries, errors = self._world_pack_refs(path, kind)
            refs[kind] = entries
            issues.extend(errors)
        # Invalid/ambiguous manifests must never be treated as satisfying an
        # active world reference. Keep them visible in the pack library, but
        # report their world references as missing until the ambiguity is fixed.
        installed = {p["uuid"].lower(): p for p in all_packs if p.get("uuid") and p.get("valid")}
        for kind, entries in refs.items():
            for entry in entries:
                record = installed.get(str(entry.get("pack_id", "")).lower())
                if not record:
                    issues.append(f"Missing {kind} pack: {entry.get('pack_id')}")
        summary = next((w for w in self.worlds() if w["name"] == name), None)
        if not summary:
            raise ValueError("World not found.")
        for kind in refs:
            active_ids = {str(item.get("pack_id", "")).lower() for item in refs[kind]}
            refs[kind] = [
                {"pack_id": item.get("pack_id"), "version": item.get("version"),
                 "installed": str(item.get("pack_id", "")).lower() in installed,
                 "name": installed.get(str(item.get("pack_id", "")).lower(), {}).get("name", "Missing pack")}
                for item in refs[kind]
            ]
        available = {kind: [p for p in all_packs if p["kind"] == kind and (p["scope"] == "shared" or p["world_name"] == name)] for kind in ("behavior", "resource")}
        for kind, items in available.items():
            active_ids = {str(item.get("pack_id", "")).lower() for item in refs[kind]}
            for item in items:
                item["enabled"] = item.get("uuid", "").lower() in active_ids
        return {"world": summary, "references": refs, "available_packs": available, "issues": issues}

    def worlds(self) -> list[dict[str, Any]]:
        base = self._worlds_root()
        active = self._read_props().get("level-name", "Bedrock level")
        if not base.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for folder in sorted(base.iterdir(), key=lambda item: item.name.lower()):
            if not folder.is_dir() or folder.is_symlink() or folder.name.startswith(".bds-omni-"):
                continue
            size = 0
            file_count = 0
            db_file_count = 0
            has_symlink = False
            try:
                for current, dirs, files in os.walk(folder, followlinks=False):
                    current_path = Path(current)
                    kept_dirs = []
                    for dirname in dirs:
                        child = current_path / dirname
                        if child.is_symlink():
                            has_symlink = True
                        else:
                            kept_dirs.append(dirname)
                    dirs[:] = kept_dirs
                    for filename in files:
                        item = current_path / filename
                        try:
                            st = item.lstat()
                            if stat.S_ISLNK(st.st_mode):
                                has_symlink = True
                                continue
                            if stat.S_ISREG(st.st_mode):
                                size += st.st_size
                                file_count += 1
                                if current_path.name == "db":
                                    db_file_count += 1
                        except OSError:
                            continue
            except OSError:
                has_symlink = True
            display_name = folder.name
            display_path = folder / "levelname.txt"
            if display_path.is_file() and not display_path.is_symlink():
                try:
                    content = display_path.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        display_name = content[:120]
                except OSError:
                    pass
            behavior_refs, behavior_errors = self._world_pack_refs(folder, "behavior")
            resource_refs, resource_errors = self._world_pack_refs(folder, "resource")
            issues = []
            level_dat = folder / "level.dat"
            database = folder / "db"
            if level_dat.is_symlink() or not level_dat.is_file():
                issues.append("level.dat is missing or is a symbolic link")
            if database.is_symlink() or not database.is_dir():
                issues.append("db directory is missing or is a symbolic link")
            elif db_file_count == 0:
                issues.append("db directory has no regular files")
            if has_symlink:
                issues.append("world contains symbolic links; export, duplicate and pack activation are disabled for safety")
            issues.extend(behavior_errors + resource_errors)
            try:
                modified = datetime.fromtimestamp(folder.stat().st_mtime).astimezone().isoformat(timespec="minutes")
            except (OSError, OverflowError, ValueError):
                # A world may be renamed or removed by an external file manager
                # while the library is being refreshed. Keep the scan usable.
                modified = "Unavailable"
                issues.append("World metadata changed while it was being scanned")
            result.append({
                "name": folder.name, "display_name": display_name,
                "active": folder.name == active, "size": size, "file_count": file_count,
                "modified": modified,
                "has_level_dat": (folder / "level.dat").is_file(), "has_db": (folder / "db").is_dir(),
                "db_file_count": db_file_count, "behavior_pack_count": len(behavior_refs),
                "resource_pack_count": len(resource_refs), "issues": issues,
                "contains_symlinks": has_symlink,
            })
        return result

    def _preflight_world_tree(self, folder: Path) -> tuple[int, int]:
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("World folder is missing or is not a normal directory.")
        size = 0
        count = 0
        for current, dirs, files in os.walk(folder, followlinks=False):
            current_path = Path(current)
            for dirname in list(dirs):
                child = current_path / dirname
                if child.is_symlink():
                    raise ValueError(f"World contains a symbolic link: {child.relative_to(folder)}")
            for filename in files:
                item = current_path / filename
                st = item.lstat()
                if not stat.S_ISREG(st.st_mode):
                    raise ValueError(f"World contains a non-regular file: {item.relative_to(folder)}")
                size += st.st_size
                count += 1
        if not (folder / "level.dat").is_file() or not (folder / "db").is_dir():
            raise ValueError("This does not look like a Bedrock world: level.dat and db/ are required.")
        if not any((folder / "db").iterdir()):
            raise ValueError("The Bedrock world database folder is empty.")
        return size, count

    @staticmethod
    def _archive_entries(zf: zipfile.ZipFile, *, max_entries: int, max_bytes: int) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
        infos = zf.infolist()
        if not infos or len(infos) > max_entries:
            raise ValueError(f"Archive must contain between 1 and {max_entries:,} entries.")
        result: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        seen: set[str] = set()
        total = 0
        for info in infos:
            raw = info.filename
            if not raw or "\\" in raw or "\x00" in raw or raw.startswith("/"):
                raise ValueError("Archive contains an unsafe path.")
            path = PurePosixPath(raw)
            if any(part in ("", ".", "..") for part in path.parts) or (path.parts and ":" in path.parts[0]):
                raise ValueError("Archive contains a path traversal or absolute path.")
            key = str(path).casefold()
            if key in seen:
                raise ValueError("Archive contains duplicate paths (case-insensitive).")
            seen.add(key)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK or (mode not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError("Archive contains a symbolic link or special file.")
            if info.flag_bits & 0x1:
                raise ValueError("Encrypted archives are not supported.")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError("Only stored/deflated ZIP archives are supported.")
            if info.file_size < 0 or info.file_size > max_bytes:
                raise ValueError("Archive contains an entry larger than the extraction limit.")
            total += info.file_size
            if total > max_bytes:
                raise ValueError("Archive expands beyond the configured extraction limit.")
            result.append((info, path))
        return result

    @staticmethod
    def _root_prefix(entries: list[tuple[zipfile.ZipInfo, PurePosixPath]], required_file: str) -> str:
        files = {str(path) for info, path in entries if not info.is_dir()}
        if required_file in files:
            return ""
        first_parts = {path.parts[0] for info, path in entries if path.parts}
        candidates = [part for part in first_parts if f"{part}/{required_file}" in files]
        if len(candidates) == 1 and all(path.parts and path.parts[0] == candidates[0] for _, path in entries):
            return candidates[0] + "/"
        raise ValueError(f"Archive must contain {required_file} at its root (or inside one enclosing folder).")

    @staticmethod
    def _extract_entries(zf: zipfile.ZipFile, entries: list[tuple[zipfile.ZipInfo, PurePosixPath]], destination: Path, prefix: str = "", max_bytes: int = MAX_CONTENT_EXTRACTED_BYTES) -> int:
        destination.mkdir(parents=True, exist_ok=False)
        written_total = 0
        for info, path in entries:
            raw = str(path)
            if prefix:
                if not raw.startswith(prefix):
                    continue
                raw = raw[len(prefix):]
                if not raw:
                    continue
                path = PurePosixPath(raw)
            if not path.parts:
                continue
            target = destination.joinpath(*path.parts)
            resolved = target.resolve()
            if destination.resolve() not in resolved.parents and resolved != destination.resolve():
                raise ValueError("Archive entry escapes the extraction folder.")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            actual = 0
            with zf.open(info, "r") as source, target.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    actual += len(chunk)
                    written_total += len(chunk)
                    if actual > info.file_size or written_total > max_bytes:
                        raise ValueError("Archive expanded beyond its declared or configured size limit.")
                    output.write(chunk)
            if actual != info.file_size:
                raise ValueError(f"Archive entry has an unexpected length: {info.filename}")
        return written_total

    def import_world_archive(self, archive_path: Path, requested_name: str = "") -> dict[str, Any]:
        self._require_content_server_stopped()
        if archive_path.stat().st_size > MAX_CONTENT_UPLOAD_BYTES:
            raise ValueError("World archive exceeds the 8 GiB upload limit.")
        worlds_root = self._worlds_root(create=True)
        stage = worlds_root / f".bds-omni-world-import-{secrets.token_hex(8)}"
        installed = False
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                entries = self._archive_entries(zf, max_entries=MAX_CONTENT_ARCHIVE_ENTRIES, max_bytes=MAX_CONTENT_EXTRACTED_BYTES)
                prefix = self._root_prefix(entries, "level.dat")
                self._extract_entries(zf, entries, stage, prefix)
                bad = zf.testzip()
                if bad:
                    raise ValueError(f"Archive CRC verification failed for {bad}.")
            self._preflight_world_tree(stage)
            name = requested_name.strip() if requested_name else ""
            if not name:
                levelname = stage / "levelname.txt"
                if levelname.is_file():
                    try:
                        name = levelname.read_text(encoding="utf-8", errors="replace").strip()
                    except OSError:
                        name = ""
            if not name:
                name = archive_path.stem
            name = self._validate_world_name(name)
            target = worlds_root / name
            if target.exists() or target.is_symlink():
                raise ValueError(f"A world folder named '{name}' already exists. Choose a different import name.")
            os.replace(stage, target)
            installed = True
            self._append_log(f"[manager] Imported world '{name}' from {archive_path.name}.")
            return {"ok": True, "message": f"Imported world '{name}'. Set it active in the world library when ready.", "world": name}
        finally:
            if not installed and stage.exists() and not stage.is_symlink():
                shutil.rmtree(stage, ignore_errors=True)

    def export_world_archive(self, name: str, destination: Path | None = None) -> Path:
        self._require_content_server_stopped()
        source = self._world_path(name)
        if not source.is_dir():
            raise ValueError("World not found.")
        world_size, world_files = self._preflight_world_tree(source)
        if world_files > MAX_CONTENT_ARCHIVE_ENTRIES:
            raise ValueError(f"World has more than {MAX_CONTENT_ARCHIVE_ENTRIES:,} files and exceeds the export entry limit.")
        if world_size > MAX_CONTENT_EXTRACTED_BYTES:
            raise ValueError("World exceeds the configured 32 GiB archive/export limit.")
        export_dir = destination or (self.backup_dir / "world-exports")
        resolved_export_dir = export_dir.expanduser().resolve()
        if resolved_export_dir == self.root.resolve() or self.root.resolve() in resolved_export_dir.parents:
            raise ValueError("World exports must be stored outside the BDS server directory.")
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:48] or "world"
        output = export_dir / f"{safe_name}-{stamp}.mcworld"
        if output.exists():
            output = export_dir / f"{safe_name}-{stamp}-{secrets.token_hex(3)}.mcworld"
        temp = output.with_name(output.name + f".{secrets.token_hex(4)}.tmp")
        try:
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4, allowZip64=True) as zf:
                for current, dirs, files in os.walk(source, followlinks=False):
                    current_path = Path(current)
                    dirs[:] = sorted(d for d in dirs if not (current_path / d).is_symlink())
                    for filename in sorted(files):
                        item = current_path / filename
                        st = item.lstat()
                        if not stat.S_ISREG(st.st_mode):
                            raise ValueError(f"World contains a non-regular file: {item.relative_to(source)}")
                        rel = item.relative_to(source).as_posix()
                        zf.write(item, rel)
            with zipfile.ZipFile(temp, "r") as check:
                bad = check.testzip()
                if bad:
                    raise ValueError(f"Export verification failed for {bad}.")
                names = set(check.namelist())
                if "level.dat" not in names or not any(item.startswith("db/") and not item.endswith("/") for item in names):
                    raise ValueError("Export verification did not find level.dat and database files.")
            os.replace(temp, output)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        self._append_log(f"[manager] Exported world '{name}' to {output.name}.")
        return output

    def world_action(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        self._require_content_server_stopped()
        action = str(action)
        if action == "create":
            name = self._validate_world_name(data.get("name"))
            target = self._worlds_root(create=True) / name
            if target.exists() or target.is_symlink():
                raise ValueError("A world with that folder name already exists.")
            self.save_properties({"level-name": name})
            return {"ok": True, "message": f"New world '{name}' selected. BDS will generate it when you start the server.", "world": name}
        if action == "duplicate":
            source_name = self._validate_world_name(data.get("name"))
            target_name = self._validate_world_name(data.get("new_name"))
            source = self._world_path(source_name)
            if not source.is_dir():
                raise ValueError("Source world not found.")
            size, _ = self._preflight_world_tree(source)
            if shutil.disk_usage(self._worlds_root()).free < size:
                raise ValueError("Not enough free disk space to duplicate this world.")
            target = self._worlds_root() / target_name
            if target.exists() or target.is_symlink():
                raise ValueError("A world with the destination name already exists.")
            stage = self._worlds_root() / f".bds-omni-world-copy-{secrets.token_hex(8)}"
            try:
                shutil.copytree(source, stage, symlinks=False)
                self._preflight_world_tree(stage)
                levelname = stage / "levelname.txt"
                if levelname.is_file() and not levelname.is_symlink():
                    levelname.write_text(target_name + "\n", encoding="utf-8")
                os.replace(stage, target)
            finally:
                if stage.exists() and not stage.is_symlink():
                    shutil.rmtree(stage, ignore_errors=True)
            self._append_log(f"[manager] Duplicated world '{source_name}' as '{target_name}'.")
            return {"ok": True, "message": f"Duplicated '{source_name}' as '{target_name}'.", "world": target_name}
        if action == "rename":
            old_name = self._validate_world_name(data.get("name"))
            new_name = self._validate_world_name(data.get("new_name"))
            source = self._world_path(old_name)
            target = self._worlds_root() / new_name
            if not source.is_dir():
                raise ValueError("World not found.")
            if target.exists() or target.is_symlink():
                raise ValueError("A world with the destination name already exists.")
            active = self._read_props().get("level-name", "Bedrock level") == old_name
            levelname_path = source / "levelname.txt"
            old_display_name = None
            if levelname_path.is_file() and not levelname_path.is_symlink():
                old_display_name = levelname_path.read_bytes()
            os.replace(source, target)
            try:
                renamed_levelname = target / "levelname.txt"
                if renamed_levelname.is_file() and not renamed_levelname.is_symlink():
                    renamed_levelname.write_text(new_name + "\n", encoding="utf-8")
                if active:
                    self.save_properties({"level-name": new_name})
            except Exception:
                try:
                    if old_display_name is not None and (target / "levelname.txt").is_file():
                        atomic_write(target / "levelname.txt", old_display_name.decode("utf-8", errors="replace"))
                finally:
                    os.replace(target, source)
                raise
            self._append_log(f"[manager] Renamed world '{old_name}' to '{new_name}'.")
            return {"ok": True, "message": f"Renamed world '{old_name}' to '{new_name}'." + (" The active level-name property was updated." if active else ""), "world": new_name}
        if action == "delete":
            name = self._validate_world_name(data.get("name"))
            source = self._world_path(name)
            if not source.is_dir():
                raise ValueError("World not found.")
            if self._read_props().get("level-name", "Bedrock level") == name:
                raise ValueError("The active world cannot be deleted. Switch to another world first.")
            deleted_dir = self.backup_dir / "deleted-worlds"
            archive = self.export_world_archive(name, deleted_dir)
            # Move the original directory out of the visible worlds namespace
            # atomically before cleanup. If recursive deletion fails part-way,
            # restore a complete world from the already-verified archive and
            # retain the hidden quarantine for manual recovery.
            quarantine = self._worlds_root() / f".bds-omni-world-delete-{secrets.token_hex(8)}"
            try:
                os.replace(source, quarantine)
            except OSError as exc:
                raise ValueError(f"World was safely archived at {archive}, but could not be moved to deletion quarantine: {exc}") from exc
            try:
                shutil.rmtree(quarantine)
            except OSError as exc:
                try:
                    recovery = self.import_world_archive(archive, name)
                    raise ValueError(
                        f"Deletion failed ({exc}). The world has been restored from the verified archive. "
                        f"A hidden recovery copy may remain at {quarantine.name}; archive: {archive.name}."
                    ) from exc
                except ValueError as restore_exc:
                    if "world has been restored" in str(restore_exc):
                        raise
                    raise ValueError(
                        f"Deletion failed ({exc}) and automatic restoration failed ({restore_exc}). "
                        f"Do not remove the recovery data at {quarantine} or the archive at {archive}."
                    ) from exc
            self._append_log(f"[manager] Deleted world '{name}' after verified archive {archive.name}.")
            return {"ok": True, "message": f"World '{name}' was archived and removed. Recovery archive: {archive.name}", "archive": archive.name}
        raise ValueError("Unknown world action.")

    def pack_action(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        self._require_content_server_stopped()
        action = str(action)
        location_id = str(data.get("location_id", ""))
        records = self._pack_records()
        record = next((item for item in records if item["location_id"] == location_id), None)
        if not record:
            raise ValueError("Pack location was not found. Refresh the pack list and try again.")
        pack_path = self._pack_path_for_record(record)
        if action == "remove":
            if not record.get("uuid"):
                raise ValueError("Cannot safely remove a pack without a valid manifest UUID.")
            same_uuid = [item for item in records if item.get("uuid", "").lower() == record["uuid"].lower()]
            if len(same_uuid) > 1:
                raise ValueError("This pack UUID is installed in multiple locations. Resolve the duplicate UUID manually before removing or activating any copy.")
            for other in records:
                if other["location_id"] == location_id:
                    continue
                if any(dep.get("uuid", "").lower() == record["uuid"].lower() for dep in other.get("dependencies", [])):
                    if other["active_worlds"]:
                        raise ValueError(f"Pack is required by active pack '{other['name']}' in world(s): {', '.join(other['active_worlds'])}. Remove that dependent pack first.")
            export_dir = self.backup_dir / "removed-packs"
            archive = self._export_pack_path(pack_path, record, export_dir)
            worlds = self._worlds_root()
            snapshots: list[tuple[Path, bytes]] = []
            updates: list[tuple[Path, bytes]] = []
            try:
                # Validate every affected reference file before changing any of them.
                if worlds.is_dir():
                    for world in worlds.iterdir():
                        if not world.is_dir() or world.is_symlink():
                            continue
                        filename = "world_behavior_packs.json" if record["kind"] == "behavior" else "world_resource_packs.json"
                        ref_path = world / filename
                        if ref_path.is_symlink():
                            raise ValueError(f"Cannot safely update {ref_path}: it is a symbolic link.")
                        if not ref_path.exists():
                            continue
                        current = ref_path.read_bytes()
                        if len(current) > 2_000_000:
                            raise ValueError(f"Cannot safely update {world.name}/{filename}: file exceeds the 2 MB safety limit.")
                        try:
                            entries = json.loads(current.decode("utf-8"))
                        except (UnicodeError, json.JSONDecodeError) as exc:
                            raise ValueError(f"Cannot safely remove references from {world.name}/{filename}: {exc}") from exc
                        if not isinstance(entries, list):
                            raise ValueError(f"Cannot safely update {world.name}/{filename}: expected a JSON array.")
                        filtered = [entry for entry in entries if not (isinstance(entry, dict) and str(entry.get("pack_id", "")).lower() == record["uuid"].lower())]
                        if len(filtered) != len(entries):
                            snapshots.append((ref_path, current))
                            updates.append((ref_path, (json.dumps(filtered, indent=2) + "\n").encode("utf-8")))
                quarantine = pack_path.with_name(f".bds-omni-pack-remove-{secrets.token_hex(8)}")
                if quarantine.exists() or quarantine.is_symlink():
                    raise ValueError("A pack-removal quarantine path unexpectedly exists. Retry the operation.")
                # Move the complete pack out of the visible inventory atomically. If any
                # following operation fails, it can be put back without reconstructing it.
                os.replace(pack_path, quarantine)
                try:
                    for ref_path, payload in updates:
                        before = next(content for path, content in snapshots if path == ref_path)
                        backup = ref_path.with_name(ref_path.name + ".before-bds-omni.bak")
                        if not backup.exists():
                            shutil.copy2(ref_path, backup)
                        atomic_write(ref_path, payload.decode("utf-8"))
                except Exception:
                    for ref_path, before in reversed(snapshots):
                        try:
                            atomic_write(ref_path, before.decode("utf-8"))
                        except Exception:
                            pass
                    if quarantine.exists() and not pack_path.exists():
                        try:
                            os.replace(quarantine, pack_path)
                        except OSError as restore_exc:
                            raise ValueError(
                                f"Pack removal failed and its original folder could not be moved back. "
                                f"Keep the verified archive at {archive} and recovery folder at {quarantine}; do not delete either."
                            ) from restore_exc
                    raise
                cleanup_warning = ""
                try:
                    shutil.rmtree(quarantine)
                except OSError as exc:
                    # The live pack path and world references are already removed. Keep
                    # the hidden quarantine and verified archive for manual cleanup.
                    cleanup_warning = f" Hidden cleanup folder remains at {quarantine.name} ({exc}); the verified archive is safe at {archive.name}."
            except Exception:
                # If validation or the atomic move failed, the original pack remains in place.
                raise
            self._append_log(f"[manager] Removed pack '{record['name']}' after archiving it as {archive.name}.")
            return {"ok": True, "message": f"Removed pack '{record['name']}'. A recovery archive was saved as {archive.name}.{cleanup_warning}", "archive": archive.name, "cleanup_warning": cleanup_warning}
        raise ValueError("Unknown pack action.")

    def _pack_path_for_record(self, record: dict[str, Any]) -> Path:
        if record["scope"] == "shared":
            parent = self.root / ("behavior_packs" if record["kind"] == "behavior" else "resource_packs")
        else:
            world = self._world_path(record["world_name"])
            parent = world / ("behavior_packs" if record["kind"] == "behavior" else "resource_packs")
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("Pack directory is no longer available.")
        target = parent / record["folder"]
        if target.is_symlink() or not target.is_dir() or parent.resolve() not in target.resolve().parents:
            raise ValueError("Pack path is not a normal directory.")
        return target

    def _export_pack_path(self, source: Path, record: dict[str, Any], destination: Path | None = None) -> Path:
        if source.is_symlink() or not source.is_dir():
            raise ValueError("Pack source directory is missing or is not a normal directory.")
        destination = destination or (self.backup_dir / "pack-exports")
        resolved_destination = destination.expanduser().resolve()
        if self.root.resolve() in resolved_destination.parents or resolved_destination == self.root.resolve():
            raise ValueError("Pack exports must be outside the BDS server directory.")
        destination.mkdir(parents=True, exist_ok=True)
        name_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", record["name"]).strip("._")[:40] or "pack"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        suffix = record["uuid"].replace("-", "")[:8] or "unknown"
        output = destination / f"{name_slug}-{suffix}-{stamp}.mcpack"
        if output.exists():
            output = destination / f"{name_slug}-{suffix}-{stamp}-{secrets.token_hex(3)}.mcpack"
        temp = output.with_name(output.name + f".{secrets.token_hex(4)}.tmp")
        try:
            file_count = 0
            total_size = 0
            for current, dirs, files in os.walk(source, followlinks=False):
                current_path = Path(current)
                if any((current_path / d).is_symlink() for d in dirs):
                    raise ValueError("Pack contains a symbolic link.")
                for filename in files:
                    item = current_path / filename
                    st = item.lstat()
                    if not stat.S_ISREG(st.st_mode):
                        raise ValueError("Pack contains a non-regular file.")
                    file_count += 1
                    total_size += st.st_size
                    if file_count > MAX_CONTENT_ARCHIVE_ENTRIES:
                        raise ValueError(f"Pack has more than {MAX_CONTENT_ARCHIVE_ENTRIES:,} files and exceeds the export entry limit.")
                    if total_size > MAX_CONTENT_EXTRACTED_BYTES:
                        raise ValueError("Pack exceeds the configured 32 GiB archive/export limit.")
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4, allowZip64=True) as zf:
                for current, dirs, files in os.walk(source, followlinks=False):
                    current_path = Path(current)
                    dirs[:] = sorted(dirs)
                    for filename in sorted(files):
                        item = current_path / filename
                        zf.write(item, item.relative_to(source).as_posix())
            with zipfile.ZipFile(temp, "r") as check:
                if check.testzip() or "manifest.json" not in check.namelist():
                    raise ValueError("Pack export verification failed.")
            os.replace(temp, output)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        return output

    def export_pack_archive(self, location_id: str) -> Path:
        self._require_content_server_stopped()
        record = next((item for item in self._pack_records() if item["location_id"] == location_id), None)
        if not record:
            raise ValueError("Pack location was not found.")
        return self._export_pack_path(self._pack_path_for_record(record), record)

    def _pack_staging_root(self) -> Path:
        root = self.root.resolve()
        staging = self.root / ".bds-omni-pack-staging"
        if staging.is_symlink():
            raise ValueError("Pack staging path is a symbolic link.")
        staging.mkdir(parents=True, exist_ok=True)
        resolved = staging.resolve()
        if root not in (resolved, *resolved.parents):
            raise ValueError("Pack staging path resolves outside the BDS folder.")
        return staging

    def _prepare_pack_archive(self, archive_path: Path, stage_parent: Path, stage_name: str) -> dict[str, Any]:
        """Extract and validate one .mcpack without installing it yet."""
        if archive_path.is_symlink() or not archive_path.is_file():
            raise ValueError("Pack archive is missing or is not a regular file.")
        if archive_path.stat().st_size > MAX_CONTENT_UPLOAD_BYTES:
            raise ValueError("Pack archive exceeds the 8 GiB upload limit.")
        stage = stage_parent / stage_name
        if stage.exists() or stage.is_symlink():
            raise ValueError("Pack staging entry already exists.")
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                entries = self._archive_entries(zf, max_entries=MAX_CONTENT_ARCHIVE_ENTRIES, max_bytes=MAX_CONTENT_EXTRACTED_BYTES)
                prefix = self._root_prefix(entries, "manifest.json")
                extracted_size = self._extract_entries(zf, entries, stage, prefix)
                bad = zf.testzip()
                if bad:
                    raise ValueError(f"Pack archive CRC verification failed for {bad}.")
            manifest_path = stage / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or not isinstance(manifest.get("header"), dict):
                raise ValueError("Pack manifest must contain a header object.")
            header = manifest["header"]
            format_version = manifest.get("format_version")
            if type(format_version) is not int or format_version not in (1, 2, 3):
                raise ValueError("Pack manifest format_version must be 1, 2, or 3.")
            try:
                pack_uuid = str(uuidlib.UUID(str(header.get("uuid", ""))))
            except (ValueError, AttributeError) as exc:
                raise ValueError("Pack header UUID is missing or invalid.") from exc
            pack_version = self._version_text(header.get("version"))
            pack_name = str(header.get("name", "")).strip()
            if not pack_name or not pack_version:
                raise ValueError("Pack manifest requires a name and a valid version.")
            min_engine = header.get("min_engine_version")
            if min_engine is not None and not self._version_text(min_engine):
                raise ValueError("Pack header min_engine_version is invalid.")
            preview_warning = "Manifest format 3 is preview-only and may not be supported by stable BDS." if format_version == 3 else ""
            modules = manifest.get("modules")
            if not isinstance(modules, list) or not modules:
                raise ValueError("Pack manifest must contain at least one module.")
            module_types: set[str] = set()
            module_uuids: set[str] = set()
            module_summaries: list[dict[str, str]] = []
            for module in modules:
                if not isinstance(module, dict):
                    raise ValueError("Pack manifest contains an invalid module entry.")
                module_type = str(module.get("type", ""))
                module_types.add(module_type)
                try:
                    module_uuid = str(uuidlib.UUID(str(module.get("uuid", ""))))
                except (ValueError, AttributeError) as exc:
                    raise ValueError("A pack module UUID is missing or invalid.") from exc
                if module_uuid == pack_uuid or module_uuid in module_uuids:
                    raise ValueError("Pack module UUIDs must be unique and differ from the header UUID.")
                module_uuids.add(module_uuid)
                module_version = self._version_text(module.get("version"))
                if not module_version:
                    raise ValueError("A pack module version is missing or invalid.")
                module_summaries.append({
                    "type": module_type,
                    "uuid": module_uuid,
                    "version": module_version,
                    "entry": str(module.get("entry", ""))[:512],
                })
            if "skin_pack" in module_types:
                raise ValueError("This archive is a skin pack. Skin packs are client-side content and cannot be installed or activated by Bedrock Dedicated Server.")
            if "world_template" in module_types:
                raise ValueError("This archive is a world template, not a server add-on pack. Import a compatible world archive through Worlds & Packs instead.")
            has_behavior = bool(module_types & {"data", "script", "client_data"})
            has_resource = "resources" in module_types
            if has_behavior == has_resource:
                detected = ", ".join(sorted(module_types)) or "none"
                raise ValueError(f"Unsupported or mixed Bedrock module types ({detected}). BDS add-on installation supports behavior packs (data/script) and resource packs (resources); a pack cannot mix those types.")
            kind = "behavior" if has_behavior else "resource"
            dependencies = manifest.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise ValueError("Pack manifest dependencies must be an array.")
            for dependency in dependencies:
                if not isinstance(dependency, dict):
                    raise ValueError("Pack manifest contains an invalid dependency entry.")
                dependency_version = self._version_text(dependency.get("version"))
                if not dependency_version:
                    raise ValueError("Pack dependency version is missing or invalid.")
                if dependency.get("module_name"):
                    if not re.fullmatch(r"@[A-Za-z0-9_./-]{1,120}", str(dependency.get("module_name", ""))):
                        raise ValueError("Pack dependency module_name is invalid.")
                else:
                    try:
                        uuidlib.UUID(str(dependency.get("uuid", "")))
                    except (ValueError, AttributeError) as exc:
                        raise ValueError("Pack dependency UUID is missing or invalid.") from exc
            return {
                "stage": stage,
                "kind": kind,
                "uuid": pack_uuid,
                "name": pack_name,
                "version": pack_version,
                "format_version": format_version,
                "module_types": sorted(module_types),
                "modules": module_summaries,
                "dependencies": dependencies,
                "min_engine_version": self._version_text(min_engine) if min_engine is not None else "",
                "preview_warning": preview_warning,
                "extracted_size": extracted_size,
                "archive_entry_count": len(entries),
                "manifest": manifest,
            }
        except (OSError, json.JSONDecodeError, UnicodeError, zipfile.BadZipFile) as exc:
            if stage.exists() and not stage.is_symlink():
                shutil.rmtree(stage, ignore_errors=True)
            raise ValueError(f"Could not inspect pack archive: {exc}") from exc
        except Exception:
            if stage.exists() and not stage.is_symlink():
                shutil.rmtree(stage, ignore_errors=True)
            raise

    def _pack_install_target(self, prepared: dict[str, Any], requested_scope: str, world_name: str) -> tuple[Path, str]:
        if requested_scope == "world":
            world_path = self._world_path(world_name)
            if not world_path.is_dir():
                raise ValueError("Choose an existing world for a world-specific pack.")
            base = world_path / ("behavior_packs" if prepared["kind"] == "behavior" else "resource_packs")
        elif requested_scope == "shared":
            base = self.root / ("behavior_packs" if prepared["kind"] == "behavior" else "resource_packs")
        else:
            raise ValueError("Pack scope must be shared or world.")
        if base.is_symlink():
            raise ValueError("Pack destination directory is a symbolic link.")
        base.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", prepared["name"]).strip("._")[:48] or "pack"
        target_name = f"{slug}-{prepared['uuid'].replace('-', '')[:8]}"
        target = base / target_name
        if target.exists() or target.is_symlink():
            raise ValueError(f"The generated pack folder already exists: {target_name}. Rename or remove it before importing.")
        return target, target_name

    def import_pack_archive(self, archive_path: Path, requested_scope: str = "shared", world_name: str = "") -> dict[str, Any]:
        self._require_content_server_stopped()
        if requested_scope not in ("shared", "world"):
            raise ValueError("Pack scope must be shared or world.")
        if requested_scope == "world":
            world_path = self._world_path(world_name)
            if not world_path.is_dir():
                raise ValueError("Choose an existing world for a world-specific pack.")
        stage_parent = self._pack_staging_root()
        stage_name = "single-" + secrets.token_hex(12)
        prepared: dict[str, Any] | None = None
        try:
            prepared = self._prepare_pack_archive(archive_path, stage_parent, stage_name)
            existing = [record for record in self._pack_records() if record.get("uuid", "").lower() == prepared["uuid"].lower()]
            if existing:
                raise ValueError(f"Pack UUID {prepared['uuid']} is already installed at {existing[0]['location_id']}.")
            target, target_name = self._pack_install_target(prepared, requested_scope, world_name)
            os.replace(prepared["stage"], target)
            self._append_log(f"[manager] Installed {prepared['kind']} pack '{prepared['name']}' in {requested_scope} scope.")
            message = f"Installed {prepared['kind']} pack '{prepared['name']}' ({prepared['version']}) in {requested_scope} scope."
            if prepared["preview_warning"]:
                message += " " + prepared["preview_warning"]
            return {"ok": True, "message": message, "warning": prepared["preview_warning"], "kind": prepared["kind"], "scope": requested_scope, "world": world_name if requested_scope == "world" else "", "uuid": prepared["uuid"], "folder": target_name, "installed_count": 1}
        finally:
            if prepared and prepared["stage"].exists() and not prepared["stage"].is_symlink():
                shutil.rmtree(prepared["stage"], ignore_errors=True)
            try:
                if stage_parent.is_dir() and not stage_parent.is_symlink() and not any(stage_parent.iterdir()):
                    stage_parent.rmdir()
            except OSError:
                pass

    @staticmethod
    def _pack_manifest_prefixes(entries: list[tuple[zipfile.ZipInfo, PurePosixPath]]) -> list[str]:
        """Find independent pack roots in a ZIP of one or more unpacked packs.

        A pack root is a directory containing manifest.json. When one pack root
        contains another manifest deeper in its own tree, the shallowest root
        wins so a pack's bundled/example content is not imported a second time.
        """
        candidates: set[str] = set()
        for info, path in entries:
            if info.is_dir() or path.name != "manifest.json":
                continue
            parent = path.parent.as_posix()
            candidates.add("" if parent == "." else parent.rstrip("/") + "/")
        if "" in candidates:
            return [""]
        roots: list[str] = []
        for prefix in sorted(candidates, key=lambda value: (value.count("/"), len(value), value.casefold())):
            if not any(prefix.startswith(parent) for parent in roots):
                roots.append(prefix)
        return roots

    @staticmethod
    def _write_pack_archive_from_zip_prefix(
        outer: zipfile.ZipFile,
        entries: list[tuple[zipfile.ZipInfo, PurePosixPath]],
        prefix: str,
        destination: Path,
    ) -> int:
        """Stream one unpacked pack directory from a composite ZIP to a temp mcpack."""
        selected = []
        for info, path in entries:
            if info.is_dir():
                continue
            raw = path.as_posix()
            if prefix:
                if not raw.startswith(prefix):
                    continue
                relative = raw[len(prefix):]
            else:
                relative = raw
            if relative:
                selected.append((info, relative))
        expected_size = sum(info.file_size for info, _ in selected)
        if expected_size > MAX_CONTENT_EXTRACTED_BYTES:
            raise ValueError("Unpacked add-on folder exceeds the 32 GiB extraction limit.")
        try:
            available = shutil.disk_usage(destination.parent).free
        except OSError:
            available = expected_size
        if available < expected_size + 1024 * 1024:
            raise ValueError("Not enough free disk space to stage this unpacked add-on folder safely.")
        written_total = 0
        member_count = 0
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as pack_zip:
            for info, relative in selected:
                member_count += 1
                output_info = zipfile.ZipInfo(relative)
                output_info.compress_type = zipfile.ZIP_STORED
                output_info.external_attr = info.external_attr
                actual = 0
                with outer.open(info, "r") as source, pack_zip.open(output_info, "w") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        actual += len(chunk)
                        written_total += len(chunk)
                        if actual > info.file_size or written_total > MAX_CONTENT_EXTRACTED_BYTES:
                            raise ValueError("Unpacked add-on folders exceed the 32 GiB extraction limit.")
                        output.write(chunk)
                if actual != info.file_size:
                    raise ValueError(f"ZIP pack entry has an unexpected length: {info.filename}")
        if member_count == 0:
            raise ValueError("A detected pack folder is empty.")
        return written_total

    def import_mod_archive(self, archive_path: Path, filename: str, requested_scope: str = "shared", world_name: str = "") -> dict[str, Any]:
        """Import Bedrock .mcpack/.mcaddon files and ZIPs of one or more pack folders."""
        self._require_content_server_stopped()
        suffix = Path(filename).suffix.lower()
        if suffix not in (".mcaddon", ".mcpack", ".zip"):
            raise ValueError("Bedrock add-ons must be .mcpack, .mcaddon, or a ZIP containing valid Bedrock pack folders. Java .jar mods are not compatible with BDS.")
        if archive_path.is_symlink() or not archive_path.is_file():
            raise ValueError("Add-on archive is missing or is not a regular file.")
        if archive_path.stat().st_size > MAX_CONTENT_UPLOAD_BYTES:
            raise ValueError("Mod archive exceeds the 8 GiB upload limit.")
        if suffix == ".mcpack":
            return self.import_pack_archive(archive_path, requested_scope, world_name)
        if requested_scope not in ("shared", "world"):
            raise ValueError("Install scope must be shared or world.")
        if requested_scope == "world" and not self._world_path(world_name).is_dir():
            raise ValueError("Choose an existing world for a world-specific add-on.")
        try:
            with zipfile.ZipFile(archive_path, "r") as outer:
                outer_entries = self._archive_entries(outer, max_entries=MAX_CONTENT_ARCHIVE_ENTRIES, max_bytes=MAX_CONTENT_EXTRACTED_BYTES)
                bad = outer.testzip()
                if bad:
                    raise ValueError(f"Add-on container CRC verification failed for {bad}.")
                nested = [(info, path) for info, path in outer_entries if not info.is_dir() and path.suffix.lower() == ".mcpack"]
                bundled_worlds = [path.name for info, path in outer_entries if not info.is_dir() and path.suffix.lower() == ".mcworld"]
                if bundled_worlds:
                    raise ValueError("This add-on bundle also contains a .mcworld archive. Import the world separately from Worlds & Packs; no content from this bundle was installed.")
                pack_prefixes = self._pack_manifest_prefixes(outer_entries)
                if not nested and not pack_prefixes:
                    raise ValueError("The archive contains no .mcpack files or folders with a root manifest.json. Import a Bedrock behavior/resource pack, .mcpack, or .mcaddon.")
                # Preserve the simple single-pack response for a plain pack ZIP or
                # a ZIP with one enclosing folder that _prepare_pack_archive knows.
                if not nested and len(pack_prefixes) == 1:
                    try:
                        self._root_prefix(outer_entries, "manifest.json")
                    except ValueError:
                        pass
                    else:
                        return self.import_pack_archive(archive_path, requested_scope, world_name)

                stage_parent = self._pack_staging_root()
                bundle_root = stage_parent / ("bundle-" + secrets.token_hex(12))
                bundle_root.mkdir(parents=False, exist_ok=False)
                prepared_packs: list[dict[str, Any]] = []
                nested_bytes = 0
                bundle_extracted_size = 0
                bundle_entry_count = 0
                try:
                    pack_sources: list[tuple[str, Any]] = []
                    pack_sources.extend(("mcpack", item) for item in nested)
                    pack_sources.extend(("folder", prefix) for prefix in pack_prefixes)
                    for index, (source_kind, source_value) in enumerate(pack_sources, start=1):
                        nested_path = bundle_root / f"source-{index}.mcpack"
                        if source_kind == "mcpack":
                            info, path = source_value
                            nested_bytes += info.file_size
                            if nested_bytes > MAX_CONTENT_EXTRACTED_BYTES:
                                raise ValueError("Nested .mcpack files exceed the 32 GiB unpacked-container limit.")
                            written = 0
                            with outer.open(info, "r") as source, nested_path.open("xb") as output:
                                while True:
                                    chunk = source.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    written += len(chunk)
                                    if written > info.file_size or written > MAX_CONTENT_EXTRACTED_BYTES:
                                        raise ValueError("Nested .mcpack expanded beyond its declared archive size.")
                                    output.write(chunk)
                            if written != info.file_size:
                                raise ValueError(f"Nested .mcpack has an unexpected length: {info.filename}")
                            source_name = path.name
                        else:
                            prefix = str(source_value)
                            nested_bytes += self._write_pack_archive_from_zip_prefix(outer, outer_entries, prefix, nested_path)
                            if nested_bytes > MAX_CONTENT_EXTRACTED_BYTES:
                                raise ValueError("Unpacked add-on folders exceed the 32 GiB container limit.")
                            source_name = prefix.rstrip("/").split("/")[-1] if prefix else Path(filename).name
                        prepared = self._prepare_pack_archive(nested_path, bundle_root, f"pack-{index}")
                        prepared["source_name"] = source_name
                        prepared_packs.append(prepared)
                        bundle_entry_count += int(prepared.get("archive_entry_count", 0))
                        if bundle_entry_count > MAX_CONTENT_ARCHIVE_ENTRIES:
                            raise ValueError(f"Combined add-on archives contain more than {MAX_CONTENT_ARCHIVE_ENTRIES:,} files. Nothing was installed.")
                        bundle_extracted_size += int(prepared.get("extracted_size", 0))
                        if bundle_extracted_size > MAX_CONTENT_EXTRACTED_BYTES:
                            raise ValueError("Combined add-on contents exceed the 32 GiB extraction limit. Nothing was installed.")
                    uuids = [item["uuid"].lower() for item in prepared_packs]
                    if len(set(uuids)) != len(uuids):
                        raise ValueError("This add-on bundle contains duplicate pack header UUIDs. Nothing was installed.")
                    installed_uuids = {record.get("uuid", "").lower() for record in self._pack_records() if record.get("uuid")}
                    duplicate = next((item for item in prepared_packs if item["uuid"].lower() in installed_uuids), None)
                    if duplicate:
                        raise ValueError(f"Pack '{duplicate['name']}' ({duplicate['uuid']}) is already installed. Nothing from this bundle was installed.")
                    destinations: list[tuple[dict[str, Any], Path, str]] = []
                    targets_seen: set[str] = set()
                    for item in prepared_packs:
                        target, target_name = self._pack_install_target(item, requested_scope, world_name)
                        target_key = str(target).casefold()
                        if target_key in targets_seen:
                            raise ValueError("The add-on bundle generates duplicate destination folders. Nothing was installed.")
                        targets_seen.add(target_key)
                        destinations.append((item, target, target_name))
                    committed: list[tuple[dict[str, Any], Path]] = []
                    try:
                        for item, target, _ in destinations:
                            os.replace(item["stage"], target)
                            committed.append((item, target))
                    except Exception:
                        for item, target in reversed(committed):
                            try:
                                os.replace(target, item["stage"])
                            except OSError:
                                if target.is_dir() and not target.is_symlink():
                                    shutil.rmtree(target, ignore_errors=True)
                        raise
                    summaries = [{"name": item["name"], "kind": item["kind"], "uuid": item["uuid"], "version": item["version"], "folder": target_name, "source": item.get("source_name", "")} for item, _, target_name in destinations]
                    self._append_log(f"[manager] Installed Bedrock add-on bundle with {len(summaries)} pack(s) in {requested_scope} scope.")
                    names = ", ".join(f"{item['name']} ({item['kind']})" for item in summaries)
                    warnings = [item["preview_warning"] for item in prepared_packs if item["preview_warning"]]
                    return {"ok": True, "mode": "bundle", "installed_count": len(summaries), "packs": summaries, "scope": requested_scope, "world": world_name if requested_scope == "world" else "", "warnings": warnings, "message": f"Installed {len(summaries)} Bedrock add-on pack(s): {names}. They are installed but not automatically activated in a world."}
                finally:
                    if bundle_root.exists() and not bundle_root.is_symlink():
                        shutil.rmtree(bundle_root, ignore_errors=True)
                    try:
                        if stage_parent.is_dir() and not stage_parent.is_symlink() and not any(stage_parent.iterdir()):
                            stage_parent.rmdir()
                    except OSError:
                        pass
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError(f"Could not import Bedrock add-on: {exc}") from exc
    def mods(self) -> dict[str, Any]:
        """Return one normalized inventory for the dedicated Mods page."""
        records = self._pack_records()
        items: list[dict[str, Any]] = []
        for record in records:
            module_types = record.get("module_types", [])
            is_script = "script" in module_types
            if record["kind"] == "behavior":
                label = "Script behavior pack" if is_script else "Behavior pack"
            else:
                label = "Resource pack"
            item = dict(record)
            item["mod_type"] = label
            item["is_script"] = is_script
            item["active"] = bool(record.get("active_worlds"))
            items.append(item)
        items.sort(key=lambda item: (not item["valid"], item["name"].casefold(), item["scope"], item["world_name"]))
        summary = {
            "total": len(items),
            "behavior": sum(1 for item in items if item["kind"] == "behavior"),
            "resource": sum(1 for item in items if item["kind"] == "resource"),
            "script": sum(1 for item in items if item["is_script"]),
            "needs_attention": sum(1 for item in items if not item["valid"]),
            "shared": sum(1 for item in items if item["scope"] == "shared"),
            "world_specific": sum(1 for item in items if item["scope"] == "world"),
        }
        return {"summary": summary, "items": items, "worlds": self.worlds(), "mutations_allowed": not bool((self.proc and self.proc.poll() is None) or self._unmanaged_bds())}

    def set_world_pack(self, world_name: str, kind: str, location_id: str, enabled: bool) -> dict[str, Any]:
        self._require_content_server_stopped()
        if kind not in ("behavior", "resource") or not isinstance(enabled, bool):
            raise ValueError("Invalid pack activation request.")
        world_path = self._world_path(world_name)
        if not world_path.is_dir():
            raise ValueError("World not found.")
        records = self._pack_records()
        selected = next((p for p in records if p["location_id"] == location_id and p["kind"] == kind), None)
        if not selected or not selected.get("uuid"):
            raise ValueError("Selected pack is not installed or has an invalid UUID.")
        duplicate_locations = [p for p in records if p.get("uuid", "").lower() == selected["uuid"].lower()]
        if len(duplicate_locations) > 1:
            raise ValueError("This pack UUID exists in multiple installed locations. Resolve the duplicate locations manually before changing a world pack stack.")
        if selected["scope"] == "world" and selected["world_name"] != world_name:
            raise ValueError("A world-specific pack can only be activated in its own world.")
        if not selected["valid"] and enabled:
            raise ValueError("This pack has an invalid manifest or unresolved dependencies and cannot be activated: " + "; ".join(selected["errors"]))
        files = {"behavior": world_path / "world_behavior_packs.json", "resource": world_path / "world_resource_packs.json"}
        current: dict[str, list[dict[str, Any]]] = {}
        originals: dict[str, bytes | None] = {}
        for pack_kind, path in files.items():
            if path.is_symlink():
                raise ValueError(f"{path.name} is a symbolic link; pack activation is disabled for safety.")
            originals[pack_kind] = path.read_bytes() if path.exists() else None
            entries, errors = self._world_pack_refs(world_path, pack_kind)
            if errors:
                raise ValueError("; ".join(errors))
            current[pack_kind] = entries
        selected_kind = current[kind]
        selected_uuid = selected["uuid"].lower()
        present = any(str(item.get("pack_id", "")).lower() == selected_uuid for item in selected_kind)
        if enabled and not present:
            selected_kind.append({"pack_id": selected["uuid"], "version": selected["manifest_version"] if "manifest_version" in selected else self._find_pack_version(selected)})
            # Bring in explicit pack UUID dependencies automatically, while preserving built-in module dependencies.
            for dependency in selected.get("dependencies", []):
                dep_uuid = str(dependency.get("uuid", "")).lower()
                if not dep_uuid:
                    continue
                dep_record = next((p for p in records if p.get("uuid", "").lower() == dep_uuid and p["valid"]), None)
                if not dep_record:
                    raise ValueError(f"Required pack dependency {dependency.get('uuid')} is not installed with a valid manifest.")
                dep_kind = dep_record["kind"]
                dep_list = current[dep_kind]
                if not any(str(item.get("pack_id", "")).lower() == dep_uuid for item in dep_list):
                    dep_list.append({"pack_id": dep_record["uuid"], "version": self._find_pack_version(dep_record)})
        elif not enabled and present:
            dependents = []
            for other in records:
                if other["location_id"] == location_id or not any(w == world_name for w in other.get("active_worlds", [])):
                    continue
                if any(str(dep.get("uuid", "")).lower() == selected_uuid for dep in other.get("dependencies", [])):
                    dependents.append(other["name"])
            if dependents:
                raise ValueError("Disable dependent pack(s) first: " + ", ".join(dependents))
            current[kind] = [item for item in selected_kind if str(item.get("pack_id", "")).lower() != selected_uuid]
        paths_written: list[Path] = []
        try:
            for pack_kind, path in files.items():
                before = originals[pack_kind]
                after_text = json.dumps(current[pack_kind], indent=2) + "\n"
                changed = (before is None and bool(current[pack_kind])) or (before is not None and json.loads(before.decode("utf-8")) != current[pack_kind])
                if changed:
                    if before is not None:
                        backup = path.with_name(path.name + ".before-bds-omni.bak")
                        if not backup.exists():
                            shutil.copy2(path, backup)
                    atomic_write(path, after_text)
                    paths_written.append(path)
        except Exception:
            for path in reversed(paths_written):
                kind_key = "behavior" if path.name == "world_behavior_packs.json" else "resource"
                before = originals[kind_key]
                try:
                    if before is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, before.decode("utf-8"))
                except Exception:
                    pass
            raise
        state = "enabled" if enabled else "disabled"
        self._append_log(f"[manager] {state.capitalize()} {kind} pack '{selected['name']}' for world '{world_name}'.")
        return {"ok": True, "message": f"{selected['name']} {state} for '{world_name}'. Restart BDS to apply the pack stack."}

    @staticmethod
    def _version_tuple(value: Any) -> tuple[int, ...] | None:
        """Parse a numeric Bedrock/manifest version prefix for advisory comparison."""
        if isinstance(value, list) and len(value) == 3 and all(type(part) is int and part >= 0 for part in value):
            return tuple(value)
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][A-Za-z0-9.-]+)?\s*", value)
            if match:
                return tuple(int(part) for part in match.groups() if part is not None)
        return None

    @staticmethod
    def _find_dependency_cycles(adjacency: dict[str, list[str]]) -> list[list[str]]:
        """Find directed dependency cycles without recursion-depth limits."""
        state: dict[str, int] = {}
        path: list[str] = []
        path_index: dict[str, int] = {}
        cycles: list[list[str]] = []
        signatures: set[tuple[str, ...]] = set()
        for root_node in adjacency:
            if state.get(root_node, 0) != 0:
                continue
            state[root_node] = 1
            path_index[root_node] = len(path)
            path.append(root_node)
            frames: list[tuple[str, int]] = [(root_node, 0)]
            while frames:
                node, next_index = frames[-1]
                neighbors = adjacency.get(node, [])
                if next_index >= len(neighbors):
                    frames.pop()
                    state[node] = 2
                    path_index.pop(node, None)
                    if path and path[-1] == node:
                        path.pop()
                    continue
                neighbor = neighbors[next_index]
                frames[-1] = (node, next_index + 1)
                neighbor_state = state.get(neighbor, 0)
                if neighbor_state == 0:
                    state[neighbor] = 1
                    path_index[neighbor] = len(path)
                    path.append(neighbor)
                    frames.append((neighbor, 0))
                elif neighbor_state == 1:
                    start_index = path_index.get(neighbor)
                    if start_index is not None:
                        cycle = path[start_index:] + [neighbor]
                        signature = tuple(sorted(set(cycle)))
                        if signature not in signatures:
                            signatures.add(signature)
                            cycles.append(cycle)
        return cycles

    def _profile_store(self) -> list[dict[str, Any]]:
        profiles = getattr(self, "stack_profiles", [])
        return profiles if isinstance(profiles, list) else []

    def _persist_stack_profiles(self) -> None:
        path = Path(getattr(self, "stack_profile_path", STACK_PROFILE_FILE))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps({"schema_version": 1, "profiles": self._profile_store()}, indent=2) + "\n")

    @staticmethod
    def _profile_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name or len(name) > 64 or any(not c.isprintable() or c in "\\/<>:\"|?*" for c in name):
            raise ValueError("Profile names must be 1–64 printable characters and cannot contain path separators or reserved filename characters.")
        return name

    def list_stack_profiles(self) -> list[dict[str, Any]]:
        """Return sanitized profile metadata, never exposing mutable internals."""
        result = []
        for profile in self._profile_store():
            if not isinstance(profile, dict):
                continue
            behavior = profile.get("behavior", [])
            resource = profile.get("resource", [])
            result.append({
                "id": str(profile.get("id", "")),
                "name": str(profile.get("name", "")),
                "world": str(profile.get("world", "")),
                "created_at": str(profile.get("created_at", "")),
                "behavior_count": len(behavior) if isinstance(behavior, list) else 0,
                "resource_count": len(resource) if isinstance(resource, list) else 0,
            })
        return sorted(result, key=lambda item: (item["name"].casefold(), item["created_at"]))

    def create_stack_profile(self, world_name: str, name: str) -> dict[str, Any]:
        world_name = self._validate_world_name(world_name)
        profile_name = self._profile_name(name)
        world = self._world_path(world_name)
        if not world.is_dir():
            raise ValueError("World not found.")
        behavior, behavior_errors = self._world_pack_refs(world, "behavior")
        resource, resource_errors = self._world_pack_refs(world, "resource")
        if behavior_errors or resource_errors:
            raise ValueError("Cannot snapshot an invalid pack stack: " + "; ".join(behavior_errors + resource_errors))
        with getattr(self, "stack_profile_lock", threading.RLock()):
            profiles = self._profile_store()
            if len(profiles) >= 50:
                raise ValueError("The profile library is limited to 50 profiles. Delete an old profile before creating another.")
            if any(str(p.get("name", "")).casefold() == profile_name.casefold() and str(p.get("world", "")).casefold() == world_name.casefold() for p in profiles):
                raise ValueError("A profile with this name already exists for the selected world.")
            profile = {
                "id": uuidlib.uuid4().hex,
                "name": profile_name,
                "world": world_name,
                "created_at": now_iso(),
                "behavior": behavior,
                "resource": resource,
            }
            profiles.append(profile)
            try:
                self._persist_stack_profiles()
            except Exception:
                profiles.remove(profile)
                raise
        self._append_log(f"[manager] Saved pack-stack profile '{profile_name}' for world '{world_name}'.")
        return {"ok": True, "profile": {k: profile[k] for k in ("id", "name", "world", "created_at")}, "message": f"Saved pack-stack profile '{profile_name}'."}

    def delete_stack_profile(self, profile_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}", str(profile_id or "")):
            raise ValueError("Invalid profile identifier.")
        with getattr(self, "stack_profile_lock", threading.RLock()):
            profiles = self._profile_store()
            profile = next((p for p in profiles if p.get("id") == profile_id), None)
            if not profile:
                raise ValueError("Pack-stack profile not found.")
            profiles.remove(profile)
            try:
                self._persist_stack_profiles()
            except Exception:
                profiles.append(profile)
                raise
        return {"ok": True, "message": f"Deleted pack-stack profile '{profile.get('name', 'Unnamed')}'."}

    def preview_stack_profile(self, profile_id: str, target_world: str) -> dict[str, Any]:
        """Resolve a profile against a target world without writing any files."""
        target_world = self._validate_world_name(target_world)
        profile = next((p for p in self._profile_store() if p.get("id") == profile_id), None)
        if not profile:
            raise ValueError("Pack-stack profile not found.")
        world_path = self._world_path(target_world)
        if not world_path.is_dir():
            raise ValueError("Target world not found.")
        issues: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        records = self._pack_records()
        available = [p for p in records if p["scope"] == "shared" or (p["scope"] == "world" and p["world_name"] == target_world)]
        desired: dict[str, list[dict[str, Any]]] = {}
        selected_records: dict[tuple[str, str], dict[str, Any]] = {}
        for kind in ("behavior", "resource"):
            entries = profile.get(kind, [])
            if not isinstance(entries, list):
                issues.append({"code": "invalid-profile", "message": f"The {kind} stack in this profile is not an array."})
                entries = []
            cleaned: list[dict[str, Any]] = []
            seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    issues.append({"code": "invalid-entry", "message": f"The {kind} stack contains a malformed entry."})
                    continue
                try:
                    pack_uuid = str(uuidlib.UUID(str(entry.get("pack_id", ""))))
                except (ValueError, AttributeError):
                    issues.append({"code": "invalid-uuid", "message": f"A {kind} stack entry has an invalid pack UUID."})
                    continue
                if pack_uuid.casefold() in seen:
                    issues.append({"code": "duplicate-reference", "message": f"The {kind} stack repeats pack {pack_uuid}."})
                    continue
                seen.add(pack_uuid.casefold())
                if not self._version_text(entry.get("version")):
                    issues.append({"code": "invalid-version", "message": f"Pack {pack_uuid} has an invalid stack version."})
                    continue
                candidates = [p for p in available if p.get("uuid", "").casefold() == pack_uuid.casefold() and p["kind"] == kind]
                if len(candidates) != 1:
                    message = "is missing from the target world's visible pack scope" if not candidates else "is ambiguous because multiple installed locations share its UUID"
                    issues.append({"code": "unresolved-pack", "message": f"Pack {pack_uuid} {message}."})
                    continue
                record = candidates[0]
                selected_records[(kind, pack_uuid.casefold())] = record
                if not record.get("valid"):
                    issues.append({"code": "invalid-pack", "message": f"{record['name']} cannot be activated: {'; '.join(record.get('errors', []))}"})
                installed_version = self._version_text(record.get("manifest_version"))
                requested_version = self._version_text(entry.get("version"))
                if installed_version != requested_version:
                    issues.append({"code": "version-mismatch", "message": f"{record['name']} is installed at {installed_version}, but the profile requires {requested_version}."})
                cleaned.append(dict(entry, pack_id=pack_uuid))
            desired[kind] = cleaned
        desired_ids = {str(item.get("pack_id", "")).casefold() for kind in desired for item in desired[kind]}
        # A profile must not apply a stack whose selected packs form a
        # dependency cycle, even when every individual UUID/version resolves.
        desired_locations = {record["location_id"] for record in selected_records.values()}
        profile_adjacency: dict[str, list[str]] = {location: [] for location in desired_locations}
        for record in selected_records.values():
            for dependency in record.get("dependencies", []):
                target_location = dependency.get("location_id", "")
                if dependency.get("resolved") and target_location in desired_locations:
                    profile_adjacency[record["location_id"]].append(target_location)
        for cycle in self._find_dependency_cycles(profile_adjacency):
            names = [next((r["name"] for r in selected_records.values() if r["location_id"] == item), item) for item in cycle]
            issues.append({"code": "profile-dependency-cycle", "message": "The selected profile contains a dependency cycle: " + " → ".join(names)})
        for kind in ("behavior", "resource"):
            for entry in desired[kind]:
                record = next((p for p in available if p.get("uuid", "").casefold() == entry["pack_id"].casefold() and p["kind"] == kind), None)
                if not record:
                    continue
                for dependency in record.get("dependencies", []):
                    dep_uuid = str(dependency.get("uuid", "")).casefold()
                    if dep_uuid and dep_uuid not in desired_ids:
                        issues.append({"code": "profile-missing-dependency", "message": f"{record['name']} requires pack {dependency.get('uuid')}, which is not included in this profile."})
                    elif not dependency.get("resolved"):
                        issues.append({"code": "dependency-invalid", "message": f"{record['name']} has an unresolved dependency: {dependency.get('detail', 'unknown dependency')}"})
                    elif dependency.get("module_name"):
                        warnings.append({"code": "runtime-dependency", "message": f"{record['name']} uses built-in module {dependency.get('module_name')}; runtime API compatibility is decided by BDS."})
        current: dict[str, list[dict[str, Any]]] = {}
        diff: dict[str, dict[str, Any]] = {}
        for kind in ("behavior", "resource"):
            entries, errors = self._world_pack_refs(world_path, kind)
            if errors:
                issues.extend({"code": "current-stack-invalid", "message": error} for error in errors)
            current[kind] = entries
            old_ids = [str(item.get("pack_id", "")).casefold() for item in entries]
            new_ids = [str(item.get("pack_id", "")).casefold() for item in desired.get(kind, [])]
            diff[kind] = {
                "before": len(old_ids), "after": len(new_ids),
                "added": [item for item in new_ids if item not in old_ids],
                "removed": [item for item in old_ids if item not in new_ids],
                "order_changed": old_ids != new_ids and set(old_ids) == set(new_ids),
            }
        return {"ok": True, "ready": not issues, "profile": {"id": profile.get("id"), "name": profile.get("name"), "source_world": profile.get("world")}, "target_world": target_world, "diff": diff, "issues": issues, "warnings": warnings, "desired": desired}

    def _save_stack_file(self, world_path: Path, kind: str, entries: list[dict[str, Any]]) -> Path:
        filename = "world_behavior_packs.json" if kind == "behavior" else "world_resource_packs.json"
        path = world_path / filename
        if path.is_symlink():
            raise ValueError(f"{filename} is a symbolic link; stack editing is disabled for safety.")
        payload = json.dumps(entries, indent=2) + "\n"
        atomic_write(path, payload)
        return path

    def _archive_stack_state(self, world_name: str, originals: dict[str, bytes | None]) -> Path:
        configured_backup_root = Path(self.backup_dir).expanduser()
        if configured_backup_root.is_symlink():
            raise ValueError("The configured backup directory is a symbolic link. Choose a real directory for pack-stack recovery archives.")
        backup_root = configured_backup_root.resolve()
        server_root = self.root.resolve()
        if server_root == backup_root or server_root in backup_root.parents:
            raise ValueError("Pack-stack recovery archives must be stored outside the BDS server directory.")
        backup_root.mkdir(parents=True, exist_ok=True)
        folder = backup_root / "pack-stack-profiles"
        if folder.is_symlink():
            raise ValueError("The pack-stack recovery directory is a symbolic link; refusing to follow it.")
        folder.mkdir(parents=True, exist_ok=True)
        if folder.resolve() == server_root or server_root in folder.resolve().parents:
            raise ValueError("Pack-stack recovery archives must remain outside the BDS server directory.")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", world_name)[:48] or "world"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        output = folder / f"{safe_name}-stack-{stamp}.zip"
        temp = output.with_name(output.name + ".tmp")
        try:
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for kind, content in originals.items():
                    if content is not None:
                        filename = "world_behavior_packs.json" if kind == "behavior" else "world_resource_packs.json"
                        archive.writestr(filename, content)
                archive.writestr(".bds-omni-stack.json", json.dumps({"world": world_name, "created_at": now_iso(), "kind": "pack-stack-recovery"}))
            with zipfile.ZipFile(temp, "r") as check:
                if check.testzip():
                    raise ValueError("Pack-stack recovery archive failed CRC verification.")
            os.replace(temp, output)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        return output

    def reorder_world_pack_stack(self, world_name: str, kind: str, ordered_ids: Any) -> dict[str, Any]:
        self._require_content_server_stopped()
        world_name = self._validate_world_name(world_name)
        if kind not in ("behavior", "resource") or not isinstance(ordered_ids, list) or len(ordered_ids) > 500:
            raise ValueError("Invalid world pack-stack reorder request.")
        world_path = self._world_path(world_name)
        if not world_path.is_dir():
            raise ValueError("World not found.")
        current, errors = self._world_pack_refs(world_path, kind)
        if errors:
            raise ValueError("Cannot reorder an invalid pack stack: " + "; ".join(errors))
        current_ids = [str(item["pack_id"]).casefold() for item in current]
        requested: list[str] = []
        for value in ordered_ids:
            try:
                requested.append(str(uuidlib.UUID(str(value))).casefold())
            except (ValueError, AttributeError):
                raise ValueError("Every stack entry must be a valid pack UUID.")
        if len(requested) != len(set(requested)) or len(requested) != len(current_ids) or set(requested) != set(current_ids):
            raise ValueError("Reorder must contain every currently active pack UUID exactly once. It cannot add or remove packs.")
        if requested == current_ids:
            return {"ok": True, "changed": False, "message": "The pack-stack order is already up to date."}
        by_id = {str(item["pack_id"]).casefold(): item for item in current}
        reordered = [by_id[item] for item in requested]
        filename = "world_behavior_packs.json" if kind == "behavior" else "world_resource_packs.json"
        path = world_path / filename
        original = path.read_bytes() if path.exists() and not path.is_symlink() else None
        if path.is_symlink():
            raise ValueError(f"{filename} is a symbolic link; stack editing is disabled for safety.")
        archive = self._archive_stack_state(world_name, {kind: original})
        try:
            self._save_stack_file(world_path, kind, reordered)
        except Exception:
            raise
        self._append_log(f"[manager] Reordered {kind} pack stack for world '{world_name}'. Recovery archive: {archive.name}.")
        return {"ok": True, "changed": True, "archive": archive.name, "message": f"Reordered {kind} pack stack for '{world_name}'. The highest-priority pack is displayed first. Restart BDS to apply changes."}

    def apply_stack_profile(self, profile_id: str, target_world: str) -> dict[str, Any]:
        self._require_content_server_stopped()
        preview = self.preview_stack_profile(profile_id, target_world)
        if not preview["ready"]:
            details = "; ".join(item["message"] for item in preview["issues"][:8])
            raise ValueError("Profile cannot be applied: " + details)
        world_path = self._world_path(target_world)
        originals: dict[str, bytes | None] = {}
        for kind in ("behavior", "resource"):
            filename = "world_behavior_packs.json" if kind == "behavior" else "world_resource_packs.json"
            path = world_path / filename
            if path.is_symlink():
                raise ValueError(f"{filename} is a symbolic link; profile apply is disabled for safety.")
            originals[kind] = path.read_bytes() if path.exists() else None
        recovery = self._archive_stack_state(target_world, originals)
        written: list[str] = []
        try:
            for kind in ("behavior", "resource"):
                self._save_stack_file(world_path, kind, preview["desired"][kind])
                written.append(kind)
        except Exception as exc:
            rollback_errors: list[str] = []
            for kind in reversed(written):
                filename = "world_behavior_packs.json" if kind == "behavior" else "world_resource_packs.json"
                path = world_path / filename
                before = originals[kind]
                try:
                    if before is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, before.decode("utf-8"))
                except Exception as rollback_exc:
                    rollback_errors.append(f"{filename}: {rollback_exc}")
            if rollback_errors:
                raise ValueError(f"Profile apply failed ({exc}) and rollback was incomplete. Recovery archive: {recovery}. Rollback errors: {'; '.join(rollback_errors)}") from exc
            raise
        profile = next((p for p in self._profile_store() if p.get("id") == profile_id), {})
        self._append_log(f"[manager] Applied pack-stack profile '{profile.get('name', 'Unnamed')}' to world '{target_world}'. Recovery archive: {recovery.name}.")
        return {"ok": True, "archive": recovery.name, "message": f"Applied profile '{profile.get('name', 'Unnamed')}' to '{target_world}'. Recovery archive: {recovery.name}. Restart BDS to apply changes."}

    def content_lab(self, world_name: str) -> dict[str, Any]:
        """Build a read-only dependency, compatibility and asset-overlap report."""
        world_name = self._validate_world_name(world_name)
        detail = self.world_detail(world_name)
        records = [p for p in self._pack_records() if p["scope"] == "shared" or (p["scope"] == "world" and p["world_name"] == world_name)]
        by_location = {p["location_id"]: p for p in records}
        behavior_refs, _ = self._world_pack_refs(self._world_path(world_name), "behavior")
        resource_refs, _ = self._world_pack_refs(self._world_path(world_name), "resource")
        ref_lists = {"behavior": behavior_refs, "resource": resource_refs}
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        findings: list[dict[str, str]] = []
        adjacency: dict[str, list[str]] = {}
        active_records: dict[str, list[dict[str, Any]]] = {"behavior": [], "resource": []}
        for kind in ("behavior", "resource"):
            for order, entry in enumerate(ref_lists[kind]):
                pack_uuid = str(entry.get("pack_id", "")).casefold()
                candidates = [p for p in records if p["kind"] == kind and p.get("uuid", "").casefold() == pack_uuid]
                if len(candidates) == 1:
                    candidates[0]["active_order"] = order
                    active_records[kind].append(candidates[0])
                else:
                    findings.append({"severity": "error", "code": "active-pack-unresolved", "message": f"Active {kind} reference {entry.get('pack_id')} is {'missing' if not candidates else 'ambiguous'} in the target world."})
        all_by_uuid: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            if record.get("uuid"):
                all_by_uuid.setdefault(record["uuid"].casefold(), []).append(record)
        for record in records:
            node_id = record["location_id"]
            adjacency[node_id] = []
            nodes.append({"id": node_id, "uuid": record.get("uuid", ""), "name": record["name"], "kind": record["kind"], "scope": record["scope"], "version": record["version"], "valid": bool(record["valid"]), "active": world_name in record.get("active_worlds", []), "module_types": record.get("module_types", []), "min_engine_version": record.get("min_engine_version", "")})
            if record.get("duplicate_uuid"):
                findings.append({"severity": "error", "code": "duplicate-uuid", "message": f"{record['name']} has a UUID duplicated in the installed pack library."})
            for error in record.get("errors", []):
                findings.append({"severity": "error", "code": "pack-validation", "message": f"{record['name']}: {error}"})
            min_engine = self._version_tuple(record.get("min_engine_version"))
            server_version = self._version_tuple(getattr(self, "server_version", ""))
            if record.get("min_engine_version") and server_version:
                width = max(len(min_engine or ()), len(server_version))
                if min_engine and tuple(min_engine) + (0,) * (width - len(min_engine)) > tuple(server_version) + (0,) * (width - len(server_version)):
                    findings.append({"severity": "warning", "code": "engine-version", "message": f"{record['name']} declares minimum engine {record['min_engine_version']}, newer than detected BDS {getattr(self, 'server_version', 'Unknown')}. Treat as a compatibility warning; BDS remains authoritative."})
            elif record.get("min_engine_version"):
                findings.append({"severity": "info", "code": "engine-unknown", "message": f"{record['name']} requires engine {record['min_engine_version']}; BDS version is not available for comparison."})
            for dep in record.get("dependencies", []):
                target_id = dep.get("location_id", "")
                edges.append({"from": node_id, "to": target_id or None, "label": dep.get("module_name") or dep.get("uuid", "Built-in module"), "resolved": bool(dep.get("resolved")), "detail": dep.get("detail", "")})
                if target_id and target_id in by_location:
                    adjacency[node_id].append(target_id)
        # Iterative traversal avoids Python recursion limits on large pack libraries.
        cycles = self._find_dependency_cycles(adjacency)
        for cycle in cycles:
            names = [by_location[item]["name"] for item in cycle if item in by_location]
            findings.append({"severity": "error", "code": "dependency-cycle", "message": "Dependency cycle: " + " → ".join(names)})
        # Compare active files by relative path. This is advisory: BDS resolves
        # precedence from its pack stack; a shared path is not automatically a bug.
        conflicts: list[dict[str, Any]] = []
        scan_budget = 350 * 1024 * 1024
        scanned_bytes = 0
        scanned_files = 0
        scan_truncated = False
        for kind in ("behavior", "resource"):
            owners: dict[str, list[dict[str, Any]]] = {}
            for record in active_records[kind]:
                try:
                    pack_path = self._pack_path_for_record(record)
                except (OSError, ValueError):
                    continue
                for current, dirs, files in os.walk(pack_path, followlinks=False):
                    current_path = Path(current)
                    dirs[:] = [d for d in dirs if not (current_path / d).is_symlink()]
                    for filename in files:
                        item = current_path / filename
                        try:
                            st = item.lstat()
                            if not stat.S_ISREG(st.st_mode) or item.is_symlink():
                                continue
                            rel = item.relative_to(pack_path).as_posix()
                            if rel.casefold() == "manifest.json":
                                continue
                            scanned_files += 1
                            if scanned_files > 20_000:
                                scan_truncated = True
                                break
                            scanned_bytes += st.st_size
                            if scanned_bytes > scan_budget:
                                scan_truncated = True
                                break
                            owners.setdefault(rel.casefold(), []).append({"record": record, "path": item, "size": st.st_size})
                        except OSError:
                            continue
                    if scan_truncated:
                        break
                if scan_truncated:
                    break
            for rel, matches in owners.items():
                if len(matches) < 2:
                    continue
                hashes: list[str | None] = []
                for match in matches:
                    if match["size"] > 16 * 1024 * 1024:
                        hashes.append(None)
                        continue
                    digest = hashlib.sha256()
                    try:
                        with match["path"].open("rb") as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                                digest.update(chunk)
                        hashes.append(digest.hexdigest())
                    except OSError:
                        hashes.append(None)
                distinct = {value for value in hashes if value is not None}
                conflicts.append({"kind": kind, "path": rel, "packs": [m["record"]["name"] for m in matches], "classification": "different-content" if len(distinct) > 1 else "same-or-unverified", "hashes_verified": all(value is not None for value in hashes)})
        for conflict in conflicts:
            severity = "warning" if conflict["classification"] == "different-content" else "info"
            findings.append({"severity": severity, "code": "asset-overlap", "message": f"{conflict['kind'].capitalize()} asset '{conflict['path']}' appears in {len(conflict['packs'])} active packs ({', '.join(conflict['packs'])}). Stack priority determines the effective asset."})
        if scan_truncated:
            findings.append({"severity": "warning", "code": "scan-limited", "message": "Asset-overlap scan reached its safety budget (20,000 files or 350 MiB). Some conflicts may not be listed."})
        summary = {
            "available_packs": len(records),
            "active_behavior": len(behavior_refs),
            "active_resource": len(resource_refs),
            "dependency_edges": len(edges),
            "cycles": len(cycles),
            "asset_overlaps": len(conflicts),
            "errors": sum(1 for item in findings if item["severity"] == "error"),
            "warnings": sum(1 for item in findings if item["severity"] == "warning"),
        }
        return {"world": world_name, "server_version": getattr(self, "server_version", "Unknown"), "summary": summary, "stacks": {kind: [{"uuid": item.get("uuid", ""), "name": item["name"], "location_id": item["location_id"], "version": item["version"], "valid": item["valid"], "active_order": i, "scope": item["scope"]} for i, item in enumerate(active_records[kind])] for kind in ("behavior", "resource")}, "nodes": nodes, "edges": edges, "cycles": cycles, "conflicts": conflicts[:200], "findings": findings[:500], "scan_truncated": scan_truncated, "mutations_allowed": not bool((getattr(self, "proc", None) and self.proc.poll() is None) or getattr(self, "_unmanaged_bds", lambda: [])())}

    def compare_backups(self, first_name: str, second_name: str) -> dict[str, Any]:
        """Compare verified archive manifests without extracting either backup."""
        if first_name == second_name:
            raise ValueError("Choose two different backups to compare.")
        self.verify_backup(first_name)
        self.verify_backup(second_name)
        first_path = self.backup_dir / first_name
        second_path = self.backup_dir / second_name
        def manifest(path: Path) -> dict[str, tuple[int, int]]:
            with zipfile.ZipFile(path, "r") as archive:
                return {item.filename: (item.file_size, item.CRC) for item in archive.infolist() if not item.is_dir() and item.filename != ".bds-omni-backup.json"}
        first = manifest(first_path)
        second = manifest(second_path)
        first_names, second_names = set(first), set(second)
        added = sorted(second_names - first_names)
        removed = sorted(first_names - second_names)
        changed = sorted(name for name in first_names & second_names if first[name] != second[name])
        return {"ok": True, "first": first_name, "second": second_name, "counts": {"added": len(added), "removed": len(removed), "changed": len(changed), "unchanged": len(first_names & second_names) - len(changed)}, "added": added[:500], "removed": removed[:500], "changed": [{"path": name, "before_bytes": first[name][0], "after_bytes": second[name][0]} for name in changed[:500]], "truncated": any(len(items) > 500 for items in (added, removed, changed)), "comparison": "Archive path, uncompressed size and ZIP CRC32. Both archives were fully CRC-verified before comparison; this does not inspect LevelDB semantics."}

    @staticmethod
    def _find_pack_version(record: dict[str, Any]) -> Any:
        return record.get("manifest_version", [0, 0, 0])

    def issue_download_ticket(self, path: Path, filename: str, content_type: str) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Export file is unavailable.")
        ticket = secrets.token_urlsafe(32)
        lock = getattr(self, "download_lock", None)
        if lock is None:
            self.download_lock = threading.Lock()
            lock = self.download_lock
        with lock:
            tickets = getattr(self, "download_tickets", {})
            now = time.time()
            tickets = {key: value for key, value in tickets.items() if value[3] > now}
            tickets[ticket] = (path, filename, content_type, now + 180)
            self.download_tickets = tickets
        return {"ok": True, "url": f"/download/{ticket}", "filename": filename, "size": path.stat().st_size, "expires_in": 180}

    def consume_download_ticket(self, ticket: str) -> tuple[Path, str, str] | None:
        lock = getattr(self, "download_lock", None)
        if lock is None:
            return None
        with lock:
            value = getattr(self, "download_tickets", {}).pop(ticket, None)
        if not value or value[3] <= time.time():
            return None
        return value[0], value[1], value[2]

    def browse(self, rel: str = "") -> dict[str, Any]:
        base = self.root.resolve()
        target = (base / rel).resolve()
        if target != base and base not in target.parents:
            raise ValueError("Path is outside the BDS folder.")
        if not target.exists() or not target.is_dir():
            raise ValueError("Folder does not exist.")
        entries = []
        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = p.lstat()
                if p.is_symlink():
                    kind, size = "symlink", st.st_size
                elif p.is_dir():
                    kind, size = "folder", 0
                else:
                    kind, size = "file", st.st_size
                entries.append({"name": p.name, "type": kind, "size": size, "modified": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="minutes")})
            except OSError:
                continue
        return {"path": "." if target == base else str(target.relative_to(base)), "entries": entries}

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_dir.is_dir():
            return []
        items = []
        for p in self.backup_dir.glob("BDS-*.zip"):
            try:
                st = p.stat()
                meta = {}
                try:
                    with zipfile.ZipFile(p, "r") as zf:
                        meta = json.loads(zf.read(".bds-omni-backup.json").decode("utf-8"))
                except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError):
                    pass
                items.append({"name": p.name, "size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="minutes"), "mode": meta.get("mode", "unknown"), "world_name": meta.get("world_name", ""), "live_snapshot": bool(meta.get("live_snapshot", False))})
            except OSError:
                pass
        return sorted(items, key=lambda x: x["modified"], reverse=True)

    def verify_backup(self, name: str) -> dict[str, Any]:
        """Verify ZIP structure, paths, metadata, and every member CRC."""
        if Path(name).name != name or not name.startswith("BDS-") or not name.endswith(".zip"):
            raise ValueError("Invalid backup filename.")
        archive = self.backup_dir / name
        if not archive.is_file() or archive.is_symlink():
            raise ValueError("Backup archive not found or is not a regular file.")
        try:
            with zipfile.ZipFile(archive, "r") as zf:
                infos = zf.infolist()
                if not infos or len(infos) > 1_000_000:
                    raise ValueError("Backup has an invalid or excessive number of entries.")
                seen: set[str] = set()
                total_size = 0
                file_count = 0
                for info in infos:
                    name_in_zip = info.filename
                    if not name_in_zip or "\x00" in name_in_zip or "\\" in name_in_zip:
                        raise ValueError("Backup contains an invalid archive path.")
                    rel = Path(name_in_zip)
                    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                        raise ValueError("Backup contains an unsafe path; verification failed.")
                    if name_in_zip in seen:
                        raise ValueError(f"Backup contains a duplicate path: {name_in_zip}")
                    seen.add(name_in_zip)
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(unix_mode):
                        raise ValueError(f"Backup contains a symbolic link: {name_in_zip}")
                    if info.flag_bits & 0x1:
                        raise ValueError("Encrypted backup entries are not supported.")
                    if not info.is_dir():
                        file_count += 1
                        total_size += info.file_size
                try:
                    meta_raw = zf.read(".bds-omni-backup.json")
                    meta = json.loads(meta_raw.decode("utf-8"))
                except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("Backup metadata is missing or invalid.") from exc
                if not isinstance(meta, dict) or meta.get("mode") not in ("world", "full"):
                    raise ValueError("Backup metadata does not identify a supported backup type.")
                mode = meta["mode"]
                world_name = str(meta.get("world_name", ""))
                if not world_name or world_name in (".", "..") or "/" in world_name or "\\" in world_name:
                    raise ValueError("Backup metadata contains an invalid world name.")
                file_names = {i.filename for i in infos if not i.is_dir()}
                if "server.properties" not in file_names:
                    raise ValueError("Backup does not contain server.properties.")
                if mode == "world":
                    prefix = f"worlds/{world_name}/"
                    if not any(n.startswith(prefix) for n in file_names):
                        raise ValueError("World snapshot does not contain its declared world folder.")
                bad = zf.testzip()
                if bad:
                    raise ValueError(f"Backup CRC check failed at {bad}.")
        except zipfile.BadZipFile as exc:
            raise ValueError("Backup is not a valid ZIP archive.") from exc
        except (OSError, RuntimeError, NotImplementedError) as exc:
            raise ValueError(f"Could not verify backup: {exc}") from exc
        return {
            "ok": True,
            "name": name,
            "mode": mode,
            "world_name": world_name,
            "file_count": file_count - 1,
            "uncompressed_bytes": total_size,
            "compressed_bytes": archive.stat().st_size,
            "message": f"Backup verified: {file_count - 1} data files passed CRC checks.",
        }

    def _collect_save_manifest(self, world_name: str) -> list[tuple[Path, int]]:
        """Use BDS save hold/query output and its byte manifest for a live snapshot.

        BDS keeps running during save hold. Copying the whole LevelDB directory at
        that point is not a consistent snapshot: save query supplies the exact
        filenames and lengths that must be copied. We honor those lengths.
        """
        self.save_ready_event.clear()
        with self.save_capture_lock:
            self.save_capture_lines = []
        self.save_capture_active = True
        try:
            self.command("save hold")
            # save hold is asynchronous. Poll save query until BDS announces readiness.
            deadline = time.monotonic() + 90
            ready = False
            while time.monotonic() < deadline:
                time.sleep(1.0)
                self.command("save query")
                if self.save_ready_event.wait(1.0):
                    ready = True
                    break
            if not ready:
                raise ValueError("BDS did not confirm that the save snapshot is ready within 90 seconds. No live backup was copied; the manager will resume saving.")
            # Let the server finish printing the filename:length manifest.
            time.sleep(0.8)
            with self.save_capture_lock:
                lines = list(self.save_capture_lines)
            entries: dict[str, int] = {}
            for line in lines:
                low = line.lower()
                if "files are now ready to be copied" in low:
                    line = line[low.index("files are now ready to be copied") + len("files are now ready to be copied"):]
                # BDS may emit the manifest as a comma-separated line or as
                # several lines. Paths can contain spaces, so split on commas
                # and parse the final :<byte-count> suffix.
                for token in line.split(","):
                    token = token.strip()
                    if not token or token.startswith(">") or "previous save has not" in token.lower():
                        continue
                    token = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", token)
                    match = re.match(r"^(.+?):([0-9]+)$", token)
                    if match:
                        entries[match.group(1).strip()] = int(match.group(2))
            manifest: list[tuple[Path, int]] = []
            for raw, length in entries.items():
                normalized = raw.replace("\\", "/")
                rel = Path(normalized)
                if rel.is_absolute() or ".." in rel.parts:
                    continue
                # Server versions print paths relative to worlds/; tolerate a
                # worlds/ prefix as well.
                if rel.parts and rel.parts[0] == "worlds":
                    source = self.root / rel
                elif rel.parts and rel.parts[0] == world_name:
                    source = self.root / "worlds" / rel
                else:
                    source = self.root / "worlds" / world_name / rel
                source = source.resolve()
                worlds_root = (self.root / "worlds").resolve()
                if worlds_root not in source.parents or not source.is_file() or source.is_symlink():
                    continue
                manifest.append((source, length))
            if not manifest:
                raise ValueError("BDS announced a ready save but no valid file:length manifest could be read. The manager refused to create a potentially inconsistent live backup.")
            return manifest
        finally:
            # The caller performs the copy before issuing save resume. Keep
            # capture active until the finally block in _create_backup().
            pass

    def _copy_exact_to_zip(self, zf: zipfile.ZipFile, source: Path, arcname: str, length: int) -> None:
        remaining = length
        with source.open("rb") as src, zf.open(arcname, "w", force_zip64=True) as dst:
            while remaining > 0:
                chunk = src.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError(f"A live snapshot file became shorter than its BDS manifest: {source}")
                dst.write(chunk)
                remaining -= len(chunk)

    def _create_backup(self, mode: str = "world", prefix: str = "BDS") -> dict[str, Any]:
        if mode not in ("world", "full"):
            raise ValueError("Backup mode must be world or full.")
        with self.backup_lock:
            if not self.root.is_dir():
                raise ValueError("BDS folder is unavailable.")
            try:
                self.backup_dir.resolve().relative_to(self.root.resolve())
                backup_inside_server = True
            except ValueError:
                backup_inside_server = False
            if backup_inside_server:
                raise ValueError("Backup folder must be outside the BDS server folder to prevent recursive or unsafe snapshots.")
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            props = self._read_props()
            world_name = props.get("level-name", "Bedrock level")
            world_path = self.root / "worlds" / world_name
            if mode == "world" and not world_path.is_dir():
                raise ValueError(f"Active world folder not found: {world_path}")
            was_running = bool(self.proc and self.proc.poll() is None)
            external = self._unmanaged_bds() if not was_running else []
            if external:
                raise ValueError("A BDS process is running outside this manager. Stop it with its existing service/terminal before backing up, or it may write to the world while files are copied.")
            manifest: list[tuple[Path, int]] = []
            held = False
            try:
                if was_running:
                    self._append_log("[manager] Preparing a consistent live snapshot with save hold/query.")
                    # Ensure save resume is attempted even if save query times out.
                    held = True
                    manifest = self._collect_save_manifest(world_name)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                safe_world = re.sub(r"[^A-Za-z0-9._-]+", "_", world_name)[:48] or "world"
                out = self.backup_dir / f"BDS-{stamp}-{mode}-{safe_world}.zip"
                tmp = out.with_suffix(".zip.part")
                with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4, allowZip64=True) as zf:
                    zf.writestr(".bds-omni-backup.json", json.dumps({"format": 1, "mode": mode, "world_name": world_name, "live_snapshot": was_running}, indent=2))
                    seen: set[str] = set()
                    if was_running:
                        # Copy stable server files normally. For the active world,
                        # copy only the exact paths and byte lengths returned by
                        # save query. This prevents a raw LevelDB copy mid-write.
                        candidates = [self.root, world_path / "world_behavior_packs.json",
                                      world_path / "world_resource_packs.json"] if mode == "full" else [
                            self.root / "server.properties", self.root / "allowlist.json",
                            self.root / "permissions.json", world_path / "world_behavior_packs.json",
                            world_path / "world_resource_packs.json", self.root / "behavior_packs",
                            self.root / "resource_packs",
                        ]
                        for candidate in candidates:
                            if not candidate.exists() or candidate.is_symlink():
                                continue
                            paths = [candidate] if candidate.is_file() else candidate.rglob("*")
                            for item in paths:
                                if item.is_symlink() or not item.is_file():
                                    continue
                                try:
                                    rel = item.relative_to(self.root)
                                except ValueError:
                                    continue
                                if mode == "full" and ("logs" in rel.parts or ".bds-omni-manager" in rel.parts):
                                    continue
                                if mode == "full" and rel.parts and rel.parts[0] == "worlds" and len(rel.parts) > 1 and rel.parts[1] == world_name:
                                    continue
                                try:
                                    if self.backup_dir.resolve() in item.resolve().parents:
                                        continue
                                except OSError:
                                    continue
                                key = rel.as_posix()
                                if key not in seen:
                                    zf.write(item, arcname=key)
                                    seen.add(key)
                        for source, length in manifest:
                            arc = source.relative_to(self.root).as_posix()
                            if arc not in seen:
                                self._copy_exact_to_zip(zf, source, arc, length)
                                seen.add(arc)
                    else:
                        if mode == "world":
                            candidates = [
                                world_path, self.root / "server.properties", self.root / "allowlist.json",
                                self.root / "permissions.json", self.root / "behavior_packs",
                                self.root / "resource_packs",
                            ]
                        else:
                            candidates = [self.root]
                        for candidate in candidates:
                            if not candidate.exists() or candidate.is_symlink():
                                continue
                            paths = [candidate] if candidate.is_file() else candidate.rglob("*")
                            for item in paths:
                                if item.is_symlink() or not item.is_file():
                                    continue
                                try:
                                    rel = item.relative_to(self.root)
                                except ValueError:
                                    continue
                                if mode == "full" and ("logs" in rel.parts or ".bds-omni-manager" in rel.parts):
                                    continue
                                try:
                                    if self.backup_dir.resolve() in item.resolve().parents:
                                        continue
                                except OSError:
                                    continue
                                key = rel.as_posix()
                                if key not in seen:
                                    zf.write(item, arcname=key)
                                    seen.add(key)
                with zipfile.ZipFile(tmp, "r") as check_zip:
                    bad_member = check_zip.testzip()
                    if bad_member:
                        raise ValueError(f"New backup failed its CRC verification at {bad_member}.")
                os.replace(tmp, out)
                self._append_log(f"[manager] Backup created and CRC-checked: {out} ({mode}).")
                self._prune_backups()
                return {"ok": True, "message": f"Backup created: {out.name}", "backup": {"name": out.name, "size": out.stat().st_size}}
            except Exception:
                try:
                    if 'tmp' in locals() and tmp.exists(): tmp.unlink()
                except OSError:
                    pass
                raise
            finally:
                if was_running:
                    self.save_capture_active = False
                if held:
                    try:
                        self.command("save resume")
                        self._append_log("[manager] Sent save resume after backup.")
                    except ValueError as exc:
                        self._append_log(f"[manager] Could not resume saving automatically: {exc}")

    def backup(self, mode: str) -> dict[str, Any]:
        return self._create_backup(mode)

    def _prune_backups(self) -> None:
        backups = sorted(self.backup_dir.glob("BDS-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[self.backup_keep:]:
            try: old.unlink()
            except OSError: pass

    def restore(self, name: str) -> dict[str, Any]:
        """Stage and verify all files, then apply with rollback on any error."""
        if self.proc and self.proc.poll() is None:
            raise ValueError("Stop BDS before restoring a backup.")
        external = self._unmanaged_bds()
        if external:
            pids = ", ".join(str(x["pid"]) for x in external)
            raise ValueError(f"A BDS process is still running outside this manager (PID {pids}). Stop it before restoring.")
        verified = self.verify_backup(name)
        archive = self.backup_dir / name
        root = self.root.resolve()
        mode = verified["mode"]
        world_name = verified["world_name"]
        total_size = int(verified["uncompressed_bytes"])
        free = shutil.disk_usage(root).free
        if free < total_size + 16 * 1024 * 1024:
            raise ValueError(
                f"Not enough free disk space to stage this restore safely. "
                f"Need approximately {total_size + 16 * 1024 * 1024:,} bytes free; have {free:,}."
            )

        stage = Path(tempfile.mkdtemp(prefix=".bds-omni-restore-", dir=root))
        payload = stage / "payload"
        rollback = stage / "rollback"
        payload.mkdir()
        rollback.mkdir()
        staged_files: list[Path] = []
        applied_files: list[tuple[Path, Path | None]] = []
        old_world: Path | None = None
        world_target: Path | None = None
        new_world_installed = False
        rollback_failed = False
        pending_previous: tuple[Path, Path] | None = None
        try:
            # Extract manually into a private staging directory. No archive path
            # is ever extracted directly into the live server tree.
            with zipfile.ZipFile(archive, "r") as zf:
                for info in zf.infolist():
                    if info.filename == ".bds-omni-backup.json" or info.is_dir():
                        continue
                    rel = Path(info.filename)
                    staged = payload / rel
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as src, staged.open("xb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                    staged_files.append(rel)

            if mode == "world":
                world_rel = Path("worlds") / world_name
                staged_world = payload / world_rel
                if not staged_world.is_dir():
                    raise ValueError("The verified archive did not stage its declared world folder.")
                worlds_root = root / "worlds"
                if worlds_root.is_symlink() or (worlds_root.exists() and not worlds_root.is_dir()):
                    raise ValueError("The server's worlds path is not a normal directory; restore was cancelled.")
                world_target = root / world_rel
                if world_target.is_symlink() or (world_target.exists() and not world_target.is_dir()):
                    raise ValueError("The target world path is not a normal directory; restore was cancelled.")

            # Preflight all non-world file targets before changing anything.
            for rel in staged_files:
                if mode == "world" and rel.parts[:2] == ("worlds", world_name):
                    continue
                target = root / rel
                current = root
                for part in rel.parts[:-1]:
                    current = current / part
                    if current.is_symlink():
                        raise ValueError(f"Restore target contains a symbolic-link directory: {current}")
                    if current.exists() and not current.is_dir():
                        raise ValueError(f"Restore target parent is not a directory: {current}")
                if target.is_symlink():
                    raise ValueError(f"Restore target is a symbolic link: {target}")
                if target.exists() and not target.is_file():
                    raise ValueError(f"Restore target is not a regular file: {target}")

            # Replace the world as a directory unit, preserving the old one in
            # the rollback area until every other file has been installed.
            if mode == "world" and world_target is not None:
                world_root = root / "worlds"
                world_root.mkdir(parents=True, exist_ok=True)
                if world_target.exists():
                    old_world = rollback / "old-world"
                    old_world.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(world_target, old_world)
                os.replace(payload / "worlds" / world_name, world_target)
                new_world_installed = True

            for rel in staged_files:
                if mode == "world" and rel.parts[:2] == ("worlds", world_name):
                    continue
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                previous: Path | None = None
                if target.exists():
                    previous = rollback / "files" / rel
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, previous)
                    pending_previous = (target, previous)
                try:
                    os.replace(payload / rel, target)
                except Exception:
                    if previous is not None and previous.exists():
                        try:
                            os.replace(previous, target)
                            pending_previous = None
                        except OSError:
                            rollback_failed = True
                    raise
                applied_files.append((target, previous))
                pending_previous = None

        except Exception as apply_error:
            if pending_previous is not None:
                target, previous = pending_previous
                try:
                    if previous.exists():
                        os.replace(previous, target)
                except OSError:
                    rollback_failed = True
            # Reverse file replacements, then put the original world folder
            # back if the world swap happened.
            for target, previous in reversed(applied_files):
                try:
                    if target.exists() and target.is_file():
                        target.unlink()
                    if previous is not None and previous.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(previous, target)
                except OSError:
                    rollback_failed = True
                    self._append_log(f"[manager] WARNING: rollback could not fully restore {target}")
            if world_target is not None and (new_world_installed or (old_world is not None and old_world.exists())):
                try:
                    if new_world_installed and world_target.exists() and world_target.is_dir() and not world_target.is_symlink():
                        shutil.rmtree(world_target)
                    if old_world is not None and old_world.exists():
                        os.replace(old_world, world_target)
                except OSError:
                    rollback_failed = True
                    self._append_log("[manager] WARNING: rollback could not fully restore the previous world folder")
            if rollback_failed:
                self._append_log(f"[manager] CRITICAL: restore rollback was incomplete. Recovery copies were retained at {stage}")
                raise RuntimeError(f"Restore failed and rollback was incomplete. Recovery copies were retained at: {stage}. Do not delete that folder until the server files have been recovered.") from apply_error
            raise
        finally:
            if not rollback_failed:
                shutil.rmtree(stage, ignore_errors=True)

        self._append_log(f"[manager] Restored verified backup {name} transactionally.")
        detail = (
            "The archived world folder was replaced as a unit, and shared server/access/pack files were restored."
            if mode == "world" else
            "Matching files were restored as an overlay; unrelated files not present in the archive were left in place."
        )
        return {"ok": True, "message": f"Restored {name}. {detail} Start BDS when ready.", "verification": verified}

    def _auto_backup_loop(self) -> None:
        while not self._scheduler_stop.wait(30):
            hours = self.auto_backup_hours
            if not hours:
                continue
            marker = self.config.get("last_auto_backup", 0)
            if time.time() - float(marker or 0) < hours * 3600:
                continue
            try:
                self._create_backup("world", "auto")
                self.config["last_auto_backup"] = time.time()
                self._persist_config()
                self._append_log("[manager] Automatic world backup completed.")
            except Exception as exc:
                self._append_log(f"[manager] Automatic backup failed: {exc}")
                self.config["last_auto_backup"] = time.time() - (hours * 3600) + 600
                self._persist_config()


class NethernetTunnelManager:
    """Safe adapter for the upstream BedrockNodeTunnel systemd --user service.

    This adapter never reads or returns the tunnel token. It only inspects the
    client version, user-service metadata and recent journal output, and only
    runs a fixed allow-list of systemctl actions.
    """

    UNIT = "bedrocknode-tunnel.service"
    ACTIONS = {"start", "stop", "restart", "enable", "disable"}
    PROC_ROOT = Path("/proc")

    def __init__(self, manager: Manager) -> None:
        self.manager = manager

    @staticmethod
    def _user_env() -> dict[str, str]:
        env = os.environ.copy()
        runtime = Path("/run/user") / str(os.getuid())
        if runtime.is_dir():
            env.setdefault("XDG_RUNTIME_DIR", str(runtime))
            bus = runtime / "bus"
            if bus.exists():
                env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}")
        return env

    def _run(self, argv: list[str], timeout: float = 6.0) -> tuple[int, str, str]:
        if not argv:
            return 127, "", "No command supplied."
        executable = shutil.which(argv[0])
        if not executable:
            return 127, "", f"Required program not found: {argv[0]}"
        try:
            result = subprocess.run(
                [executable, *argv[1:]],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                close_fds=True,
                env=self._user_env(),
            )
            return result.returncode, result.stdout or "", result.stderr or ""
        except subprocess.TimeoutExpired:
            return 124, "", f"Command timed out after {timeout:g} seconds."
        except OSError as exc:
            return 126, "", str(exc)

    @staticmethod
    def _parse_properties(text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def _extract_flag(exec_start: str, name: str, default: str) -> str:
        # systemctl show renders ExecStart as a structured property. Only read
        # the three harmless networking flags; never expose arbitrary arguments
        # (especially a possible -token value) to the UI or logs.
        pattern = rf"(?:^|[\s;=])-{re.escape(name)}(?:=|\s+)(?:\"([^\"]*)\"|'([^']*)'|([^;\s}}]+))"
        match = re.search(pattern, exec_start)
        if not match:
            return default
        return next((part for part in match.groups() if part is not None), default)

    @staticmethod
    def _split_host_port(target: str) -> tuple[str, int] | None:
        value = target.strip()
        if value.startswith("["):
            close = value.find("]")
            if close < 0 or value[close + 1:close + 2] != ":":
                return None
            host, port_text = value[1:close], value[close + 2:]
        else:
            host, sep, port_text = value.rpartition(":")
            if not sep:
                return None
        try:
            port = int(port_text)
        except ValueError:
            return None
        if not host or not 1 <= port <= 65535:
            return None
        return host, port

    @staticmethod
    def _probe_tcp(target: str) -> bool | None:
        parsed = NethernetTunnelManager._split_host_port(target)
        if not parsed:
            return None
        host, port = parsed
        if host in ("0.0.0.0", "::", "*"):
            host = "127.0.0.1"
        try:
            with socket.create_connection((host, port), timeout=0.35):
                return True
        except OSError:
            return False

    def _unit_properties(self) -> tuple[dict[str, str], str | None]:
        if not shutil.which("systemctl"):
            return {}, "systemctl is not installed or not available on PATH."
        wanted = (
            "LoadState", "ActiveState", "SubState", "UnitFileState", "FragmentPath",
            "MainPID", "ActiveEnterTimestamp", "InvocationID", "WorkingDirectory",
            "Result", "NRestarts", "ExecStart",
        )
        args = ["systemctl", "--user", "show", self.UNIT]
        args.extend(f"--property={key}" for key in wanted)
        args.append("--no-pager")
        code, stdout, stderr = self._run(args, timeout=5)
        if code != 0:
            message = (stderr or stdout).strip()
            if "not found" in message.lower() or "could not be found" in message.lower():
                return {"LoadState": "not-found"}, None
            return {}, message or "Unable to query the systemd user service manager."
        props = self._parse_properties(stdout)
        if not props:
            return {}, "systemctl returned no service properties."
        return props, None

    def _journal(self, limit: int = 160, invocation_id: str | None = None) -> list[str]:
        """Read recent service logs, optionally restricted to one systemd run.

        Invocation filtering prevents an old successful connection from making
        a newly restarted, currently-disconnected tunnel look healthy.
        """
        if not shutil.which("journalctl"):
            return []
        args = [
            "journalctl", "--user", "-u", self.UNIT, "-n", str(max(1, min(1000, int(limit)))),
            "--no-pager", "-o", "short-iso",
        ]
        if invocation_id and re.fullmatch(r"[0-9a-fA-F]{32}", invocation_id):
            args.append(f"_SYSTEMD_INVOCATION_ID={invocation_id}")
        code, stdout, _stderr = self._run(args, timeout=6)
        if code != 0:
            return []
        return [line.rstrip() for line in stdout.splitlines() if line.strip()][-max(1, min(1000, int(limit))):]

    @staticmethod
    def _extract_exec_path(exec_start: str) -> str | None:
        """Extract only the executable path from systemd's structured ExecStart."""
        match = re.search(r"(?:^|[\s{;])path=(?:\"([^\"]+)\"|'([^']+)'|([^;\s}]+))", exec_start)
        if not match:
            return None
        candidate = next((part for part in match.groups() if part), "")
        if Path(candidate).name != "nethernet-client":
            return None
        path = Path(candidate)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            return None
        return str(path)

    def _client_info(self, exec_start: str = "") -> dict[str, Any]:
        # Prefer the exact nethernet-client binary selected by the service unit.
        # Fall back to PATH when the unit uses a wrapper or is not installed.
        binary = self._extract_exec_path(exec_start) or shutil.which("nethernet-client")
        if not binary:
            return {"installed": False, "path": "", "version": "Not installed", "version_source": "not-found"}
        code, stdout, stderr = self._run([binary, "-version"], timeout=4)
        reported = (stdout or stderr).strip().splitlines()
        version = reported[-1].strip() if code == 0 and reported else "Version unavailable"
        # Do not let arbitrary output become unbounded UI content.
        source = "service-executable" if self._extract_exec_path(exec_start) == binary else "PATH"
        return {"installed": True, "path": binary, "version": version[:100], "version_source": source}

    def _other_clients(self, service_pid: str | int | None = None) -> list[dict[str, Any]]:
        """Find separately launched nethernet-client processes without reading args.

        The service MainPID is excluded. Only argv[0]/comm are inspected; token
        arguments are never read into a returned value or manager log.
        """
        try:
            owned_pid = int(service_pid or 0)
        except (TypeError, ValueError):
            owned_pid = 0
        found: list[dict[str, Any]] = []
        try:
            entries = list(self.PROC_ROOT.iterdir())
        except OSError:
            return found
        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == owned_pid:
                continue
            try:
                raw_argv0 = (entry / "cmdline").read_bytes().split(b"\x00", 1)[0]
                argv0 = raw_argv0.decode("utf-8", "replace")
                comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if Path(argv0).name == "nethernet-client" or comm in {"nethernet-client", "nethernet-clien"}:
                found.append({"pid": pid, "process": "nethernet-client (outside systemd unit)"})
        return found

    def state(self) -> dict[str, Any]:
        props, systemd_error = self._unit_properties()
        exec_start = props.get("ExecStart", "")
        client = self._client_info(exec_start)
        invocation_id = props.get("InvocationID", "")
        invocation_scoped = bool(re.fullmatch(r"[0-9a-fA-F]{32}", invocation_id))
        # Only current-invocation logs are trusted for connection health. The
        # separate Logs panel can still show historical service logs.
        logs = self._journal(160, invocation_id if invocation_scoped else None) if props.get("LoadState") == "loaded" and invocation_scoped else []
        active_state = props.get("ActiveState", "unknown")
        loaded = props.get("LoadState") == "loaded"
        active = active_state == "active"
        external_clients = self._other_clients(props.get("MainPID", "0"))
        tcp_target = self._extract_flag("" if not exec_start else exec_start, "local-http", "127.0.0.1:19132")
        udp_ip = self._extract_flag(exec_start, "local-udp-ip", "Auto-detected")
        udp_port_text = self._extract_flag(exec_start, "local-udp-port", "47334")
        try:
            udp_port = int(udp_port_text)
            if not 1 <= udp_port <= 65535:
                udp_port = 47334
        except ValueError:
            udp_port = 47334

        properties = self.manager._read_props()
        transport = properties.get("transport", "").strip().lower()
        try:
            server_port = int(properties.get("server-port", "19132"))
        except ValueError:
            server_port = 19132
        target_parts = self._split_host_port(tcp_target)
        target_port = target_parts[1] if target_parts else None
        target_reachable = self._probe_tcp(tcp_target)
        udp_mapping = properties.get("server-udp-ports", "").strip()

        # Match Go's os.UserConfigDir behavior: an empty or relative
        # XDG_CONFIG_HOME must not turn this into a cwd-relative token lookup.
        xdg_config = os.environ.get("XDG_CONFIG_HOME", "").strip()
        config_home = Path(xdg_config).expanduser() if xdg_config and Path(xdg_config).expanduser().is_absolute() else HOME / ".config"
        token_candidates = [config_home / "bedrocknode-tunnel" / "bedrocknode-token.txt"]
        service_workdir = props.get("WorkingDirectory", "").strip()
        if service_workdir and service_workdir not in ("-", "~"):
            workdir = Path(service_workdir).expanduser()
        else:
            # systemd user services default to the user's home directory when
            # WorkingDirectory= is unset. The upstream client also checks a
            # token file relative to its current working directory.
            workdir = HOME
        if workdir.is_absolute():
            fallback_token = workdir / "bedrocknode-token.txt"
            if fallback_token not in token_candidates:
                token_candidates.append(fallback_token)
        token_configured = False
        token_permissions_ok: bool | None = None
        for token_path in token_candidates:
            try:
                token_stat = token_path.stat()
            except OSError:
                continue
            if stat.S_ISREG(token_stat.st_mode) and token_stat.st_size > 0:
                token_configured = True
                token_permissions_ok = (stat.S_IMODE(token_stat.st_mode) & 0o077) == 0
                break

        # Infer tunnel health from explicit client log messages. systemd's
        # active state alone only proves the process is running, not connected.
        event_map = (
            ("connected", "authenticated tunnel established"),
            ("local_unreachable", "local bedrock tcp server unreachable"),
            ("claim_required", "first time setup"),
            ("claim_required", "claim code:"),
            ("claim_required", "auth error"),
            ("claim_required", "deleting saved token and re-claiming"),
            ("reconnecting", "tunnel disconnected"),
            ("reconnecting", "connection failed"),
            ("reconnecting", "handshake failed"),
            ("reconnecting", "yamux client error"),
            ("connecting", "dialing bedrock node tunnel"),
        )
        health, last_event = ("stopped", "Service is not active")
        if loaded and active:
            if invocation_scoped:
                health, last_event = ("connecting", "Service is active; waiting for a connection event in this service run")
                for line in reversed(logs):
                    low = line.lower()
                    match = next(((status, marker) for status, marker in event_map if marker in low), None)
                    if match:
                        health, last_event = match[0], line[-500:]
                        break
            else:
                health, last_event = ("connecting", "Current systemd invocation ID is unavailable; historical logs are not used to claim a connection")
        elif not loaded:
            health, last_event = ("not_installed", "The bedrocknode-tunnel user service is not installed")

        diagnostics: list[dict[str, str]] = []
        def add(level: str, title: str, detail: str) -> None:
            diagnostics.append({"level": level, "title": title, "detail": detail})

        if not client["installed"]:
            add("warning", "Tunnel client not found", "Install BedrockNodeTunnel using the official Arch package instructions, then refresh this panel.")
        if systemd_error:
            add("warning", "User service manager unavailable", systemd_error)
        elif not loaded:
            add("warning", "User service not installed", f"Expected unit: {self.UNIT}")
        elif not active:
            add("warning", "Tunnel service stopped", "Start the Bedrock Node Tunnel service after confirming the BDS target settings.")
        else:
            add("success", "Tunnel service running", f"systemd reports {props.get('SubState', 'active')} (PID {props.get('MainPID', '—')}).")

        if transport != "nethernet":
            if transport:
                add("warning", "BDS transport is not NetherNet", f"server.properties currently has transport={transport}. Bedrock Node Tunnel expects NetherNet.")
            else:
                add("warning", "Transport not explicitly configured", "No transport key was found. Check the installed BDS version and its bundled server configuration guide; do not assume this build's default.")
        else:
            add("success", "BDS transport is NetherNet", "server.properties explicitly selects NetherNet.")

        if target_parts and target_port == server_port:
            add("success", "TCP signaling target matches", f"Tunnel local target {tcp_target} matches server-port={server_port}.")
        elif target_parts:
            add("warning", "TCP target and BDS port differ", f"Tunnel targets {tcp_target}, while server.properties sets server-port={server_port}. Align them if this difference is not intentional.")
        else:
            add("warning", "Tunnel TCP target is invalid", f"The client target {tcp_target!r} is not a valid host:port value.")

        if target_reachable is True:
            add("success", "Local TCP signaling port reachable", f"A local TCP connection to {tcp_target} succeeded.")
        elif target_reachable is False:
            add("warning", "Local TCP signaling port unreachable", f"Nothing accepted a TCP connection at {tcp_target}. Start BDS and verify the client's -local-http target.")
        else:
            add("warning", "Local TCP target not testable", "The client target could not be parsed as host:port.")

        if udp_mapping:
            add("info", "UDP gameplay mapping present", "server-udp-ports is configured. Compare it with the exact four settings supplied by your Bedrock Node dashboard.")
        else:
            add("warning", "UDP gameplay mapping not found", "server-udp-ports is empty or absent. Check the exact mapping supplied by your Bedrock Node dashboard and the BDS host guide before exposing the server.")

        if active and not invocation_scoped:
            add("info", "Current-session log verification unavailable", "This systemd version did not provide a usable InvocationID. The manager will not infer a connected state from older tunnel logs.")

        if external_clients:
            pids = ", ".join(str(item["pid"]) for item in external_clients)
            add("warning", "Another tunnel client process is running", f"Found nethernet-client outside {self.UNIT} (PID {pids}). Stop the foreground client before starting or restarting the systemd service to avoid duplicate tunnel sessions.")

        if token_configured:
            add("success", "Tunnel claim token present", "A token file exists. Its contents are never read or sent to the manager UI.")
            if token_permissions_ok is False:
                add("warning", "Token file permissions are broad", "The token file is accessible by group or other users. The upstream client normally stores it with owner-only permissions.")
        else:
            add("info", "Tunnel not claimed yet", "Start the client once to display its claim link, then finish linking it on the Bedrock Node website.")

        return {
            "unit": self.UNIT,
            "systemd_available": bool(shutil.which("systemctl")),
            "systemd_error": systemd_error or "",
            "service_installed": loaded,
            "active": active,
            "active_state": active_state,
            "sub_state": props.get("SubState", "unknown"),
            "enabled": props.get("UnitFileState", "") in ("enabled", "enabled-runtime"),
            "unit_file_state": props.get("UnitFileState", "unknown"),
            "pid": props.get("MainPID", "0"),
            "external_clients": external_clients,
            "active_since": props.get("ActiveEnterTimestamp", ""),
            "invocation_logs_scoped": invocation_scoped,
            "result": props.get("Result", ""),
            "restarts": props.get("NRestarts", "0"),
            "fragment_path": props.get("FragmentPath", ""),
            "client": client,
            "health": health,
            "health_label": {
                "connected": "Tunnel connected",
                "connecting": "Connecting",
                "reconnecting": "Reconnecting",
                "local_unreachable": "BDS target unreachable",
                "claim_required": "Claim required",
                "stopped": "Stopped",
                "not_installed": "Not installed",
            }.get(health, "Unknown"),
            "last_event": last_event,
            "recent_logs_available": bool(logs),
            "target": {"tcp": tcp_target, "udp_ip": udp_ip, "udp_port": udp_port, "tcp_reachable": target_reachable},
            "bds": {
                "transport": transport or "Not set",
                "server_ip": properties.get("server-ip", ""),
                "server_port": server_port,
                "udp_mapping_configured": bool(udp_mapping),
                "udp_mapping": udp_mapping,
                "max_players": properties.get("max-players", "10"),
            },
            "token": {"configured": token_configured, "permissions_ok": token_permissions_ok},
            "diagnostics": diagnostics,
            "integration": {
                "start_with_bds": self.manager.tunnel_start_with_bds,
                "stop_with_bds": self.manager.tunnel_stop_with_bds,
            },
        }

    def logs(self, limit: int = 160) -> dict[str, Any]:
        # The log viewer intentionally includes history across restarts; health
        # decisions use _journal(..., invocation_id) from state() instead.
        lines = self._journal(max(20, min(400, int(limit))))
        return {"unit": self.UNIT, "lines": lines, "available": bool(lines)}

    def action(self, action: str) -> dict[str, Any]:
        if action not in self.ACTIONS:
            raise ValueError("Unsupported tunnel action.")
        props, error = self._unit_properties()
        if error:
            raise ValueError(error)
        if props.get("LoadState") != "loaded":
            raise ValueError(f"The {self.UNIT} user service is not installed. Install the official BedrockNodeTunnel Arch package first.")
        if action in {"start", "restart"}:
            external = self._other_clients(props.get("MainPID", "0"))
            if external:
                pids = ", ".join(str(item["pid"]) for item in external)
                raise ValueError(f"A foreground nethernet-client process is already running outside {self.UNIT} (PID {pids}). Stop that foreground client first to avoid duplicate tunnel sessions.")
        code, stdout, stderr = self._run(["systemctl", "--user", action, self.UNIT], timeout=12)
        if code != 0:
            detail = (stderr or stdout).strip() or f"systemctl {action} failed with exit code {code}."
            raise ValueError(detail[-1200:])
        message = (stdout or stderr).strip()
        if not message:
            message = f"Bedrock Node Tunnel service {action} request completed."
        self.manager._append_log(f"[manager] Bedrock Node Tunnel: systemctl --user {action} {self.UNIT}")
        return {"ok": True, "message": message[:500]}


MANAGER = Manager()
TUNNEL_MANAGER = NethernetTunnelManager(MANAGER)
TOKEN = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    server_version = f"BDSOmni/{APP_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the terminal quiet; manager events appear in the in-app log.
        return

    def _send(self, code: int, data: Any, content_type: str = "application/json; charset=utf-8") -> bool:
        """Send a response, treating a browser disconnect as a normal event.

        Browsers can cancel an in-flight request when a tab is closed, a page is
        reloaded, or navigation replaces the request. In that case the socket is
        already gone; trying to send an error response from an outer exception
        handler only creates a second BrokenPipeError and a noisy traceback.
        """
        if isinstance(data, (dict, list)):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = bytes(data)
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
            return True
        except ConnectionError:
            # BrokenPipeError, ConnectionResetError and ConnectionAbortedError
            # all mean the browser has gone away. Do not attempt another write.
            self.close_connection = True
            return False

    def _send_download(self, path: Path, filename: str, content_type: str = "application/octet-stream") -> bool:
        if path.is_symlink() or not path.is_file():
            self._send(404, {"error": "Export file was not found."})
            return False
        try:
            size = path.stat().st_size
            safe_ascii = re.sub(r'[^A-Za-z0-9._-]+', '_', filename).strip("._") or "download.bin"
            encoded = quote(filename, safe="")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{safe_ascii}"; filename*=UTF-8\'\'{encoded}')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            with path.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return True
        except ConnectionError:
            self.close_connection = True
            return False

    def _receive_content_upload(self, path: str) -> dict[str, Any]:
        length_text = self.headers.get("Content-Length", "")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise ValueError("A valid Content-Length header is required for uploads.") from exc
        if length <= 0 or length > MAX_CONTENT_UPLOAD_BYTES:
            raise ValueError("Upload must be between 1 byte and 8 GiB.")
        filename = unquote(self.headers.get("X-Upload-Filename", "")).strip()
        if not filename or filename in (".", "..") or Path(filename).name != filename or any(ord(c) < 32 for c in filename):
            raise ValueError("Upload filename is missing or unsafe.")
        if path == "/api/worlds/import":
            allowed = (".mcworld", ".zip")
            explanation = "World imports accept .mcworld or .zip."
        elif path == "/api/mods/import":
            allowed = (".mcaddon", ".mcpack", ".zip")
            explanation = "Bedrock mod imports accept .mcaddon, .mcpack, or a ZIP containing valid Bedrock packs. Java .jar mods are not supported by BDS."
        else:
            allowed = (".mcpack", ".zip")
            explanation = "Pack imports accept .mcpack or .zip."
        if Path(filename).suffix.lower() not in allowed:
            raise ValueError("Unsupported archive extension. " + explanation)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="bds-omni-upload-", suffix=Path(filename).suffix.lower(), dir=STATE_DIR)
        temp_path = Path(temp_name)
        received = 0
        try:
            with os.fdopen(fd, "wb") as output:
                while received < length:
                    chunk = self.rfile.read(min(1024 * 1024, length - received))
                    if not chunk:
                        raise ValueError("Upload ended before all declared bytes were received.")
                    received += len(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if received != length:
                raise ValueError("Upload length did not match Content-Length.")
            if path == "/api/worlds/import":
                world_name = unquote(self.headers.get("X-World-Name", "")).strip()
                result = MANAGER.import_world_archive(temp_path, world_name)
            else:
                scope = self.headers.get("X-Pack-Scope", "shared")
                world_name = unquote(self.headers.get("X-Pack-World", "")).strip()
                if path == "/api/mods/import":
                    result = MANAGER.import_mod_archive(temp_path, filename, scope, world_name)
                else:
                    result = MANAGER.import_pack_archive(temp_path, scope, world_name)
            return result
        finally:
            temp_path.unlink(missing_ok=True)

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        return host.startswith("127.0.0.1:") or host.startswith("localhost:")

    def _authorized(self) -> bool:
        if not self._host_ok():
            return False
        origin = self.headers.get("Origin")
        if origin and not (origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:")):
            return False
        return secrets.compare_digest(self.headers.get("X-BDS-Token", ""), TOKEN)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            try:
                html = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8").replace("__BDS_TOKEN__", TOKEN).replace("__APP_VERSION__", APP_VERSION)
                self._send(200, html, "text/html; charset=utf-8")
            except OSError as exc:
                self._send(500, f"Unable to load UI: {exc}", "text/plain; charset=utf-8")
            return
        if parsed.path.startswith("/static/"):
            rel = parsed.path.removeprefix("/static/")
            target = (Path(__file__).parent / "static" / rel).resolve()
            base = (Path(__file__).parent / "static").resolve()
            if base not in target.parents or not target.is_file():
                self._send(404, "Not found", "text/plain; charset=utf-8")
                return
            ctype = "image/svg+xml" if target.suffix == ".svg" else "text/plain; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)
            return
        if parsed.path.startswith("/download/"):
            if not self._host_ok():
                self._send(403, {"error": "Forbidden"})
                return
            ticket = parsed.path.removeprefix("/download/")
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", ticket):
                self._send(404, {"error": "Download ticket not found."})
                return
            entry = MANAGER.consume_download_ticket(ticket)
            if not entry:
                self._send(404, {"error": "Download ticket expired or already used. Generate a new export."})
                return
            self._send_download(entry[0], entry[1], entry[2])
            return
        if not self._authorized():
            self._send(403, {"error": "Forbidden"})
            return
        try:
            if parsed.path == "/api/state":
                self._send(200, MANAGER.status())
            elif parsed.path == "/api/properties":
                self._send(200, MANAGER.properties())
            elif parsed.path == "/api/access":
                self._send(200, MANAGER.access_files())
            elif parsed.path == "/api/packs":
                self._send(200, MANAGER.packs())
            elif parsed.path == "/api/mods":
                self._send(200, MANAGER.mods())
            elif parsed.path == "/api/content-lab":
                world_name = parse_qs(parsed.query).get("world", [""])[0]
                self._send(200, MANAGER.content_lab(world_name))
            elif parsed.path == "/api/content-profiles":
                self._send(200, MANAGER.list_stack_profiles())
            elif parsed.path == "/api/worlds":
                self._send(200, MANAGER.worlds())
            elif parsed.path == "/api/worlds/detail":
                name = parse_qs(parsed.query).get("name", [""])[0]
                self._send(200, MANAGER.world_detail(name))
            elif parsed.path == "/api/browse":
                rel = parse_qs(parsed.query).get("path", [""])[0]
                self._send(200, MANAGER.browse(rel))
            elif parsed.path == "/api/logs":
                since = int(parse_qs(parsed.query).get("since", ["0"])[0])
                self._send(200, {"logs": MANAGER.log_since(since), "last_id": MANAGER.log_id})
            elif parsed.path == "/api/commands":
                self._send(200, COMMANDS)
            elif parsed.path == "/api/command-forms":
                self._send(200, COMMAND_FORMS)
            elif parsed.path == "/api/backups":
                self._send(200, MANAGER.list_backups())
            elif parsed.path == "/api/tunnel/state":
                self._send(200, TUNNEL_MANAGER.state())
            elif parsed.path == "/api/tunnel/logs":
                self._send(200, TUNNEL_MANAGER.logs())
            elif parsed.path == "/api/metrics":
                self._send(200, MANAGER.metrics())
            elif parsed.path == "/api/health":
                self._send(200, MANAGER.health())
            elif parsed.path == "/api/schedules":
                self._send(200, MANAGER.scheduled_jobs_state())
            else:
                self._send(404, {"error": "Not found"})
        except Exception as exc:
            self._send(400, {"error": str(exc)})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(403, {"error": "Forbidden"})
            return
        request_path = urlparse(self.path).path
        if request_path in ("/api/worlds/import", "/api/packs/import", "/api/mods/import"):
            try:
                result = self._receive_content_upload(request_path)
                self._send(200, result)
            except Exception as exc:
                self._send(400, {"error": str(exc)})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 2_000_000:
                raise ValueError("Request body too large.")
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object.")
            path = urlparse(self.path).path
            if path == "/api/worlds/export":
                export_path = MANAGER.export_world_archive(str(data.get("name", "")))
                result = MANAGER.issue_download_ticket(export_path, export_path.name, "application/x-minecraft-world")
            elif path == "/api/packs/export":
                export_path = MANAGER.export_pack_archive(str(data.get("location_id", "")))
                result = MANAGER.issue_download_ticket(export_path, export_path.name, "application/x-minecraft-pack")
            elif path == "/api/dialog/choose-directory":
                kind = str(data.get("kind", ""))
                if kind == "server":
                    initial, title = MANAGER.root, "Choose Bedrock Dedicated Server folder"
                elif kind == "backup":
                    initial, title = MANAGER.backup_dir, "Choose BDS backup folder"
                else:
                    raise ValueError("Unknown folder type.")
                selected = choose_directory_dialog(initial, title)
                result = {"cancelled": selected is None, "path": str(selected) if selected else ""}
            elif path == "/api/start":
                result = MANAGER.start()
                if MANAGER.tunnel_start_with_bds:
                    try:
                        TUNNEL_MANAGER.action("start")
                        result["message"] += " Bedrock Node Tunnel start requested."
                    except ValueError as exc:
                        MANAGER._append_log(f"[manager] Automatic tunnel start failed: {exc}")
                        result["warning"] = str(exc)
            elif path == "/api/stop":
                managed_running = bool(MANAGER.proc and MANAGER.proc.poll() is None)
                lifecycle_warnings: list[str] = []
                if MANAGER.tunnel_stop_with_bds and managed_running:
                    try:
                        TUNNEL_MANAGER.action("stop")
                    except ValueError as exc:
                        warning = f"Tunnel stop failed: {exc}"
                        lifecycle_warnings.append(warning)
                        MANAGER._append_log(f"[manager] Automatic {warning}")
                result = MANAGER.stop()
                if lifecycle_warnings:
                    result["warning"] = " ".join(lifecycle_warnings)[:1200]
            elif path == "/api/restart":
                managed_running = bool(MANAGER.proc and MANAGER.proc.poll() is None)
                lifecycle_warnings: list[str] = []
                if MANAGER.tunnel_stop_with_bds and managed_running:
                    try:
                        TUNNEL_MANAGER.action("stop")
                    except ValueError as exc:
                        warning = f"Tunnel stop failed: {exc}"
                        lifecycle_warnings.append(warning)
                        MANAGER._append_log(f"[manager] Automatic {warning}")
                result = MANAGER.restart()
                if MANAGER.tunnel_start_with_bds:
                    try:
                        TUNNEL_MANAGER.action("start")
                    except ValueError as exc:
                        warning = f"Tunnel start failed: {exc}"
                        lifecycle_warnings.append(warning)
                        MANAGER._append_log(f"[manager] Automatic {warning}")
                if lifecycle_warnings:
                    result["warning"] = " ".join(lifecycle_warnings)[:1200]
            elif path == "/api/tunnel/config/preview":
                imported = parse_bedrock_node_property_lines(data.get("text", ""))
                values = MANAGER.validate_property_updates(imported)
                current = MANAGER._read_props()
                changes = [{"key": key, "current": current.get(key, "(not set)"), "new": value, "changed": current.get(key) != value} for key, value in values.items()]
                result = {"ok": True, "values": values, "changes": changes, "restart_required": bool(MANAGER.proc and MANAGER.proc.poll() is None)}
            elif path == "/api/tunnel/config/import":
                imported = parse_bedrock_node_property_lines(data.get("text", ""))
                result = MANAGER.save_properties(imported)
            elif path == "/api/tunnel/action": result = TUNNEL_MANAGER.action(str(data.get("action", "")))
            elif path == "/api/tunnel/settings": result = MANAGER.set_tunnel_settings(data)
            elif path == "/api/schedules": result = MANAGER.upsert_scheduled_job(data.get("job", {}))
            elif path == "/api/schedules/delete": result = MANAGER.delete_scheduled_job(str(data.get("id", "")))
            elif path == "/api/command": result = MANAGER.command(str(data.get("command", "")))
            elif path == "/api/command/preview": result = build_guided_command(str(data.get("form", "")), data.get("values", {}))
            elif path == "/api/config/server-dir": result = MANAGER.set_server_dir(str(data.get("server_dir", "")))
            elif path == "/api/config/backups": result = MANAGER.set_backup_settings(data)
            elif path == "/api/properties": result = MANAGER.save_properties(data.get("values", {}))
            elif path == "/api/access": result = MANAGER.save_access_files(data)
            elif path == "/api/worlds/action": result = MANAGER.world_action(str(data.get("action", "")), data)
            elif path == "/api/packs/action": result = MANAGER.pack_action(str(data.get("action", "")), data)
            elif path == "/api/worlds/packs": result = MANAGER.set_world_pack(str(data.get("world", "")), str(data.get("kind", "")), str(data.get("location_id", "")), data.get("enabled"))
            elif path == "/api/content-lab/reorder": result = MANAGER.reorder_world_pack_stack(str(data.get("world", "")), str(data.get("kind", "")), data.get("ordered_ids"))
            elif path == "/api/content-profiles":
                action = str(data.get("action", ""))
                if action == "create": result = MANAGER.create_stack_profile(str(data.get("world", "")), str(data.get("name", "")))
                elif action == "delete": result = MANAGER.delete_stack_profile(str(data.get("id", "")))
                elif action == "preview": result = MANAGER.preview_stack_profile(str(data.get("id", "")), str(data.get("target_world", "")))
                elif action == "apply": result = MANAGER.apply_stack_profile(str(data.get("id", "")), str(data.get("target_world", "")))
                else: raise ValueError("Unknown content profile action.")
            elif path == "/api/backups/compare": result = MANAGER.compare_backups(str(data.get("first", "")), str(data.get("second", "")))
            elif path == "/api/backup": result = MANAGER.backup(str(data.get("mode", "world")))
            elif path == "/api/backup/verify": result = MANAGER.verify_backup(str(data.get("name", "")))
            elif path == "/api/restore": result = MANAGER.restore(str(data.get("name", "")))
            else:
                self._send(404, {"error": "Not found"})
                return
            self._send(200, result)
        except Exception as exc:
            self._send(400, {"error": str(exc)})


def main() -> None:
    host, port = "127.0.0.1", 8765
    httpd: ThreadingHTTPServer | None = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        print("Could not find an available local port (8765-8794).", file=sys.stderr)
        raise SystemExit(1)
    url = f"http://127.0.0.1:{port}/"
    print(f"\n{APP_NAME} {APP_VERSION}")
    print(f"Local UI: {url}")
    print(f"BDS folder: {MANAGER.root}")
    print("Only listens on 127.0.0.1. Keep this process running while managing BDS.")
    print("Press Ctrl+C to stop the manager and gracefully stop its managed BDS process.\n")
    try:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    except Exception:
        pass
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nManager shutting down. Stopping its managed BDS process cleanly…")
    finally:
        # Stop schedulers and wait for any in-flight live backup to release save hold
        # before stopping the BDS process.
        MANAGER.shutdown()
        try:
            managed_running = bool(MANAGER.proc and MANAGER.proc.poll() is None)
            if MANAGER.tunnel_stop_with_bds and managed_running:
                try:
                    TUNNEL_MANAGER.action("stop")
                except ValueError as exc:
                    MANAGER._append_log(f"[manager] Tunnel shutdown warning: {exc}")
            if managed_running:
                MANAGER.stop()
                print("Managed BDS server stopped.")
        except Exception as exc:
            print(f"Warning: could not stop managed BDS cleanly: {exc}", file=sys.stderr)
        httpd.server_close()


if __name__ == "__main__":
    main()
