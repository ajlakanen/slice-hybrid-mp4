#!/usr/bin/env python3
"""
Pilkkoo OBS Studion Hybrid MP4 -tallenteen chapter-merkkien kohdalta clipeiksi.

Jokaisesta chapter-merkistä tehdään clippi, joka alkaa edellisestä merkistä
(tai videon alusta, jos edellistä merkkiä ei ole) ja päättyy kyseiseen merkkiin.
Viimeisen merkin jälkeinen häntä tallennetaan omaksi clipikseen, ellei annettu --no-tail.

Vaatii: ffmpeg ja ffprobe PATHissa. Jos ne puuttuvat, skripti tarjoutuu asentamaan
ne käyttöjärjestelmän paketinhallinnalla (winget/choco/scoop, brew/port, apt/dnf/pacman/...).

Käyttö:
    python slice-hybrid-mp4.py tallenne.mp4
    python slice-hybrid-mp4.py tallenne.mp4 -o clipit/ --reencode
    python slice-hybrid-mp4.py tallenne.mp4 --dry-run
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
    """Palauttaa ffmpegin asennuskomennot (lista komentoja) tälle käyttöjärjestelmälle,
    tai None, jos tuettua paketinhallintaa ei löydy."""
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
    else:  # Linux ja muut Unixit
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
        return ("Asenna winget (App Installer, Microsoft Store) ja aja: winget install Gyan.FFmpeg\n"
                "tai lataa https://www.gyan.dev/ffmpeg/builds/ ja lisää bin-kansio PATHiin.")
    if sys.platform == "darwin":
        return "Asenna Homebrew (https://brew.sh) ja aja: brew install ffmpeg"
    return f"Asenna ffmpeg-paketti jakelusi paketinhallinnalla tai katso {DOWNLOAD_URL}"


def refresh_windows_path():
    """Lukee PATHin rekisteristä, jotta juuri asennettu ffmpeg löytyy
    ilman uuden terminaalin avaamista."""
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
    """Varmistaa, että ffmpeg ja ffprobe löytyvät ja käynnistyvät."""
    missing = [t for t in TOOLS if shutil.which(t) is None]
    if missing:
        offer_install(missing)
    errors = [e for e in map(launch_error, TOOLS) if e]
    if errors:
        sys.exit("\n\n".join(errors))


def launch_error(tool: str):
    """Kokeilee käynnistää työkalun. Palauttaa virheilmoituksen, tai None jos toimii."""
    path = shutil.which(tool)
    try:
        subprocess.run([path, "-version"], capture_output=True, check=True)
        return None
    except (OSError, subprocess.CalledProcessError) as e:
        msg = f"{path} löytyy, mutta sitä ei voi käynnistää: {e}"
        if getattr(e, "winerror", None) == 1920:  # ERROR_CANT_ACCESS_FILE
            msg += ("\nTiedosto on todennäköisesti pilvikansion (Dropbox, OneDrive) vain verkossa "
                    "-tiedosto, jota ei saada ladattua. Käynnistä pilvisovellus ja merkitse "
                    "tiedosto pysymään tällä laitteella, tai asenna ffmpeg pilvikansion ulkopuolelle.")
        return msg


def offer_install(missing):
    """Tarjoaa puuttuvan ffmpegin asennusta. Lopettaa ohjelman, jos asennus ei onnistu."""
    print(f"{' ja '.join(missing)} ei löydy PATHista.", file=sys.stderr)

    cmds = install_commands()
    if cmds is None:
        sys.exit(manual_hint())
    shown = " && ".join(" ".join(c) for c in cmds)
    if not sys.stdin.isatty():
        sys.exit(f"Asenna ffmpeg komennolla:\n    {shown}")

    try:
        answer = input(f"Asennetaanko ffmpeg nyt komennolla\n    {shown}\n[k/E] ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() not in ("k", "kyllä", "kylla", "y", "yes"):
        sys.exit(f"ffmpeg tarvitaan. Asenna se komennolla\n    {shown}\nja aja skripti uudelleen.")

    for cmd in cmds:
        # shutil.which löytää myös .cmd/.ps1-shimit (esim. scoop), joita pelkkä nimi ei löydä
        exe = shutil.which(cmd[0]) or cmd[0]
        if subprocess.run([exe, *cmd[1:]]).returncode != 0:
            sys.exit(f"Asennus epäonnistui. Asenna ffmpeg käsin: {DOWNLOAD_URL}")

    if sys.platform == "win32":
        refresh_windows_path()
    if any(shutil.which(t) is None for t in TOOLS):
        sys.exit("Asennus valmis, mutta ffmpeg ei vielä näy PATHissa. "
                 "Avaa uusi terminaali ja aja skripti uudelleen.")
    print("ffmpeg asennettu.\n")


def probe(path: Path):
    """Palauttaa (chapter-merkit [(aika_s, otsikko)], kesto_s)."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_chapters", "-show_format", str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"ffprobe epäonnistui:\n{res.stderr}")
    data = json.loads(res.stdout)
    duration = float(data["format"]["duration"])

    marks = []
    for ch in data.get("chapters", []):
        t = float(ch["start_time"])
        title = (ch.get("tags") or {}).get("title", "").strip()
        marks.append((t, title))
    marks.sort(key=lambda m: m[0])

    # Poista merkki videon alusta ja mahdolliset duplikaatit
    cleaned = []
    for t, title in marks:
        if t < 0.05:
            continue
        if cleaned and abs(cleaned[-1][0] - t) < 0.05:
            continue
        cleaned.append((t, title))
    return cleaned, duration


