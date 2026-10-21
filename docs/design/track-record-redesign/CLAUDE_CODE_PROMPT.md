Read docs/design/track-record-redesign/README.md fully, then skim mockup.dc.html (the renderVals() method holds the exact computation and styling) and cohort-fixture.json (golden numbers).

This is an approved redesign of the Track record page. Do NOT implement anything yet.

1. Find the current Track record implementation in this repo (backend route/query that builds the checkpoint cohort, the page template/components, the CSV export, the checkpoint collector). Map each section of the spec (§3–§6) to the files that will change. Note anything in the spec that conflicts with the current schema or architecture and say how you'd resolve it.
2. Check existing labels and milestones: `gh label list`, `gh issue list --state open --limit 100`. Don't duplicate open issues (e.g. #106, #109); link them where related.
3. Create the issues from spec §8 with `gh issue create` in nhattruong0204/gmgn-revival-radar:
   - One epic issue "Track record page redesign" containing the goal, a link to the spec path, and a task list of the child issues.
   - One issue per row in §8. Each body: context (1–2 lines), concrete scope with the real file paths you found, acceptance criteria taken from §5/§7 (including the golden fixture values where relevant), dependencies as "Depends on #N", and out-of-scope notes.
   - Labels: reuse existing ones; create `track-record`, `frontend`, `backend`, `data` only if missing.
   - Create in dependency order so you can reference real issue numbers, then edit the epic's task list with the final numbers.
4. Commit the docs/design/track-record-redesign/ folder on a branch `docs/track-record-redesign` and open a PR referencing the epic. Don't merge.
5. Finish with a table: issue #, title, area, depends on.

Show me the full list of issue titles and the label plan before running any `gh issue create`, and wait for my OK.
