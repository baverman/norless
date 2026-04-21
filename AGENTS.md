# Agent Instructions

Before editing code, tests, or project files, ask for explicit permission. Do not
jump directly to fixes, implementation, or file modifications unless the user has
clearly granted permission for that specific change.

Before creating a new file, check whether the path already exists with a filesystem
check such as `test -e <path>` or `ls -la <path>`. Do not rely only on `rg --files`,
`git ls-files`, or `git status`, because ignored or untracked user files may be
hidden from those commands.

If the path exists and is ignored or untracked, treat it as user-owned: read it
before editing, preserve its contents, and ask before replacing it.
