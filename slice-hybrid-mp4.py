#!/usr/bin/env python3
"""
Splits an OBS Studio Hybrid MP4 recording into clips at its chapter markers.

For each chapter marker, a clip is made that starts at the previous marker
(or at the start of the video if there is no previous marker) and ends at that marker.
The tail after the last marker is saved as a clip of its own, unless --no-tail is given.

Requires: ffmpeg and ffprobe in PATH. If they are missing, the script offers to install
them with the OS package manager (winget/choco/scoop, brew/port, apt/dnf/pacman/...).

Usage:
    python slice-hybrid-mp4.py recording.mp4
    python slice-hybrid-mp4.py recording.mp4 -o clips/ --reencode
    python slice-hybrid-mp4.py recording.mp4 --dry-run
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TOOLS = ("ffmpeg", "ffprobe")
DOWNLOAD_URL = "https://ffmpeg.org/download.html"


def install_commands():
    """Returns the ffmpeg install commands (a list of commands) for this operating system,
    or None if no supported package manager is found."""
    if sys.platform == "win32":
        managers = [
            ("winget", [["winget", "install", "--id", "Gyan.FFmpeg", "-e"]]),
            ("choco", [["choco", "install", "ffmpeg"]]),
            ("scoop", [["scoop", "install", "ffmpeg"]]),
        ]
    elif sys.platform == "darwin":
        managers = [
            ("brew", [["brew", "install", "ffmpeg"]]),
            ("port", [["sudo", "port", "install", "ffmpeg"]]),
        ]
    else:  # Linux and other Unixes
        sudo = ["sudo"] if os.geteuid() != 0 and shutil.which("sudo") else []
        managers = [
            ("apt-get", [sudo + ["apt-get", "update"], sudo + ["apt-get", "install", "ffmpeg"]]),
            ("dnf", [sudo + ["dnf", "install", "ffmpeg"]]),
            ("pacman", [sudo + ["pacman", "-S", "ffmpeg"]]),
            ("zypper", [sudo + ["zypper", "install", "ffmpeg"]]),
            ("apk", [sudo + ["apk", "add", "ffmpeg"]]),
        ]
    for exe, cmds in managers:
        if shutil.which(exe):
            return cmds
    return None


def manual_hint() -> str:
    if sys.platform == "win32":
        return ("Install winget (App Installer from the Microsoft Store) and run: winget install Gyan.FFmpeg\n"
                "or download https://www.gyan.dev/ffmpeg/builds/ and add its bin folder to PATH.")
    if sys.platform == "darwin":
        return "Install Homebrew (https://brew.sh) and run: brew install ffmpeg"
    return f"Install the ffmpeg package with your distribution's package manager, or see {DOWNLOAD_URL}"


def refresh_windows_path():
    """Reads PATH from the registry so that a freshly installed ffmpeg is found
    without opening a new terminal."""
    import winreg
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ]
    parts = []
    for root, sub in keys:
        try:
            with winreg.OpenKey(root, sub) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    os.environ["PATH"] = os.pathsep.join(parts + [os.environ.get("PATH", "")])


def ensure_ffmpeg():
    """Makes sure that ffmpeg and ffprobe can be found and started."""
    missing = [t for t in TOOLS if shutil.which(t) is None]
    if missing:
        offer_install(missing)
    errors = [e for e in map(launch_error, TOOLS) if e]
    if errors:
        sys.exit("\n\n".join(errors))


def launch_error(tool: str):
    """Tries to start the tool. Returns an error message, or None if it works."""
    path = shutil.which(tool)
    try:
        subprocess.run([path, "-version"], capture_output=True, check=True)
        return None
    except (OSError, subprocess.CalledProcessError) as e:
        msg = f"{path} was found but could not be started: {e}"
        if getattr(e, "winerror", None) == 1920:  # ERROR_CANT_ACCESS_FILE
            msg += ("\nThe file is probably an online-only file in a cloud folder (Dropbox, OneDrive) "
                    "that could not be downloaded. Start the cloud app and set the file to always "
                    "be kept on this device, or install ffmpeg outside the cloud folder.")
        return msg


def offer_install(missing):
    """Offers to install the missing ffmpeg. Exits the program if the installation fails."""
    print(f"{' and '.join(missing)} not found in PATH.", file=sys.stderr)

    cmds = install_commands()
    if cmds is None:
        sys.exit(manual_hint())
    shown = " && ".join(" ".join(c) for c in cmds)
    if not sys.stdin.isatty():
        sys.exit(f"Install ffmpeg with:\n    {shown}")

    try:
        answer = input(f"Install ffmpeg now with this command?\n    {shown}\n[y/N] ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        sys.exit(f"ffmpeg is required. Install it with\n    {shown}\nand run the script again.")

    for cmd in cmds:
        # shutil.which also finds .cmd/.ps1 shims (e.g. scoop) that the bare name would miss
        exe = shutil.which(cmd[0]) or cmd[0]
        if subprocess.run([exe, *cmd[1:]]).returncode != 0:
            sys.exit(f"Installation failed. Install ffmpeg manually: {DOWNLOAD_URL}")

    if sys.platform == "win32":
        refresh_windows_path()
    if any(shutil.which(t) is None for t in TOOLS):
        sys.exit("Installation finished, but ffmpeg is not in PATH yet. "
                 "Open a new terminal and run the script again.")
    print("ffmpeg installed.\n")


def probe(path: Path):
    """Returns (chapter markers [(time_s, title)], duration_s)."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_chapters", "-show_format", str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"ffprobe failed:\n{res.stderr}")
    data = json.loads(res.stdout)
    duration = float(data["format"]["duration"])

    marks = []
    for ch in data.get("chapters", []):
        t = float(ch["start_time"])
        title = (ch.get("tags") or {}).get("title", "").strip()
        marks.append((t, title))
    marks.sort(key=lambda m: m[0])

    # Drop a marker at the very start of the video, and any duplicates
    cleaned = []
    for t, title in marks:
        if t < 0.05:
            continue
        if cleaned and abs(cleaned[-1][0] - t) < 0.05:
            continue
        cleaned.append((t, title))
    return cleaned, duration


