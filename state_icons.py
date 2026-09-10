#!/usr/bin/env python3
"""Report a configurable lifecycle glyph as a HerdR sidebar token."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

TOKENS = {
    "working": "state_working_line",
    "done": "state_done_line",
    "blocked": "state_blocked_line",
    "idle": "state_idle_line",
    "unknown": "state_unknown_line",
}
SPACE_TOKENS = {
    status: token.replace("state_", "space_", 1)
    for status, token in TOKENS.items()
}
LEGACY_TOKENS = ("state_icon_custom", "state_line_custom")
BLANK_ICON = "⠀"
DEFAULT_FRAMES = ("⠙", "⠸", "⢰", "⣠", "⣄", "⡆", "⠇", "⠋")
DEFAULT_ICONS = {
    "done": "󰄬",
    "blocked": "󰅖",
    "idle": "󰝦",
    "unknown": "󰋗",
    "needs-input": "󰋗",
}


@dataclass(frozen=True)
class Settings:
    frames: tuple[str, ...] = DEFAULT_FRAMES
    icons: tuple[tuple[str, str], ...] = tuple(DEFAULT_ICONS.items())
    frame_seconds: float = 0.15
    done_hold_seconds: float = 6.0

    def glyph(self, status: str, frame: int) -> str:
        if status == "working":
            return self.frames[frame % len(self.frames)]
        return dict(self.icons).get(status, dict(self.icons)["unknown"])


def config_path() -> Path:
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if config_dir:
        return Path(config_dir) / "config.toml"
    return Path.home() / ".config/herdr/plugins/config/flowreaction.state-icons/config.toml"


def load_settings(path: Path | None = None) -> Settings:
    target = path or config_path()
    if not target.is_file():
        return Settings()

    with target.open("rb") as config_file:
        config = tomllib.load(config_file)

    icon_config = config.get("icons", {})
    animation = config.get("animation", {})
    frames = icon_config.get("working", DEFAULT_FRAMES)
    if not isinstance(frames, list) or not frames or not all(isinstance(item, str) and item for item in frames):
        frames = DEFAULT_FRAMES

    icons = {
        state: icon_config.get(state, default)
        if isinstance(icon_config.get(state, default), str)
        else default
        for state, default in DEFAULT_ICONS.items()
    }
    frame_seconds = animation.get("frame-seconds", 0.15)
    done_hold_seconds = animation.get("done-hold-seconds", 6.0)

    return Settings(
        frames=tuple(frames),
        icons=tuple(icons.items()),
        frame_seconds=frame_seconds if isinstance(frame_seconds, (int, float)) and frame_seconds > 0 else 0.15,
        done_hold_seconds=(
            done_hold_seconds
            if isinstance(done_hold_seconds, (int, float)) and done_hold_seconds >= 0
            else 6.0
        ),
    )


def herdr(*args: str) -> dict:
    command = [os.environ.get("HERDR_BIN_PATH", "herdr"), *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def agents() -> list[tuple[str, str, str]]:
    data = herdr("agent", "list")
    found = []
    for agent in data.get("result", {}).get("agents", []):
        pane = agent.get("pane_id")
        status = agent.get("agent_status")
        workspace = agent.get("workspace_id")
        tab = agent.get("tab_id")
        if isinstance(pane, str) and isinstance(status, str):
            tab_id = tab if isinstance(tab, str) else ""
            workspace_id = workspace if isinstance(workspace, str) else tab_id.partition(":")[0]
            found.append((pane, status, workspace_id))
    return found


def workspaces() -> list[tuple[str, str, str]]:
    data = herdr("workspace", "list")
    found = []
    for workspace in data.get("result", {}).get("workspaces", []):
        workspace_id = workspace.get("workspace_id")
        label = workspace.get("label")
        status = workspace.get("agent_status")
        if all(isinstance(value, str) for value in (workspace_id, label, status)):
            found.append((workspace_id, label, status))
    return found


def resolve_status(
    status: str,
    previous_status: str | None,
    done_deadline: float,
    now: float,
    hold_seconds: float,
) -> tuple[str, float]:
    if status == "working":
        return status, 0.0
    if previous_status == "working" and status in ("idle", "done"):
        done_deadline = now + hold_seconds
    if now < done_deadline:
        return "done", done_deadline
    return status, 0.0


def compose_line(glyph: str, workspace: str) -> str:
    return " ".join(part for part in (glyph, workspace) if part)


def compose_workspace_line(settings: Settings, status: str, frame: int, label: str) -> str:
    glyph = BLANK_ICON if status == "unknown" else settings.glyph(status, frame)
    return compose_line(glyph, label)


def frame_delay(started_at: float, now: float, interval: float) -> float:
    return max(0.0, interval - (now - started_at))


def report(source: str, pane: str, line: str | None, status: str | None = None) -> None:
    args = ["pane", "report-metadata", pane, "--source", source]
    for token in LEGACY_TOKENS:
        args += ["--clear-token", token]

    active = TOKENS.get(status or "")
    for token in TOKENS.values():
        if line is not None and token == active:
            args += ["--token", f"{token}={line}"]
        else:
            args += ["--clear-token", token]
    herdr(*args)


def report_workspace(
    source: str,
    workspace: str,
    line: str | None,
    status: str | None = None,
) -> None:
    args = ["workspace", "report-metadata", workspace, "--source", source]
    active = SPACE_TOKENS.get(status or "")
    for token in SPACE_TOKENS.values():
        if line is not None and token == active:
            args += ["--token", f"{token}={line}"]
        else:
            args += ["--clear-token", token]
    herdr(*args)


def state_dir() -> Path:
    base = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.environ.get("TMPDIR", "/tmp")
    directory = Path(base) / "herdr-state-icons"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def acquire_animator_lock() -> TextIO | None:
    lock = (state_dir() / "animator.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def process_is_running(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text().strip()), 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def spawn_animator() -> None:
    if process_is_running(state_dir() / "animator.pid"):
        return
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--animate"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def animate(source: str, settings: Settings) -> int:
    lock = acquire_animator_lock()
    if lock is None:
        return 0

    pid_file = state_dir() / "animator.pid"
    stop_file = state_dir() / "animator.stop"
    pid_file.write_text(str(os.getpid()))
    stop_file.unlink(missing_ok=True)
    pane_previous: dict[str, str] = {}
    pane_shown: dict[str, str] = {}
    pane_done_until: dict[str, float] = {}
    workspace_previous: dict[str, str] = {}
    workspace_shown: dict[str, str] = {}
    workspace_done_until: dict[str, float] = {}
    frame = 0

    def exit_cleanly(*_: object) -> None:
        stop_file.touch()

    signal.signal(signal.SIGTERM, exit_cleanly)
    try:
        while not stop_file.exists():
            frame_started = time.monotonic()
            now = frame_started
            current_workspaces = workspaces()
            labels = {workspace: label for workspace, label, _ in current_workspaces}
            current_agents = agents()
            live_panes = {pane for pane, _, _ in current_agents}
            live_workspaces = {workspace for workspace, _, _ in current_workspaces}
            keep_running = False

            for pane, status, workspace_id in current_agents:
                display_status, deadline = resolve_status(
                    status,
                    pane_previous.get(pane),
                    pane_done_until.get(pane, 0.0),
                    now,
                    settings.done_hold_seconds,
                )
                pane_previous[pane] = status
                if deadline:
                    pane_done_until[pane] = deadline
                else:
                    pane_done_until.pop(pane, None)
                line = compose_line(settings.glyph(display_status, frame), labels.get(workspace_id, ""))
                if pane_shown.get(pane) != line:
                    report(source, pane, line, display_status)
                    pane_shown[pane] = line
                keep_running |= display_status == "working" or pane in pane_done_until

            for pane in pane_shown.keys() - live_panes:
                report(source, pane, None)
                pane_shown.pop(pane, None)
                pane_previous.pop(pane, None)
                pane_done_until.pop(pane, None)

            for workspace, label, status in current_workspaces:
                display_status, deadline = resolve_status(
                    status,
                    workspace_previous.get(workspace),
                    workspace_done_until.get(workspace, 0.0),
                    now,
                    settings.done_hold_seconds,
                )
                workspace_previous[workspace] = status
                if deadline:
                    workspace_done_until[workspace] = deadline
                else:
                    workspace_done_until.pop(workspace, None)
                line = compose_workspace_line(settings, display_status, frame, label)
                if workspace_shown.get(workspace) != line:
                    report_workspace(source, workspace, line, display_status)
                    workspace_shown[workspace] = line
                keep_running |= display_status == "working" or workspace in workspace_done_until

            for workspace in workspace_shown.keys() - live_workspaces:
                report_workspace(source, workspace, None)
                workspace_shown.pop(workspace, None)
                workspace_previous.pop(workspace, None)
                workspace_done_until.pop(workspace, None)

            if not keep_running:
                break
            frame += 1
            time.sleep(frame_delay(frame_started, time.monotonic(), settings.frame_seconds))
    finally:
        pid_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
        lock.close()
    return 0


def stop(source: str) -> None:
    pid_file = state_dir() / "animator.pid"
    (state_dir() / "animator.stop").touch()
    if process_is_running(pid_file):
        os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
    for pane, _, _ in agents():
        report(source, pane, None)
    for workspace, _, _ in workspaces():
        report_workspace(source, workspace, None)


def main() -> int:
    plugin_id = os.environ.get("HERDR_PLUGIN_ID", "flowreaction.state-icons")
    source = f"plugin:{plugin_id}"
    argument = sys.argv[1] if len(sys.argv) > 1 else ""
    if argument == "--animate":
        return animate(source, load_settings())
    if argument == "--stop":
        stop(source)
        return 0
    spawn_animator()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
