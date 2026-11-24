#!/usr/bin/env bash
# Adopt the operator's EXISTING ripped movies/TV/music into the media data layout,
# by HARDLINKING, on the NAS itself (over SSH). The originals are NEVER moved,
# renamed, re-permissioned or deleted: every file simply gets a second name
#   <artnas_export>/<media_data_subdir>/media/{movies,tv,music}/<same relative path>
# pointing at the same inode (same bytes, no extra space). Sonarr/Radarr then
# only ever touch the second name; the original path stays as it is. (Lidarr is
# additionally configured never to write audio tags into files: docs/decisions.md.)
#
# Sources: media_existing_paths in ansible/group_vars/all/media.yml, e.g.
#   - { path: /volume1/Artzima/Movies, kind: movies }   children = movie folders
#   - { path: /volume1/Artzima/TV Shows, kind: tv }     children = show folders
#   - { path: /volume1/Artzima/Music, kind: music }     children = artist folders
# Steps and the arr import: README "Bringing in your existing rips".
#
# Safety:
#   - Hardlinks only work inside ONE filesystem. For each source it compares
#     st_dev of the source and of the data folder and REFUSES if they differ
#     (e.g. another UGOS shared folder that is its own btrfs subvolume or
#     volume), printing a verified-copy command to use instead.
#   - Refuses sources that overlap the data folder.
#   - Existing destination files are never overwritten: a file that already has
#     the same inode counts as done (re-runs are safe), a DIFFERENT file at the
#     same destination path is a conflict (reported, skipped, exit 1).
#   - Movies stored as LOOSE files in a source root (no title folder) get one: a
#     video "<name>.mkv" is linked as movies/<name>/<name>.mkv, and its sidecars
#     ("<name>.en.srt", "<name>.nfo") join it. Radarr imports one folder per movie.
#     The same mapping is used for linking and verifying, so re-runs stay idempotent.
#   - New destination directories get media_puid:media_pgid and mode 2775. File
#     ownership/modes are NOT changed (that would change the originals too: same
#     inode); files that are not world-readable are reported (Jellyfin reads as "other").
#   - Afterwards: counts and total sizes of the originals and the links, and a
#     check that EVERY original file has a destination with an equal inode.
#
# DRY-RUN BY DEFAULT (read-only: find/stat/du). --apply creates the links.
#
# Usage: scripts/media-adopt.sh [--apply] [--no-sudo]      (make media-adopt [MEDIA_ADOPT_ARGS=--apply])
#   --apply     create directories and hardlinks, then verify
#   --no-sudo   run as the SSH user instead of `sudo` on the NAS. The default uses
#               sudo (asks for the NAS user's password on a TTY), because the
#               kernel's protected_hardlinks only lets you link files you own or
#               can write, and the data folder belongs to media_puid.
# Env overrides (defaults in brackets):
#   SSH_KEY [~/.ssh/id_ed25519]  KNOWN_HOSTS [~/.ssh/known_hosts_lab]
#   NAS [devartech@<artnas ip from ansible/hosts.yml>]  PY [tools venv python, else python3]
set -euo pipefail

APPLY=0
SUDO=1
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --no-sudo) SUDO=0 ;;
    -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-${ARTZIMA_TOOLS:-$HOME/.local/share/artzima-tools}/venv/bin/python}"
[ -x "$PY" ] || PY=python3

# Everything from the inventory (scripts/labdata.py): no path or address is written here.
# Output: line 1 = "ip<TAB>data dir<TAB>uid<TAB>gid<TAB>NAS write root", then one "kind<TAB>path" per source.
CONF="$("$PY" - "$REPO" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1] + "/scripts")
import labdata
d = labdata.load()
m = d["media"]
print(f"{d['hosts']['artnas']['ip']}\t{labdata.nas_path(d, m['media_data_subdir'])}\t{m['media_puid']}\t{m['media_pgid']}\t{d['lab']['artnas_export']}")
for e in m.get("media_existing_paths") or []:
    kind, path = e.get("kind"), str(e.get("path", ""))
    if kind not in ("movies", "tv", "music") or not path.startswith("/") or "\t" in path or "\n" in path:
        sys.exit(f"media_existing_paths: bad entry {e!r} (need path: /absolute/path, kind: movies|tv|music)")
    print(f"{kind}\t{path.rstrip('/')}")
EOF
)"
IFS=$'\t' read -r NAS_IP DATA PUID PGID NAS_ROOT <<<"$(head -n1 <<<"$CONF")"
mapfile -t SOURCES < <(tail -n +2 <<<"$CONF")
if [ "${#SOURCES[@]}" -eq 0 ]; then
  echo "media_existing_paths is empty (ansible/group_vars/all/media.yml): inspect the NAS first," >&2
  echo "see README \"Bringing in your existing rips\"." >&2
  exit 2
fi

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
KNOWN_HOSTS="${KNOWN_HOSTS:-$HOME/.ssh/known_hosts_lab}"
NAS="${NAS:-devartech@$NAS_IP}"
SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o ConnectTimeout=5
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KNOWN_HOSTS $HOME/.ssh/known_hosts")

