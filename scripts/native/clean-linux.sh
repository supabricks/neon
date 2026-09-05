#!/usr/bin/env bash
# Run the developer archive in a disposable minimal Ubuntu userspace, offline.
set -euo pipefail

if [[ $EUID != 0 || $# != 2 ]]; then
  echo "Usage: sudo $0 ARCHIVE NEW_REPORT_DIRECTORY (requires debootstrap, unshare and tini)" >&2
  exit 2
fi
archive=$(realpath "$1")
reports=$(realpath -m "$2")
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir "$reports"
runtime_root=$(mktemp -d /tmp/supabricks-native-root.XXXXXX)

finish() {
  status=$?
  for log in "$runtime_root"/tmp/supabricks-native-*/*.log \
             "$runtime_root"/work/native-smoke.json \
             "$runtime_root"/debootstrap/debootstrap.log; do
    if [[ -f "$log" ]]; then cp "$log" "$reports/"; fi
  done
  if [[ $status == 0 ]]; then
    rm -rf -- "$runtime_root"
  else
    echo "Failed minimal runtime retained: $runtime_root" >&2
  fi
  exit "$status"
}
trap finish EXIT

(cd -- "$(dirname -- "$archive")" && sha256sum -c "$(basename -- "$archive").sha256")
debootstrap --variant=minbase --include=python3 --force-check-gpg noble \
  "$runtime_root" https://archive.ubuntu.com/ubuntu
chmod 755 "$runtime_root"
mkdir -p "$runtime_root/work/engine" "$runtime_root/work/scripts" "$runtime_root/home/native"
tar -xzf "$archive" --strip-components=1 -C "$runtime_root/work/engine"
cp "$scripts/build.py" "$scripts/smoke.py" "$runtime_root/work/scripts/"
printf 'native:x:1000:1000:Native test:/home/native:/bin/sh\n' >> "$runtime_root/etc/passwd"
printf 'native:x:1000:\n' >> "$runtime_root/etc/group"
chown -R 1000:1000 "$runtime_root/work" "$runtime_root/home/native"
chroot "$runtime_root" dpkg-query -W > "$reports/packages.txt"

# These mounts and the loopback interface exist only in the new namespaces.
# The engine runs as an ordinary user; bootstrap is the only networked phase.
unshare --mount --net --pid --ipc --fork tini -- bash -euc '
  mount -t proc proc "$1/proc"
  mount --rbind /dev "$1/dev"
  mount -t tmpfs -o mode=1777 tmpfs "$1/dev/shm"
  ip link set lo up
  chroot --userspec=1000:1000 "$1" /usr/bin/env -i \
    PATH=/usr/bin:/bin HOME=/home/native LANG=C.UTF-8 \
    /usr/bin/python3 -c '\''
import json, runpy, shutil, socket, sys
from pathlib import Path
for tool in ("cc", "gcc", "clang", "cargo", "java", "docker", "brew"):
    assert shutil.which(tool) is None, f"unexpected build tool: {tool}"
assert {name for _, name in socket.if_nameindex()} == {"lo"}
sys.path.insert(0, "/work/scripts")
sys.argv = ["smoke.py", "/work/engine", "--report", "/work/native-smoke.json"]
runpy.run_path("/work/scripts/smoke.py", run_name="__main__")
path = Path("/work/native-smoke.json")
report = json.loads(path.read_text())
report["runtime_environment"] = {"userspace": "Ubuntu noble minbase with Python test harness", "network": "loopback only", "build_tools": "absent", "user": "unprivileged"}
path.write_text(json.dumps(report, indent=2) + "\n")
'\''
' bash "$runtime_root"
