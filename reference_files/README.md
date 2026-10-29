# reference_files/

Put the confidential reference files here (they are git-ignored):

* `Σχισμενα.xlsx` – the production workbook (a copy; the app never edits files here)
* `ΕΤΙΚΕΤΑ ΚΟΜΜΕΝΑ-1.label` and/or `ΕΤΙΚΕΤΑ ΚΟΜΜΕΝΑ-1(1).label` – VS
* `ΕΤΙΚΕΤΑ ΠΑΓΟΥΝΗ-1.label` – PG
* `ΕΤΙΚΕΤΑ ΠΑΓΟΥΝΗ-2.label` – PG0
* `ΕΤΙΚΕΤΑ ΕΞΑΓΩΓΕΣ.label` – EX
* `scans/` – sample scanned production documents (PDF/JPG/PNG)

Then run `vspm report` to regenerate `docs/REFERENCE_REPORT.md` and
`python -m pytest tests/test_reference_files.py` to test against them.
