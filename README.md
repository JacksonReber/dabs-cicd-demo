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
6. Deploying prod from **GitHub Actions** authenticated via **OIDC** (no stored secret).

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
| staging | development | dabs_cicd_staging | the deploying developer | that developer's home |
| prod | production | dabs_cicd_prod | a **service principal** | shared (`/Workspace/Shared/.bundle/...`) |

Why this matters:

- **dev / staging** use **development mode**, whose defaults give you exactly what you want
  for shared dev work: each developer deploys to their **own** workspace home and the
  pipeline **runs as that developer**, so developers never overwrite each other. No
  `root_path` or `run_as` is configured for these targets — it's the mode default.
- **prod** uses **production mode**: it deploys to a **shared, identity-neutral location**
  and runs as a **service principal** (not any individual), which is the secure production
  pattern. In CI/CD the same SP should perform the deploy.

### CI/CD: deploying prod from GitHub Actions

Prod is deployed by **GitHub Actions**, not from a laptop. The workflow in
[`.github/workflows/deploy-prod.yml`](.github/workflows/deploy-prod.yml) runs `databricks
bundle deploy -t prod` (then runs the pipeline) on every push to `main` and on manual
dispatch — gated behind a GitHub **Environment** named `prod`, where you attach
required-reviewer rules so a merge only deploys after approval.

The deploy authenticates as the **prod service principal** using **OpenID Connect (OIDC)** —
Databricks calls this **Workload Identity Federation**. There is **no Databricks secret
stored in GitHub**:

1. GitHub mints a short-lived **OIDC token** for the workflow run.
2. A **federation policy** on the service principal trusts that token, scoped to this exact
   repo + environment (`repo:<org>/<repo>:environment:prod`).
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
│       └── deploy-prod.yml   # GitHub Actions: OIDC deploy to the prod target
└── pyproject.toml            # local test config only
```

---

## Standing it up end-to-end

The repo gives you the *pattern* and a *runnable workflow*, but the workspace hosts, the
service principal, and the OIDC trust are yours to fill in — you can't commit someone else's
identity or trust policy. Here is the path from a fresh clone to a working multi-environment
deploy, **easiest first**. You can stop after step 4 if you only want dev/staging; steps 5–6
add the hardened, automated prod path.

### 1. Prerequisites

- The [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/install) installed and authenticated.
- Access to at least one Databricks workspace (up to three if you want dev / staging / prod
  fully separated).
- The ability to create catalogs/schemas in each target's catalog — or an existing catalog
  to point at instead (see step 3).
- **For the prod path only:** account-admin rights to create a service principal and its
  federation policy.

### 2. Get the code and run the tests

```bash
git clone <your-fork-url> && cd dabs-cicd-demo
pip install -e ".[dev]"
pytest                      # confirms the shared library works before touching any workspace
```

### 3. Point the bundle at your workspaces

In `databricks.yml`:

- Set each target's `workspace.host` (replace the `<…-workspace>` placeholders).
- Decide catalogs. By default they derive to `dabs_cicd_<env>`; if those don't exist, either
  create them, override per target (the `sandbox` target shows the override pattern), or pass
  `--var catalog=<existing_catalog>` at deploy time.

### 4. Deploy dev / staging yourself — the fastest path to "it works"

```bash
databricks bundle validate -t dev
databricks bundle deploy   -t dev
databricks bundle run dabs_cicd_pipeline -t dev
```

These run in **development mode as you**, in your own workspace home — no service principal
or CI required. Swap `-t dev` for `-t staging` for the other environment. This proves the
pipeline end-to-end before you wire up any prod/OIDC machinery.

### 5. Set up hardened prod (service principal + OIDC) — the CI path

1. **Create the prod service principal** and put its application ID in
   `prod_service_principal` in `databricks.yml`.
2. **Grant the SP** `USE CATALOG, CREATE SCHEMA` on the prod catalog (plus `CAN_MANAGE` on
   the deployed bundle — the `permissions` block already declares this).
3. **Create the SP's federation policy** trusting GitHub's OIDC issuer, scoped to **your**
   `repo:<org>/<repo>:environment:prod`.
4. **In GitHub:** create an Environment named `prod` (add required reviewers for change
   control), and add repository variables `DATABRICKS_HOST` and `DATABRICKS_CLIENT_ID` (both
   are identifiers, not secrets).
5. **Push to `main`** (or run the workflow manually) → GitHub Actions deploys and runs prod
   **as the SP**, with no stored secret.

Exact CLI commands and a worked example are in
[`docs/prod-oidc-deploy.md`](docs/prod-oidc-deploy.md).

### 6. Operate

- The scheduled job (`resources/jobs.yml`) ships **paused** in every environment. Remove the
  explicit `pause_status: PAUSED` line to restore the dev-paused / prod-unpaused behavior.
- Tear down any target with `databricks bundle destroy -t <target>`.