def build_segments(marks, duration, include_tail: bool, name_by: str):
    """Muodostaa listan (alku, loppu, nimi)."""
    segments = []
    prev_t, prev_title = 0.0, "alku"
    for t, title in marks:
        name = title if name_by == "loppu" else prev_title
        segments.append((prev_t, t, name))
        prev_t, prev_title = t, title
    if include_tail and duration - prev_t > 0.05:
        name = "loppu" if name_by == "loppu" else prev_title
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


class Progress:
    """Piirtää clipin ja koko ajon edistymisen yhdelle, paikallaan päivittyvälle riville.
    Jos tuloste ei ole terminaali (esim. ohjattu tiedostoon), palkkia ei piirretä."""

    def __init__(self, total: float):
        self.total = total      # kaikkien clippien yhteiskesto (s)
        self.finished = 0.0     # valmiiden clippien yhteiskesto (s)
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
            text += f"  {speed:.1f}x  kaikki {done / self.total:.0%}, ~{fmt_short(eta)} jäljellä"
        bar_w = 20
        filled = round(bar_w * pos / self.length)
        self.frame += 1
        spin = self.spinner[self.frame % len(self.spinner)]
        line = f"  {spin} {self.full * filled}{self.empty * (bar_w - filled)} {text}"
        # Rivi ei saa rivittyä, muuten \r palaa vain viimeisen rivin alkuun
        self._write(line[:shutil.get_terminal_size().columns - 1])

    def end(self, ok: bool):
        self.finished += self.length
        line = f"  {'valmis' if ok else 'VIRHE'} ({fmt_short(time.monotonic() - self.clip_started)})"
        if self.enabled:
            self._write(line)
            line = ""
        print(line, flush=True)
        self.width = 0

    def _write(self, line: str):
        # Täytetään välilyönneillä, jotta edellisen, pidemmän rivin loppu pyyhkiytyy
        sys.stdout.write("\r" + line.ljust(self.width))
        sys.stdout.flush()
        self.width = len(line)


def cut(src: Path, dst: Path, start: float, end: float, reencode: bool, progress: Progress):
    # Vain video ja ääni: OBS:n Hybrid MP4:n chapter-dataraita ei leikkaudu ajassa oikein
    # stream copyssa, vaan venyttää clipin keston ja siirtää videon alun keskelle aikajanaa.
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

    pos = [0.0]  # käsitelty kohta clipissä sekunteina, päivittyy lukijasäikeestä

    def read_progress(stream):
        for line in stream:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit():
                pos[0] = int(value) / 1_000_000

    progress.begin(end - start)
    # stderr tiedostoon, jotta täysi putki ei jumita ffmpegiä sillä aikaa kun luetaan stdoutia
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
            sys.exit("Keskeytetty.")
        reader.join()
        ok = proc.returncode == 0
        progress.end(ok)
        if not ok:
            err.seek(0)
            print(f"  VIRHE: {dst.name}\n{err.read().decode(errors='replace')}", file=sys.stderr)
    return ok


def main():
    ap = argparse.ArgumentParser(
        description="Pilkkoo OBS Hybrid MP4 -tallenteen chapter-merkkien kohdalta clipeiksi.")
    ap.add_argument("input", type=Path, help="OBS:n tuottama .mp4")
    ap.add_argument("-o", "--outdir", type=Path, default=None,
                    help="Kohdekansio (oletus: <tiedostonimi>_clipit lähdetiedoston vieressä)")
    ap.add_argument("--reencode", action="store_true",
                    help="Uudelleenkoodaa video (framen tarkat leikkaukset, hitaampi). "
                         "Oletus on stream copy, jolloin leikkaus osuu lähimpään keyframeen.")
    ap.add_argument("--no-tail", action="store_true",
                    help="Älä tee clippiä viimeisen merkin jälkeisestä osuudesta")
    ap.add_argument("--name-by", choices=["loppu", "alku"], default="loppu",
                    help="Nimetäänkö clippi sen merkin mukaan, johon se päättyy (loppu, oletus) "
                         "vai josta se alkaa (alku)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Näytä vain, mitä tehtäisiin")
    args = ap.parse_args()

    ensure_ffmpeg()

    src: Path = args.input
    if not src.is_file():
        sys.exit(f"Tiedostoa ei löydy: {src}")

    marks, duration = probe(src)
    if not marks:
        sys.exit("Tiedostosta ei löytynyt chapter-merkkejä (ffprobe -show_chapters palautti tyhjän).")

    segments = build_segments(marks, duration, not args.no_tail, args.name_by)
    outdir = args.outdir or src.with_name(f"{src.stem}_clipit")

    print(f"{src.name}: kesto {fmt_time(duration)}, {len(marks)} chapter-merkkiä -> {len(segments)} clippiä")
    print(f"Kohde: {outdir}\n")

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
    print(f"\nValmis: {ok}/{len(plan)} clippiä kansiossa {outdir} ({took})")
    if ok != len(plan):
        sys.exit(1)


if __name__ == "__main__":
    main()
