#!/usr/bin/env python3
"""Build the PG17 native engine using only public, pinned sources."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BINARIES = ["pageserver", "safekeeper", "storage_broker", "compute_ctl"]


def output(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def run(*args):
    print("+ " + " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 8))
    parser.add_argument("--fetch", action="store_true", help="fetch exact submodule gitlinks")
    parser.add_argument("--check", action="store_true", help="also run PostgreSQL regression tests")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if " " in str(ROOT):
        parser.error("upstream Makefiles need a build path without spaces; the bundle may be relocated")
    if (platform.system(), platform.machine()) not in {
        ("Linux", "x86_64"), ("Darwin", "arm64")
    }:
        parser.error("supported builders: Linux x86_64 and macOS arm64")
    required = ["git", "make", "cargo", "cc", "clang", "pkg-config", "bison", "flex", "protoc"]
    missing = [tool for tool in required if not shutil.which(tool)]
    if missing:
        parser.error("missing build tools: " + ", ".join(missing))

    # postgres_ffi has unconditional bindings for all four majors. Headers for
    # those majors are required even though only PG17 is built and shipped.
    paths = [f"vendor/postgres-v{major}" for major in (14, 15, 16, 17)]
    if args.fetch:
        run("git", "submodule", "update", "--init", "--depth", "1", *paths)
    sources = []
    for path in paths:
        pin = output("git", "ls-tree", "HEAD", path).split()[2]
        actual = output("git", "-C", str(ROOT / path), "rev-parse", "HEAD")
        if actual != pin:
            parser.error(f"{path}: checkout {actual} differs from gitlink {pin}")
        if output("git", "-C", str(ROOT / path), "status", "--porcelain", "--untracked-files=no"):
            parser.error(f"{path}: tracked source changes; commit them before building")
        sources.append({"path": path, "commit": pin,
                        "role": "runtime" if path.endswith("v17") else "build-headers"})

    os.environ.setdefault("CARGO_BUILD_JOBS", str(args.jobs))
    os.environ["CARGO_PROFILE_RELEASE_DEBUG"] = "false"
    os.environ["NEON_RUST_CFLAGS"] = os.environ.get("CFLAGS", "")
    os.environ["NEON_RUST_CXXFLAGS"] = os.environ.get("CXXFLAGS", "")
    common = ["make", f"-j{args.jobs}", "BUILD_TYPE=release", "CARGO_BUILD_FLAGS=--locked"]
    run(*common, "postgres-headers-install", "postgres-install-v17")
    run(*common, "POSTGRES_VERSIONS=v17", "walproposer-lib")
    run("cargo", "build", "--locked", "--release",
        *[flag for binary in BINARIES for flag in ("--bin", binary)])
    if args.check:
        run(*common, "POSTGRES_VERSIONS=v17", "postgres-check-v17")

    report = {
        "schema_version": 1,
        "neon_commit": output("git", "rev-parse", "HEAD"),
        "neon_dirty": bool(output("git", "status", "--porcelain")),
        "postgres_major": 17,
        "sources": sources,
        "cargo_lock_sha256": digest(ROOT / "Cargo.lock"),
        "builder": {"os": platform.platform(), "architecture": platform.machine(),
                    "rustc": output("rustc", "--version"), "cc": output("cc", "--version")},
        "build": {"profile": "release", "debug": False, "jobs": args.jobs,
                  "environment": {name: os.environ.get(name, "") for name in
                                  ("CFLAGS", "LDFLAGS", "RUSTFLAGS", "MACOSX_DEPLOYMENT_TARGET")}},
        "postgres_regression": "passed" if args.check else "not-run",
        "native_qualification": "not-run",
    }
    path = ROOT / "build/native-build.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Build provenance: {path}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
