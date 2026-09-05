#!/usr/bin/env python3
"""Exercise a relocated PG17 engine bundle in an isolated, disposable cell."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import re
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from build import digest


def lsn(value):
    high, low = value.split("/")
    return (int(high, 16) << 32) + int(low, 16)


class Cell:
    def __init__(self, root, bundle):
        self.root = root
        self.bundle = bundle
        self.pg = bundle / "pg_install/v17/bin"
        self.processes = []
        self.expected_stops = set()
        self.logs = []
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
                                   "SENTRY_DSN", "AUTOSCALING", "RUST_LOG", "PGHOST", "PGPORT",
                                   "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSERVICE", "PGSERVICEFILE"}}
        self.env.update(PATH=str(self.pg) + ":/usr/bin:/bin", PGCONNECT_TIMEOUT="2")
        self.tenant, self.main, self.child = (uuid.uuid4().hex for _ in range(3))
        sockets = [socket.socket() for _ in range(11)]
        try:
            for sock in sockets:
                sock.bind(("127.0.0.1", 0))
            (self.broker, self.skpg, self.skhttp, self.pspg, self.pshttp,
             self.sqlmain, self.sqlchild, self.extmain, self.intmain, self.extchild, self.intchild) = (
                sock.getsockname()[1] for sock in sockets
            )
        finally:
            for sock in sockets:
                sock.close()

    def start(self, name, *args):
        log = (self.root / f"{name}.log").open("a")
        self.logs.append(log)
        process = subprocess.Popen(list(map(str, args)), cwd=self.root, env=self.env,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.processes.append((name, process))
        return process

    def stop(self, process, abrupt=False):
        self.expected_stops.add(process.pid)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL if abrupt else signal.SIGTERM)
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                raise RuntimeError("graceful shutdown timed out")

    def close(self):
        for _, process in reversed(self.processes):
            self.stop(process, abrupt=True)
        for log in self.logs:
            log.close()

    def wait(self, action, timeout=90):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            for name, process in self.processes:
                if process.pid not in self.expected_stops and process.poll() is not None:
                    raise RuntimeError(f"{name} exited with {process.returncode}; see {self.root / (name + '.log')}")
            try:
                result = action()
                if result:
                    return result
            except (OSError, urllib.error.URLError, subprocess.CalledProcessError) as error:
                last = error
            time.sleep(0.2)
        raise RuntimeError(f"readiness deadline exceeded: {last}")

    def api(self, method, path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.pshttp}/v1/{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"}, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            return json.loads(data) if data else True

    def sql(self, port, query):
        return subprocess.check_output([
            str(self.pg / "psql"), "-XAt", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
            "-p", str(port), "-U", "cloud_admin", "-d", "postgres", "-c", query,
        ], env=self.env, cwd=self.root, text=True, stderr=subprocess.PIPE, timeout=30).strip()

    def start_storage(self):
        for name in ("pageserver", "safekeeper", "remote-test-storage"):
            (self.root / name).mkdir(exist_ok=True)
        ps = self.root / "pageserver"
        (ps / "identity.toml").write_text("id = 1\n")
        # LocalFs deliberately serves only as a test backend here. This is not
        # S3 compatibility, acknowledged-object durability or power-loss proof.
        (ps / "pageserver.toml").write_text(f'''
listen_pg_addr = "127.0.0.1:{self.pspg}"
listen_http_addr = "127.0.0.1:{self.pshttp}"
pg_distrib_dir = {json.dumps(str(self.bundle / 'pg_install'))}
broker_endpoint = "http://127.0.0.1:{self.broker}"
pg_auth_type = "Trust"
http_auth_type = "Trust"
control_plane_emergency_mode = true
control_plane_api = "http://127.0.0.1:1"
virtual_file_io_mode = "buffered"
remote_storage = {{local_path = {json.dumps(str(self.root / 'remote-test-storage'))}}}
''')
        self.start("broker", self.bundle / "bin/storage_broker", "--listen-addr", f"127.0.0.1:{self.broker}")
        self.start("safekeeper", self.bundle / "bin/safekeeper", "-D", self.root / "safekeeper",
                   "--id", "1", "--listen-pg", f"127.0.0.1:{self.skpg}",
                   "--listen-http", f"127.0.0.1:{self.skhttp}",
                   "--broker-endpoint", f"http://127.0.0.1:{self.broker}")
        self.start("pageserver", self.bundle / "bin/pageserver", "-D", ps)
        self.wait(lambda: self.api("GET", "status"))

    def compute(self, name, timeline, sqlport, external, internal):
        settings = {
            "listen_addresses": "127.0.0.1", "port": str(sqlport), "shared_buffers": "16MB",
            "max_connections": "30", "wal_level": "logical", "wal_log_hints": "on",
            "max_wal_senders": "10", "max_replication_slots": "10", "fsync": "on",
            "synchronous_commit": "on", "synchronous_standby_names": "walproposer",
            "shared_preload_libraries": "neon", "neon.tenant_id": self.tenant,
            "neon.timeline_id": timeline, "neon.safekeepers": f"127.0.0.1:{self.skpg}",
            "neon.pageserver_connstring": f"host=127.0.0.1 port={self.pspg}",
        }
        # All values are fixture-generated. compute_ctl also reads storage
        # connection settings from this structured list before starting PG.
        config = {"spec": {"format_version": 1.0, "suspend_timeout_seconds": -1,
            "cluster": {"roles": [{"name": "cloud_admin", "encrypted_password": None, "options": None}],
                        "databases": [], "settings": [
                            {"name": k, "value": v, "vartype": "string"}
                            for k, v in settings.items()]},
            "delta_operations": []}, "compute_ctl_config": {"jwks": {"keys": []}, "tls": None}}
        path = self.root / f"{name}-spec.json"
        path.write_text(json.dumps(config))
        process = self.start(name, self.bundle / "bin/compute_ctl", "--dev", "--compute-id", name,
            "--pgdata", self.root / f"{name}-pgdata", "--config", path,
            "--pgbin", self.pg / "postgres", "--connstr",
            f"postgresql://cloud_admin@127.0.0.1:{sqlport}/postgres",
            "--http-listen-addr", "127.0.0.1", "--external-http-port", external,
            "--internal-http-port", internal)
        self.wait(lambda: self.sql(sqlport, "SELECT 1") == "1")
        return process

    def exercise(self):
        self.start_storage()
        self.api("PUT", f"tenant/{self.tenant}/location_config",
                 {"mode": "AttachedSingle", "generation": 1,
                  "tenant_conf": {"lazy_slru_download": True}})
        self.api("POST", f"tenant/{self.tenant}/timeline", {"new_timeline_id": self.main, "pg_version": 17})
        parent = self.compute("main", self.main, self.sqlmain, self.extmain, self.intmain)
        version = self.sql(self.sqlmain, "SHOW server_version_num")
        assert 170000 <= int(version) < 180000, version
        self.sql(self.sqlmain, "CREATE TABLE orders(id integer PRIMARY KEY, amount numeric(12,2)); "
                              "INSERT INTO orders SELECT i, i * 1.25 FROM generate_series(1,1000) i;")
        assert self.sql(self.sqlmain, "SELECT count(*), sum(amount) FROM orders") == "1000|625625.00"
        # Exceed shared_buffers and exercise bulk index writes after the
        # maintained PG17 series removed the redundant block-LSN callbacks.
        self.sql(self.sqlmain, "CREATE TABLE history(id int, span int4range, pad text); "
                 "ALTER TABLE history ALTER COLUMN pad SET STORAGE PLAIN; "
                 "INSERT INTO history SELECT i, int4range(i, i+1), repeat(md5(i::text),64) "
                 "FROM generate_series(1,10000) i; CREATE INDEX ON history USING gist(span);")
        self.sql(self.sqlmain, "VACUUM ANALYZE history")
        point = self.sql(self.sqlmain, "SELECT pg_current_wal_flush_lsn()")
        self.wait(lambda: lsn(self.api("GET", f"tenant/{self.tenant}/timeline/{self.main}")["last_record_lsn"]) >= lsn(point))
        self.api("POST", f"tenant/{self.tenant}/timeline", {
            "new_timeline_id": self.child, "ancestor_timeline_id": self.main, "ancestor_start_lsn": point})
        child = self.compute("child", self.child, self.sqlchild, self.extchild, self.intchild)
        assert self.sql(self.sqlchild, "SELECT count(*) FROM orders") == "1000"
        self.sql(self.sqlchild, "UPDATE orders SET amount = 99.99 WHERE id = 1")
        assert self.sql(self.sqlmain, "SELECT amount FROM orders WHERE id = 1") == "1.25"
        self.stop(child)
        self.compute("child", self.child, self.sqlchild, self.extchild, self.intchild)
        assert self.sql(self.sqlchild, "SELECT amount FROM orders WHERE id = 1") == "99.99"
        def historical_read(_):
            return self.sql(self.sqlchild, "SET enable_seqscan=off; "
                            "SELECT id FROM history WHERE span @> 7319").splitlines()[-1]
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(historical_read, range(16))) == ["7319"] * 16
        # An acknowledged write must survive abrupt loss of the compute group.
        self.stop(parent, abrupt=True)
        self.compute("main", self.main, self.sqlmain, self.extmain, self.intmain)
        assert self.sql(self.sqlmain, "SELECT count(*), sum(amount) FROM orders") == "1000|625625.00"
        with urllib.request.urlopen(f"http://127.0.0.1:{self.pshttp}/metrics", timeout=10) as response:
            metrics = response.read().decode()
        slru = re.search(r'pageserver_smgr_query_started_global_count\{[^\n]*get_slru_segment[^\n]*\} ([0-9.]+)', metrics)
        assert slru and float(slru.group(1)) > 0, "lazy SLRU downloads were not exercised"
        return {"status": "PASS", "postgres_version_num": int(version), "branch_lsn": point,
                "checks": ["relocated bundle with spaces", "PG17 SQL and exact decimals",
                           "explicit-LSN branch", "parent/child isolation", "graceful compute restart",
                           "acknowledged writes survive abrupt compute restart",
                           "GiST index and concurrent reads after cache eviction/restart",
                           "lazy SLRU download exercised"],
                "limits": ["LocalFs test remote backend", "no S3 or power-loss qualification",
                           "trusted local test credentials", "not a platform lifecycle test"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.bundle / "manifest.json").read_text())
    for name, checksum in manifest["bundle"]["files"].items():
        path = (args.bundle / name).resolve()
        if not path.is_relative_to(args.bundle.resolve()) or digest(path) != checksum:
            raise ValueError(f"bundle integrity failure: {name}")
    root = Path(tempfile.mkdtemp(prefix="supabricks-native-"))
    relocated = root / "bundle with spaces"
    shutil.copytree(args.bundle, relocated, symlinks=True)
    cell = Cell(root, relocated)
    try:
        report = cell.exercise()
        report["neon_commit"] = manifest["neon_commit"]
        report["sources"] = manifest["sources"]
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    except BaseException:
        print(f"Failed test state and logs retained: {root}", flush=True)
        raise
    finally:
        cell.close()
    shutil.rmtree(root)


if __name__ == "__main__":
    main()
