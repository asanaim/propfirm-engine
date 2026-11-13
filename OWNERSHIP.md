# Ownership record

Facts a buyer's lawyer needs to trace title to this repository. Counts are
read from the git history of `main` on 13 Nov 2026 and are updated when the
record is reviewed. Legal conclusions are not drawn here; they belong to
the owner's lawyer (decision D-13.16 in `docs/decisions.md`).

## Owner

| Item | Value |
| --- | --- |
| Owner and copyright holder | Md. Abu Sayed Naim (GitHub account `asanaim`, commit name "Sayed Naim") |
| Licence | Proprietary, all rights reserved (`LICENSE`) |
| Repository | `github.com/asanaim/propfirm-engine`, private |
| First commit | 29 Oct 2026 (`87e9f70`, repository skeleton) |
| Brand | the name "pipridge" and the assets in `brand/` |

## Human contributors

| Person | Role | Commits on `main` |
| --- | --- | --- |
| Md. Abu Sayed Naim | Owner. Set every requirement and decision, reviewed and merged every pull request | 84 merge commits (as "Sayed Naim" and "asanaim") |

No other person has committed to this repository.

## AI tools

| Tool | Provider | Used for | Evidence |
| --- | --- | --- | --- |
| Claude (model Claude Opus 5.5), through Claude Code and the Claude apps | Anthropic | Writing code, tests, documentation and specifications to the owner's instructions; research; the brand name screen and the logo, built as vector outlines with no font | All 109 non-merge commits carry `Co-Authored-By: Claude Opus 5.5`; 43 of them are authored as "Claude", 66 as "asanaim" |

No other AI tool produced material in this repository (owner's statement,
13 Nov 2026).

## Points for the buyer's review

1. Nearly all code and text in this repository was produced with an AI
   tool, directed and accepted by the owner. Whether and how far such
   material is protected by copyright differs between jurisdictions, and
   the AI provider's terms of service govern rights in its output. Both
   are for the owner's lawyer to confirm in writing.
2. Third-party software is used under its own licences: Python packages in
   `pyproject.toml` and `uv.lock`; browser packages in the
   `public/third-party-notices.txt` that each app's build writes
   (`portal/`, `backoffice/`).
   The licence scan of step 7 of the build plan produces a bill of
   materials for the data room.
3. Fonts: only free fonts under the SIL Open Font License are used. The
   logo and wordmark are vector outlines and contain no font.
4. Market data: synthetic prices only. Licensed prices (Twelve Data) are
   held only while subscribed and are not part of the repository
   (`CLAUDE.md`, data licensing).
5. No employee, contractor or third-party developer has contributed, so no
   assignment of rights from such a person is needed. This changes if one
   joins: each then signs an assignment before their first commit.

## Keeping this record

- Reviewed at each release and before any sale or licence.
- A new contributor, human or tool, is added before their first merged
  change.
