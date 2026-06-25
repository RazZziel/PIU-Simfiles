#!/usr/bin/env python3
"""Download song-group media archives from the project's Google Drive.

This repository only ships the simfiles (.ssc). All media (mp3/mpg/png/...) is
distributed as one .zip per song group on a public Google Drive folder (see
README.md). This script lets you download any of those groups and merges the
media into the matching repo folder without overwriting the updated .ssc files.

Usage examples:
    # Interactive menu (no arguments):
    python3 Scripts/download_media.py

    # Just list the available groups:
    python3 Scripts/download_media.py --list

    # Download specific groups (by number or by name fragment):
    python3 Scripts/download_media.py 01 05
    python3 Scripts/download_media.py "NX~NX2" PHOENIX

    # Download everything:
    python3 Scripts/download_media.py --all

    # Only download the zips, do not extract/merge:
    python3 Scripts/download_media.py 01 --download-only --keep-zip

By default each selected group is: downloaded -> extracted -> merged into the
matching repo folder (rsync --ignore-existing, so existing .ssc files are kept)
-> the downloaded zip and temp files are removed.
"""

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

# Public Google Drive folder that hosts the per-group media archives.
# (Folder URL is documented in README.md.)
DRIVE_FOLDER_ID = "1RzbXIH3LCT-p3XAJv-OMUalL1s8mQNPm"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
VENV_DIR = os.path.join(SCRIPT_DIR, ".venv")

USER_AGENT = "Mozilla/5.0 (PIU-Simfiles download_media.py)"


# --------------------------------------------------------------------------- #
# gdown bootstrap (auto-venv)
# --------------------------------------------------------------------------- #
def ensure_gdown():
    """Make sure `gdown` is importable, bootstrapping a local venv if needed.

    If gdown is already available in the current interpreter, returns immediately.
    Otherwise it creates Scripts/.venv (once), installs gdown into it, and
    re-executes this script using the venv's Python.
    """
    try:
        import gdown  # noqa: F401
        return
    except ImportError:
        pass

    # Avoid an infinite re-exec loop.
    if os.environ.get("_PIU_DL_BOOTSTRAPPED") == "1":
        sys.exit("error: gdown is still unavailable after bootstrapping the venv.")

    venv_python = os.path.join(VENV_DIR, "bin", "python")
    if not os.path.exists(venv_python):
        print("gdown not found; creating a local virtualenv at Scripts/.venv ...")
        import venv

        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        print("Installing gdown (one-time) ...")
        subprocess.check_call(
            [venv_python, "-m", "pip", "install", "--quiet", "--upgrade", "pip", "gdown"]
        )

    # Re-exec this script with the venv interpreter.
    env = dict(os.environ, _PIU_DL_BOOTSTRAPPED="1")
    os.execve(venv_python, [venv_python, os.path.abspath(__file__)] + sys.argv[1:], env)


# --------------------------------------------------------------------------- #
# Listing the Drive folder (standard library only)
# --------------------------------------------------------------------------- #
def fetch_groups():
    """Return an ordered list of (index, name, file_id) for every group zip.

    `name` is the group name without the .zip extension (matches the repo folder).
    Scrapes Google Drive's embeddedfolderview, which needs no authentication for a
    public folder.
    """
    url = f"https://drive.google.com/embeddedfolderview?id={DRIVE_FOLDER_ID}#list"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")

    entries = re.findall(
        r'<div class="flip-entry"[^>]*id="entry-([^"]+)"[^>]*>.*?'
        r'flip-entry-title">([^<]+)<',
        html,
        re.S,
    )
    if not entries:
        sys.exit(
            "error: could not parse any files from the Drive folder.\n"
            "       The folder layout may have changed, or access was blocked."
        )

    groups = []
    for idx, (file_id, raw_name) in enumerate(entries, start=1):
        name = raw_name.strip()
        if name.lower().endswith(".zip"):
            name = name[: -len(".zip")]
        groups.append((idx, name, file_id))
    return groups


