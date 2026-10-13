"""Scan Git blobs without displaying credentials or changing local files.

Use --staged before committing; by default scan the committed HEAD tree.
This targeted check is not a guarantee that every kind of secret is detected.
"""
import argparse
import ast
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SECRET_NAME = re.compile(r"(?:api[_-]?key|token|secret|password|passwd)", re.I)
PATTERNS = (
    ("API key pattern", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}")),
    ("GitHub token pattern", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("AWS access key pattern", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("private key", re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("credential in URL", re.compile(rb"https?://[^\s/:@]+:[^\s/@]+@")),
)
LITERAL = re.compile(
    r"^\s*(?:export\s+)?[\w-]*(?:api[_-]?key|token|secret|password|passwd)[\w-]*"
    r"\s*[:=]\s*(['\"])([^'\"\r\n]+)\1\s*[,#]?", re.I | re.M)


def is_placeholder(value):
    value = value.strip().lower()
    return (not value or value in {"empty", "dummy", "test", "none", "changeme"}
            or value.startswith(("your_", "your-", "example", "fake", "test-", "<", "${")))


def local_secret_values():
    """Read configured values in memory; never print them or source .env."""
    values = []
    candidates = list(os.environ.items())
    for path in (ROOT / ".env", ROOT / "abaqus_agent" / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"\s*(?:export\s+)?([\w-]+)\s*=\s*(.*)", line)
            if match:
                value = match[2].strip()
                if value.startswith(("'", '"')):
                    try:
                        value = ast.literal_eval(value)
                    except (SyntaxError, ValueError):
                        value = value.strip("'\"")
                else:
                    value = value.split(" #", 1)[0].strip()
                candidates.append((match[1], value))
    for name, value in candidates:
        if (SECRET_NAME.search(name) and isinstance(value, str)
                and len(value) >= 6 and not is_placeholder(value)):
            values.append(value.encode("utf-8"))
    return set(values)


def inspect_blob(path, content, secrets=()):
    """Return redacted findings only; never return matching content."""
    p = PurePosixPath(path)
    reasons = []
    if ((p.name.startswith(".env") and p.name != ".env.example")
            or p.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
            or p.name in {"credentials.json", "secrets.json"}):
        reasons.append("credential file must not be published")
    if (path.startswith(("workspace/", "tests/native_runs/", "abaqus_agent/state/", "state/"))
            and p.name != ".gitkeep") or ".ipc" in p.parts:
        reasons.append("local runtime data must not be published")
    if any(secret in content for secret in secrets):
        reasons.append("matches a configured local secret")
    for reason, pattern in PATTERNS:
        if pattern.search(content):
            reasons.append(reason)
    text = content.decode("utf-8", errors="replace")
    if any(not is_placeholder(match[2]) for match in LITERAL.finditer(text)):
        reasons.append("literal credential assignment")
    return sorted(set(reasons))


def git_bytes(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="scan index blobs, not working files")
    args = parser.parse_args()
    try:
        if args.staged:
            paths = git_bytes("ls-files", "-z").split(b"\0")
        else:
            paths = git_bytes("ls-tree", "-r", "--name-only", "-z", "HEAD").split(b"\0")
        paths = [p.decode("utf-8") for p in paths if p]
        if not paths:
            print("FAIL: no files available for release checking.")
            return 1
        secrets = local_secret_values()
        findings = []
        total = 0
        for path in paths:
            content = git_bytes("show", (":" if args.staged else "HEAD:") + path)
            total += len(content)
            for reason in inspect_blob(path, content, secrets):
                findings.append((path, reason))
        if findings:
            for path, reason in findings:
                print("FAIL: %s: %s" % (path, reason))
            return 1
        print("PASS: %d %s files, %.2f MiB; no configured local secrets or known credential patterns detected."
              % (len(paths), "staged" if args.staged else "committed", total / (1024 * 1024)))
        return 0
    except (subprocess.CalledProcessError, OSError, UnicodeError):
        print("FAIL: unable to inspect Git blobs. Initialize Git and stage files first.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
