# UI/UX Pro Max core snapshot

This directory contains only the offline core data and Python search scripts imported from
`nextlevelbuilder/ui-ux-pro-max-skill` release `v2.15.0`, commit
`a38d04c3d5c298c851dbe5e6ee1965ee3de42cb5`.

The upstream installer, templates, sibling skills, package-manager metadata, image-generation
helpers, and project-writing workflows are intentionally excluded. JobSlayer invokes only
`scripts/search.py --json` through `UIUXProMaxAdvisor`; provider output is advisory evidence and
cannot mutate SUID, workflow state, source code, permissions, verification, or activation.

The exact included tree is locked by `integrations/ui-ux-pro-max/lock.json`. Do not edit the
snapshot in place. Import a new version into a new version directory, retain its upstream license,
update the lock in a reviewed change, and run `./jobslayer check`.

The upstream MIT license is retained at `2.15.0/LICENSE`.
