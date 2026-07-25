#!/usr/bin/env python3
"""Download song-group media archives for this repository.

This repository only ships the simfiles (.ssc). All media (mp3/mpg/png/...) is
distributed as one .zip per song group on public Google Drive folders (see
README.md). This script downloads any of those groups and merges the media into
the matching repo folder without overwriting the repo's updated .ssc files.

Google Drive applies a per-file download quota to popular public files ("Too
many users have viewed or downloaded this file recently"). To cope with that the
script knows about more than one source, checks availability before starting a
multi-GB transfer, and can import an archive you downloaded by hand.

Usage examples:
    # Interactive menu (no arguments):
    python3 Scripts/download_media.py

    # List the groups and which sources carry them:
    python3 Scripts/download_media.py --list
    python3 Scripts/download_media.py --list --check   # also probe live quota

    # Download specific groups (by number or by name fragment):
    python3 Scripts/download_media.py 01 05
    python3 Scripts/download_media.py "NX~NX2" PHOENIX

    # Download everything:
    python3 Scripts/download_media.py --all

    # Force a specific source:
    python3 Scripts/download_media.py 02 --source mirror

    # Only download the zips, do not extract/merge:
    python3 Scripts/download_media.py 01 --download-only

    # Import an archive you already downloaded (e.g. via a browser):
    python3 Scripts/download_media.py --zip "02 - S.E.~EXTRA.zip"

By default each selected group is: downloaded -> extracted -> merged into the
matching repo folder (rsync --ignore-existing, so existing .ssc files are kept)
-> the zip and temp files are removed.
"""

import argparse
import http.cookiejar
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
# Ordered list of (label, drive_folder_id). The first entry is the canonical
# folder linked from README.md; later entries are fallbacks used only when an
# earlier source is unavailable (typically because of Google's per-file quota).
#
# Notes on the folders that have appeared in README.md over time:
#   * 1RzbXIH3LCT-p3XAJv-OMUalL1s8mQNPm - current link, all 16 groups.
#   * 1S1kLTRQoj3WptFkbg24__KqHJ1m13yhc - older link, 11 of the 16 groups. Kept
#     as a fallback only: being the older upload its contents may predate the
#     "re-added previously deleted channels / packs" update.
#   * 1pO9rbaPUwTTDFuEo_4tX8S1BEwmfukeF - NOT a mirror. Its "Songs" subfolder is
#     literally the primary folder above, so it offers no quota relief.
SOURCES = [
    ("primary", "1RzbXIH3LCT-p3XAJv-OMUalL1s8mQNPm"),
    ("mirror", "1S1kLTRQoj3WptFkbg24__KqHJ1m13yhc"),
]
SOURCE_LABELS = [label for label, _ in SOURCES]

# Text Google serves instead of the file when a public file is rate-limited.
QUOTA_MARKER = "Too many users have viewed or downloaded this file"

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
# Listing the Drive folders (standard library only)
# --------------------------------------------------------------------------- #
def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", USER_AGENT)]
    return op


def fetch_folder_entries(folder_id, timeout=60):
    """Return [(file_id, name), ...] for a public Drive folder.

    Uses Drive's embeddedfolderview, which needs no authentication.
    """
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    html = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    return re.findall(
        r'<div class="flip-entry"[^>]*id="entry-([^"]+)"[^>]*>.*?'
        r'flip-entry-title">([^<]+)<',
        html,
        re.S,
    )


def _clean_name(raw):
    name = raw.strip()
    if name.lower().endswith(".zip"):
        name = name[: -len(".zip")]
    return name


def build_catalog():
    """Return an ordered list of group dicts: {index, name, ids: {source: id}}.

    Ordering and naming come from the primary source; extra groups found only in
    a fallback source are appended. A source that cannot be listed is reported
    but does not abort the run.
    """
    catalog = []
    by_name = {}

    for label, folder_id in SOURCES:
        try:
            entries = fetch_folder_entries(folder_id)
        except Exception as e:  # network/parse problems must not be fatal
            print(f"  ! could not list {label} source: {_short(e)}", file=sys.stderr)
            continue
        if not entries:
            print(f"  ! {label} source returned no files", file=sys.stderr)
            continue
        for file_id, raw_name in entries:
            name = _clean_name(raw_name)
            group = by_name.get(name)
            if group is None:
                group = {"index": len(catalog) + 1, "name": name, "ids": {}}
                by_name[name] = group
                catalog.append(group)
            group["ids"].setdefault(label, file_id)

    if not catalog:
        sys.exit(
            "error: could not retrieve the group list from any source.\n"
            "       Check your connection, or the Drive folders may have changed."
        )
    return catalog


