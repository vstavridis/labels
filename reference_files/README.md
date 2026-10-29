# reference_files/

Put the confidential reference files here (everything except this README is git-ignored):

* a copy of the production workbook `Σχισμενα.xlsx` (the app never edits files in this folder)
* the four DYMO `.label` templates (VS, PG, PG0, EX). Their names do not matter: the app recognises each
  template by its COUNTER prefix. If two files have the same prefix (e.g. a template and its "(1)" copy),
  choose one in `.env`: `VSPM_TEMPLATE_FILES=VS=<file name>.label`
* `scans/` – sample scanned production documents (PDF/JPG/PNG) for the pilot

Then run `vspm report` to regenerate `docs/REFERENCE_REPORT.md` and
`python -m pytest tests/test_reference_files.py` to test against them.
