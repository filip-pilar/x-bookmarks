# Working instructions

- Lead with the answer and keep communication concise. Distinguish implemented, verified and published work.
- Complete authorized work. Ask only for material missing information or authority.
- Never write unit tests. Use the existing TypeScript, build, Python syntax and release checks.
- Keep implementation and documentation tied to current requirements and concrete failure modes.

## Repository

- Source lives in `plugin/`; generated binaries and bundles are not committed.
- Never commit personal Client IDs, configuration, OAuth tokens, service capabilities, models, libraries or downloaded media.
- Public releases use each recipient's own X app, credits and account.
- Read `docs/KNOWN_ISSUES.md` before claiming installation, import or media processing works.
- Setup and updates finish paused. Do not start imports, retries, enrichment or schedules without authorization.

## Validation and releases

- Run `npm run check` and `npm run build` from `plugin/` for TypeScript changes.
- Check Python syntax with `python3 -m compileall -q plugin/python plugin/scripts plugin/vendor/transcribe/scripts scripts`.
- Run `python3 scripts/check-release.py` for source version consistency.
- Version with `python3 scripts/bump-version.py MAJOR.MINOR.PATCH`; follow `docs/RELEASING.md` for builds and tags.
- Public artifact validation: `python3 scripts/check-release.py --archive release/bookmark-library-release.zip`.
- Use Conventional Commits and `codex/` for new task branches. Never overwrite published release assets or move published tags.
