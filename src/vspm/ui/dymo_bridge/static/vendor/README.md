# Vendored: DYMO Connect Framework

* File: `dymo.connect.framework.js`
* Source: https://github.com/dymosoftware/dymo-connect-framework (branch `master`), downloaded 29/10/2026
* SHA-256: `717ad8a2488d8d936709dc99dde012f4af4d6a407e4d2ba0d4d2ba47e5c43888`
* License: MIT, Copyright (c) 2020 SANFORD L.P. — see `LICENSE-dymo-connect-framework.txt`
* Compatible with DYMO Label Software (v8, web service 8.5+) and DYMO Connect (per DYMO's README).

The file is served to the browser from the app itself (no third-party CDN at run time).
Update procedure: download the new file, check the diff, update the SHA-256 above and
`EXPECTED_FRAMEWORK_SHA256` in `src/vspm/ui/dymo_bridge/__init__.py`, run the browser tests.
