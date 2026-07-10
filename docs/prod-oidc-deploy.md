# Hardened prod deploys: service principal + OIDC

This document describes how the `prod` target is locked down so that **deployed bundle
artifacts cannot be modified or deleted by ordinary users**, and how to deploy prod from
GitHub Actions with **no stored Databricks secret** using Workload Identity Federation
(OIDC).

> **Status of this doc.** The hardening (SP as deploy identity → SP owns the files →
> users read-only) was **proven end-to-end** against the prod workspace using an OAuth
> **M2M secret** (see "What was actually tested" below). The **OIDC** variant here is a
> grounded reference — same auth mechanism, just secret-less — and is **not** executed
> against the internal sandbox environment (which enforces a corp-egress-only IP ACL that blocks
> GitHub-hosted runners). Apply it in a customer account where the account admin holds
> the required rights and the prod workspace has no such network restriction.

## The core idea

`run_as` only sets the identity a pipeline/job *runs* as. **File ownership comes from
whoever authenticates the `databricks bundle deploy` call.** So to ensure no human owns
or can tamper with prod artifacts, the **service principal must be the deployer**, not a
person. There are two ways for the SP to authenticate a deploy:

| | OAuth M2M secret | Workload Identity Federation (OIDC) |
|---|---|---|
| Credential | A long-lived client secret stored in CI | A short-lived token GitHub mints per run |
| Stored secret? | Yes (must be guarded/rotated) | **No** — nothing to leak |
| Recommendation | Acceptable | **Preferred** by Databricks |

Both authenticate the deploy *as the SP*, so both produce the same outcome: the SP owns
the deployed files. OIDC just removes the stored secret.

## What the `prod` target enforces (see `databricks.yml`)

- `run_as.service_principal_name` → pipeline runs as the SP.
- `workspace.root_path` → the SP's **own home** (`/Workspace/Users/<sp-app-id>/...`),
  which is identity-neutral (a service identity, not a person) **and** not world-writable
  — unlike `/Workspace/Shared`, which grants read/write to all workspace users.
- `permissions` → only the SP can `CAN_MANAGE`; everyone else is `CAN_VIEW` (read-only).

> **Honest caveat:** workspace/account **admins bypass object ACLs by design.** The real
> protection is: few admins + guarded SP credentials (CI secret store, or OIDC so there
> is no secret) + Git as the source of truth + audit logs. The ACL stops *ordinary*
> users; it does not stop admins.

## One-time setup (requires account admin)

### 1. Create the service principal

```bash
databricks account service-principals create --display-name "dabs-cicd-prod-runner"
# note the application ID (a UUID) and the service principal ID
```

### 2. Create the federation policy on the SP

Trusts GitHub's OIDC issuer for a specific repo + environment. Verified against the
Databricks CLI (`databricks account service-principal-federation-policy create`).

```bash
databricks account service-principal-federation-policy create <SERVICE_PRINCIPAL_ID> \
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

### 3. Grant the SP what it needs in the prod workspace

```sql
-- The pipeline runs as the SP, so the SP must be able to create the medallion schemas.
GRANT USE CATALOG, CREATE SCHEMA ON CATALOG <prod_catalog> TO `<sp-app-id>`;
```

Also ensure the SP is added to the prod workspace and the deploying identity (the SP
itself, via OIDC) has `CAN_MANAGE` on the bundle — which the `permissions` block in
`databricks.yml` provides.

### 4. Configure the GitHub repo

- Create a GitHub **Environment** named `prod` with required reviewers (this is your
  change-control gate — merges to `main` won't deploy until approved).
- Add repository **variables** (not secrets — neither value is sensitive with OIDC):
  - `DATABRICKS_HOST` = `https://<prod-workspace>.cloud.databricks.com`
  - `DATABRICKS_CLIENT_ID` = the SP's application ID (UUID)

### 5. The workflow

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

## Worked example — the values used in this demo

The mechanism was proven on the prod workspace with these concrete values (the SP
and grants were created live; the federation policy below is the OIDC equivalent of the
M2M secret that was actually used):

| Field | Value |
|---|---|
| Prod workspace | `https://<prod-workspace>.cloud.databricks.com` |
| Account ID | `<databricks-account-id>` |
| SP display name | `dabs-cicd-prod-runner` |
| SP application ID | `<sp-application-id>` |
| Repo | `JacksonReber/dabs-cicd-demo` |
| Prod catalog | `serverless_stable_prod_catalog` (override; real demo uses `dabs_cicd_prod`) |

So the federation policy for this repo would be:

```bash
databricks account service-principal-federation-policy create <dabs-cicd-prod-runner-sp-id> \
  --json '{
    "oidc_policy": {
      "issuer": "https://token.actions.githubusercontent.com",
      "audiences": ["<databricks-account-id>"],
      "subject": "repo:JacksonReber/dabs-cicd-demo:environment:prod"
    }
  }'
```

> In this demo's sandbox workspace the `dabs_cicd_*` catalogs can't be created (the sandbox blocks
> `CREATE CATALOG`), so deploys pass `--var catalog=serverless_stable_prod_catalog
> --var prod_service_principal=<sp-app-id>`. In a real prod account those become the
> committed defaults in `databricks.yml`, and the workflow's `databricks bundle deploy
> -t prod` runs clean with no overrides.

## What was actually tested vs. referenced

- **Tested live** against `<prod-workspace>`: created the SP; granted it
  `USE CATALOG, CREATE SCHEMA`; minted an OAuth M2M secret; deployed + ran the pipeline
  authenticated **as the SP**; verified the deployed files are owned by the SP with
  `users: CAN_READ` and `admins: CAN_MANAGE`.
- **Referenced (not executed)**: this OIDC federation policy + GitHub Actions workflow.
  Grounded in the Databricks CLI command shape and the docs below. Blocked from live
  testing internally by the sandbox's IP ACL (network) and account-admin requirements.

## Sources

- [Enable workload identity federation for GitHub Actions (AWS)](https://docs.databricks.com/aws/en/dev-tools/auth/provider-github)
- [Configure a federation policy (AWS)](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-policy)
- [Enable workload identity federation in CI/CD (AWS)](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-provider)
- [GitHub Actions for Databricks (AWS)](https://docs.databricks.com/aws/en/dev-tools/ci-cd/github)
