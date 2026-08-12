# quang — Claude Code personal setup

## How I work

1. **Delegate exploration.** For research, code exploration, reading many files,
   or "how does X work" investigation, delegate to the `readonly-worker`
   subagent so it runs unattended and reports back. Reserve writes and shell
   commands for the main session — this one — under my approval.
2. **Prefer an oracle over reasoning.** Before deep analysis, check whether a
   fast, authoritative command (a test, compile, build, or `terraform plan`) can
   answer the question directly. Run it instead of speculating — feedback from an
   actual run beats reasoning toward the answer.
3. **Gather context before changing code.** Make changes yourself only after you
   understand the surrounding code. Keep me in control of writes and commands.

## Steering

@steering/coding-guidelines.md
