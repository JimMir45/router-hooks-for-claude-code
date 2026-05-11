# Contributing to Router Hook for Claude Code

Thanks for considering a contribution! This project is OSS-first and we welcome:

- Bug reports
- Feature ideas
- Pull requests for hook fixes / new hook types
- New adapters for other AI agents (Cursor, Codex, Cline, OpenHands, etc.)
- Documentation improvements
- Real-world dogfood stories

## Quick start for contributors

```bash
git clone https://github.com/JimMir45/router-hooks-for-claude-code
cd router-hooks-for-claude-code
./install.sh                    # installs to ~/.router-hook/
# edit hook/*.py
./tests/run-e2e.py              # run e2e test suite
./tests/run-combo.py            # run combo test suite
```

## What we look for in PRs

- **Small, focused diffs.** One concept per PR.
- **Tests for new behavior.** Add cases to `tests/cases/*.jsonl`.
- **No new pip dependencies.** Standard library only.
- **Backwards-compatible hook contract.** Existing users shouldn't have to re-install.
- **Clear commit messages.** Conventional Commits style: `feat:`, `fix:`, `docs:`, `refactor:`, etc.

## Areas that need help

| Area | What's needed |
|---|---|
| **Cursor / Cline adapters** | Translate hook spec → Cursor's `.cursorrules` / Cline rules |
| **Codex CLI adapter** | Map our 8-event model onto Codex's hook surface |
| **Local LLM support** | Better ollama / vllm integration for the router LLM |
| **Hook spec doc** | Formalize the contract so other tools can implement it |
| **Real-world case studies** | Share your router-logs (anonymized) for the README |

## Reporting bugs

Open an Issue with:
1. What you tried (prompt + framework)
2. What you expected
3. What happened (relevant `~/.claude/router-logs/*.log` excerpt if not sensitive)
4. Your environment (CC version, Python version, OS)

## Filing feature requests

Open a Discussion first. We'd rather talk about it before you write code, especially for new hook types or breaking changes.

## Discord / community

Coming soon. For now, GitHub Issues and Discussions are the channels.

## Code of Conduct

Be kind. Disagree on substance, not on people. Project owners reserve the right to remove abusive comments / contributors.

## License

By contributing, you agree your contributions are licensed under MIT (same as the project).
