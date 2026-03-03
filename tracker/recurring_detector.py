from collections import Counter
from statistics import median

from django.db.models import Count

from .models import Transaction, RecurringTransaction, DismissedSuggestion


def detect_recurring_patterns():
    # Descriptions already covered by active recurring transactions
    active_descriptions = set(
        RecurringTransaction.objects.filter(is_active=True)
        .values_list('description', flat=True)
    )
    # Descriptions the user has dismissed
    dismissed_descriptions = set(
        DismissedSuggestion.objects.values_list('description', flat=True)
    )
    excluded = active_descriptions | dismissed_descriptions

    # Only non-recurring transactions, grouped by description with 3+ occurrences
    candidates = (
        Transaction.objects.filter(recurring_source__isnull=True)
        .exclude(description__in=excluded)
        .values('description')
        .annotate(cnt=Count('id'))
        .filter(cnt__gte=3)
    )

    suggestions = []
    for entry in candidates:
        desc = entry['description']
        txns = list(
            Transaction.objects.filter(
                description=desc, recurring_source__isnull=True
            )
            .order_by('date')
            .values('date', 'amount', 'category_id', 'category__name', 'source_id', 'source__name')
        )
        result = analyze_transaction_group(desc, txns)
        if result:
            suggestions.append(result)

    suggestions.sort(key=lambda s: s['occurrences'], reverse=True)
    return suggestions


def analyze_transaction_group(description, transactions):
    if len(transactions) < 3:
        return None

    dates = [t['date'] for t in transactions]
    intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]

    if not intervals:
        return None

    med = median(intervals)
    if med <= 0:
        return None

    frequency, interval = _match_frequency(med)
    if not frequency:
        return None

    # Consistency: fraction of intervals within 20% of the median
    tolerance = med * 0.2
    consistent = sum(1 for gap in intervals if abs(gap - med) <= tolerance)
    consistency = consistent / len(intervals)
    if consistency < 0.7:
        return None

    # Average amount
    amounts = [float(t['amount']) for t in transactions]
    avg_amount = round(sum(amounts) / len(amounts), 2)

    # Most common category
    category_ids = [t['category_id'] for t in transactions if t['category_id']]
    most_common_category_id = Counter(category_ids).most_common(1)[0][0] if category_ids else None
    category_name = None
    if most_common_category_id:
        for t in transactions:
            if t['category_id'] == most_common_category_id:
                category_name = t['category__name']
                break

    # Most common source
    source_ids = [t['source_id'] for t in transactions if t['source_id']]
    most_common_source_id = Counter(source_ids).most_common(1)[0][0] if source_ids else None
    source_name = None
    if most_common_source_id:
        for t in transactions:
            if t['source_id'] == most_common_source_id:
                source_name = t['source__name']
                break

    # Determine day_of_month / day_of_week
    if frequency == 'weekly':
        weekdays = [d.weekday() for d in dates]
        day_of_week = Counter(weekdays).most_common(1)[0][0]
        day_of_month = 1
    else:
        days = [d.day for d in dates]
        day_of_month = Counter(days).most_common(1)[0][0]
        day_of_week = None

    return {
        'description': description,
        'amount': avg_amount,
        'frequency': frequency,
        'interval': interval,
        'day_of_month': day_of_month,
        'day_of_week': day_of_week,
        'category_id': most_common_category_id,
        'category_name': category_name,
        'source_id': most_common_source_id,
        'source_name': source_name,
        'occurrences': len(transactions),
        'consistency_score': round(consistency, 2),
        'start_date': dates[0].isoformat(),
    }


def _match_frequency(median_days):
    ranges = [
        (6, 8, 'weekly', 1),
        (28, 31, 'monthly', 1),
        (55, 65, 'monthly', 2),
        (88, 95, 'monthly', 3),
        (360, 370, 'yearly', 1),
    ]
    for low, high, freq, intv in ranges:
        if low <= median_days <= high:
            return freq, intv
    return None, None
