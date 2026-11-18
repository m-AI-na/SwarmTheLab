#!/usr/bin/env python3
"""Find and remove credentials and personal traces before this instance is imaged and cloned.

    unset HISTFILE                                  # first: keep these commands out of shell history
    cd ~/workshop
    python3 tools/prepare_image.py check            # report only (default); changes nothing
    python3 tools/prepare_image.py check --extra you@uni.edu   # also look for your email, name, etc.
    python3 tools/prepare_image.py scrub            # remove what 'check' found (asks first)
    python3 tools/prepare_image.py check --clone    # on a participant clone: expects its key file

Run it as the workshop user (not with sudo), from a plain terminal, with VS Code and all
browsers closed. It never prints a secret: only where one was found.
Exit code: 0 = clean, 1 = something found. Needs only the Python that ships with Ubuntu.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
_REPO = Path(__file__).resolve().parent.parent     # this file lives in the workshop repo's tools/ folder
WORKSHOP = _REPO if (_REPO / "00_preflight.ipynb").exists() else HOME / "workshop"
KEY_DIR = HOME / ".config" / "swarm-lab"

# ---- What a secret looks like ---------------------------------------------------------
PATTERNS = [(label, re.compile(rx)) for label, rx in [
    ("API key (OpenAI / Anthropic / OpenRouter style)",
     r"(?<![A-Za-z0-9])sk-(?:ant-|proj-|svcacct-|admin-|or-)?[A-Za-z0-9_\-]{20,}"),
    ("GitHub token", r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    ("Hugging Face token", r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{30,}"),
    ("AWS access key ID", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ("Private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("OAuth / JWT token", r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    ("Password or token inside a URL", r"https?://[^/\s:@]+:[^/\s@]{8,}@"),
]]
ASSIGNMENT = re.compile(
    r"(?i)\b(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|CLAUDE_CODE_OAUTH_TOKEN|HF_TOKEN|"
    r"HUGGING_FACE_HUB_TOKEN|GITHUB_TOKEN|GH_TOKEN|AWS_SECRET_ACCESS_KEY|NCBI_API_KEY)[\"']?\s*[=:]\s*[\"']?([^\s\"',;]+)")
PLACEHOLDER_WORDS = ("your", "example", "placeholder", "dummy", "changeme", "xxxx", "test", "fake", "redacted",
                     "hidden", "<", "...", "…", "replace")


def real_assignment(line):
    """KEY=value where the value looks like a real key: not a placeholder, a variable or a short test string."""
    for m in ASSIGNMENT.finditer(line):
        v = m.group(1)
        if (len(v) >= 20 and not v.startswith(("$", "os.", "{", "[", "getpass", "None"))
                and not any(w in v.lower() for w in PLACEHOLDER_WORDS)):
            return True
    return False


# ---- Where credentials and personal traces are stored ------------------------------------
# (label, path or glob relative to HOME, kind). 'scrub' deletes all of them.
# kind: 'identity' = must never be in the image; 'trace' = recreated just by logging in, so ignored by
# 'check --clone'; 'keys' = the workshop key file, which a participant clone is expected to have.
STORES = [
    ("Claude Code sign-in, settings and chat transcripts", ".claude", "identity"),
    ("Claude Code account file", ".claude.json", "identity"),
    ("Claude Code account file backups", ".claude.json.backup*", "identity"),
    ("Claude Code permission grants for the workshop folder", str(WORKSHOP / ".claude" / "settings.local.json"), "identity"),
    ("Anthropic CLI profiles", ".config/anthropic", "identity"),
    ("Claude desktop app data", ".config/Claude", "identity"),
    ("Codex sign-in and history", ".codex", "identity"),
    ("GNOME online accounts", ".config/goa-1.0", "identity"),
    ("Firefox profile (cookies, saved logins)", "snap/firefox/common/.mozilla", "identity"),
    ("Firefox profile (cookies, saved logins)", ".mozilla", "identity"),
    ("Chrome profile", ".config/google-chrome", "identity"),
    ("Chromium profile", ".config/chromium", "identity"),
    ("Chromium profile", "snap/chromium/common/chromium", "identity"),
    ("Git saved credentials", ".git-credentials", "identity"),
    ("GitHub CLI sign-in", ".config/gh", "identity"),
    ("Hugging Face token", ".cache/huggingface/token", "identity"),
    ("Hugging Face token", ".huggingface", "identity"),
    ("AWS credentials", ".aws", "identity"),
    ("Docker registry logins", ".docker/config.json", "identity"),
    ("netrc logins", ".netrc", "identity"),
    ("GnuPG keys", ".gnupg", "identity"),
    ("Deleted files in the desktop Trash", ".local/share/Trash", "identity"),
    ("SSH private keys", ".ssh/id_*", "identity"),
    ("IPython / Jupyter cell history", ".ipython/profile_default/history.sqlite*", "identity"),
    ("Workshop key file", ".config/swarm-lab", "keys"),
    ("Desktop keyring (saved passwords and app sign-ins)", ".local/share/keyrings", "trace"),
    ("SSH known hosts", ".ssh/known_hosts*", "trace"),
    ("Shell and Python history", ".bash_history", "trace"),
    ("Shell and Python history", ".python_history", "trace"),
    ("Shell and Python history", ".lesshst", "trace"),
    ("Shell and Python history", ".viminfo", "trace"),
    ("Shell and Python history", ".wget-hsts", "trace"),
    ("Recently used files list", ".local/share/recently-used.xbel", "trace"),
    ("Jupyter runtime files", ".local/share/jupyter/runtime", "trace"),
    ("Old self-test result", "preflight_result.json", "trace"),
]
VSCODE_DIR = HOME / ".config" / "Code"
VSCODE_KEEP = {"User/settings.json", "User/keybindings.json", "User/snippets"}

# ---- What to read when looking for secrets ------------------------------------------------
SCAN_DIRS = [".config", ".claude", ".codex", ".aws", ".ssh", ".docker", ".huggingface", ".ipython", ".jupyter",
             ".gnupg", ".local/share/jupyter", ".local/share/Trash", ".cache", "Desktop", "Documents", "Downloads"]
SYSTEM_FILES = [Path("/etc/environment"), Path("/etc/bash.bashrc")] + sorted(Path("/etc/profile.d").glob("*"))
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "site-packages", "Cache", "CachedData", "Code Cache",
             "GPUCache", "CachedExtensionVSIXs", "Crashpad", "pip", "uv", "ms-playwright", "thumbnails",
             "fontconfig", "mesa_shader_cache", "tracker3"}
SKIP_PATHS = {WORKSHOP / "data"}          # the datasets and the Open Targets copy: large, and not secret
SKIP_SUFFIXES = {".h5ad", ".h5", ".hdf5", ".npz", ".npy", ".parquet", ".zip", ".gz", ".tar", ".bz2", ".xz",
                 ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".so", ".whl", ".mtx", ".loom", ".zarr", ".pt", ".ckpt"}
MAX_BYTES = 500_000_000                  # big text files (e.g. a long run's trace) are read line by line
APPS = {"firefox", "firefox-bin", "chrome", "chromium", "code", "claude", "claude-desktop", "Claude"}

REQUIRED_CACHE = {                       # what each module's pre-run copy must contain
    "module1": ["research_strategy.json", "team_meeting_1.json", "merged.json"],
    "module2": ["inventory.json", "analysis_log.json", "paper_agents.json"],
    "module3": ["last_run.json"],
}


def conda_dirs():
    """Conda activation scripts and saved env vars for every environment."""
    found = []
    for base in ("miniforge3", "miniconda3", "anaconda3", "mambaforge", ".conda"):
        root = HOME / base
        if not root.is_dir():
            continue
        for env in [root] + sorted((root / "envs").glob("*")):
            found.append(env / "etc" / "conda")
            found.append(env / "conda-meta" / "state")
    return found


def iter_scan_targets():
    try:   # every regular file directly in the home folder (.bashrc, .npmrc, notes.txt …)
        yield from sorted(p for p in HOME.iterdir() if not p.is_dir())
    except OSError:
        pass
    yield from SYSTEM_FILES
    yield from conda_dirs()
    for name in SCAN_DIRS:
        yield HOME / name
    yield WORKSHOP


def _skipped(path):
    return any(path == s or s in path.parents for s in SKIP_PATHS)


def walk(path):
    if path.is_symlink() or _skipped(path):
        return
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        return
    for root, dirs, files in os.walk(path):
        here = Path(root)
        if ".git" in dirs and (here / ".git" / "config").is_file():
            yield here / ".git" / "config"          # a token in a clone URL lives here
        dirs[:] = [d for d in dirs if d != ".git" and d not in SKIP_DIRS and not d.endswith(".zarr")
                   and not _skipped(here / d)]
        for f in files:
            p = here / f
            if p.suffix.lower() not in SKIP_SUFFIXES and not p.is_symlink():
                yield p


def iter_lines(p):
    """Lines of a regular text file. Skips FIFOs, devices, binaries and anything over MAX_BYTES."""
    try:
        st = p.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return
        with open(p, "rb") as f:
            if b"\x00" in f.read(8192):
                return
            f.seek(0)
            for i, raw in enumerate(f, 1):
                yield i, raw.decode("utf-8", errors="ignore")
    except OSError:
        return


def scan_secrets(extra, clone=False):
    """Return [(label, path, line_number)]. Never returns the secret itself."""
    extra_res = [(f"Personal string '{s[:3]}…'", re.compile(re.escape(s), re.I)) for s in extra]
    hits, seen = [], set()
    for target in iter_scan_targets():
        for p in walk(target):
            if p in seen or (clone and (p == KEY_DIR or KEY_DIR in p.parents)):
                continue
            seen.add(p)
            for i, line in iter_lines(p):
                for label, rx in PATTERNS + extra_res:
                    if rx.search(line):
                        hits.append((label, p, i))
                if real_assignment(line):
                    hits.append(("Key or token assignment", p, i))
    return hits


def present_stores(clone=False):
    out = []
    for label, rel, kind in STORES:
        if clone and kind != "identity":
            continue
        for p in sorted(HOME.glob(rel)) if any(c in rel for c in "*?[") else [HOME / rel]:
            if p.exists() or p.is_symlink():
                if label == "SSH private keys" and p.suffix == ".pub":
                    continue
                out.append((label, p))
    if VSCODE_DIR.is_dir() and not clone:
        extras = [p for p in VSCODE_DIR.rglob("*") if p.is_file()
                  and not any(str(p.relative_to(VSCODE_DIR)).startswith(k) for k in VSCODE_KEEP)]
        if extras:
            out.append(("VS Code sign-ins, state and logs (settings and extensions are kept)", VSCODE_DIR))
    return out


def workshop_outputs():
    """Participant-facing leftovers from the organiser's own test runs."""
    out = []
    outputs = WORKSHOP / "outputs"
    if outputs.is_dir() and any(outputs.iterdir()):
        out.append(("Your test-run outputs (participants should start with an empty folder)", outputs))
    for nb in sorted(WORKSHOP.glob("*.ipynb")):
        try:
            cells = json.loads(nb.read_text()).get("cells", [])
        except (OSError, ValueError):
            continue
        if any(c.get("outputs") or c.get("execution_count") for c in cells if c.get("cell_type") == "code"):
            out.append(("Saved outputs in a workshop notebook", nb))
    vbt = WORKSHOP / "virtual-biotech"
    for name in ("runs", "sessions"):   # runs from trying the Virtual Biotech's own CLI
        d = vbt / name
        if d.is_dir() and any(d.iterdir()):
            out.append(("Virtual Biotech test runs (transcripts and results)", d))
    return out