# ---------------------------------------------------------------- runs ON THE NAS
# Args: apply(0|1) write_root data_dir uid gid kind1 src1 [kind2 src2 ...]
# Writes ONLY below data_dir/media, which must resolve inside write_root (/volume1/Artzima).
# Sources are only read (a hardlink adds a name in data_dir; the source folder is unchanged).
adopt_remote() {
  set -uo pipefail
  local apply="$1" root="$2" data="$3" uid="$4" gid="$5"
  shift 5
  local rc=0 kind src dst dev_src dev_dst rel f d s_id d_id stem
  local -a stems=()
  # Source-relative path -> destination-relative path (see "LOOSE files" above).
  map_rel() {
    local r="$1" s best=""
    if [ "$kind" != movies ] || [ "${r#*/}" != "$r" ]; then printf '%s' "$r"; return; fi
    for s in ${stems[@]+"${stems[@]}"}; do
      case "$r" in "$s".*) [ "${#s}" -gt "${#best}" ] && best="$s" ;; esac
    done
    if [ -n "$best" ]; then printf '%s/%s' "$best" "$r"; else printf '%s' "$r"; fi
  }
  local n_files n_dirs n_linked n_done n_conflict n_verified n_missing n_other n_unreadable n_loose
  local bytes_src bytes_dst
  human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1 bytes"; }

  if [ ! -d "$data" ]; then
    echo "REFUSE: data folder $data does not exist: create it first (docs/manual-steps.md §3)"
    return 1
  fi
  case "$(readlink -f "$data")/" in
    "$(readlink -f "$root")"/?*) ;;
    *) echo "REFUSE: data folder $data resolves outside $root: this repo never writes there"; return 1 ;;
  esac
  while [ $# -gt 0 ]; do
    kind="$1" src="$2"
    shift 2
    dst="$data/media/$kind"
    echo
    echo "=== $kind: $src -> $dst"
    if [ ! -d "$src" ]; then
      echo "REFUSE: source $src is not a directory"; rc=1; continue
    fi
    src="$(readlink -f "$src")"
    case "$src/" in "$(readlink -f "$data")"/*)
      echo "REFUSE: source $src is inside the data folder"; rc=1; continue ;;
    esac
    case "$(readlink -f "$data")/" in "$src"/*)
      echo "REFUSE: the data folder is inside source $src"; rc=1; continue ;;
    esac
    # Same filesystem? Compare with the destination if it exists, else the data folder.
    dev_src="$(stat -c %d "$src")"
    if [ -d "$dst" ]; then dev_dst="$(stat -c %d "$dst")"; else dev_dst="$(stat -c %d "$data")"; fi
    if [ "$dev_src" != "$dev_dst" ]; then
      echo "REFUSE: $src (st_dev $dev_src) and $data (st_dev $dev_dst) are on different filesystems"
      echo "        (another volume, or a UGOS shared folder that is its own btrfs subvolume):"
      echo "        hardlinks are impossible. Use a VERIFIED COPY instead (needs the space twice):"
      echo "          sudo rsync -aH --info=progress2 '$src/' '$dst/'"
      echo "          sudo rsync -aHcn --itemize-changes '$src/' '$dst/'   # verify: must print nothing"
      echo "        or move the share onto the same volume. The originals stay either way."
      rc=1; continue
    fi
    echo "same filesystem (st_dev $dev_src): hardlinks possible"

    n_dirs="$(find "$src" -mindepth 1 -type d | wc -l)"
    n_files="$(find "$src" -type f | wc -l)"
    n_other="$(find "$src" -mindepth 1 ! -type f ! -type d | wc -l)"
    n_loose="$(find "$src" -mindepth 1 -maxdepth 1 -type f | wc -l)"
    n_unreadable="$(find "$src" -type f ! -perm -o=r | wc -l)"
    bytes_src="$(find "$src" -type f -printf '%s\n' | awk '{s+=$1} END {printf "%.0f", s}')"
    echo "originals: $n_files files in $n_dirs folders, $(human "$bytes_src")"
    [ "$n_other" -gt 0 ] && echo "WARN: $n_other symlinks/special files: NOT linked (not media files)"
    stems=()
    if [ "$kind" = movies ]; then
      while IFS= read -r -d '' f; do
        case "${f,,}" in *.mkv | *.mp4 | *.m4v | *.avi | *.ts | *.m2ts | *.iso) stems+=("${f%.*}") ;; esac
      done < <(cd "$src" && find . -mindepth 1 -maxdepth 1 -type f -printf '%P\0')
      [ "${#stems[@]}" -gt 0 ] && echo "loose movie files: ${#stems[@]}, each gets its own folder: $(printf '%s/; ' "${stems[@]}")"
      n_loose=0
      while IFS= read -r -d '' f; do
        [ "$(map_rel "$f")" = "$f" ] && n_loose=$((n_loose + 1)) && echo "WARN: loose file without a matching video, linked flat: $f"
      done < <(cd "$src" && find . -mindepth 1 -maxdepth 1 -type f -printf '%P\0')
    fi
    [ "$n_loose" -gt 0 ] && echo "WARN: $n_loose files directly in $src: linked, but the arrs need one folder per title (see README)"
    [ "$n_unreadable" -gt 0 ] && echo "WARN: $n_unreadable files are not world-readable: Jellyfin (reads as 'other') can't see them (README)"

    # 1. directories (new ones owned by the media user, setgid)
    if [ "$apply" = 1 ]; then
      mkdir -p "$dst" && chown "$uid:$gid" "$dst" 2>/dev/null
      while IFS= read -r -d '' rel; do
        d="$dst/$rel"
        if [ ! -d "$d" ]; then
          mkdir "$d" && chown "$uid:$gid" "$d" && chmod 2775 "$d" || { echo "ERROR: mkdir $d"; rc=1; }
        fi
      done < <(cd "$src" && find . -mindepth 1 -type d -printf '%P\0')
      for stem in ${stems[@]+"${stems[@]}"}; do
        d="$dst/$stem"
        if [ ! -d "$d" ]; then
          mkdir "$d" && chown "$uid:$gid" "$d" && chmod 2775 "$d" || { echo "ERROR: mkdir $d"; rc=1; }
        fi
      done
    fi

    # 2. files: link, or count as done (same inode) / conflict (different file)
    n_linked=0 n_done=0 n_conflict=0
    while IFS= read -r -d '' rel; do
      f="$src/$rel" d="$dst/$(map_rel "$rel")"
      if [ -e "$d" ]; then
        if [ "$(stat -c %d:%i "$f")" = "$(stat -c %d:%i "$d")" ]; then
          n_done=$((n_done + 1))
        else
          n_conflict=$((n_conflict + 1)); echo "CONFLICT (different file already there, skipped): $d"
        fi
      elif [ "$apply" = 1 ]; then
        if ln -- "$f" "$d"; then n_linked=$((n_linked + 1)); else echo "ERROR: ln $f"; rc=1; fi
      else
        n_linked=$((n_linked + 1))
      fi
    done < <(cd "$src" && find . -type f -printf '%P\0')
    if [ "$apply" = 1 ]; then
      echo "linked now: $n_linked, already linked: $n_done, conflicts: $n_conflict"
    else
      echo "DRY-RUN: would link $n_linked, already linked: $n_done, conflicts: $n_conflict"
    fi
    [ "$n_conflict" -gt 0 ] && rc=1

    # 3. verify: every original has a destination with the SAME inode
    if [ "$apply" = 1 ]; then
      n_verified=0 n_missing=0 bytes_dst=0
      while IFS= read -r -d '' rel; do
        s_id="$(stat -c %d:%i "$src/$rel")"
        d="$dst/$(map_rel "$rel")"
        d_id="$(stat -c %d:%i "$d" 2>/dev/null || true)"
        if [ "$s_id" = "$d_id" ]; then
          n_verified=$((n_verified + 1))
          bytes_dst=$((bytes_dst + $(stat -c %s "$d")))
        else
          n_missing=$((n_missing + 1)); echo "NOT LINKED: $src/$rel"
        fi
      done < <(cd "$src" && find . -type f -printf '%P\0')
      echo "links:     $n_verified files, $(human "$bytes_dst") (same inodes as the originals)"
      echo "disk used by originals + links together: $(du -scb "$src" "$dst" 2>/dev/null | tail -n1 | cut -f1 | { read -r b; human "$b"; }) (hardlinks are counted once)"
      if [ "$n_verified" -eq "$n_files" ] && [ "$n_missing" -eq 0 ]; then
        echo "VERIFIED: all $n_files originals have a link with an equal inode"
      else
        echo "FAILED: $n_missing of $n_files originals have no equal-inode link"; rc=1
      fi
    fi
  done
  echo
  [ "$apply" = 1 ] || echo "dry-run only: nothing was changed. Re-run with --apply (MEDIA_ADOPT_ARGS=--apply)."
  echo "Originals were not modified. Keep them until you decide otherwise (README)."
  return "$rc"
}

# ---------------------------------------------------------------- ship it and run it
ARGS=("$APPLY" "$NAS_ROOT" "$DATA" "$PUID" "$PGID")
for line in "${SOURCES[@]}"; do
  ARGS+=("${line%%$'\t'*}" "${line#*$'\t'}")
done
echo "NAS $NAS: $([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN), data folder $DATA, ${#SOURCES[@]} source(s)"

# The function travels inline (bash -c): no helper file is written on the NAS.
SCRIPT="$(declare -f adopt_remote)"$'\n''adopt_remote "$@"'
RUN=(bash -c "$SCRIPT" media-adopt "${ARGS[@]}")
[ "$SUDO" = 1 ] && RUN=(sudo "${RUN[@]}")
# -t: sudo may ask for the password on a terminal.
ssh -t "${SSH_OPTS[@]}" "$NAS" "$(printf '%q ' "${RUN[@]}")"
