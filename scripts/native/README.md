# Native PG17 developer bundle

E01 build tooling owned by `supabricks/neon`. Product installation and lifecycle
remain in `supabricks/platform`. These tools produce a developer archive; they
do not publish a release or install services on the machine.

## Build

Use a Linux x86_64 or macOS arm64 builder, Python 3.12 and the repository's Rust
1.88.0 toolchain. Source checkout/build paths must not contain spaces because of
the inherited Makefiles; relocated runtime paths are tested with spaces.

Ubuntu 24.04 build dependencies:

```sh
sudo apt-get install build-essential libtool libreadline-dev zlib1g-dev flex bison \
  libseccomp-dev libssl-dev clang pkg-config cmake protobuf-compiler \
  libprotobuf-dev libcurl4-openssl-dev libicu-dev patchelf
```

macOS build dependencies (build machine only):

```sh
brew install flex bison openssl@3 protobuf icu4c pkg-config
```

From the repository root:

```sh
rustup toolchain install 1.88.0 --profile minimal
python3 scripts/native/build.py --fetch --check
python3 scripts/native/bundle.py build/supabricks-engine
python3 scripts/native/smoke.py build/supabricks-engine --report build/native-smoke.json
```

`--fetch` checks out immutable submodule gitlinks and never follows branch tips.
`postgres_ffi` needs headers from all four PG14–17 source trees; only PG17
Postgres, its utilities and Neon extensions are compiled into the bundle. Cargo
uses `--locked` and all dependencies are public. Builds use release optimization
without Rust debug information. The build records source identities, compiler
settings, Cargo.lock hash and whether PostgreSQL regression tests ran.

Build dependencies belong on a builder. A user of the assembled bundle needs
neither a compiler nor Docker, Kubernetes, Homebrew or Java. Qualification on a
separate clean host and the platform installer are subsequent gates.

## Assembly and tests

The archive contains `bin/{pageserver,safekeeper,storage_broker,compute_ctl}`,
`pg_install/v17/`, shared runtime libraries, primary license texts and
`manifest.json`. The packager follows the linked dependency closure, copies
non-system libraries, rewrites ELF RPATH/Mach-O dependencies and rejects library
name collisions. Linux retains glibc/the system loader; macOS retains OS system
libraries and uses ad-hoc signing after relocation edits. This is not Developer
ID signing/notarization. The archive has a SHA-256 sidecar; its manifest also
records file hashes, symlink destinations and original library provenance.

The smoke verifies recorded file hashes, copies the bundle to a path containing
spaces, strips library-path overrides, starts an isolated cell and connects
using its bundled `psql`. It verifies PG17 and exact decimals, an explicit-LSN
branch after ingestion, parent/child isolation, graceful compute restart, and
acknowledged data after abrupt compute termination. Compute directories are
freshly reconstructed by `compute_ctl` on restart.

Every fixture service uses loopback and disposable ports/data. The harness
owns its process groups, stops them on exit and retains logs on failure. The
fixture uses trusted local credentials and **LocalFs test remote storage**:
it is not S3 compatibility, machine/power-loss durability, backup restore or
platform lifecycle qualification. Test backend selection must not be reused as
the product storage default. Candidate S3 and process-supervisor artifacts are
separate platform dependencies.

The [standalone workflow](../../.github/workflows/native-engine.yml) builds and
tests both targets without private Neon actions or secrets. It retains logs and
developer artifacts. Successful Linux results do not qualify macOS, and passing
this smoke does not complete the platform's release checklist.

## Source updates and remaining release gates

First reproduce the PG17 gitlink in the inspected Neon baseline, then qualify
the maintained `supabricks/postgres/sb/REL_17_STABLE` branch. PG17 minor updates
must preserve the inherited integration patches and pass both PG regression
and storage/compute tests. A current upstream PG17 minor is required before
public preview. PG18 needs an explicit engine compatibility port.

Outstanding distribution work includes full transitive license/source inventory,
clean-host and externally-offline tests, macOS Developer ID/notarization policy,
S3 acknowledgment durability, supervisor lifecycle and artifact signing. A
developer archive is not marked `qualified` in the platform component lock.
Engine changes are recorded in [EC-0001/EC-0002](../../docs/supabricks/engine-changes.md).