def running_apps():
    names = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            names.add(Path(f"/proc/{pid}/comm").read_text().strip())
        except OSError:
            pass
    return sorted(names & APPS)


def cache_ready():
    """Outputs may only be cleared if a complete pre-run copy of each module exists."""
    problems = []
    for module, files in REQUIRED_CACHE.items():
        out_dir, cache_dir = WORKSHOP / "outputs" / module, WORKSHOP / "cache" / module
        if not (out_dir.is_dir() and any(out_dir.iterdir())):
            continue
        missing = [f for f in files if not (cache_dir / f).exists()]
        if module == "module3" and not missing:
            try:
                name = Path(json.loads((cache_dir / "last_run.json").read_text())["run_dir"]).name
            except (OSError, ValueError, KeyError, TypeError):
                name = None
            if not name or not (cache_dir / "runs" / name).is_dir():
                missing.append(f"runs/{name or '<the run folder>'}")
        if missing:
            problems.append(f"{cache_dir} is missing: {', '.join(missing)}")
    return problems


def repo_changes():
    """Tracked workshop files that differ from the GitHub version. Participants get them exactly as they are."""
    if not (WORKSHOP / ".git").is_dir() or not shutil.which("git"):
        return []
    try:
        r = subprocess.run(["git", "-C", str(WORKSHOP), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line[3:] for line in r.stdout.splitlines() if len(line) > 3]


def remove(p):
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)


