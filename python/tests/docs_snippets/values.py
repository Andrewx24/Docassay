from docassay.normalize import values_equal

same_number = values_equal("$1,200.00", "1200")
same_date = values_equal("Nov. 1 2026", "2026-11-01")
different_text = values_equal("Acme", "Acme Corp")