def build_segments(marks, duration, include_tail: bool, name_by: str):
    """Builds a list of (start, end, name)."""
    segments = []
    prev_t, prev_title = 0.0, "start"
    for t, title in marks:
        name = title if name_by == "end" else prev_title
        segments.append((prev_t, t, name))
        prev_t, prev_title = t, title
    if include_tail and duration - prev_t > 0.05:
        name = "end" if name_by == "end" else prev_title
        segments.append((prev_t, duration, name))
    return segments


def safe_name(s: str) -> str:
    s = re.sub(r"[^\w\-. äöåÄÖÅ]", "_", s, flags=re.UNICODE).strip(" ._")
    s = re.sub(r"_+", "_", s)
    return s[:60] or "clip"


def fmt_time(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def fmt_short(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


class Progress:
    """Draws the progress of the clip and the whole run on one line that updates in place.
    If the output is not a terminal (e.g. redirected to a file), no bar is drawn."""

    def __init__(self, total: float):
        self.total = total      # combined duration of all clips (s)
        self.finished = 0.0     # combined duration of finished clips (s)
        self.started = time.monotonic()
        self.enabled = sys.stdout.isatty()
        utf = "utf" in (sys.stdout.encoding or "").lower()
        self.spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if utf else "|/-\\"
        self.full, self.empty = ("█", "░") if utf else ("#", "-")
        self.frame = 0
        self.width = 0

    def begin(self, length: float):
        self.length = max(length, 1e-6)
        self.clip_started = time.monotonic()

    def draw(self, pos: float):
        if not self.enabled:
            return
        now = time.monotonic()
        pos = min(pos, self.length)
        done = self.finished + pos
        text = f"{pos / self.length:4.0%}  {fmt_short(pos)}/{fmt_short(self.length)}"
        if pos > 0:
            speed = pos / (now - self.clip_started)
            eta = (self.total - done) * (now - self.started) / done
            text += f"  {speed:.1f}x  overall {done / self.total:.0%}, ~{fmt_short(eta)} left"
        bar_w = 20
        filled = round(bar_w * pos / self.length)
        self.frame += 1
        spin = self.spinner[self.frame % len(self.spinner)]
        line = f"  {spin} {self.full * filled}{self.empty * (bar_w - filled)} {text}"
        # The line must not wrap, otherwise \r only returns to the start of its last row
        self._write(line[:shutil.get_terminal_size().columns - 1])

    def end(self, ok: bool):
        self.finished += self.length
        line = f"  {'done' if ok else 'ERROR'} ({fmt_short(time.monotonic() - self.clip_started)})"
        if self.enabled:
            self._write(line)
            line = ""
        print(line, flush=True)
        self.width = 0

    def _write(self, line: str):
        # Pad with spaces so that the end of a previous, longer line gets erased
        sys.stdout.write("\r" + line.ljust(self.width))
        sys.stdout.flush()
        self.width = len(line)


def cut(src: Path, dst: Path, start: float, end: float, reencode: bool, progress: Progress):
    # Video and audio only: with stream copy, the chapter data track of an OBS Hybrid MP4
    # isn't cut at the right times; instead it stretches the clip's duration and moves the
    # start of the video to the middle of the timeline.
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
           "-progress", "pipe:1",
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
           "-map", "0:v", "-map", "0:a?", "-map_chapters", "-1"]
    if reencode:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "copy"]
    else:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd += ["-movflags", "+faststart", str(dst)]

    pos = [0.0]  # position reached in the clip in seconds, updated by the reader thread

    def read_progress(stream):
        for line in stream:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit():
                pos[0] = int(value) / 1_000_000

    progress.begin(end - start)
    # stderr goes to a file so that a full pipe can't stall ffmpeg while stdout is being read
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
        reader = threading.Thread(target=read_progress, args=(proc.stdout,), daemon=True)
        reader.start()
        try:
            while proc.poll() is None:
                progress.draw(pos[0])
                time.sleep(0.1)
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            dst.unlink(missing_ok=True)
            print()
            sys.exit("Interrupted.")
        reader.join()
        ok = proc.returncode == 0
        progress.end(ok)
        if not ok:
            err.seek(0)
            print(f"  ERROR: {dst.name}\n{err.read().decode(errors='replace')}", file=sys.stderr)
    return ok