def clear_vscode():
    for p in sorted(VSCODE_DIR.iterdir()):
        if p.name == "User":
            for q in sorted(p.iterdir()):
                if f"User/{q.name}" not in VSCODE_KEEP:
                    remove(q)
        else:
            remove(p)


def clear_notebook_outputs(nb):
    data = json.loads(nb.read_text())
    for c in data.get("cells", []):
        if c.get("cell_type") == "code":
            c["outputs"], c["execution_count"] = [], None
    nb.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def report(hits, stores, leftovers, clone=False):
    if hits:
        print(f"\n🔑 Possible secrets in {len({h[1] for h in hits})} file(s) (values not shown):")
        for label, p, line in hits[:200]:
            print(f"   {label}: {p}:{line}")
        if len(hits) > 200:
            print(f"   … and {len(hits) - 200} more")
    if stores:
        print("\n🗂  Sign-ins and personal data on this machine:")
        for label, p in stores:
            print(f"   {label}: {p}")
    if leftovers:
        print("\n🧹 Test-run leftovers:")
        for label, p in leftovers:
            print(f"   {label}: {p}")
    auth = HOME / ".ssh" / "authorized_keys"
    if auth.is_file():
        n = sum(1 for _, line in iter_lines(auth) if line.strip() and not line.startswith("#"))
        print(f"\nℹ️  {auth} holds {n} key(s), and every clone will accept SSH logins with them. Make sure each is RAIL's.")
    changed = repo_changes()
    if changed:
        print(f"ℹ️  {len(changed)} file(s) in {WORKSHOP} differ from the GitHub version, and every clone gets them as they are now: "
              f"{', '.join(changed[:10])}{' …' if len(changed) > 10 else ''}. "
              f"Restore one with: git -C {WORKSHOP} checkout -- FILE")
    if clone:
        print(f"ℹ️  Workshop key file: {'present' if (KEY_DIR / 'keys.env').exists() else 'not set up yet'} "
              "(expected on a participant clone; not checked for secrets).")
    if not (hits or stores or leftovers):
        print("\n✅ Clean: no secrets, sign-ins or test-run leftovers found.")