def print_menu(groups):
    print(f"\nAvailable song groups ({len(groups)}) on Google Drive:\n")
    width = max(len(name) for _, name, _ in groups)
    for idx, name, _ in groups:
        print(f"  [{idx:>2}] {name:<{width}}")
    print()


# --------------------------------------------------------------------------- #
# Selection resolution
# --------------------------------------------------------------------------- #
def _leading_number(name):
    m = re.match(r"\s*(\d+)", name)
    return m.group(1) if m else None


def match_token(token, groups):
    """Resolve one user token to a single group tuple, or raise ValueError."""
    token = token.strip()
    if not token:
        raise ValueError("empty selection")

    # By menu index / group number (e.g. "5", "05").
    if token.isdigit():
        n = int(token)
        # First: exact menu index.
        for g in groups:
            if g[0] == n:
                return g
        # Then: leading group number ("05 - NX~NX2" -> 5).
        matches = [g for g in groups if _leading_number(g[1]) and int(_leading_number(g[1])) == n]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"'{token}' is ambiguous")
        raise ValueError(f"no group matches number '{token}'")

    # By name (case-insensitive substring).
    low = token.lower()
    exact = [g for g in groups if g[1].lower() == low]
    if len(exact) == 1:
        return exact[0]
    subs = [g for g in groups if low in g[1].lower()]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        names = ", ".join(g[1] for g in subs)
        raise ValueError(f"'{token}' is ambiguous (matches: {names})")
    raise ValueError(f"no group matches '{token}'")


def resolve_tokens(tokens, groups):
    """Resolve a list of tokens to a de-duplicated, order-preserving list of groups.

    Each CLI argument is first tried verbatim (so a quoted full name like
    "15 - MOBILE EDITION" is matched as one token). Only if that fails is the
    argument split on commas/whitespace and each piece matched individually
    (so "01,05" or "01 05" inside a single argument still work).
    """
    selected = []
    seen = set()
    errors = []

    def add(group):
        if group[2] not in seen:
            seen.add(group[2])
            selected.append(group)

    def handle_piece(piece):
        if not piece:
            return
        if piece.lower() == "all":
            for g in groups:
                add(g)
            return
        try:
            add(match_token(piece, groups))
        except ValueError as e:
            errors.append(str(e))

    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok.lower() == "all":
            for g in groups:
                add(g)
            continue
        # Try the whole argument first (handles quoted full names).
        try:
            add(match_token(tok, groups))
            continue
        except ValueError:
            pass
        # Fall back to splitting on commas/whitespace.
        for piece in re.split(r"[,\s]+", tok):
            handle_piece(piece)

    if errors:
        for e in errors:
            print(f"  ! {e}", file=sys.stderr)
    return selected


