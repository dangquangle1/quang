# quang

My public personal projects

## Layout

| Path | What it is |
|---|---|
| `bootstrap/` | One-time setup: S3 state bucket, GitHub OIDC provider, and the CI roles. Run locally once. Shared by all projects. |
| `projects/` | One directory per project — each an independent Terraform stack with its own state file and CI pipeline. See `projects/README.md`. |
| `.github/workflows/_terraform.yml` | Reusable pipeline engine: `plan` on PRs, gated `apply` on merge to `main`. |
| `.github/workflows/project-*.yml` | Per-project pipeline — triggers only on that project's paths, calls the engine. |
| `bootstrap/aws-setup.md` | Setup runbook (SSO → bootstrap → GitHub → deploy). |
| `claude-config/` | Snapshot of my Claude Code config (steering, subagent, settings). Copy into `~/.claude/` to use. |

## Projects
