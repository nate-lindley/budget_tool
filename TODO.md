# Budget Tool - Feature Roadmap

## Transaction & Data Entry

### Transaction Search [x]
Full-text search across transaction descriptions from the index page. Add a search input that filters beyond the existing category/source/date filters.

### Duplicate Detection [x]
Flag potential duplicate transactions (same amount + date + similar description) during manual entry and CSV import. Show a warning before saving with the option to proceed or cancel.

### Transaction Notes/Tags [x]
Add an optional notes field and/or tags to transactions for additional context without changing the description (e.g., tag a dinner as "business" or "date night"). Support filtering by tag.

### Plaid Integration [ ]
Integrate with Plaid API to automatically import transactions from linked bank accounts and credit cards. Map Plaid categories to existing categories, auto-assign sources, and reduce manual data entry.

---

## Budgeting & Goals

### Savings Goals [x]
Track progress toward specific financial goals (emergency fund, vacation, down payment, etc.) with target amounts and deadlines. Show progress bars and projected completion dates based on average monthly savings rate.

### Budget Alerts/Thresholds [ ]
Visual warnings on the index page and reports when a category hits 80%/90%/100% of its monthly budget. Show as colored badges or progress bars next to category names. Could optionally support browser notifications.

### Income Budgeting [ ]
Add expected monthly income so you can track actual vs. expected and see a true surplus/deficit. Support multiple income sources with different expected amounts and frequencies.

### Projected Monthly Spend [ ]
Based on recurring transactions + average discretionary spending patterns, project what the current month's total will be before it's over. Show projected vs. budget on the dashboard.

---

## Reporting & Analytics

### Spending Trends/Anomalies [ ]
Highlight categories where spending is significantly above the trailing average. For example: "Dining out is 40% higher than your 6-month average this month." Surface these on the index page or a dedicated insights section.

### Custom Date Range Reports [ ]
Allow arbitrary start/end dates for all reports instead of only month/YTD/TTM. Useful for tracking project-based spending like "how much did the kitchen renovation cost from March-August?"

### Export to CSV/PDF [ ]
Export any report view to CSV or generate a PDF summary. Include charts and tables in PDF output. CSV export for raw data analysis in spreadsheets.

---

## Rewards & Credit Cards

### Rewards Optimization [x]
After logging a transaction, highlight when a purchase was made on a suboptimal card. Show what the best card would have been and how much more you could have earned. Help learn from mistakes and build better habits over time.

### Annual Fee Break-Even Analysis [ ]
For each card with an annual fee, calculate how much you need to spend (and in which categories) to make the fee worthwhile compared to a no-fee alternative. Show current progress toward break-even for the year.

---

## UX & Quality of Life

### Dark Mode [ ]
Add a theme toggle using Bootstrap 5's built-in `data-bs-theme="dark"` support. Persist the preference across sessions.
