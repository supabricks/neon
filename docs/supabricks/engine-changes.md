# Engine Change Ledger (EC-)

Every divergence of `sb/main` from upstream **neondatabase/neon** (pageserver,
safekeeper, broker, storage controller, compute_ctl, the `neon` extension,
compute images) has an entry here. Commits carry `Engine-Change: EC-NNNN`
trailers. Upstream-first: what upstream will take goes upstream; we carry only
what it won't, with the reason recorded here.

**Upstream baseline (inception 2026-08-28):** `neondatabase/neon@8f60b04` (main,
2026-05-25), tag `baseline/neon-8f60b04`. The publicly shipped images
(`ghcr.io/neondatabase/neon:latest` == tag `8464`, built 2025-08-26, and the
matching `compute-node-v16/v17`) correspond to ≈ `main@77e22e4` (2025-08-25);
the two are 10 commits apart. Upstream's `release` branch froze 2025-07-25 and
public image publishing stopped 2025-09-02; treat `sb/main` as the mainline.

**Postgres submodules:** `vendor/postgres-v{14..17}` point at
neondatabase/postgres commits (see `vendor/revisions.json`); they will be
re-pointed at tags cut from `supabricks/postgres` `sb/REL_*_STABLE` branches.
`vendor/postgres-v18` does not exist upstream — adding it is EC-0001's job.

---

## Entry template

```markdown
## EC-NNNN: Title
*Category: hook | behavior | subsystem | build | test-only | temporary(expiry: YYYY-MM-DD)*
*Origin: ours · Upstream status: upstreamable | submitted(<link>) | blocked(<why>) | never(<justification>)*
*Owner:*

**What**: files/areas touched.
**Why**: the problem this solves.
**Exit plan** (mandatory): how this divergence dies.
**Verification**: tests that catch regression.
```

---

## EC-0001: Build images from source at pinned commits; add vendor/postgres-v18
*Category: build · Origin: ours · Upstream status: n/a (build infrastructure) · Owner: TODO*

**What**: CI that builds `neon` and `compute-node-v16/17/18` images from `sb/main`
with `vendor/postgres-v*` pointed at `supabricks/postgres` tags; adds the missing
`vendor/postgres-v18` submodule (upstream has `REL_18_STABLE_neon` but never
wired it into main).
**Why**: upstream stopped publishing images (2025-09-02); no change — including
PG 18 — can ship otherwise. Also the supply-chain/sovereignty requirement.
**Exit plan**: none needed; this is ownership, not divergence.
**Verification**: the built images pass the platform's e2e/chaos/restore gates.

### 2026-09-05 addendum: native PG17 distribution first

*Owner: Supabricks platform maintainers · Status: implementation in progress*

The first Supabricks native bundle targets PG17 on Linux x86_64 and macOS arm64.
PG18 remains subsequent integration work: the current engine's bindings, version
dispatch and WAL handling implement PG14–17. Native packaging precedes new images.

**What**: `scripts/native/` build, bundle and isolated smoke tools; standalone
native CI; `pgxn/neon/Makefile` isolates PostgreSQL C warning flags from the Rust
communicator's third-party C dependencies. All four pinned PG source trees supply
build headers; only PG17 is compiled and packaged as a compute. No private CI
action or token is required. Record source pins, compiler settings, dependencies
and artifact checksums. Preserve the distinction between a developer archive
and a qualified distribution.

**Why**: local installation requires native, relocatable engine binaries.
Unconditionally inherited PGXS `-Werror=vla` breaks jemalloc compilation when
Cargo rebuilds the communicator inside an extension build. Separate
`NEON_RUST_CFLAGS`/`NEON_RUST_CXXFLAGS` carry explicitly selected Rust C flags.

**Exit plan**: upstream the narrow Makefile correction where practical; native
distribution scripts remain Supabricks-owned build infrastructure.

