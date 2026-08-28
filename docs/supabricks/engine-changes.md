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