MANUAL_STEPS = """
Before running 'scrub', do these by hand (deleting files alone doesn't cancel a sign-in on the server):
  1. Claude Code (VS Code panel): type /logout. In a terminal: claude auth logout.
  2. VS Code: Accounts menu (bottom-left) -> sign out of every account; turn Settings Sync off. Quit VS Code.
  3. Browser: sign out of claude.ai, platform.openai.com, platform.claude.com, GitHub, Google. Quit the browser.
  4. In the OpenAI and Anthropic consoles, revoke the keys you used while setting up this machine.
     Give participants fresh, capped keys instead.
  5. In this terminal, run 'unset HISTFILE' so these commands (and your --extra email) aren't saved to history.
After 'scrub': run 'check' again, then shut down straight away (sudo poweroff) without logging back in,
and ask RAIL to make the image. Afterwards, log in to one clone and run
'python3 ~/workshop/tools/prepare_image.py check --clone' there.
If you ever used sudo -i or a root shell, also clear root's history: sudo rm -f /root/.bash_history
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", choices=["check", "scrub"], default="check")
    ap.add_argument("--extra", action="append", default=[], metavar="TEXT",
                    help="also search for this text, e.g. your email address or name (repeatable)")
    ap.add_argument("--clone", action="store_true",
                    help="checking a participant clone: its key file, keyring and history are expected")
    ap.add_argument("--yes", action="store_true", help="scrub without asking for confirmation")
    ap.add_argument("--force", action="store_true", help="scrub even if apps are still running (not recommended)")
    args = ap.parse_args()

    if os.geteuid() == 0:
        sys.exit("Run this as the workshop user, without sudo. (As root it would check /root instead of your home.)")
    if args.action == "scrub" and args.clone:
        sys.exit("'scrub' is for the organiser's instance before imaging. On a clone it would delete the participant's keys.")

    print(f"Checking {HOME} and system start-up files{' (participant clone)' if args.clone else ''} …")
    hits = scan_secrets(args.extra, clone=args.clone)
    stores, leftovers = present_stores(clone=args.clone), workshop_outputs()
    report(hits, stores, leftovers, clone=args.clone)
    found = bool(hits or stores or leftovers)

    if args.action == "check":
        if found and not args.clone:
            print(MANUAL_STEPS)
        sys.exit(1 if found else 0)

    apps = running_apps()
    if apps and not args.force:
        sys.exit(f"\n❌ Close these first, then run scrub again: {', '.join(apps)}. "
                 "(Run this from GNOME Terminal, not VS Code's terminal.)")
    problems = cache_ready()
    if problems:
        sys.exit("\n❌ Copy your test-run results into the cache first (see the notebooks' organiser notes):\n   "
                 + "\n   ".join(problems))
    if not found:
        sys.exit(0)
    if not args.yes and input("\nDelete the sign-ins, personal data and test-run leftovers listed above? [y/N] ").strip().lower() != "y":
        sys.exit("Nothing changed.")

    for label, p in stores:
        if p == VSCODE_DIR:
            clear_vscode()
        else:
            remove(p)
        print(f"   removed  {label}: {p}")
    for label, p in leftovers:
        if p.suffix == ".ipynb":
            clear_notebook_outputs(p)
            print(f"   cleared  {label}: {p}")
        else:
            remove(p)
            p.mkdir(parents=True, exist_ok=True)
            print(f"   emptied  {label}: {p}")

    remaining = scan_secrets(args.extra)
    if remaining:
        print(f"\n⚠️ {len(remaining)} possible secret(s) remain in files scrub doesn't delete automatically. "
              "Edit or delete these yourself:")
        for label, p, line in remaining[:200]:
            print(f"   {label}: {p}:{line}")
        print(MANUAL_STEPS)
        sys.exit(1)
    print("\n✅ Done. Run 'check' once more, then: sudo poweroff (don't log back in before the image is made).")


if __name__ == "__main__":
    main()
