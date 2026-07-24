# dabs-cicd-demo

A minimal Databricks Asset Bundle (DAB) reference for CI/CD. It demonstrates a serverless
**Spark Declarative Pipeline** (bronze → silver → gold, PySpark) and the multi-environment
DABs patterns that aren't obvious out of the box.

**Contents**

- [What this template includes](#what-this-template-includes) — the patterns it demonstrates and how the pieces fit.
- [Standing it up end-to-end](#standing-it-up-end-to-end) — what *you* configure to make it actually run in your own account.

---

## What this template includes

The patterns this repo demonstrates:

1. Declaring **variables** in `databricks.yml` and **deriving** them per environment.
2. Passing those variables into a **pipeline resource** — as resource fields (`catalog`)
   and via the `configuration` block (`env` / `quality_threshold`).
3. **Referencing** the configuration variables in pipeline code with `spark.conf.get(...)`.
4. Sharing a **`libraries/` helper package** across pipeline files on serverless.
5. Environment-appropriate **`run_as` identity** and **deploy location** (`root_path`).
6. A full **CI/CD pipeline** in GitHub Actions — PR validation (`ci.yml`) and a gated
   **staging → prod promotion** (`deploy.yml`) — authenticated via **OIDC** (no stored secret).

### The variable flow (the core idea)

```
databricks.yml  (variables, mostly DERIVED from the target name)
      │
      ▼
resources/pipelines.yml
      ├─ catalog: ${var.catalog}   ──►  pipeline's default catalog (dabs_cicd_<env>)
      └─ configuration: { env, quality_threshold }
                              │
                              ▼
src/*.py   spark.conf.get("env") / spark.conf.get("quality_threshold")
                              │
                              ▼
                        labels / filter logic / output data
```

#### Pipelines vs jobs

In a **job**, parameters arrive as `base_parameters` and are read with `dbutils.widgets`.
In a **pipeline**, they arrive in the pipeline's `configuration` block and are read with
`spark.conf.get(...)`. This repo uses the pipeline pattern.

### Variables derived from the target

The targets are named `dev` / `staging` / `prod`, and so are the catalogs, so the bundle
derives both `catalog` and `env` from `${bundle.target}` instead of repeating them per
target:

```yaml
variables:
  catalog:
    default: dabs_cicd_${bundle.target}   # → dabs_cicd_dev | dabs_cicd_staging | dabs_cicd_prod
  env:
    default: ${bundle.target}            # → dev | staging | prod
```

Adding or maintaining an environment is then just adding a target — there is no per-env
catalog/env wiring to keep in sync. Only `quality_threshold` is set per target, because it
is a genuine per-environment policy knob (not derivable from the name).

> Testing elsewhere: override the catalog without touching the file, e.g.
> `databricks bundle deploy -t dev --var="catalog=my_sandbox_catalog"`.

### What it builds

Tables are written **schema-qualified, one schema per medallion layer** (schema names are
consistent across all environments):

| Layer | File | Output table | Notes |
|-------|------|-------------|-------|
| Bronze | `src/bronze.py` | `bronze.trades` | generates 10 mock trades, stamps `env` from config |
| Silver | `src/silver.py` | `silver.trades` | computes `trade_value`, filters by `quality_threshold` |
| Gold | `src/gold.py` | `gold.portfolio_summary` | per-account totals |

All tables land in the target's catalog (`dabs_cicd_<env>`), in the `bronze` / `silver` /
`gold` schemas.

Because `quality_threshold` differs per target, the silver/gold row counts change by
environment with no code change:

| Target | quality_threshold | silver rows |
|--------|-------------------|-------------|
| dev | 0 | 10 |
| staging | 5000 | 8 |
| prod | 15000 | 7 |

### Shared library on serverless

`libraries/` is a normal Python package imported by the pipeline files
(`from libraries.config import get_conf`). It resolves on serverless because the pipeline
sets `root_path: ..` (the bundle root), which Lakeflow automatically adds to `sys.path`.
`pyproject.toml` is only for running the unit tests locally — it does **not** package the
library onto the cluster, and the `--editable` install pattern is deliberately avoided
because it no-ops on serverless.

### Environments, identity, and deploy location

Three separate workspaces (a single workspace also works — just point every target at it).

| Target | Mode | catalog | run_as | Deploys to (`root_path`) |
|--------|------|---------|--------|--------------------------|
| dev (default) | development | dabs_cicd_dev | the deploying developer | that developer's home (`/Workspace/Users/<dev>/.bundle/...`) |
| staging | (default) | dabs_cicd_staging | a **staging service principal** | the SP's home (`/Workspace/Users/<staging-sp>/.bundle/...`) |
| prod | production | dabs_cicd_prod | a **prod service principal** | the SP's home (`/Workspace/Users/<prod-sp>/.bundle/...`) |

Why this matters:

- **dev** uses **development mode**, whose defaults give you exactly what you want for
  shared dev work: each developer deploys to their **own** workspace home and the pipeline
  **runs as that developer**, so developers never overwrite each other. No `root_path` or
  `run_as` is configured — it's the mode default.
- **staging** is a **stable, CI-owned** environment, not a personal dev target. It has its
  **own service principal** that both deploys and runs it (via OIDC from CI), so
  integration tests execute against a reproducible, un-prefixed deployment rather than
  whatever a developer last pushed. That is why it is *not* development mode — it mirrors
  prod's identity model (minus the hardened `permissions` lock, so it stays easy to debug).
  This split matches databricks/mlops-stacks, where only `dev` is development mode.
- **prod** uses **production mode**: it runs as a **service principal** (not any
  individual), deploys to that SP's identity-neutral home, pins `git.branch: main` so it
  can only deploy from the release branch, and locks the deployment with `permissions`. In
  CI/CD the same SP performs the deploy, so the SP — not a human — owns the files.

### CI/CD: the full pipeline in GitHub Actions

The repo ships the complete `validate → deploy → run` lifecycle as two workflows, so nothing
reaches a workspace from a laptop:

**1. [`ci.yml`](.github/workflows/ci.yml) — on every pull request.** Runs the unit tests
(`pytest` over `libraries/`), then `databricks bundle validate` against **staging** and
**prod** in parallel. A broken bundle or a failing test fails the PR — it can't merge.

**2. [`deploy.yml`](.github/workflows/deploy.yml) — on merge to `main`.** A gated promotion:

```
merge to main
   │
   ▼
deploy-staging      validate → deploy → run   (the run IS the integration test)
   │  needs: (must succeed)
   ▼
deploy-prod         validate → deploy → run   (behind the `prod` Environment approval gate)
```

Staging deploys and runs first; prod starts **only if staging fully succeeds** (`needs:`),
and then only after the required reviewers on the `prod` GitHub **Environment** approve. This
is the recommended promotion order — stage before prod, humans gate prod — enforced in the
workflow rather than left to convention. (databricks/mlops-stacks achieves the same split by
routing `main` → staging and a `release` branch → prod; this repo keeps one protected branch
and chains the jobs.)

Every job authenticates as its environment's **service principal** using **OpenID Connect
(OIDC)** —
Databricks calls this **Workload Identity Federation**. There is **no Databricks secret
stored in GitHub**:

1. GitHub mints a short-lived **OIDC token** for the workflow run.
2. A **federation policy** on each service principal trusts that token, scoped to this exact
   repo + environment (`repo:<org>/<repo>:environment:prod` for the prod SP,
   `:environment:staging` for the staging SP).
3. The Databricks CLI exchanges the OIDC token for a short-lived Databricks token and
   deploys **as the SP** (`DATABRICKS_AUTH_TYPE: github-oidc`).

Why OIDC matters *for this project*: because the **SP authenticates the deploy, the SP owns
the deployed workspace files** — no human does. That is what makes the prod hardening in
`databricks.yml` (identity-neutral `root_path`, `run_as` the SP, `CAN_VIEW`-only for everyone
else) actually hold. And since the token is minted per-run and never stored, there is no
long-lived secret to leak or rotate.

The one-time setup (create the SP, its federation policy, and the GitHub Environment), a
worked example, and the honest "what was tested vs. referenced" notes are in
[`docs/prod-oidc-deploy.md`](docs/prod-oidc-deploy.md).

**References**

- [Databricks — workload identity federation for GitHub Actions (AWS)](https://docs.databricks.com/aws/en/dev-tools/auth/provider-github)
- [Databricks — configure a federation policy (AWS)](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-policy)
- [Databricks — GitHub Actions for Databricks (AWS)](https://docs.databricks.com/aws/en/dev-tools/ci-cd/github)
- [GitHub — about security hardening with OpenID Connect](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-security-hardening-with-openid-connect)

### Project structure

```
.
├── databricks.yml            # bundle name, derived variables, three targets
├── resources/
│   ├── pipelines.yml         # pipeline resource; wires variables in
│   └── jobs.yml              # scheduled job that triggers the pipeline
├── libraries/                # shared, importable helpers (not pipeline defs)
│   ├── config.py             # get_conf, parse_threshold
│   └── naming.py             # table_fqn, label_for_env
├── src/
│   ├── bronze.py             # generate mock trades       → bronze.trades
│   ├── silver.py             # enrich + quality filter    → silver.trades
│   └── gold.py               # aggregate                  → gold.portfolio_summary
├── tests/                    # pytest for libraries/ (local only)
├── docs/
│   └── prod-oidc-deploy.md   # SP + OIDC hardening: one-time setup & worked example
├── .github/
│   └── workflows/
│       ├── ci.yml            # PR: pytest + bundle validate (staging & prod)
│       └── deploy.yml        # merge→main: gated staging → prod promotion (OIDC)
└── pyproject.toml            # local test config only
```

---

## Standing it up end-to-end

Complete, runnable shell. Workflows, both GitHub Environments (`staging`, `prod`), and all four CI variables already exist. Variables hold obvious placeholders. You supply only what can't be committed: workspace URLs, service principals, OIDC trust policy.

Ordered easiest-first. Stop after step 4 for local dev. Steps 5 and 6 add CI-owned staging and hardened prod.

### 1. Prerequisites

- [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/install) installed and authenticated.
- At least one workspace. Up to three for fully separated dev / staging / prod.
- Rights to create catalogs/schemas in each target's catalog, or an existing catalog to point at (step 3).
- Prod/staging CI path only: account-admin rights to create a service principal + federation policy.

### 2. Clone and test

```bash
git clone <your-fork-url> && cd dabs-cicd-demo
pip install -e ".[dev]"
pytest                      # confirms the shared library works before touching any workspace
```

### 3. Point the bundle at your workspaces

Placeholders to replace in `databricks.yml`:

- `workspace.host` on each target. Swap the `<…-workspace>` values for real workspace URLs.
- Catalogs: default to `dabs_cicd_<env>`. If those don't exist, create them, override per target (see the `sandbox` target), or pass `--var catalog=<existing_catalog>` at deploy time.
- `prod_service_principal` / `staging_service_principal`. Filled in step 5.

### 4. Deploy dev yourself

Fastest path to "it works". Runs in development mode as you, in your own workspace home. No service principal or CI.

```bash
databricks bundle validate -t dev
databricks bundle deploy   -t dev
databricks bundle run dabs_cicd_pipeline -t dev
```

Proves the pipeline end-to-end before any SP/OIDC machinery. Staging and prod are CI-owned and deploy as service principals (step 5), not by hand.

### 5. Wire up the CI environments (staging + prod)

Staging and prod each authenticate from GitHub Actions as their own service principal via OIDC. GitHub-side shell is already scaffolded. Only real identities are missing.

**Already scaffolded, nothing to create:**

- **Environments** `staging` and `prod`. Attach required-reviewer protection to `prod` before going live: this is the human approval gate before `deploy.yml` touches prod.
- **Repository variables**, pre-filled with placeholders. Identifiers, not secrets, so plain variables. Workflows read them via `vars.*`:

  | Variable | Placeholder | Used by |
  |----------|-------------|---------|
  | `DATABRICKS_HOST` | `https://<prod-workspace>.cloud.databricks.com` | prod jobs |
  | `DATABRICKS_CLIENT_ID` | `<prod-service-principal-application-id>` | prod jobs |
  | `DATABRICKS_STAGING_HOST` | `https://<staging-workspace>.cloud.databricks.com` | staging jobs |
  | `DATABRICKS_STAGING_CLIENT_ID` | `<staging-service-principal-application-id>` | staging jobs |

**What you supply, repeated for each of `staging` and `prod`** (identical steps; only the environment name and SP differ). Steps 1 through 3 need account-admin. Exact CLI commands + worked example: [`docs/prod-oidc-deploy.md`](docs/prod-oidc-deploy.md).

1. **Create a service principal.** Put its application ID in the matching `databricks.yml` variable (`staging_service_principal` / `prod_service_principal`).
2. **Grant the SP** `USE CATALOG, CREATE SCHEMA` on that environment's catalog. Prod also declares a `CAN_MANAGE` `permissions` lock; staging is left open for easy debugging.
3. **Create the SP's federation policy.** Trusts GitHub's OIDC issuer, scoped to `repo:<org>/<repo>:environment:staging` (or `:environment:prod`).
4. **Replace the placeholder GitHub variables** with real values:

   ```bash
   gh variable set DATABRICKS_STAGING_HOST      --body "https://<your-staging-workspace>.cloud.databricks.com"
   gh variable set DATABRICKS_STAGING_CLIENT_ID --body "<your-staging-sp-app-id>"
   gh variable set DATABRICKS_HOST              --body "https://<your-prod-workspace>.cloud.databricks.com"
   gh variable set DATABRICKS_CLIENT_ID         --body "<your-prod-sp-app-id>"
   ```

Then the pipeline runs itself. Open a PR → `ci.yml` tests + validates. Merge to `main` → `deploy.yml` deploys/tests staging, then (after `prod` approval) deploys/runs prod. All as the SPs, no stored secret.

### 6. Operate

- Scheduled job (`resources/jobs.yml`) ships PAUSED in every environment. Remove the explicit `pause_status: PAUSED` line to restore dev-paused / prod-unpaused behavior.
- Tear down any target: `databricks bundle destroy -t <target>`.

### Caveats and decisions

Things to weigh before running this for real:

- **Merging to `main` costs compute.** `deploy.yml` deploys *and runs* the pipeline in staging, then prod, on every merge. Serverless spins up each time. The prod schedule ships paused, but the CI-triggered `bundle run` is not.
- **Variable scope is repo-level.** All four CI variables are readable by every job. For tighter isolation, move each pair to its matching Environment scope. `vars.*` resolves at either level, so no workflow change needed. Worth doing for real prod identifiers so unrelated workflows and fork PRs can't read them.
- **`git.branch: main` on prod gates deploy, not validate.** Prod refuses to deploy from any branch except `main`. `workflow_dispatch` from a feature branch, or a stale local checkout, will fail. Intended safeguard.
- **Staging is CI-only now.** Moving staging to `run_as` a service principal + SP-home `root_path` means a laptop `bundle deploy -t staging` no longer works cleanly. Want manual staging deploys? That friction is the tradeoff.
- **The staging "integration test" is shallow.** `bundle run` proves the pipeline builds and runs. It asserts nothing about correctness (row counts, schema, values). A pipeline that runs but emits wrong data still passes the gate. Add `execute_sql` assertions after the run for a real test.
- **`databricks/setup-cli@main` tracks a moving target.** A breaking CLI change could break the pipeline silently. Pin a version (e.g. `@v0.236.0`) for a real production repo.
- **OIDC path is unproven.** Local `bundle validate` was tested against a live workspace; the SP-over-OIDC auth was never run end-to-end (needs the SPs + federation policies created). First real Actions run is where federation subject mismatches or missing SP grants surface.
- **FEVM workspaces block GitHub-hosted runners.** Corp-egress-only IP ACL 403s the runner at the network layer. Use a non-FEVM workspace, or a self-hosted runner inside the allowed network.