def main():
    ap = argparse.ArgumentParser(
        description="Splits an OBS Hybrid MP4 recording into clips at its chapter markers.")
    ap.add_argument("input", type=Path, help="the .mp4 file recorded by OBS")
    ap.add_argument("-o", "--outdir", type=Path, default=None,
                    help="output folder (default: <name>_clips next to the input file)")
    ap.add_argument("--reencode", action="store_true",
                    help="re-encode the video for frame-accurate cuts (slower); by default "
                         "the video is stream-copied, so cuts snap to the previous keyframe")
    ap.add_argument("--no-tail", action="store_true",
                    help="don't make a clip of the part after the last marker")
    ap.add_argument("--name-by", choices=["end", "start"], default="end",
                    help="name each clip after the marker where it ends (end, the default) "
                         "or where it starts (start)")
    ap.add_argument("--dry-run", action="store_true",
                    help="only show what would be done")
    args = ap.parse_args()

    ensure_ffmpeg()

    src: Path = args.input
    if not src.is_file():
        sys.exit(f"File not found: {src}")

    marks, duration = probe(src)
    if not marks:
        sys.exit("No chapter markers found in the file (ffprobe -show_chapters returned none).")

    segments = build_segments(marks, duration, not args.no_tail, args.name_by)
    outdir = args.outdir or src.with_name(f"{src.stem}_clips")

    print(f"{src.name}: duration {fmt_time(duration)}, "
          f"{plural(len(marks), 'chapter marker')} -> {plural(len(segments), 'clip')}")
    print(f"Output folder: {outdir}\n")

    width = len(str(len(segments)))
    plan = []
    for i, (start, end, name) in enumerate(segments, 1):
        dst = outdir / f"{src.stem}_{i:0{width}d}_{safe_name(name)}{src.suffix}"
        plan.append((start, end, dst))
        print(f"  {i:>{width}}. {fmt_time(start)} - {fmt_time(end)}  {dst.name}")

    if args.dry_run:
        return

    outdir.mkdir(parents=True, exist_ok=True)
    ok = 0
    print()
    progress = Progress(sum(end - start for start, end, _ in plan))
    for i, (start, end, dst) in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {dst.name}", flush=True)
        if cut(src, dst, start, end, args.reencode, progress):
            ok += 1
    took = fmt_short(time.monotonic() - progress.started)
    print(f"\nDone: {ok}/{plural(len(plan), 'clip')} in {outdir} ({took})")
    if ok != len(plan):
        sys.exit(1)


if __name__ == "__main__":
    main()
