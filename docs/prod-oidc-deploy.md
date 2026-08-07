# Hardened prod deploys: service principal + OIDC

This document describes how the `prod` target is locked down so that **deployed bundle
artifacts cannot be modified or deleted by ordinary users**, and how to deploy prod from
GitHub Actions with **no stored Databricks secret** using Workload Identity Federation
(OIDC).

> **Status of this doc.** The **two-service-principal split** described here — deploy SP
> owns the files (CAN_MANAGE), run SP is `run_as` (CAN_RUN only) — was **proven
> end-to-end via GitHub OIDC** against a live workspace: the deploy succeeded, the
> pipeline ran as the run SP, and an authenticated write by the run SP into the deploy
> SP's folder was **denied with 403** (see "What was actually tested" below). Apply it in
> a customer account where the account admin holds the required rights and the prod
> workspace has no network restriction that blocks GitHub-hosted runners (some managed
> environments enforce a corp-egress-only IP ACL that 403s runners at the network layer).

## The core idea

Two things are separate, and prod uses **two service principals** to keep them separate —
the least-privilege split [Databricks recommends](https://docs.databricks.com/aws/en/developers/best-practices):

1. **Who owns the deployed files.** File ownership comes from **whoever authenticates the
   `databricks bundle deploy` call** — *not* from `run_as`. So the **DEPLOY SP** must be
   the deployer, and it ends up owning the files and holding `CAN_MANAGE`. This is the
   tamper-proofing. The deploy SP holds **no** Unity Catalog data grants.
2. **Who the pipeline runs as.** `run_as` sets the execution identity. The **RUN SP** holds
   the UC data grants (`USE CATALOG`, `CREATE SCHEMA`) and `CAN_RUN` on the files, but never
   `CAN_MANAGE` — it can read and execute the deployed code, but cannot tamper with it.

Splitting them means neither identity holds the other's power: a leaked run SP can't alter
prod artifacts; a leaked deploy SP can't touch prod data. It also gives independent
rotation and cleaner audit trails.

**The deploy SP authenticates via OIDC** (Workload Identity Federation) — GitHub mints a
short-lived token per run, so there is **no stored Databricks secret** to leak or rotate.
(An OAuth M2M client secret also works and produces the same file-ownership outcome, but
must be guarded and rotated; OIDC is preferred.)

### The three-tier permission model

The split relies on three distinct grants — don't conflate them:

| Grant | On what | Who gets it | Why |
|---|---|---|---|
| **`CAN_USE`** (Service Principal User) | the **run SP object** (account-level) | deploy SP | lets the deployer *assign* the run SP as `run_as` — deploy fails without it |
| **`CAN_RUN`** | the deployed **files** | run SP | read + execute the code; **required** — `CAN_VIEW` alone won't let it run |
| **`CAN_MANAGE`** | the deployed **files** | **deploy SP only** | modify/delete artifacts — this is the tamper-proofing |

> **Adding another CI-owned environment (e.g. staging) reuses this exact mechanism.** Any
> additional CI target gets its own deploy + run SP pair, an OIDC federation policy on the
> deploy SP — scoped to `repo:<org>/<repo>:environment:<name>` — the same `CAN_USE`
> cross-grant, and its own host/client-id repo variables. See the README "Caveats and
> decisions" section for the full set of changes to add a staging stage.

## What the `prod` target enforces (see `databricks.yml`)

- `run_as.service_principal_name` → pipeline runs as the **run SP**.
- `workspace.root_path` → the **deploy SP's** own home (`/Workspace/Users/<deploy-sp-app-id>/...`),
  which is identity-neutral (a service identity, not a person) **and** not world-writable
  — unlike `/Workspace/Shared`, which grants read/write to all workspace users.
- `permissions` → **deploy SP** `CAN_MANAGE`, **run SP** `CAN_RUN`, everyone else `CAN_VIEW`.

> **Honest caveat:** workspace/account **admins bypass object ACLs by design.** The real
> protection is: few admins + guarded SP credentials (CI secret store, or OIDC so there
> is no secret) + Git as the source of truth + audit logs. The ACL stops *ordinary*
> users; it does not stop admins.

## One-time setup (requires account admin)

### 1. Create the two service principals

```bash
databricks account service-principals create --display-name "dabs-cicd-deploy"
databricks account service-principals create --display-name "dabs-cicd-run"
# note each application ID (a UUID) AND numeric id — you need both below
```

Put the two application IDs into the `deploy_service_principal` and
`run_service_principal` variables in `databricks.yml`.

### 2. Grant the deploy SP `CAN_USE` on the run SP

This is the cross-grant that lets the deploy SP set `run_as` to the run SP. **The deploy
fails without it.** It is a grant on the run **SP object** (account-level), via the
account access-control rule-set API (read the current rule set for its `etag`, then add a
`servicePrincipal.user` grant for the deploy SP — keep any existing grants):

```bash
ACCT=<account-id>; RUN_APP=<run-sp-app-id>; DEPLOY_APP=<deploy-sp-app-id>
RS="accounts/$ACCT/servicePrincipals/$RUN_APP/ruleSets/default"

# read current rule set → note the returned "etag"
databricks account access-control get-rule-set "$RS" ""

# add servicePrincipal.user (= CAN_USE) for the deploy SP, preserving existing grants
databricks account access-control update-rule-set --json "{
  \"name\": \"$RS\",
  \"rule_set\": {
    \"name\": \"$RS\", \"etag\": \"<etag-from-get>\",
    \"grant_rules\": [
      { \"principals\": [\"servicePrincipals/$DEPLOY_APP\"], \"role\": \"roles/servicePrincipal.user\" }
    ]
  }
}"
```

### 3. Create the federation policy on the DEPLOY SP

Trusts GitHub's OIDC issuer for a specific repo + environment. The policy goes on the
**deploy SP** (it's the identity CI authenticates as). Verified against the Databricks CLI.

```bash
databricks account service-principal-federation-policy create <DEPLOY_SP_NUMERIC_ID> \
  --json '{
    "oidc_policy": {
      "issuer": "https://token.actions.githubusercontent.com",
      "audiences": ["<DATABRICKS_ACCOUNT_ID>"],
      "subject": "repo:<github-org>/<repo>:environment:prod"
    }
  }'
```

- **`issuer`** — always `https://token.actions.githubusercontent.com` for GitHub Actions.
- **`audiences`** — your Databricks **account ID**. (If omitted, the account ID is used
  by default; Databricks recommends setting it explicitly.)
- **`subject`** — must **exactly** match the OIDC token's `sub` claim. The
  `:environment:prod` form ties authentication to a GitHub **Environment** named `prod`,
  so you can attach required-reviewer protection. Other valid forms include
  `repo:<org>/<repo>:ref:refs/heads/main`.
- For a **reusable** workflow, set `"subject_claim": "job_workflow_ref"` and use the
  subject `"<org>/<repo>/.github/workflows/<file>.yml@refs/heads/main"`.

### 4. Grant each SP what it needs in the prod workspace

Add **both** SPs to the prod workspace, then grant the data privileges to the **run SP
only** (the deploy SP gets none — least privilege):

```sql
-- The RUN SP executes the pipeline, so it needs to create the medallion schemas.
GRANT USE CATALOG, CREATE SCHEMA ON CATALOG <prod_catalog> TO `<run-sp-app-id>`;
```

The deploy SP's `CAN_MANAGE` and the run SP's `CAN_RUN` on the bundle come from the
`permissions` block in `databricks.yml` — no manual grant needed.

### 5. Configure the GitHub repo

- Create a GitHub **Environment** named `prod` with required reviewers (this is your
  change-control gate — merges to `main` won't deploy until approved).
- Add repository **variables** (not secrets — neither value is sensitive with OIDC):
  - `DATABRICKS_HOST` = `https://<prod-workspace>.cloud.databricks.com`
  - `DATABRICKS_CLIENT_ID` = the **deploy SP's** application ID (UUID) — CI authenticates
    as the deploy SP; the run SP is named only in `databricks.yml`'s `run_as`.

### 6. The workflow

See [`.github/workflows/deploy-prod.yml`](../.github/workflows/deploy-prod.yml). Key
elements (verified against Databricks docs):

```yaml
permissions:
  id-token: write
  contents: read
env:
  DATABRICKS_AUTH_TYPE: github-oidc
  DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST }}
  DATABRICKS_CLIENT_ID: ${{ vars.DATABRICKS_CLIENT_ID }}
steps:
  - uses: actions/checkout@v4
  - uses: databricks/setup-cli@main
  - run: databricks bundle deploy -t prod
  - run: databricks bundle run dabs_cicd_pipeline -t prod
```

## What was actually tested vs. referenced

The full two-SP split was **proven end-to-end via GitHub OIDC** against a live workspace
(`JacksonReber/dabs-cicd-demo`, `environment:prod`). What was verified:

1. **OIDC deploy succeeded** — the deploy SP authenticated with no stored secret (GitHub
   minted the token per run), deployed the bundle, and assigned `run_as` to the *separate*
   run SP. The deploy setting `run_as` to another SP is itself proof the `CAN_USE`
   cross-grant (step 2) was in place — it fails without it.
2. **Tamper-proofing holds** — the deployed folder's ACL resolved to
   `deploy SP: CAN_MANAGE`, `run SP: CAN_RUN`, `users: CAN_READ`, `admins: CAN_MANAGE`.
   Files live in the **deploy SP's** home.
3. **Pipeline ran as the run SP** — `run_as` = run SP; the medallion schemas it created
   (`bronze`/`silver`/`gold`) are **owned by the run SP**, confirming its UC grants (not
   the deploy SP's) are what made the run succeed.
4. **The run SP genuinely cannot tamper** — authenticated *as the run SP*, a write into
   the deploy SP's folder was **denied `403 PERMISSION_DENIED`** ("Missing required
   permissions [Manage]"), while a read of the same folder **succeeded**. So the run SP
   can execute the deployed code but cannot modify it — least privilege, empirically.

Two learnings worth calling out (both are baked into `databricks.yml`):

- The run SP needs an **explicit `CAN_RUN`** in the `permissions` block. `CAN_VIEW`/read
  alone does not permit execution.
- The `CAN_USE` (Service Principal User) grant is **on the run SP object at the account
  level** — not a workspace-file ACL. It's what lets the deployer assign `run_as`.

> **Honest caveat (still true):** workspace/account **admins bypass object ACLs by
> design.** The 403 above confirms the run SP is an *ordinary* (non-admin) identity, which
> is the whole point. The real protection is few admins + guarded SP credentials (OIDC, so
> no secret) + Git as source of truth + audit logs — the ACL stops ordinary users, not admins.

## Sources

- [Enable workload identity federation for GitHub Actions (AWS)](https://docs.databricks.com/aws/en/dev-tools/auth/provider-github)
- [Configure a federation policy (AWS)](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-policy)
- [Enable workload identity federation in CI/CD (AWS)](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-provider)
- [GitHub Actions for Databricks (AWS)](https://docs.databricks.com/aws/en/dev-tools/ci-cd/github)