def print_menu(catalog, show_sources=True):
    print(f"\nAvailable song groups ({len(catalog)}):\n")
    width = max(len(g["name"]) for g in catalog)
    for g in catalog:
        extra = ""
        if show_sources:
            srcs = ",".join(s for s in SOURCE_LABELS if s in g["ids"])
            extra = f"  [{srcs}]"
        print(f"  [{g['index']:>2}] {g['name']:<{width}}{extra}")
    print()


# --------------------------------------------------------------------------- #
# Availability probing (detects Google's per-file quota before downloading)
# --------------------------------------------------------------------------- #
def probe_source(file_id, timeout=30):
    """Return (available: bool, detail: str) without downloading the payload.

    Mirrors what gdown does: request the download page, follow the confirmation
    form, then inspect the response headers. The body is never consumed for a
    real file, so this costs two small requests.
    """
    op = _opener()
    try:
        r = op.open(
            f"https://drive.google.com/uc?export=download&id={file_id}", timeout=timeout
        )
        ctype = r.headers.get("Content-Type", "")
        if "text/html" not in ctype:
            size = r.headers.get("Content-Length")
            r.close()
            return True, _fmt_size(size)
        html = r.read().decode("utf-8", "replace")
        r.close()
    except Exception as e:
        return False, _short(e)

    if QUOTA_MARKER in html:
        return False, "quota exceeded"

    action = re.search(r'action="([^"]+)"', html)
    if not action:
        return False, "no download form in response"
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', html))
    url = action.group(1) + "?" + urllib.parse.urlencode(fields)

    try:
        r2 = op.open(url, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read(4000).decode("utf-8", "replace")
        return False, "quota exceeded" if QUOTA_MARKER in body else f"HTTP {e.code}"
    except Exception as e:
        return False, _short(e)

    ctype = r2.headers.get("Content-Type", "")
    if "text/html" in ctype:
        body = r2.read(4000).decode("utf-8", "replace")
        r2.close()
        return False, "quota exceeded" if QUOTA_MARKER in body else "unexpected HTML response"
    size = r2.headers.get("Content-Length")
    r2.close()
    return True, _fmt_size(size)


def _fmt_size(size):
    try:
        n = int(size)
    except (TypeError, ValueError):
        return "available"
    return f"{n / (1024 * 1024):.0f} MB"


def _short(exc, limit=160):
    msg = " ".join(str(exc).split())
    return msg[:limit] + ("..." if len(msg) > limit else "")


# --------------------------------------------------------------------------- #
# Selection resolution
# --------------------------------------------------------------------------- #
def _leading_number(name):
    m = re.match(r"\s*(\d+)", name)
    return m.group(1) if m else None


def match_token(token, catalog):
    """Resolve one user token to a single group dict, or raise ValueError."""
    token = token.strip()
    if not token:
        raise ValueError("empty selection")

    # By menu index / group number (e.g. "5", "05").
    if token.isdigit():
        n = int(token)
        for g in catalog:
            if g["index"] == n:
                return g
        matches = [
            g for g in catalog
            if _leading_number(g["name"]) and int(_leading_number(g["name"])) == n
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"'{token}' is ambiguous")
        raise ValueError(f"no group matches number '{token}'")

    # By name (case-insensitive exact, then unique substring).
    low = token.lower()
    exact = [g for g in catalog if g["name"].lower() == low]
    if len(exact) == 1:
        return exact[0]
    subs = [g for g in catalog if low in g["name"].lower()]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        names = ", ".join(g["name"] for g in subs)
        raise ValueError(f"'{token}' is ambiguous (matches: {names})")
    raise ValueError(f"no group matches '{token}'")


def resolve_tokens(tokens, catalog):
    """Resolve tokens to a de-duplicated, order-preserving list of groups.

    Each argument is first tried verbatim (so a quoted full name like
    "15 - MOBILE EDITION" matches as one token). Only if that fails is the
    argument split on commas/whitespace and each piece matched individually
    (so "01,05" or "01 05" inside a single argument still work).
    """
    selected = []
    seen = set()
    errors = []

    def add(group):
        if group["name"] not in seen:
            seen.add(group["name"])
            selected.append(group)

    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok.lower() == "all":
            for g in catalog:
                add(g)
            continue
        try:
            add(match_token(tok, catalog))
            continue
        except ValueError:
            pass
        for piece in re.split(r"[,\s]+", tok):
            if not piece:
                continue
            if piece.lower() == "all":
                for g in catalog:
                    add(g)
                continue
            try:
                add(match_token(piece, catalog))
            except ValueError as e:
                errors.append(str(e))

    for e in errors:
        print(f"  ! {e}", file=sys.stderr)
    return selected


def interactive_select(catalog):
    print_menu(catalog)
    print("Enter group numbers and/or names (space/comma separated), or 'all'.")
    try:
        raw = input("Selection: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return []
    if not raw:
        return []
    return resolve_tokens([raw], catalog)


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


def zip_top_level_dir(zip_path):
    """Return the single top-level directory inside the zip, or None."""
    with zipfile.ZipFile(zip_path) as zf:
        tops = {n.split("/")[0] for n in zf.namelist() if "/" in n or n.endswith("/")}
        tops = {t for t in tops if t}
    return tops.pop() if len(tops) == 1 else None


# --------------------------------------------------------------------------- #
# Extract + merge
# --------------------------------------------------------------------------- #
def merge_zip(zip_path, dest_root, name):
    """Extract `zip_path` and merge its media into <dest_root>/<name>.

    Uses rsync --ignore-existing so the repo's updated .ssc files are preserved.
    Returns True on success.
    """
    tmp = tempfile.mkdtemp(prefix="piu-merge-")
    try:
        print("Extracting ...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)

        src = os.path.join(tmp, name)
        if not os.path.isdir(src):
            tops = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
            if len(tops) == 1:
                src = os.path.join(tmp, tops[0])
            else:
                print(f"  ! unexpected zip layout for '{name}': {tops}", file=sys.stderr)
                return False

        dest = os.path.join(dest_root, name)
        os.makedirs(dest, exist_ok=True)
        print(f"Merging into: {dest}")
        # --ignore-existing keeps the repo's updated .ssc files untouched.
        subprocess.check_call(["rsync", "-a", "--ignore-existing", src + "/", dest + "/"])
        print(f"  media files now present: {count_media(dest)}")
        return True
    finally:
        _force_rmtree(tmp)


# --------------------------------------------------------------------------- #
# Download flow
# --------------------------------------------------------------------------- #
def source_order(group, preference):
    """Return [(label, file_id), ...] to try, honoring --source."""
    ids = group["ids"]
    if preference == "auto":
        labels = [s for s in SOURCE_LABELS if s in ids]
    else:
        labels = [preference] if preference in ids else []
    return [(label, ids[label]) for label in labels]


def print_quota_help(group):
    ids = group["ids"]
    link_id = ids.get("primary") or next(iter(ids.values()), None)
    print(
        f"\n  All known sources for '{group['name']}' are currently rate-limited by\n"
        f"  Google Drive. This is a per-file quota on the shared file, not an error\n"
        f"  in this script; it usually clears within a few hours (up to 24h).\n"
        f"\n  What you can do:\n"
        f"    1. Wait a while and run this script again.\n"
    )
    if link_id:
        print(
            f"    2. Open the file in a browser while signed in to a Google account:\n"
            f"         https://drive.google.com/file/d/{link_id}/view\n"
            f"    3. On that page use \"Make a copy\" to copy it into your own Drive,\n"
            f"       then download your copy (a file you own has its own quota).\n"
        )
    print(
        f"    4. Once the .zip is on disk, import it without re-downloading:\n"
        f"         python3 Scripts/download_media.py --zip \"{group['name']}.zip\"\n"
    )


def process_group(group, dest_root, *, download_only, keep_zip, source_pref, probe=True):
    """Download one group (trying each allowed source) and merge it.

    Returns True on success. Never raises for expected network/quota problems.
    """
    import gdown

    name = group["name"]
    print(f"\n=== {name} ===")

    order = source_order(group, source_pref)
    if not order:
        have = ", ".join(sorted(group["ids"])) or "none"
        print(f"  ! not available from source '{source_pref}' (available: {have})",
              file=sys.stderr)
        return False

    quota_hit = False
    for label, file_id in order:
        if probe:
            ok, detail = probe_source(file_id)
            if not ok:
                print(f"  {label}: unavailable ({detail})")
                if "quota" in detail:
                    quota_hit = True
                continue
            print(f"  {label}: available ({detail})")

        tmp = tempfile.mkdtemp(prefix="piu-dl-")
        zip_path = os.path.join(tmp, f"{name}.zip")
        keep_tmp = False
        try:
            print(f"  downloading from {label} ...")
            try:
                out = gdown.download(id=file_id, output=zip_path, quiet=False)
            except Exception as e:  # gdown raises FileURLRetrievalError, etc.
                msg = _short(e, 300)
                if QUOTA_MARKER in str(e):
                    quota_hit = True
                    print(f"  ! {label}: download quota exceeded", file=sys.stderr)
                else:
                    print(f"  ! {label}: download failed: {msg}", file=sys.stderr)
                continue

            if not out or not os.path.exists(zip_path):
                print(f"  ! {label}: download produced no file", file=sys.stderr)
                continue
            if not zipfile.is_zipfile(zip_path):
                print(f"  ! {label}: downloaded file is not a valid zip", file=sys.stderr)
                continue

            print(f"  downloaded {os.path.getsize(zip_path) / (1024 * 1024):.0f} MB "
                  f"from {label}")

            if keep_zip or download_only:
                kept = os.path.join(dest_root, f"{name}.zip")
                shutil.move(zip_path, kept)
                zip_path = kept
                print(f"  saved zip -> {kept}")
                if download_only:
                    return True

            return merge_zip(zip_path, dest_root, name)
        finally:
            if not keep_tmp:
                _force_rmtree(tmp)

    if quota_hit:
        print_quota_help(group)
    else:
        print(f"  ! could not download '{name}' from any source", file=sys.stderr)
    return False


# --------------------------------------------------------------------------- #
# Local zip import
# --------------------------------------------------------------------------- #
def import_zip(zip_path, dest_root, keep_zip):
    """Extract + merge an already-downloaded archive. Deletes it unless kept."""
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        print(f"error: no such file: {zip_path}", file=sys.stderr)
        return False
    if not zipfile.is_zipfile(zip_path):
        print(f"error: not a valid zip archive: {zip_path}", file=sys.stderr)
        return False

    # Prefer the folder name stored inside the archive; fall back to the filename.
    name = zip_top_level_dir(zip_path)
    if not name:
        name = _clean_name(os.path.basename(zip_path))
        print(f"  ! could not detect a single top-level folder; using '{name}'")

    print(f"\n=== {name} (local zip) ===")
    print(f"Source: {zip_path} ({os.path.getsize(zip_path) / (1024 * 1024):.0f} MB)")

    if not merge_zip(zip_path, dest_root, name):
        return False

    if keep_zip:
        print(f"  kept zip: {zip_path}")
    else:
        try:
            os.remove(zip_path)
            print(f"  removed zip: {zip_path}")
        except OSError as e:
            print(f"  ! could not remove zip: {_short(e)}", file=sys.stderr)
    return True


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
    p.add_argument("--check", action="store_true",
                   help="With --list, probe each source for quota availability "
                        "(slower: two small requests per file).")
    p.add_argument("--source", choices=["auto"] + SOURCE_LABELS, default="auto",
                   help="Which source to use (default: auto = try each in order).")
    p.add_argument("--zip", metavar="PATH",
                   help="Import an already-downloaded archive instead of "
                        "downloading: extract and merge it, then delete it "
                        "(use --keep-zip to keep).")
    p.add_argument("--dest", default=REPO_ROOT,
                   help="Destination root (default: repo root).")
    p.add_argument("--download-only", action="store_true",
                   help="Only download the zip(s); do not extract/merge "
                        "(implies --keep-zip).")
    p.add_argument("--keep-zip", action="store_true",
                   help="Keep the zip instead of deleting it after merging.")
    p.add_argument("--no-probe", action="store_true",
                   help="Skip the pre-flight availability check.")
    p.add_argument("-y", "--yes", action="store_true",
                   help="Do not prompt for confirmation before downloading.")
    return p.parse_args()


def main():
    args = parse_args()
    dest_root = os.path.abspath(args.dest)

    # Local import needs neither gdown nor network access.
    if args.zip:
        return 0 if import_zip(args.zip, dest_root, args.keep_zip) else 2

    print("Fetching group list ...")
    catalog = build_catalog()

    if args.list:
        print_menu(catalog)
        if args.check:
            print("Probing availability (this takes a moment) ...\n")
            for g in catalog:
                states = []
                for label, file_id in source_order(g, "auto"):
                    ok, detail = probe_source(file_id)
                    states.append(f"{label}={'OK' if ok else detail}")
                print(f"  {g['name']:<22} {'  '.join(states)}")
            print()
        return 0

    if args.all:
        selected = list(catalog)
    elif args.groups:
        selected = resolve_tokens(args.groups, catalog)
    else:
        selected = interactive_select(catalog)

    if not selected:
        print("Nothing selected; exiting.")
        return 1

    print("\nSelected groups:")
    for g in selected:
        srcs = ",".join(s for s in SOURCE_LABELS if s in g["ids"])
        print(f"  - {g['name']}  [{srcs}]")
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

    # Bootstrap gdown only once we know we are going to download something.
    ensure_gdown()

    ok = 0
    for group in selected:
        try:
            if process_group(
                group, dest_root,
                download_only=args.download_only,
                keep_zip=args.keep_zip,
                source_pref=args.source,
                probe=not args.no_probe,
            ):
                ok += 1
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return 130
        except Exception as e:  # never abort the whole run on one bad group
            print(f"  ! unexpected error for '{group['name']}': {_short(e, 300)}",
                  file=sys.stderr)

    print(f"\nDone: {ok}/{len(selected)} group(s) processed successfully.")
    return 0 if ok == len(selected) else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
