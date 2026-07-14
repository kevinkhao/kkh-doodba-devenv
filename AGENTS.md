# AGENTS.md

# Project context

## Response style

- Terse. No preamble or trailing summaries.
- KISS: lead with the answer, then only the context needed to act on it. A few sentences
  or a tight list, not a write-up.
- Plain language by default. Show code/XML only when asked or when it's the deliverable,
  not to pad an explanation.
- One recommendation, not an options survey. Skip caveats and alternatives unless they
  change what to do.
- Never use the em dash ("—"). Use a comma, colon, parentheses, or a period instead.
- Answer from general Odoo knowledge first. Read source only when asked or when exact
  names are needed for a paste-ready deliverable.
- Ask for the Odoo version once per session if not stated (supported: 17.0, 18.0, 19.0).

## Version control

- Only ever write, commit, push, or open PRs against repos owned by your own GitHub
  account. Every other repo (360ERP, odoo, OCA, etc.) is read-only: never push or PR to
  it.
- Never propose git operations on your own. Perform them (commit, branch, push, PR) only
  when explicitly asked in that turn.
- When asked: work on a feature branch, never commit or push to the default branch
  directly, never force-push or skip hooks unless told to.

## Source repositories (browse on GitHub by version branch)

- Odoo Community + base addons: github.com/odoo/odoo, branch 17.0 / 18.0 / 19.0.
- Odoo Enterprise: github.com/odoo/enterprise, matching version branch (private,
  requires access).
- Odoo developer documentation: github.com/odoo/documentation, matching version branch,
  or odoo.com/documentation/<version>.
- OCA modules: github.com/OCA/<repo>, matching version branch.
- 360ERP modules: see reference_360erp_github.md.

## Looking things up

- Odoo core/base source + docs → github.com/odoo/odoo and github.com/odoo/documentation
  (version branch), or raw.githubusercontent.com for exact files.
- 360ERP team modules → github.com/360ERP/<repo> via the web UI or `gh` CLI.
- OCA modules → github.com/OCA/<repo> (version branch); check 360ERP's 360_community
  collection first if relevant.

## Citations

Render every file reference as a clickable GitHub link including the version branch and
`#Lline` when applicable, e.g.
`[res_users.py:169](https://github.com/odoo/odoo/blob/19.0/addons/auth_oauth/models/res_users.py#L169)`.

Render every commit reference as a clickable link to the commit URL, e.g.
`[c7fa8c9](https://github.com/odoo/enterprise/commit/c7fa8c9)`. Never emit bare SHAs.

Render every OCA / 360ERP / Odoo addon, module, or repo name as a clickable GitHub link:
repo root, or `tree/<branch>/<module>` when branch and path are known. OCA →
`https://github.com/OCA/<repo>/tree/<branch>/<module>`; 360ERP →
`https://github.com/360ERP/<repo>/tree/<branch>/<path>`; Odoo core/enterprise → matching
version branch. If a subfolder path wasn't verified live, flag that it may 404.

## Writing tests

Keep the tests concise and focused on the specific functionality being tested. Use
descriptive names for test methods and classes to clearly indicate what is being tested.
Group tests together as much as possible where it makes sense to use a single running
setup to test multiple related scenarios. Avoid unnecessary duplication of setup code
across tests.
