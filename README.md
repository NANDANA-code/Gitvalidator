# GAV - Git Accessibility Validator

Blocks any Pull Request from merging into `main` if it contains
accessibility violations in HTML, Markdown, or DITA files.

## How It Works

1. Developer opens a Pull Request targeting `main`
2. GitHub Actions triggers GAV automatically
3. Changed files detected via `git diff origin/main...HEAD`
4. Three engines run:
   - HTML → axe-core/cli (WCAG 2.1 AA)
   - Markdown → markdownlint-cli
   - DITA/XML → custom lxml Python parser
5. Any violation found → merge is blocked

## DITA Accessibility Rules

| Rule | Element | Requires | WCAG |
|---|---|---|---|
| DITA-A11Y-001 | image | alt child | 1.1.1 |
| DITA-A11Y-002 | table | thead child | 1.3.1 |
| DITA-A11Y-003 | fig | title child | 1.1.1 |

## Setup

```bash
npm install
pip3 install lxml
```

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | All checks passed - merge allowed |
| 1 | Violations found - merge blocked |