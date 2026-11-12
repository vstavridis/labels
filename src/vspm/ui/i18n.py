"""Greek (default) and English interface strings."""

from __future__ import annotations

STRINGS: dict[str, tuple[str, str]] = {
    # navigation
    "app_title": ("VS Production Manager", "VS Production Manager"),
    "nav_dashboard": ("Πίνακας ελέγχου", "Dashboard"),
    "nav_recognition": ("Αναγνώριση εγγράφου", "Document recognition"),
    "nav_review": ("Έλεγχος γραμμών", "Row review"),
    "nav_print": ("Ετικέτα & εκτύπωση", "Label & print"),
    "nav_history": ("Ιστορικό", "History"),
    "nav_printer": ("Εκτυπωτής DYMO", "DYMO printer"),
    "auth_misconfigured": ("Η σύνδεση εργαζομένων δεν έχει ρυθμιστεί — η εφαρμογή παραμένει κλειστή.",
                           "Employee login is not configured - the app stays closed."),
    "auth_login_needed": ("Συνδεθείτε με τον λογαριασμό εργασίας σας.", "Sign in with your work account."),
    "auth_login": ("Σύνδεση", "Sign in"),
    "auth_denied": ("Ο λογαριασμός {e} δεν έχει πρόσβαση.", "The account {e} has no access."),
    "auth_logout": ("Αποσύνδεση", "Sign out"),
    "dymo_templates": ("Πρότυπα DYMO", "DYMO templates"),
    "cs_stamp_title": ('Barcode πάνω στις σαρώσεις (Google Drive)', 'Barcodes written onto the scans (Google Drive)'),
    "cs_stamp_help": ('Μετά την πρώτη εκτύπωση το barcode γράφεται στη στήλη BARCODE της γραμμής, στη φωτογραφία στο Google Drive. Η αρχική σάρωση μένει στο ιστορικό εκδόσεων του αρχείου (Διαχείριση εκδόσεων). Αν ο πίνακας δεν αναγνωριστεί, τα barcode γράφονται σε λωρίδα κάτω από τη φωτογραφία.', "After the first print the barcode is written into the row's BARCODE cell of the photo in Google Drive. The original scan stays in the file's version history (Manage versions). If the table is not recognised, the barcodes are listed in a strip under the photo."),
    "cs_stamp_mode": ('Τρόπος', 'Mode'),
    "cs_stamp_now": ('Εγγραφή barcode στις σαρώσεις τώρα ({n})', 'Write barcodes onto scans now ({n})'),
    "cs_from_app": ('Αλλάχτηκε στην εφαρμογή — υπερισχύει των Secrets', 'Changed in the app — overrides the Secrets'),
    "cs_from_secrets": ('Από τα Streamlit Secrets', 'From the Streamlit Secrets'),
    "cs_reset_to_secrets": ('Επαναφορά {n} τιμών από τα Secrets', 'Reset {n} value(s) to the Secrets'),
    "fill_help": ("⬇ Συμπλήρωση στήλης: η τιμή της 1ης γραμμής μπαίνει σε όλες τις άλλες (μη τυπωμένες) γραμμές.", "⬇ Fill column: the first row's value goes into all other (unprinted) rows."),
    "fill_one_help": ("Βάζει «{v}» στη στήλη {c} όλων των γραμμών που δεν έχουν τυπωθεί.", "Puts '{v}' into {c} of every row not printed yet."),
    "filled": ('Συμπληρώθηκαν {n} κελιά στη στήλη {c}.', 'Filled {n} cell(s) in {c}.'),
    "excel_onedrive_on": ('Excel: οι γραμμές γράφονται αυτόματα στο Σχισμενα.xlsx του OneDrive μετά την εκτύπωση (Ρυθμίσεις → Πηγές cloud).', 'Excel: rows are written automatically to Σχισμενα.xlsx on OneDrive after printing (Settings → Cloud sources).'),
    "excel_onedrive_off": ('Excel: η εγγραφή στο βιβλίο του OneDrive είναι σε παύση (Ρυθμίσεις → Πηγές cloud) ή δεν έχει οριστεί ο σύνδεσμος· οι γραμμές μένουν εδώ σε αναμονή.', 'Excel: writing to the OneDrive workbook is paused (Settings → Cloud sources) or no link is set; rows wait here.'),
    "autosaved": ("Οι αλλαγές αποθηκεύονται αυτόματα. Οι τιμές με ⚠ επιβεβαιώνονται όταν τυπώσετε τις ετικέτες.", "Changes are saved automatically. Values marked ⚠ are confirmed when you print the labels."),
    "print_all": ("Εκτύπωση DYMO / Excel", "Print DYMO / Excel"),
    "print_all_help": ("Τυπώνει μία μία όλες τις ετικέτες αυτού του εγγράφου που δεν έχουν τυπωθεί ({n}) και "
                       "γράφει κάθε μία στο Excel. Το πάτημα επιβεβαιώνει και τις τιμές με ⚠.",
                       "Prints one by one every label of this document not printed yet ({n}) and writes each one to "
                       "Excel. Pressing it also confirms the values marked ⚠."),
    "all_printed": ("Όλες οι ετικέτες αυτού του εγγράφου έχουν τυπωθεί.", "Every label of this document is printed."),
    "batch_progress": ("Εκτύπωση {i} από {n}…", "Printing {i} of {n}…"),
    "batch_done": ("Τέλος: τυπώθηκαν {ok} από {n} ετικέτες.", "Done: {ok} of {n} labels printed."),
    "batch_stopped": ("Η σειρά εκτύπωσης σταμάτησε στο {b}: {e}. Ελέγξτε τον εκτυπωτή και πατήστε ξανά "
                      "«Εκτύπωση DYMO / Excel» για τις υπόλοιπες.",
                      "Printing stopped at {b}: {e}. Check the printer and press Print DYMO / Excel again for the rest."),
    "batch_waiting": ("Η ετικέτα δεν στάλθηκε ακόμη: {e}. Ελέγξτε τον DYMO και πατήστε «Δοκιμή ξανά» στο πλαίσιο "
                      "του εκτυπωτή· η σειρά συνεχίζει μόνη της.",
                      "The label was not sent yet: {e}. Check the DYMO and press Try again in the printer box; the "
                      "series then goes on by itself."),
    "batch_stop": ("Διακοπή σειράς", "Stop the series"),
    "series_missing": ("Δεν βρέθηκε ο τελευταίος αριθμός barcode της σειράς {s}: ούτε μετρητής στο αρχείο DYMO "
                       "ούτε barcode {s} στη στήλη H του Excel. Ελέγξτε τις Ρυθμίσεις → Πηγές cloud (πρότυπα, "
                       "Excel) ή ορίστε τον αριθμό στις Ρυθμίσεις → Σειρές barcode.",
                       "The last barcode of series {s} was not found: no counter in the DYMO file and no {s} "
                       "barcode in column H of the workbook. Check Settings → Cloud sources (templates, workbook) "
                       "or set the number in Settings → Barcode series."),
    "series_auto_set": ("Σειρά {s}: συνεχίζει από {b} (βάσει {src}).", "Series {s}: continues from {b} (from {src})."),
    "print_all_needs_station": ("Ενεργοποιείται όταν αυτός ο υπολογιστής έχει τυπώσει και επιβεβαιώσει το TEST0001 "
                                "(σελίδα «Εκτυπωτής DYMO»).",
                                "Available once this PC printed and confirmed TEST0001 (page DYMO printer)."),
    "series_template_counters": ("Μετρητές όπως είναι αποθηκευμένοι στα αρχεία DYMO (για βοήθεια· ο πραγματικός "
                                 "τελευταίος αριθμός μπορεί να είναι μεγαλύτερος αν το αρχείο δεν αποθηκεύτηκε μετά "
                                 "την τελευταία εκτύπωση):",
                                 "Counters as saved in the DYMO files (a hint: the real last number may be higher if "
                                 "the file was not saved after the last print):"),
    "series_not_set": ("δεν έχει οριστεί", "not set"),
    "batch_skipped": ("Γραμμή #{i} δεν τυπώθηκε: {e}", "Row #{i} not printed: {e}"),
    "cloud_sources": ('Πηγές cloud', 'Cloud sources'),
    "cs_intro": ('Τα πρότυπα DYMO και το Σχισμενα.xlsx διαβάζονται από το OneDrive σας, οι σαρώσεις από το Google Drive. Τα αρχικά αρχεία δεν αλλάζουν ποτέ (εκτός από το Excel, μόνο αν ενεργοποιηθεί η αυτόματη εγγραφή).', 'DYMO templates and Σχισμενα.xlsx are read from your OneDrive, scans from Google Drive. Original files are never changed (except the workbook, only if automatic writing is enabled).'),
    "cs_templates_url": ('Φάκελος OneDrive με τα πρότυπα DYMO (σύνδεσμος)', 'OneDrive folder with the DYMO templates (link)'),
    "cs_template_files": ('Ονόματα αρχείων (VS=…;PG=…;PG0=…;EX=…) — κενό = αναγνώριση από τον COUNTER', 'File names (VS=…;PG=…;PG0=…;EX=…) — empty = detect by COUNTER'),
    "cs_workbook_url": ('Βιβλίο Excel παραγωγής στο OneDrive (σύνδεσμος)', 'Production Excel workbook on OneDrive (link)'),
    "cs_gdrive_url": ('Φάκελος Google Drive με τις σαρώσεις (σύνδεσμος)', 'Google Drive folder with the scans (link)'),
    "cs_ms_account": ('Λογαριασμός Microsoft', 'Microsoft account'),
    "cs_connect": ('Σύνδεση λογαριασμού Microsoft', 'Connect Microsoft account'),
    "cs_device_help": ('Ανοίξτε {u} , πληκτρολογήστε τον κωδικό **{c}** και συνδεθείτε με τον λογαριασμό που έχει το OneDrive. Μετά πατήστε «Συνδέθηκα».', "Open {u} , enter the code **{c}** and sign in with the account that owns the OneDrive. Then press 'I have signed in'."),
    "cs_signed_in": ('Συνδέθηκα', 'I have signed in'),
    "cs_connected": ('Συνδέθηκε: {a}', 'Connected: {a}'),
    "cs_disconnect": ('Αποσύνδεση Microsoft', 'Disconnect Microsoft'),
    "cs_templates": ('Πρότυπα DYMO από το OneDrive', 'DYMO templates from OneDrive'),
    "cs_test_onedrive": ('Έλεγχος σύνδεσης OneDrive', 'Test OneDrive connection'),
    "cs_refresh_templates": ('Ανανέωση προτύπων DYMO', 'Refresh DYMO templates'),
    "cs_folder_ok": ('Φάκελος «{f}»: {n} αρχεία .label', "Folder '{f}': {n} .label files"),
    "cs_last_refresh": ('Τελευταία ανανέωση: {t} {ok}', 'Last refresh: {t} {ok}'),
    "cs_file": ('Αρχείο', 'File'),
    "cs_last_sync": ('Τελευταίος συγχρονισμός', 'Last sync'),
    "cs_error": ('Σφάλμα', 'Error'),
    "cs_workbook": ('Βιβλίο Excel στο OneDrive', 'Excel workbook on OneDrive'),
    "cs_excel_mode": ('Λειτουργία Excel: {m} — {p}', 'Excel mode: {m} — {p}'),
    "cs_prod_on": ("κάθε τυπωμένη ετικέτα γράφεται αυτόματα στο βιβλίο παραγωγής", "every printed label is written to the production workbook automatically"),
    "cs_prod_off": ("ΣΕ ΠΑΥΣΗ: οι γραμμές μένουν σε αναμονή μέχρι να ενεργοποιηθεί ξανά", "PAUSED: rows wait until writing is switched on again"),
    "cs_test_workbook": ('Έλεγχος πρόσβασης στο Excel', 'Test Excel workbook access'),
    "cs_workbook_ok": ('{name}: {size} bytes, φύλλο {sheet}, τελευταία γραμμή {last_row}, {barcodes} barcode, τύποι L–O: {formulas_L_O_in_last_row}', '{name}: {size} bytes, sheet {sheet}, last row {last_row}, {barcodes} barcodes, formulas L–O: {formulas_L_O_in_last_row}'),
    "cs_prod_toggle": ("Αυτόματη εγγραφή στο βιβλίο παραγωγής (OneDrive)", "Automatic writing to the production workbook (OneDrive)"),
    "cs_sync_now": ('Συγχρονισμός γραμμών σε αναμονή τώρα', 'Sync pending rows now'),
    "cs_backups": ('Αντίγραφα ασφαλείας του βιβλίου ({n})', 'Workbook backups ({n})'),
    "cs_gdrive_info": ('Φάκελος: {f} · λειτουργία: {m} · λογαριασμός υπηρεσίας: {sa}', 'Folder: {f} · mode: {m} · service account: {sa}'),
    "cs_test_gdrive": ('Έλεγχος σύνδεσης Google Drive', 'Test Google Drive connection'),
    "cs_refresh_scans": ('Ανανέωση τελευταίων σαρώσεων', 'Refresh latest scans'),
    "cs_gdrive_ok": ('{n} πρόσφατες σαρώσεις', '{n} latest scans'),
    "cs_gdrive_share": ('Μοιραστείτε τον φάκελο στο Google Drive με {sa} (Viewer).', 'Share the Google Drive folder with {sa} (Viewer).'),
    "excel_cloud_note": ("Στο cloud η εφαρμογή δεν γράφει στο Σχισμενα.xlsx του OneDrive. Οι ετικέτες μένουν εδώ "
                         "«σε αναμονή Excel»: κατεβάστε τις γραμμές, περάστε τις στο βιβλίο και ανεβάστε ένα αντίγραφο "
                         "για έλεγχο.",
                         "On the cloud the app does not write the OneDrive Σχισμενα.xlsx. Labels stay here as "
                         "'waiting for Excel': download the rows, enter them in the workbook and upload a copy to "
                         "check."),
    "reconcile_upload": ("Αντίγραφο του Σχισμενα.xlsx (από το OneDrive) για έλεγχο καταχώρησης",
                         "Copy of Σχισμενα.xlsx (from OneDrive) to check what is already entered"),
    "reconcile_uploaded": ("Έλεγχος αντιγράφου", "Check the copy"),
    "barcode_series": ("Σειρές barcode", "Barcode series"),
    "cloud_settings_readonly": ("Στο cloud οι ρυθμίσεις ορίζονται στα Secrets της φιλοξενίας (Streamlit Cloud → "
                                "App settings → Secrets). Εδώ εμφανίζονται μόνο για έλεγχο.",
                                "On the cloud, settings come from the hosting secrets (Streamlit Cloud → App "
                                "settings → Secrets). They are shown here read-only."),
    "admin_only": ("Μόνο οι διαχειριστές (VSPM_ADMIN_EMAILS) μπορούν να αλλάξουν αυτά.",
                   "Only administrators (VSPM_ADMIN_EMAILS) can change these."),
    "templates_help": ("Τα πρότυπα .label φυλάσσονται στη βάση (όχι στο δημόσιο repository). Κάθε έκδοση κρατιέται: "
                       "οι επανεκτυπώσεις χρησιμοποιούν την έκδοση με την οποία τυπώθηκε η ετικέτα.",
                       "The .label templates are kept in the database (not in the public repository). Every version "
                       "is kept: reprints use the version the label was printed with."),
    "template_source": ("Προέλευση", "Source"),
    "upload_label_file": ("Αρχείο .label από το DYMO Label", ".label file from DYMO Label"),
    "upload": ("Ανέβασμα", "Upload"),
    "template_uploaded": ("Το πρότυπο {k} ενεργοποιήθηκε ({s}…). Τυπώστε TEST0001 για να το ελέγξετε.",
                          "Template {k} is now active ({s}…). Print TEST0001 to check it."),
    "template_versions": ("Όλες οι εκδόσεις", "All versions"),
    "series_other_mode": ("Οι σειρές διαχειρίζονται εδώ μόνο με VSPM_BARCODE_MODE=database (τώρα: {m}).",
                          "Series are managed here only with VSPM_BARCODE_MODE=database (now: {m})."),
    "series_help": ("Η κοινή βάση δίνει κάθε αριθμό μία φορά, σε όλους τους υπολογιστές. Ο μετρητής (COUNTER) του "
                    "προτύπου DYMO δεν χρησιμοποιείται για τις ετικέτες της εφαρμογής.",
                    "The shared database issues every number once, for all computers. The DYMO template COUNTER "
                    "is not used for labels printed by the app."),
    "series_none": ("Καμία σειρά δεν έχει οριστεί: η εκτύπωση νέων ετικετών είναι κλειστή μέχρι να οριστεί.",
                    "No series initialised: printing new labels is closed until one is set."),
    "series_set": ("Ορισμός επόμενου αριθμού", "Set next number"),
    "series_set_help": ("Βάλτε τον ΕΠΟΜΕΝΟ ελεύθερο αριθμό (μεγαλύτερο από κάθε barcode που έχει ήδη τυπωθεί, και "
                        "χειροκίνητα). Δεν μπορεί να μετακινηθεί προς τα πίσω.",
                        "Enter the NEXT free number (higher than every barcode already printed, manual ones "
                        "included). It can never move backwards."),
    "series_next": ("Επόμενος αριθμός", "Next number"),
    "series_width": ("Ψηφία", "Digits"),
    "series_confirm": ("Πληκτρολογήστε το barcode για επιβεβαίωση", "Type the barcode to confirm"),
    "series_preview": ("Το επόμενο barcode θα είναι **{b}**", "The next barcode will be **{b}**"),
    "series_confirm_mismatch": ("Η επιβεβαίωση πρέπει να είναι ακριβώς {b}", "The confirmation must be exactly {b}"),
    "series_next_positive": ("Ο αριθμός πρέπει να είναι μεγαλύτερος από 0", "The number must be greater than 0"),
    "series_saved": ("Αποθηκεύτηκε: επόμενο barcode {b}", "Saved: next barcode {b}"),
    "observe_title": ("Δήλωση barcode που τυπώθηκαν χειροκίνητα", "Report manually printed barcodes"),
    "observe_help": ("Barcode που τύπωσε κάποιος με τον μετρητή του DYMO: καταχωρούνται ώστε να μη δοθούν ποτέ ξανά.",
                     "Barcodes someone printed with the DYMO counter: recorded so they are never issued again."),
    "observe_codes": ("Barcode (ένα ανά γραμμή ή με κενά)", "Barcodes (one per line or space-separated)"),
    "observe_submit": ("Καταχώρηση", "Record"),
    "observe_done": ("Καταχωρήθηκαν {n}, ήδη γνωστά {a}, μη έγκυρα {i}", "Recorded {n}, already known {a}, invalid {i}"),
    "block_title": ("Δέσμευση μπλοκ για χειροκίνητη εκτύπωση", "Reserve a block for manual printing"),
    "block_help": ("Συνεχόμενοι αριθμοί για όποιον τυπώνει ακόμη από το DYMO Label: ορίστε τον μετρητή του "
                   "προτύπου στον πρώτο αριθμό του μπλοκ.",
                   "Consecutive numbers for anyone still printing from DYMO Label: set the template counter to the "
                   "first number of the block."),
    "block_count": ("Πλήθος", "Count"),
    "block_submit": ("Δέσμευση", "Reserve"),
    "block_done": ("Δεσμεύτηκαν {f} – {l}", "Reserved {f} – {l}"),
    "nav_settings": ("Ρυθμίσεις", "Settings"),
    "language": ("Γλώσσα", "Language"),
    "operator": ("Χειριστής", "Operator"),
    # dashboard
    "latest_docs": ("Τελευταία {n} σκαναρισμένα έγγραφα", "Latest {n} scanned documents"),
    "refresh": ("Ανανέωση", "Refresh"),
    "labels_today": ("Ετικέτες σήμερα", "Labels today"),
    "excel_pending": ("Excel σε αναμονή", "Excel pending"),
    "excel_errors": ("Σφάλματα Excel", "Excel errors"),
    "print_errors": ("Σφάλματα εκτύπωσης", "Print errors"),
    "print_unconfirmed": ("Ανεπιβεβαίωτες εκτυπώσεις", "Unconfirmed prints"),
    "rows_recognized": ("Γραμμές", "Rows"),
    "printed": ("Εκτυπωμένες", "Printed"),
    "status": ("Κατάσταση", "Status"),
    "uploaded": ("Ανέβηκε", "Uploaded"),
    "open": ("Άνοιγμα", "Open"),
    "problems": ("Ρυθμίσεις που χρειάζονται προσοχή", "Configuration needing attention"),
    "recovery": ("Εργασίες που διακόπηκαν", "Interrupted work"),
    "changed_doc": ("Το έγγραφο άλλαξε μετά την επεξεργασία", "Document changed after processing"),
    "no_docs": ("Δεν βρέθηκαν έγγραφα.", "No documents found."),
    # recognition
    "select_doc": ("Επιλογή εγγράφου", "Select document"),
    "read_ai": ("Ανάγνωση με AI", "Read with AI"),
    "reading": ("Ανάγνωση χειρόγραφου…", "Reading handwriting…"),
    "ocr_done": ("Αναγνωρίστηκαν {n} γραμμές. Ελέγξτε τες στη σελίδα «Έλεγχος γραμμών».",
                 "{n} rows recognised. Review them on the Row review page."),
    "ocr_again_warn": ("Νέα ανάγνωση αντικαθιστά τις μη εκτυπωμένες γραμμές αυτού του εγγράφου.",
                       "Reading again replaces the unprinted rows of this document."),
    "reread_printed": ("Αυτό το έγγραφο έχει ήδη εκτυπωμένες ετικέτες. Οι εκτυπωμένες γραμμές δεν αλλάζουν και "
                       "δεν δημιουργούνται ξανά.",
                       "This document already has printed labels. Printed rows are kept and are not created again."),
    "reread_confirm": ("Κατάλαβα - νέα ανάγνωση μόνο για τις μη εκτυπωμένες γραμμές",
                       "Understood - re-read only the unprinted rows"),
    "possible_duplicate": ("Πιθανή διπλή εκτύπωση: {e}", "Possible duplicate: {e}"),
    "print_anyway": ("Είναι διαφορετική γραμμή - εκτύπωση παρ' όλα αυτά",
                     "It is a different line - print anyway"),
    "verify": ("Έλεγχος ετικέτας και Excel", "Verify label and Excel record"),
    "printer_intro": ("Αυτή η σελίδα ελέγχει την υπηρεσία DYMO και τον εκτυπωτή του υπολογιστή στον οποίο είναι "
                      "ανοιχτός αυτός ο browser. Οι ετικέτες τυπώνονται πάντα στον DYMO αυτού του υπολογιστή.",
                      "This page checks the DYMO service and printer of the computer this browser runs on. Labels "
                      "always print on this computer's DYMO."),
    "this_pc": ("Αυτός ο υπολογιστής", "This computer"),
    "waiting_browser": ("Αναμονή απάντησης από τον browser…", "Waiting for the browser…"),
    "dymo_service": ("Υπηρεσία DYMO", "DYMO service"),
    "printers_found": ("Εκτυπωτές DYMO", "DYMO printers"),
    "selected_printer": ("Εκτυπωτής", "Printer"),
    "verified": ("Επιβεβαιωμένος", "Verified"),
    "details": ("Λεπτομέρειες διάγνωσης", "Diagnostic details"),
    "station_name": ("Θέση", "Station"),
    "top_frame": ("Πλαίσιο σελίδας", "Page frame"),
    "embedded_cross_origin": ("μέσα σε ξένη σελίδα", "embedded in another site"),
    "embedded_hint": ("Η εφαρμογή εμφανίζεται μέσα σε σελίδα άλλης διεύθυνσης. Αν η υπηρεσία DYMO δεν βρίσκεται, "
                      "ανοίξτε την εφαρμογή απευθείας από τη δική της διεύθυνση.",
                      "The app is shown inside a page of another address. If the DYMO service is not found, open "
                      "the app directly at its own address."),
    "test_print": ("Δοκιμαστική εκτύπωση TEST0001", "Test print TEST0001"),
    "test_print_help": ("Τυπώνει ετικέτα με barcode TEST0001 στο επιλεγμένο πρότυπο. Δεν δεσμεύει κανέναν αριθμό, "
                        "δεν γράφει στο Excel και δεν αλλάζει τους μετρητές DYMO.",
                        "Prints a label with barcode TEST0001 on the chosen template. It allocates no number, writes "
                        "nothing to Excel and does not change any DYMO counter."),
    "print_test": ("Εκτύπωση TEST0001", "Print TEST0001"),
    "download_test_label": ("Λήψη TEST0001 .label", "Download TEST0001 .label"),
    "job": ("Εργασία", "Job"),
    "confirm_test_question": ("Βγήκε η ετικέτα TEST0001 σωστά (ίδια εμφάνιση με τις κανονικές ετικέτες) και ο "
                              "σαρωτής διαβάζει TEST0001;",
                              "Did the TEST0001 label come out correctly (same look as normal labels) and does the "
                              "scanner read TEST0001?"),
    "test_ok": ("Ναι — σωστή ετικέτα, ο σαρωτής διαβάζει TEST0001", "Yes — correct label, scanner reads TEST0001"),
    "test_bad": ("Όχι — πρόβλημα", "No — problem"),
    "station_verified": ("Ο υπολογιστής και ο εκτυπωτής επιβεβαιώθηκαν για εκτύπωση.",
                         "This computer and printer are verified for printing."),
    "test_failed_note": ("Καταγράφηκε. Δείτε τον οδηγό αντιμετώπισης προβλημάτων πριν από νέα δοκιμή.",
                         "Recorded. See the troubleshooting guide before trying again."),
    "all_stations": ("Όλοι οι υπολογιστές", "All computers"),
    "station_not_verified": ("Αυτός ο υπολογιστής/εκτυπωτής δεν έχει επιβεβαιωθεί. Ανοίξτε τη σελίδα «Εκτυπωτής DYMO», "
                             "τυπώστε TEST0001 και επιβεβαιώστε το αποτέλεσμα.",
                             "This computer/printer is not verified yet. Open the 'DYMO printer' page, print TEST0001 "
                             "and confirm the result."),
    "sending_to_this_pc": ("Η ετικέτα στέλνεται στον DYMO αυτού του υπολογιστή…",
                           "The label is being sent to this computer's DYMO…"),
    "previous_unknown": ("Η προηγούμενη αποστολή της {b} δεν απάντησε (π.χ. έκλεισε η σελίδα). Ελέγξτε τον εκτυπωτή: "
                         "βγήκε η ετικέτα;",
                         "The previous print request for {b} got no answer (e.g. the page was closed). Check the "
                         "printer: did the label come out?"),
    "it_came_out": ("Ναι, βγήκε σωστά", "Yes, it came out correctly"),
    "not_out_print_again": ("Όχι — εκτύπωση ξανά (ίδιο barcode)", "No — print again (same barcode)"),
    "use_reprint": ("OK - χρησιμοποιήστε «Επανεκτύπωση» (ίδιο barcode).", "OK - use Reprint (same barcode)."),
    "manual_fallback": ("Η εκτύπωση από τον browser δεν λειτουργεί; Λήψη αρχείου .label",
                        "Browser printing not working? Download the .label file"),
    "manual_fallback_help": ("Το αρχείο περιέχει την έτοιμη ετικέτα με το ΔΕΣΜΕΥΜΕΝΟ barcode (χωρίς σύνδεση με τον "
                             "μετρητή DYMO). Ανοίξτε το στο DYMO Label v8, ΜΗΝ αλλάξετε τίποτα, τυπώστε 1 αντίτυπο και "
                             "επιβεβαιώστε εδώ.",
                             "The file contains the prepared label with the RESERVED barcode (not linked to the DYMO "
                             "counter). Open it in DYMO Label v8, change NOTHING, print 1 copy and confirm here."),
    "reserve_for_manual": ("Δέσμευση barcode για χειροκίνητη εκτύπωση", "Reserve the barcode for manual printing"),
    "download_label": ("Λήψη {b}.label", "Download {b}.label"),
    "manual_confirm_question": ("Τυπώσατε το {b}.label από το DYMO Label v8 και βγήκε σωστά με barcode {b};",
                                "Did you print {b}.label from DYMO Label v8 and did it come out correctly with "
                                "barcode {b}?"),
    "manual_printed_ok": ("Ναι, τυπώθηκε σωστά", "Yes, printed correctly"),
    "manual_not_printed": ("Όχι, δεν τυπώθηκε", "No, not printed"),
    "browser_jobs": ("Εργασίες εκτύπωσης browser", "Browser print jobs"),
    "go_review": ("Μετάβαση στον έλεγχο", "Go to review"),
    "page": ("Σελίδα", "Page"),
    # review
    "save": ("Αποθήκευση", "Save"),
    "saved": ("Αποθηκεύτηκε.", "Saved."),
    "confirm_flags": ("Έλεγξα τις τιμές με ⚠ και τον τύπο ετικέτας στις εγκεκριμένες γραμμές",
                      "I checked the ⚠ values and the label type of the approved rows"),
    "need_confirm": ("Επιβεβαιώστε τις τιμές με ⚠ (κουτάκι κάτω από τον πίνακα) για να εγκριθούν οι γραμμές.",
                     "Tick the confirmation box below the table to approve rows with ⚠ values."),
    "approved": ("Εγκρ.", "Approved"),
    "row_errors": ("Η γραμμή {i} δεν εγκρίθηκε: {e}", "Row {i} not approved: {e}"),
    "review_help": ("Διορθώστε τις τιμές και επιλέξτε τύπο ετικέτας. ⚠ = αβέβαιη ανάγνωση.",
                    "Correct values and choose the label type. ⚠ = uncertain reading."),
    # print
    "select_row": ("Γραμμή παραγωγής", "Production row"),
    "preview": ("Προεπισκόπηση", "Preview label"),
    "print_save": ("Εκτύπωση & Αποθήκευση", "Print & Save"),
    "reprint": ("Επανεκτύπωση", "Reprint existing label"),
    "retry_excel": ("Επανάληψη Excel", "Retry failed Excel sync"),
    "confirm_ok": ("Η ετικέτα βγήκε σωστά", "Label printed correctly"),
    "confirm_bad": ("Η ετικέτα ΔΕΝ βγήκε", "Label did NOT print"),
    "barcode": ("Barcode", "Barcode"),
    "barcode_at_print": ("θα δεσμευτεί κατά την εκτύπωση", "reserved when printing"),
    "print_status": ("Εκτύπωση", "Print"),
    "excel_status": ("Excel", "Excel"),
    "template": ("Ετικέτα", "Template"),
    "not_printable": ("Δεν μπορεί να εκτυπωθεί ακόμη:", "Not printable yet:"),
    "no_rows": ("Δεν υπάρχουν γραμμές. Διαβάστε πρώτα ένα έγγραφο.", "No rows. Read a document first."),
    # history
    "filters": ("Φίλτρα", "Filters"),
    "date_from": ("Από", "From"),
    "date_to": ("Έως", "To"),
    "customer": ("Πελάτης", "Customer"),
    "document": ("Έγγραφο", "Document"),
    "staging": ("Καταχωρήσεις Excel σε αναμονή", "Pending Excel entries"),
    "apply_now": ("Εγγραφή στο Excel τώρα", "Write to Excel now"),
    "reconcile": ("Έλεγχος στο Excel (συμφωνία)", "Reconcile with workbook"),
    "export_staging": ("Λήψη αρχείου για χειροκίνητη καταχώρηση", "Download sheet for manual entry"),
    "attempts": ("Ιστορικό ενεργειών", "Attempt history"),
    # settings
    "integration_checks": ("Έλεγχος συνδέσεων", "Check integrations"),
    "customer_rules": ("Κανόνες πελατών / ετικετών", "Customer / label rules"),
    "env_settings": ("Ρυθμίσεις εφαρμογής (.env)", "Application settings (.env)"),
    "restart_note": ("Οι αλλαγές ισχύουν μετά το «Επαναφόρτωση».", "Changes apply after 'Reload'."),
    "reload": ("Επαναφόρτωση", "Reload"),
    "logs": ("Αρχείο καταγραφής", "Log"),
    "test_customer": ("Δοκιμή ονόματος πελάτη", "Test a customer name"),
}


