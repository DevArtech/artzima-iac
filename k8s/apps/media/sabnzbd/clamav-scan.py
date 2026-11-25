#!/usr/bin/env python3
"""SABnzbd post-processing script: scan a finished job's folder with ClamAV (clamd).

Mounted read-only at SABnzbd's script_dir (/scripts, ConfigMap sabnzbd-scripts, see
k8s/apps/media/sabnzbd.yaml); the media-wiring Job sets it as the script of category
"*" (every category inherits it) and turns on script_can_fail, so a non-zero exit
marks the job Failed in SABnzbd's history and Sonarr/Radarr/Lidarr never import it
(they import only Completed jobs; Failed = blocklist + search again).

Fail closed: exit 1 when anything is found, when clamd cannot be reached (after
retrying a few minutes: clamd restarting or reloading signatures) and when a file
cannot be read. Exit 0 only when every file was scanned clean.

On a find, the job folder is MOVED (one rename on the same /data volume, never a
copy + delete, never overwriting) to the quarantine folder next to the complete folder
(/data/usenet/quarantine/<UTC time>_<job>) with a <same name>.txt report beside it.
Nothing is ever deleted. Release it by hand after checking it, or delete it.

The scan streams every file to clamd (INSTREAM, python stdlib only: the SABnzbd image
needs no ClamAV). Files with a video/audio extension whose first bytes ARE that
container format send only their first HEAD_MIB (a real MKV/MP4/FLAC cannot run;
streaming 30 GB of video over NFS would take minutes per job); every other file
(and media-named files without a media header, i.e. a disguised executable) is
sent up to MAX_MIB, which must stay below clamd's StreamMaxLength
(k8s/apps/media/clamav/clamd.conf).

SABnzbd's arguments: 1 = final job folder, 2 = NZB name, 3 = job name, 5 = category,
7 = post-processing status (0 = OK). Run by hand (same exit codes):
  kubectl -n media exec deploy/sabnzbd -- /scripts/clamav-scan.py /data/usenet/complete/<cat>/<job>
SABnzbd shows the LAST printed line in its history, so the summary is printed last.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import socket
import struct
import sys
import time

HOST = os.environ.get("CLAMD_HOST", "clamav.media.svc.cluster.local")
PORT = int(os.environ.get("CLAMD_PORT", "3310"))
COMPLETE = pathlib.Path(os.environ.get("CLAMSCAN_COMPLETE_DIR", "/data/usenet/complete"))
QUARANTINE = pathlib.Path(os.environ.get("CLAMSCAN_QUARANTINE_DIR", "/data/usenet/quarantine"))
# Category folders directly under COMPLETE: never moved as a whole (only a job inside them).
CATEGORY_DIRS = set(os.environ.get("CLAMSCAN_CATEGORY_DIRS", "tv,movies,music,audio,software").split(","))
HEAD_MIB = int(os.environ.get("CLAMSCAN_HEAD_MIB", "32"))
MAX_MIB = int(os.environ.get("CLAMSCAN_MAX_MIB", "512"))
RETRIES = int(os.environ.get("CLAMSCAN_RETRIES", "10"))  # x 30 s: clamd restart / signature reload
TIMEOUT = 600  # per socket operation (a reload pauses clamd for up to a minute)
CHUNK = 1 << 20

# Extension -> header check. Only these get the head-only scan, and only if the header matches.
def _MP4(b: bytes) -> bool:
    return b[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip")


def _MPEG_AUDIO(b: bytes) -> bool:  # ID3 tag or an MPEG/ADTS frame sync
    return b[:3] == b"ID3" or (len(b) > 1 and b[0] == 0xFF and b[1] & 0xE0 == 0xE0)


MEDIA = {
    "mkv": lambda b: b[:4] == b"\x1a\x45\xdf\xa3", "webm": lambda b: b[:4] == b"\x1a\x45\xdf\xa3",
    "mka": lambda b: b[:4] == b"\x1a\x45\xdf\xa3",
    "mp4": _MP4, "m4v": _MP4, "mov": _MP4, "m4a": _MP4, "m4b": _MP4,
    "avi": lambda b: b[:4] == b"RIFF", "wav": lambda b: b[:4] == b"RIFF",
    "ts": lambda b: b[:1] == b"\x47", "m2ts": lambda b: b[4:5] == b"\x47", "mts": lambda b: b[4:5] == b"\x47",
    "mpg": lambda b: b[:3] == b"\x00\x00\x01", "mpeg": lambda b: b[:3] == b"\x00\x00\x01",
    "wmv": lambda b: b[:4] == b"\x30\x26\xb2\x75", "wma": lambda b: b[:4] == b"\x30\x26\xb2\x75",
    "flac": lambda b: b[:4] == b"fLaC", "mp3": _MPEG_AUDIO, "aac": _MPEG_AUDIO,
    "ogg": lambda b: b[:4] == b"OggS", "opus": lambda b: b[:4] == b"OggS",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def clamd(command: bytes, payload=None) -> str:
    """One clamd session (z-terminated commands). payload: an open file, sent as INSTREAM."""
    with socket.create_connection((HOST, PORT), timeout=TIMEOUT) as s:
        s.sendall(b"z" + command + b"\0")
        if payload is not None:
            f, limit = payload
            left = limit
            while left > 0:
                data = f.read(min(CHUNK, left))
                if not data:
                    break
                s.sendall(struct.pack("!L", len(data)) + data)
                left -= len(data)
            s.sendall(struct.pack("!L", 0))
        reply = b""
        while not reply.endswith(b"\0"):
            data = s.recv(4096)
            if not data:
                break
            reply += data
    return reply.rstrip(b"\0").decode(errors="replace").strip()


def wait_for_clamd() -> str:
    for attempt in range(1, RETRIES + 1):
        try:
            if clamd(b"PING") == "PONG":
                return clamd(b"VERSION")
            err = "no PONG"
        except OSError as e:
            err = str(e)
        log(f"clamd {HOST}:{PORT} not answering ({err}); retry {attempt}/{RETRIES} in 30 s")
        time.sleep(30)
    raise ConnectionError(f"clamd {HOST}:{PORT} unreachable")


def scan_file(path: pathlib.Path) -> tuple[str, str]:
    """-> (verdict, detail): verdict OK, FOUND or ERROR."""
    with path.open("rb") as f:
        head = f.read(16)
        f.seek(0)
        check = MEDIA.get(path.suffix.lower().lstrip("."))
        if check and check(head):
            limit, how = HEAD_MIB << 20, f"first {HEAD_MIB} MiB (media header ok)"
        else:
            limit = MAX_MIB << 20
            how = "whole file" if path.stat().st_size <= limit else f"first {MAX_MIB} MiB"
            if check:
                how += f"; NO {path.suffix} header"
        reply = clamd(b"INSTREAM", (f, limit))
    # "stream: OK" | "stream: <signature> FOUND" | "<message> ERROR"
    body = reply.split(":", 1)[1].strip() if reply.startswith("stream:") else reply
    if body == "OK":
        return "OK", how
    if body.endswith(" FOUND"):
        return "FOUND", body[: -len(" FOUND")]
    return "ERROR", body


def quarantine(job: pathlib.Path, report: list[str]) -> pathlib.Path | None:
    """Move the job folder (one rename, same filesystem) into QUARANTINE; never overwrite."""
    try:
        rel = job.resolve().relative_to(COMPLETE.resolve())
    except ValueError:
        log(f"NOT moved: {job} is outside {COMPLETE}")
        return None
    if not rel.parts or (len(rel.parts) == 1 and rel.parts[0] in CATEGORY_DIRS):
        log(f"NOT moved: {job} is the complete folder or a category folder, not one job")
        return None
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = QUARANTINE / f"{stamp}_{job.name}"
    n = 1
    while dest.exists() or dest.with_name(dest.name + ".txt").exists():
        dest = QUARANTINE / f"{stamp}_{job.name}.{n}"
        n += 1
    os.rename(job, dest)  # same /data volume: atomic, nothing copied or deleted
    dest.with_name(dest.name + ".txt").write_text(
        f"ClamAV finds in SABnzbd job {job} (moved here {stamp}):\n" + "\n".join(report) + "\n")
    return dest


def main() -> int:
    if len(sys.argv) < 2:
        log("usage: clamav-scan.py <job folder> [SABnzbd's other arguments]")
        return 2
    job = pathlib.Path(sys.argv[1])
    pp_status = sys.argv[7] if len(sys.argv) > 7 else "0"
    if not job.exists():
        log(f"ClamAV: FAILED, job folder {job} does not exist (SABnzbd status {pp_status})")
        return 1
    try:
        version = wait_for_clamd()
    except ConnectionError as e:
        log(f"ClamAV: FAILED (fail closed), {e}; the job is not imported, retry it from History")
        return 1
    log(f"clamd: {version}")

    files = [job] if job.is_file() else sorted(
        pathlib.Path(root) / name for root, _, names in os.walk(job) for name in names)
    found, errors, scanned = [], [], 0
    for path in files:
        if path.is_symlink() or not path.is_file():
            log(f"skip {path} (not a regular file)")
            continue
        try:
            verdict, detail = scan_file(path)
        except OSError as e:
            verdict, detail = "ERROR", str(e)
        rel = path.relative_to(job) if path != job else path.name
        scanned += 1
        if verdict == "FOUND":
            found.append(f"{rel}: {detail}")
            log(f"FOUND {rel}: {detail}")
        elif verdict == "ERROR":
            errors.append(f"{rel}: {detail}")
            log(f"ERROR {rel}: {detail}")
        else:
            log(f"ok    {rel} ({detail})")

    if found:
        names = sorted({f.rsplit(": ", 1)[1] for f in found})
        try:
            dest = quarantine(job, found + [f"error {e}" for e in errors])
        except OSError as e:
            log(f"move to quarantine failed: {e}")
            dest = None
        where = f"moved to {dest}" if dest else "NOT moved (see above)"
        log(f"ClamAV: INFECTED ({', '.join(names)}) in {len(found)} of {scanned} files; {where}")
        return 1
    if errors:
        log(f"ClamAV: FAILED (fail closed), {len(errors)} of {scanned} files not scanned: {errors[0]}")
        return 1
    log(f"ClamAV: clean ({scanned} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
