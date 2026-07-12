export interface IndicatorDefinition {
  slug: string;
  section: string;
  title: string;
  status: "published" | "partial" | "pending";
  summary: string;
  formula: string;
  fields: string[];
  example: string;
  pitfalls: string[];
  nRule: string;
}

export const indicators: IndicatorDefinition[] = [
  {
    slug: "direct-award",
    section: "4.1",
    title: "Ποσοστό απευθείας αναθέσεων",
    status: "published",
    summary: "Μερίδιο αναθέσεων ενός φορέα που έγιναν με τη διαδικασία «Απευθείας ανάθεση», σε πλήθος και αξία.",
    formula: "DA_count = N_direct / N_total · DA_value = value_direct / value_total",
    fields: ["procedureType.key", "totalCostWithoutVAT", "totalCostWithVAT", "organizationVatNumber"],
    example: "Αν ένας φορέας έχει 80 απευθείας αναθέσεις σε 100 αναθέσεις το 2025, το DA_count είναι 80%.",
    pitfalls: [
      "Μικροί φορείς με πολλές μικρές προμήθειες έχουν φυσιολογικά υψηλότερο ποσοστό σε πλήθος.",
      "Η αξία και το N πρέπει να διαβάζονται μαζί με το ποσοστό.",
      "Η απευθείας ανάθεση είναι νόμιμη κάτω από τα όρια του ν.4412/2016.",
    ],
    nRule: "Δημοσιεύεται ανά φορέα/έτος με N αναθέσεων διαθέσιμο στο CSV και στο site.",
  },
  {
    slug: "hhi",
    section: "4.2",
    title: "Συγκέντρωση αναδόχων (HHI)",
    status: "published",
    summary: "Μετρά πόσο συγκεντρωμένη είναι η αξία συμβάσεων ενός φορέα σε λίγους αναδόχους.",
    formula: "HHI = Σ(s_i²), όπου s_i το μερίδιο αξίας κάθε αναδόχου.",
    fields: ["contractingDataDetails.contractingMembersDataList", "totalCostWithoutVAT", "organizationVatNumber"],
    example: "Αν ένας ανάδοχος παίρνει το 50% της αξίας και δύο άλλοι από 25%, το HHI είναι 0,375.",
    pitfalls: [
      "Εξειδικευμένες αγορές μπορεί να έχουν λίγους νόμιμους προμηθευτές.",
      "Σε κοινοπραξίες όλη η αξία πιστώνεται στον πρώτο καταχωρημένο ανάδοχο στη v1.",
      "Κάτω από N=10 εμφανίζεται «Ανεπαρκή δεδομένα».",
    ],
    nRule: "Ελάχιστο N=10 συμβάσεις ανά φορέα/έτος.",
  },
  {
    slug: "single-bid",
    section: "4.3",
    title: "Single-bid rate",
    status: "published",
    summary: "Μερίδιο ανταγωνιστικών διαδικασιών όπου καταγράφηκε μία μόνο προσφορά.",
    formula: "SB = N_single_bid / N_with_bids, με coverage = N_with_bids / N_competitive.",
    fields: ["contract.bidsSubmitted / bidsSubmitted", "procedureType", "organizationVatNumber"],
    example: "Αν 30 από 50 ανταγωνιστικές διαδικασίες με έγκυρο bidsSubmitted έχουν μία προσφορά, το single-bid είναι 60%.",
    pitfalls: [
      "Δεν είναι ιστορική σειρά πριν από το cutover 2025-04.",
      "Απευθείας αναθέσεις, άρθρο 32 και άρθρο 128 εξαιρούνται από τον παρονομαστή.",
      "Garbage τιμές bidsSubmitted εκτός [1,100] εξαιρούνται και μετρώνται ξεχωριστά.",
    ],
    nRule: "Ελάχιστο N=5 έγκυρες ανταγωνιστικές διαδικασίες με bidsSubmitted.",
  },
  {
    slug: "benford",
    section: "4.4",
    title: "Έλεγχος Benford",
    status: "published",
    summary: "Στατιστική απόκλιση της κατανομής 1ου/2ου ψηφίου των ποσών πληρωμών φορέα από την αναμενόμενη κατανομή Benford, με τα κατώφλια Nigrini.",
    formula: "P(d) = log10(1 + 1/d) · MAD = mean(|observed_d − P(d)|) · Nigrini bands: close/acceptable/marginal/nonconformity ανά MAD.",
    fields: ["payment.totalCostWithoutVAT", "organizationVatNumber (μέσω vat_resolver)"],
    example: "Σε δείγμα 1.000 ποσών, το πρώτο ψηφίο 1 αναμένεται σε ~30,1% των ποσών και το 9 σε ~4,6%.",
    pitfalls: [
      "Ο Benford δεν είναι απόδειξη κανενός ισχυρισμού — δευτερεύον στατιστικό σήμα, ποτέ μόνο του.",
      "Θεσμικά κατώφλια (π.χ. όρια απευθείας ανάθεσης) παράγουν φυσιολογικές αποκλίσεις από την αναμενόμενη κατανομή.",
      "Το 1ο ψηφίο υπολογίζεται από το string της απόλυτης τιμής (όχι log του float) για αποφυγή float artifacts· το 2ο ψηφίο μόνο για ποσά ≥10.",
      "Δημοσιεύεται σε δύο επίπεδα περιόδου (ανά έτος και «όλη η περίοδος») ώστε μεσαίοι φορείς που δεν πιάνουν N=300/έτος να έχουν έστω τη συγκεντρωτική τιμή.",
    ],
    nRule: "Ελάχιστο N=300 έγκυρα ποσά ανά κελί (φορέας × περίοδος)· κάτω από αυτό εμφανίζεται «Ανεπαρκή δεδομένα».",
  },
  {
    slug: "discount",
    section: "4.6",
    title: "Ποσοστό έκπτωσης προκήρυξης → ανάθεση",
    status: "published",
    summary: "Σύγκριση εκτιμώμενης αξίας προκήρυξης με τιμή κατακύρωσης ως proxy της τελικής αξίας.",
    formula: "discount = (est_value - final_value) / est_value.",
    fields: ["notice.referenceNumber", "auction.noticeRefNo", "budget", "totalCostWithoutVAT"],
    example: "Προκήρυξη 100.000€ και ανάθεση 85.000€ δίνουν έκπτωση 15%.",
    pitfalls: [
      "Η σύνδεση notice↔contract είναι σχεδόν κενή, άρα χρησιμοποιείται auction ως proxy.",
      "Χαμηλή κάλυψη εμφανίζεται ως ανεπαρκής ένδειξη.",
      "Μεμονωμένη μηδενική έκπτωση δεν σημαίνει τίποτα.",
    ],
    nRule: "Ελάχιστο N=5 συνδεδεμένες διαδικασίες, με coverage δίπλα στον δείκτη.",
  },
  {
    slug: "deadline",
    section: "4.7",
    title: "Διάμεση προθεσμία υποβολής",
    status: "partial",
    summary: "Ημέρες από δημοσίευση notice έως καταληκτική ημερομηνία υποβολής, μόνο για ανταγωνιστικές διαδικασίες.",
    formula: "days = finalSubmissionDate - submissionDate, διάμεσος ανά φορέα/έτος.",
    fields: ["submissionDate", "finalSubmissionDate", "typeOfProcedure", "organizationVatNumber"],
    example: "Πέντε διαδικασίες με προθεσμίες 10, 12, 15, 20, 25 ημερών έχουν διάμεσο 15 ημέρες.",
    pitfalls: [
      "Το pct_short_deadline δεν δημοσιεύεται μέχρι νομική επιβεβαίωση ελάχιστων ορίων.",
      "Απευθείας ανάθεση, άρθρο 32 και άρθρο 128 εξαιρούνται από τη διάμεσο.",
      "Αρνητικές ή κενές ημερομηνίες εξαιρούνται και η κάλυψη δημοσιεύεται.",
    ],
    nRule: "Ελάχιστο N=5 έγκυρες διαδικασίες.",
  },
  {
    slug: "composite",
    section: "4.8",
    title: "Σύνθετος δείκτης",
    status: "published",
    summary: "Μη σταθμισμένος μέσος των διαθέσιμων δημοσιευμένων flags ενός φορέα.",
    formula: "mean(DA_count, DA_value, HHI, discount flag, deadline percentile, single-bid), όπου υπάρχουν.",
    fields: ["indicator_direct_award.csv", "indicator_hhi.csv", "indicator_discount_rate.csv", "indicator_deadlines.csv", "indicator_single_bid.csv"],
    example: "Αν υπάρχουν τέσσερα flags 0,20, 0,40, 0,50, 0,70, το composite είναι 0,45.",
    pitfalls: [
      "Δεν εμφανίζεται ποτέ μόνος του χωρίς τα επιμέρους flags και τα N.",
      "Δεν περιλαμβάνει bid-splitting ούτε pct_short_deadline.",
      "Δεν είναι κατάταξη ηθικής ή νομιμότητας.",
    ],
    nRule: "Χρησιμοποιεί μόνο διαθέσιμα components· τα ανεπαρκή N εξαιρούνται από τον μέσο.",
  },
  {
    slug: "coverage",
    section: "4.9",
    title: "Κάλυψη δεδομένων",
    status: "published",
    summary: "Γνωστά κενά και ποιοτικά μέτρα των raw δεδομένων που επηρεάζουν την ερμηνεία.",
    formula: "Ποσοστά πληρότητας ανά έτος για ΑΦΜ, CPV, ποσά και γνωστά κενά.",
    fields: ["completeness_report.json", "data/raw/_backfill_failures.json"],
    example: "Τα `auction_2021_02` και `auction_2025_08` είναι μόνιμα κενά και δηλώνονται στο report.",
    pitfalls: [
      "Τα έτη με μόνιμο κενό auction υποεκτιμούν πλήθος και αξία.",
      "Το checksum ΑΦΜ είναι ποιοτικό μέτρο, όχι φίλτρο αποδοχής entity resolution.",
      "Το payment entity δεν είναι ακόμη πλήρες.",
    ],
    nRule: "Δημοσιεύεται ως report ανά έτος, όχι ως risk flag.",
  },
];

export const publishedIndicators = indicators.filter((i) => i.slug !== "bid-splitting");