def t(key: str, lang: str = "el", **kw) -> str:
    el, en = STRINGS.get(key, (key, key))
    s = el if lang == "el" else en
    return s.format(**kw) if kw else s


STATUS_LABELS = {
    "el": {
        "new": "Νέο", "downloaded": "Λήφθηκε", "ocr_complete": "Αναγνωρίστηκε", "in_review": "Σε έλεγχο",
        "partially_printed": "Μερικώς εκτυπωμένο", "completed": "Ολοκληρώθηκε", "error": "Σφάλμα",
        "draft": "Πρόχειρο", "needs_review": "Θέλει έλεγχο", "approved": "Εγκρίθηκε",
        "barcode_reserved": "Δεσμεύτηκε barcode", "print_submitted": "Στάλθηκε",
        "print_unconfirmed": "Στάλθηκε - ανεπιβεβαίωτο", "printed_confirmed": "Εκτυπώθηκε ✔",
        "print_failed_confirmed": "Απέτυχε (δηλώθηκε)", "not_printed": "Όχι", "not_queued": "—",
        "pending": "Σε αναμονή", "staged": "Σε αναμονή (staging)", "queued_remote": "Σε ουρά",
        "synchronized": "Καταχωρήθηκε ✔", "conflict": "Σύγκρουση",
    },
}


def status_label(code: str, lang: str = "el") -> str:
    return STATUS_LABELS.get(lang, {}).get(code, code.replace("_", " "))
