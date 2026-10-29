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
    "nav_settings": ("Ρυθμίσεις", "Settings"),
    "language": ("Γλώσσα", "Language"),
    "operator": ("Χειριστής", "Operator"),
    # dashboard
    "latest_docs": ("Τελευταία 5 σκαναρισμένα έγγραφα", "Latest 5 scanned documents"),
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
    "review_help": ("Διορθώστε τις τιμές, επιλέξτε τύπο ετικέτας και τσεκάρετε «Εγκρ.». ⚠ = αβέβαιη ανάγνωση.",
                    "Correct values, choose the label type and tick Approved. ⚠ = uncertain reading."),
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