**Verification**: clean native source build, PostgreSQL regression suite,
relocated archive smoke (SQL, explicit-LSN branching, isolation, graceful and
abrupt compute restart). The smoke's LocalFs test backend does not qualify S3
or acknowledged-object durability. Platform E01 reports record results/limits;
do not infer macOS qualification from Linux results.

CI checks out the exact PR head rather than a synthetic merge commit, and stores
generated logs under the ignored build directory so they do not mislabel source
cleanliness. Assembly rechecks the finished dependency closure with loader
overrides removed; every non-system library must resolve inside the bundle.
Linux CI additionally bootstraps a minimal Ubuntu userspace and repeats runtime
checks as a non-root user with build tools absent and networking limited to an
isolated loopback interface. This uses the runner's kernel, not a separate VM.

## EC-0002: Configure compute_ctl HTTP bind address for native use
*Category: behavior · Origin: ours · Upstream status: upstreamable · Owner: Supabricks platform maintainers*

**What**: add `--http-listen-addr` to `compute_ctl`, pass the parsed IP to both
HTTP listeners. Default remains `::` for existing deployments; the native
harness supplies `127.0.0.1` explicitly.

**Why**: the inherited internal and external HTTP servers both listen on all
interfaces. A per-user native cell needs a loopback-only management surface.

**Exit plan**: replace with the upstream equivalent if one is adopted; no change
to PostgreSQL or storage protocols is involved.

**Verification**: bind both real HTTP listeners with an explicit loopback address
in the compute_tools test, and launch compute_ctl with that option in native
SQL/branch/restart qualification. Invalid addresses are rejected by clap's IP parser.

### EC-0001 PG17 source qualification

The original PG17.5 gitlink passed 222 PostgreSQL regressions and the relocated
Linux SQL/branch/compute-restart smoke. Advance `vendor/postgres-v17` and its
version record together to the existing Supabricks PG17.8 source commit
`56692dfb680281a963c7470fc7f0fec7f65ecfd4`, then rerun those gates. The other
PG gitlinks remain header-only build inputs. This source selection introduces
no new Postgres core patch. The current upstream PG17 minor, full source/license
inventory and public-release qualification remain required before preview.

## EC-0003: Match the maintained PG17 extension interfaces
*Category: hook · Origin: ours · Upstream status: upstreamable · Owner: Supabricks platform maintainers*

**What**: adapt the Neon extension and WAL-redo extension to the existing PG17.8
patch series. Skip registration of removed block-LSN hooks, allocate the
last-written-LSN lock as an extension-owned named tranche when core no longer
defines it, and install the independent SLRU download hook. The legacy interfaces
remain for the pinned older majors. The PG17.8 threshold describes the qualified
Supabricks patch series, not a promise about arbitrary vanilla PG17 headers.

**Why**: the PG branch contains `a42a079b61c053e35c35e63cfd52449da92b5ddb`
(remove redundant block-LSN hooks), `9aa0b42bfd49d3842c6436f50fd9baabbd005b0a`
(move the lock into the extension), and `c93daf0889e17bf7cf9184d12c4aff9ed4d5084c`
(move SLRU download/materialization out of core), but the engine's extension
still expected the old interfaces. No PostgreSQL core patch is added here.

The SLRU adapter materializes a complete file before exposing it. It uses an
atomic hard-link operation that does not replace an existing destination, so a
concurrent downloader cannot overwrite a segment another backend has already
materialized and modified. This is a disposable compute cache, reconstructed
on startup; remote storage and WAL remain authoritative.

**Exit plan**: adopt the upstream equivalent when available. Remove legacy
interface branches only when the corresponding engine majors are retired.

**Verification**: PG17.8 native build and 224 PostgreSQL regression tests passed
on Linux. Expanded relocation smoke passed SQL/decimals, explicit-LSN branching,
isolation, graceful/abrupt compute restart, GiST index reads after exceeding the
buffer cache, concurrent readers and positive SLRU-request metrics with lazy
download enabled. macOS and broader sharded/replica tests remain separate gates;
the native smoke is not a replacement for the full Neon regression suite.