def interactive_select(groups):
    print_menu(groups)
    print("Enter group numbers and/or names (space/comma separated), or 'all'.")
    try:
        raw = input("Selection: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return []
    if not raw:
        return []
    return resolve_tokens([raw], groups)


# --------------------------------------------------------------------------- #
# Filesystem helpers
# --------------------------------------------------------------------------- #
def _force_rmtree(path):
    """rmtree that also removes read-only files (zip entries can be read-only)."""

    def on_error(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=on_error)


def count_media(folder):
    exts = (".mp3", ".mpg", ".mp4", ".png", ".ogg", ".jpg", ".jpeg", ".wav")
    n = 0
    for _root, _dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(exts):
                n += 1
    return n


# --------------------------------------------------------------------------- #
# Per-group processing
# --------------------------------------------------------------------------- #
def process_group(group, dest_root, *, download_only, keep_zip):
    import gdown

    _idx, name, file_id = group
    print(f"\n=== {name} ===")

    tmp = tempfile.mkdtemp(prefix="piu-dl-")
    zip_path = os.path.join(tmp, f"{name}.zip")
    try:
        print("Downloading ...")
        out = gdown.download(id=file_id, output=zip_path, quiet=False)
        if not out or not os.path.exists(zip_path):
            print(f"  ! download failed for '{name}'", file=sys.stderr)
            return False

        if not zipfile.is_zipfile(zip_path):
            print(f"  ! downloaded file is not a valid zip for '{name}'", file=sys.stderr)
            return False

        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        print(f"  downloaded {size_mb:.0f} MB")

        if keep_zip or download_only:
            kept = os.path.join(dest_root, f"{name}.zip")
            shutil.move(zip_path, kept)
            print(f"  saved zip -> {kept}")
            if download_only:
                return True
            zip_path = kept  # extract from the kept copy below

        print("Extracting ...")
        extract_dir = os.path.join(tmp, "extract")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        # The zip contains a single top-level folder matching the group name.
        src = os.path.join(extract_dir, name)
        if not os.path.isdir(src):
            # Fall back to the only top-level entry if naming differs.
            tops = [d for d in os.listdir(extract_dir)
                    if os.path.isdir(os.path.join(extract_dir, d))]
            if len(tops) == 1:
                src = os.path.join(extract_dir, tops[0])
            else:
                print(f"  ! unexpected zip layout for '{name}': {tops}", file=sys.stderr)
                return False

        dest = os.path.join(dest_root, name)
        os.makedirs(dest, exist_ok=True)
        print(f"Merging into: {dest}")
        # --ignore-existing keeps the repo's updated .ssc files untouched.
        subprocess.check_call(
            ["rsync", "-a", "--ignore-existing", src + "/", dest + "/"]
        )
        print(f"  media files now present: {count_media(dest)}")
        return True
    finally:
        _force_rmtree(tmp)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args():
    p = argparse.ArgumentParser(
        description="Download song-group media archives from Google Drive and "
                    "merge them into the repo folders.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("groups", nargs="*",
                   help="Group numbers and/or name fragments (e.g. 01 05 PHOENIX). "
                        "Omit for an interactive menu.")
    p.add_argument("--all", action="store_true", help="Select every group.")
    p.add_argument("--list", action="store_true",
                   help="List the available groups and exit.")
    p.add_argument("--dest", default=REPO_ROOT,
                   help="Destination root (default: repo root).")
    p.add_argument("--download-only", action="store_true",
                   help="Only download the zip(s); do not extract/merge "
                        "(implies --keep-zip).")
    p.add_argument("--keep-zip", action="store_true",
                   help="Keep the downloaded zip after merging.")
    p.add_argument("-y", "--yes", action="store_true",
                   help="Do not prompt for confirmation before downloading.")
    return p.parse_args()


def main():
    args = parse_args()

    # Bootstrap gdown before any interactive work. If a venv re-exec is needed it
    # happens here (preserving sys.argv), so prompts are never shown twice. This
    # is a no-op once gdown is importable, and is skipped for --list.
    if not args.list:
        ensure_gdown()

    print("Fetching group list from Google Drive ...")
    groups = fetch_groups()

    if args.list:
        print_menu(groups)
        return 0

    if args.all:
        selected = list(groups)
    elif args.groups:
        selected = resolve_tokens(args.groups, groups)
    else:
        selected = interactive_select(groups)

    if not selected:
        print("Nothing selected; exiting.")
        return 1

    dest_root = os.path.abspath(args.dest)
    print("\nSelected groups:")
    for _idx, name, _fid in selected:
        print(f"  - {name}")
    print(f"Destination: {dest_root}")

    if not args.yes:
        try:
            ans = input(
                "\nThese archives can be several GB each. Proceed? [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 1

    ok = 0
    for group in selected:
        if process_group(
            group, dest_root,
            download_only=args.download_only,
            keep_zip=args.keep_zip,
        ):
            ok += 1

    print(f"\nDone: {ok}/{len(selected)} group(s) processed successfully.")
    return 0 if ok == len(selected) else 2


if __name__ == "__main__":
    sys.exit(main())
