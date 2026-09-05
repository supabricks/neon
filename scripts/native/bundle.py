#!/usr/bin/env python3
"""Assemble a relocatable developer bundle from a successful native build."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile

from build import BINARIES, ROOT, digest, output

SYSTEM_LINUX = re.compile(r"^(ld-linux.*|lib(c|m|dl|pthread|rt|resolv|util)\.so(?:\..*)?)$")


def capture(*args, env=None):
    return subprocess.check_output(list(map(str, args)), text=True, env=env).strip()


def command(*args):
    subprocess.run(list(map(str, args)), check=True)


def binary(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) in {b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}


def dependencies(path, system, env=None):
    if system == "Linux":
        listing = capture("ldd", path, env=env)
        if "not found" in listing:
            raise ValueError(f"unresolved dependency for {path}:\n{listing}")
        return [(name, Path(source)) for name, source in re.findall(
            r"^\s*(\S+) => (/.+?) \(", listing, re.MULTILINE
        ) if not SYSTEM_LINUX.match(name)]
    result = []
    for line in capture("otool", "-L", path, env=env).splitlines()[1:]:
        name = line.strip().split(" (", 1)[0]
        if name.startswith(("/usr/lib/", "/System/Library/")):
            continue
        source = name.replace("@loader_path", str(path.parent))
        source = source.replace("@executable_path", str(path.parent))
        if source.startswith("@rpath/"):
            rpaths = re.findall(r"cmd LC_RPATH\n\s*cmdsize \d+\n\s*path (.*?) \(offset",
                                capture("otool", "-l", path, env=env))
            candidates = [Path(r.replace("@loader_path", str(path.parent))) / source[7:]
                          for r in rpaths]
            source = next((str(p) for p in candidates if p.exists()), source)
        if not Path(source).is_file():
            raise ValueError(f"unresolved dependency {name} for {path}")
        if Path(source).resolve() != path.resolve():
            result.append((name, Path(source)))
    return result


def assemble(destination):
    system = platform.system()
    if system not in {"Linux", "Darwin"}:
        raise ValueError("unsupported bundle platform")
    if destination.exists():
        raise ValueError(f"output already exists: {destination}; choose a new directory")
    report = json.loads((ROOT / "build/native-build.json").read_text())
    if report["neon_commit"] != output("git", "rev-parse", "HEAD"):
        raise ValueError("source HEAD changed since build; rebuild to refresh provenance")
    destination.mkdir(parents=True)
    (destination / "bin").mkdir()
    (destination / "lib").mkdir()
    (destination / "licenses").mkdir()
    shutil.copy2(ROOT / "LICENSE", destination / "licenses/neon.txt")
    shutil.copy2(ROOT / "NOTICE", destination / "licenses/neon-NOTICE.txt")
    shutil.copy2(ROOT / "vendor/postgres-v17/COPYRIGHT", destination / "licenses/postgres.txt")
    shutil.copytree(ROOT / "pg_install/v17", destination / "pg_install/v17", symlinks=True)
    for name in BINARIES:
        shutil.copy2(ROOT / "target/release" / name, destination / "bin" / name)

    # Resolve against original build locations before changing loader metadata.
    pairs = [(ROOT / "target/release" / name, destination / "bin" / name) for name in BINARIES]
    pairs += [(ROOT / "pg_install/v17" / p.relative_to(destination / "pg_install/v17"), p)
              for p in (destination / "pg_install/v17").rglob("*") if binary(p)]
    external = {}
    visited = set()
    patches = []
    while pairs:
        source, target = pairs.pop()
        if target in visited:
            continue
        visited.add(target)
        deps = dependencies(source, system)
        changes = []
        for name, dep in deps:
            filename = dep.name
            # ldd returns the soname as `name`, including versioned symlinks.
            if system == "Linux":
                filename = name
            copied = destination / "lib" / filename
            checksum = digest(dep)
            if filename in external and external[filename]["source_sha256"] != checksum:
                raise ValueError(f"conflicting library basename: {filename}")
            if not copied.exists():
                shutil.copy2(dep, copied)
                external[filename] = {"source": str(dep), "source_sha256": checksum}
                pairs.append((dep, copied))
            changes.append((name, copied))
        patches.append((target, changes))

    for target, changes in patches:
        relative = os.path.relpath(destination / "lib", target.parent)
        if system == "Linux":
            command("patchelf", "--set-rpath", f"$ORIGIN/{relative}", target)
        else:
            for name, copied in changes:
                command("install_name_tool", "-change", name,
                        f"@loader_path/{relative}/{copied.name}", target)
            if target.suffix == ".dylib":
                command("install_name_tool", "-id", f"@loader_path/{target.name}", target)
            command("codesign", "--force", "--sign", "-", target)

    # A successful copy is insufficient if loader metadata still selects a
    # library from the builder. Resolve the finished closure without overrides.
    clean_env = {key: value for key, value in os.environ.items()
                 if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "DYLD_LIBRARY_PATH",
                                "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"}}
    for target, _ in patches:
        for name, dependency in dependencies(target, system, env=clean_env):
            if not dependency.resolve().is_relative_to(destination):
                raise ValueError(f"packaged dependency escapes bundle: {target}: {name} -> {dependency}")

    report["bundle"] = {
        "kind": "developer", "system_libraries": "glibc/loader" if system == "Linux" else "macOS SDK",
        "loader_check": "passed without library-path overrides",
        "external_libraries": external,
        "license_inventory": "primary licenses included; transitive distribution audit pending",
        "files": {str(p.relative_to(destination)): digest(p) for p in sorted(destination.rglob("*"))
                  if p.is_file() and not p.is_symlink()},
        "symlinks": {str(p.relative_to(destination)): os.readlink(p)
                     for p in sorted(destination.rglob("*")) if p.is_symlink()},
    }
    for p, link in report["bundle"]["symlinks"].items():
        if not (destination / p).resolve().is_relative_to(destination):
            raise ValueError(f"bundle symlink escapes output: {p} -> {link}")
    (destination / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    archive = destination.with_name(destination.name + ".tar.gz")
    if archive.exists():
        raise ValueError(f"archive already exists: {archive}")
    with tarfile.open(archive, "w:gz", compresslevel=3) as tar:
        tar.add(destination, arcname=destination.name)
    archive.with_name(archive.name + ".sha256").write_text(f"{digest(archive)}  {archive.name}\n")
    print(f"Developer bundle: {archive}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        assemble(args.output.resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
