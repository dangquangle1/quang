# projects

One directory per project — each an **independent Terraform stack** with its own
state file and its own CI pipeline. A change to one project never plans or
applies another.

## Add a new project

Copy the [`_template/`](_template) scaffold — never an existing project.

1. `cp -r projects/_template projects/<name>`
2. In `<name>/backend.tf`, replace `PROJECT_NAME` in the state key with `<name>`.
3. In `<name>/providers.tf`, set the `region` and replace `PROJECT_NAME` in the
   `Project` tag.
4. Move the workflow out: copy `<name>/workflow.yml.tmpl` to
   `.github/workflows/project-<name>.yml`, replace `PROJECT_NAME` throughout, then
   delete `<name>/workflow.yml.tmpl`.
5. Add your resources in `<name>/main.tf`.
6. Locally: `cd projects/<name> && cp backend.hcl.example backend.hcl`
   (fill the bucket), then `terraform init -backend-config=backend.hcl`.

All projects share the one state bucket and the two CI roles from `bootstrap/` —
each just uses a different state key.
