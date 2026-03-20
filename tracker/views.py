from django.shortcuts import render, HttpResponseRedirect
from django.contrib import messages
from .models import *
from decimal import Decimal
from datetime import datetime, timedelta, date
from django.db.models import Sum, Q, Exists, OuterRef, Min, Max, F, Value, DecimalField, Case, When, BooleanField
import calendar
from django.db.models.functions import ExtractYear, ExtractMonth, Coalesce
import csv
from io import TextIOWrapper
from django.core.paginator import Paginator
import time
from .recurring_detector import detect_recurring_patterns

CREDIT_CATEGORY_Q = (
    Q(category__name__istartswith='card-cash-') |
    Q(category__name__istartswith='miles-credit-') |
    Q(category__name__istartswith='credit-')
)

SPEND_TREND_LOOKBACK_MONTHS = 6

BLANKET_MULTIPLIERS = {
    'cashback': Decimal('0.01'),
    'miles': Decimal('1.0'),
    'card_cash_miles': Decimal('2.0'),  # 2x miles on non-rent spend
    'none': Decimal('0'),
}

# card_cash_miles sources also earn card cash, added as a miles equivalent for comparison purposes.
# 0.5 bonus makes them rank above a card with the same miles rate but below one with more miles.
CARD_CASH_MILES_BONUS = Decimal('0.5')

# Cents per point/mile used to normalize miles against cashback for comparison only.
# Display always shows raw miles (e.g. "2.00x"), never the converted value.
CPP = Decimal('0.012')


def comparison_value(source, raw_multiplier):
    """Return a dollar-per-dollar comparison value for ranking cards against each other.
    Miles are converted to dollar value using CPP; cashback is already in dollar terms.
    card_cash_miles gets the 0.5-mile card-cash bonus before conversion.
    """
    if source.reward_type == 'cashback':
        return raw_multiplier
    effective_miles = raw_multiplier + CARD_CASH_MILES_BONUS if source.reward_type == 'card_cash_miles' else raw_multiplier
    return effective_miles * CPP


def get_monthly_cumulative_spend(year, month):
    """Returns a 31-element list of cumulative spend for the given month."""
    transactions = annotate_net_amount(
        Transaction.objects.filter(date__month=month, date__year=year)
    ).exclude(category__name__iexact='income').exclude(
        category__reporting_category__name__iexact='income'
    ).exclude(CREDIT_CATEGORY_Q)

    daily_totals = (
        transactions.order_by("date")
        .values("date")
        .annotate(daily_total=Sum("net_amount"))
    )

    day_cumulative = {}
    cumulative = 0.0
    for item in daily_totals:
        cumulative += float(item["daily_total"]) * -1
        day_cumulative[item["date"].day] = cumulative

    result = []
    last_val = 0.0
    for day in range(1, 32):
        if day in day_cumulative:
            last_val = day_cumulative[day]
        result.append(last_val)
    return result


def annotate_reporting_category(queryset):
    return queryset.annotate(
        reporting_category_id=Coalesce('category__reporting_category_id', 'category_id'),
        reporting_category_name=Coalesce('category__reporting_category__name', 'category__name'),
        reporting_category_budget=Coalesce('category__reporting_category__budget', 'category__budget'),
    )


def annotate_net_amount(queryset):
    return queryset.annotate(
        net_amount=F('amount') + Coalesce('reimbursement', Value(0, output_field=DecimalField()))
    )


def quarter_label(date_value):
    if not date_value:
        return ""
    quarter = (date_value.month - 1) // 3 + 1
    return f"{date_value.year}Q{quarter}"


def get_better_card_tip(transaction):
    """Check if a better reward card existed for this transaction's category/amount."""
    if not transaction.category or not transaction.source:
        return None
    if transaction.amount <= 0:
        return None
    cat_name = transaction.category.name.lower()
    if cat_name.startswith('card-cash-') or cat_name.startswith('miles-credit-') or cat_name.startswith('credit-'):
        return None

    current_quarter = quarter_label(transaction.date)

    reward_cats = RewardCategory.objects.filter(
        category=transaction.category
    ).select_related('source')

    configured = {}
    for rc in reward_cats:
        if rc.applicable_quarter and rc.applicable_quarter != current_quarter:
            continue
        configured[rc.source_id] = rc.multiplier

    today = date.today()
    all_sources = Source.objects.exclude(reward_type='none').filter(
        Q(opened_on__isnull=True) | Q(opened_on__lte=today)
    ).filter(
        Q(closed_on__isnull=True) | Q(closed_on__gte=today)
    )

    def effective_raw(source):
        return configured[source.id] if source.id in configured else BLANKET_MULTIPLIERS.get(source.reward_type, Decimal('0'))

    used_raw = effective_raw(transaction.source)
    used_cmp = comparison_value(transaction.source, used_raw)

    best_source = None
    best_raw = used_raw
    best_cmp = used_cmp
    for source in all_sources:
        raw = effective_raw(source)
        cmp = comparison_value(source, raw)
        if cmp > best_cmp:
            best_cmp = cmp
            best_raw = raw
            best_source = source

    if best_source is None:
        return None

    missed_value = (best_cmp - used_cmp) * transaction.amount
    return {
        'best_source_name': best_source.name,
        'best_rate_label': format_rate_label(best_raw, best_source.reward_type),
        'used_rate_label': format_rate_label(used_raw, transaction.source.reward_type),
        'missed_value': missed_value,
        'amount': transaction.amount,
    }


def format_rate_label(multiplier, reward_type):
    if reward_type == 'cashback':
        percent = multiplier * 100
        if percent == int(percent):
            return f"{int(percent)}%"
        return f"{percent:.2f}%"
    return f"{multiplier:.2f}x"


def build_card_recommendations():
    """For each category with configured reward multipliers, return the best card.
    Blanket rates from all active cards are considered alongside explicit RewardCategory entries,
    so a card with a strong all-category rate appears as a candidate for every relevant category.
    """
    current_quarter = quarter_label(date.today())
    today = date.today()

    active_sources = list(Source.objects.exclude(reward_type='none').filter(
        Q(opened_on__isnull=True) | Q(opened_on__lte=today)
    ).filter(
        Q(closed_on__isnull=True) | Q(closed_on__gte=today)
    ))

    reward_cats = RewardCategory.objects.select_related('source', 'category').filter(
        source__in=active_sources
    )

    # Build: category -> {source_id -> (multiplier, is_quarterly)}
    # Start with blanket rates for all active sources across every category that has
    # at least one explicit RewardCategory entry.
    explicit_categories = {}  # category -> set of source_ids with explicit entries
    configured = {}           # (category, source_id) -> (multiplier, is_quarterly)

    for rc in reward_cats:
        active_q = (not rc.applicable_quarter) or (rc.applicable_quarter == current_quarter)
        if not active_q:
            continue
        cat = rc.category
        if cat not in explicit_categories:
            explicit_categories[cat] = set()
        explicit_categories[cat].add(rc.source_id)
        configured[(cat, rc.source_id)] = (rc.multiplier, bool(rc.applicable_quarter))

    recommendations = []
    for category in sorted(explicit_categories.keys(), key=lambda c: c.name):
        entries = []
        for source in active_sources:
            if (category, source.id) in configured:
                raw_multiplier, is_quarterly = configured[(category, source.id)]
            else:
                raw_multiplier = BLANKET_MULTIPLIERS.get(source.reward_type, Decimal('0'))
                is_quarterly = False
            entries.append((source, comparison_value(source, raw_multiplier), raw_multiplier, is_quarterly))

        sorted_entries = sorted(entries, key=lambda e: e[1], reverse=True)
        best_source, best_cmp, best_raw, is_quarterly = sorted_entries[0]
        runner_up = sorted_entries[1] if len(sorted_entries) > 1 else None
        recommendations.append({
            'category': category,
            'best_source': best_source,
            'best_multiplier': best_raw,
            'best_rate_label': format_rate_label(best_raw, best_source.reward_type),
            'best_reward_type': best_source.reward_type,
            'is_quarterly': is_quarterly,
            'current_quarter': current_quarter,
            'runner_up': {
                'source': runner_up[0],
                'multiplier': runner_up[2],
                'rate_label': format_rate_label(runner_up[2], runner_up[0].reward_type),
                'reward_type': runner_up[0].reward_type,
            } if runner_up else None,
        })
    return recommendations


def quarter_date_range(label):
    if not label or "Q" not in label:
        return None, None
    year_part, quarter_part = label.split("Q", 1)
    try:
        year = int(year_part)
        quarter = int(quarter_part)
    except ValueError:
        return None, None
    if quarter < 1 or quarter > 4:
        return None, None
    start_month = 1 + (quarter - 1) * 3
    end_month = start_month + 2
    start_date = datetime(year, start_month, 1).date()
    end_day = calendar.monthrange(year, end_month)[1]
    end_date = datetime(year, end_month, end_day).date()
    return start_date, end_date

# Create your views here.
def index(request):
    """
    View function for the index page of the budget tool.
    """
    # Auto-generate due recurring transactions on page load
    generate_due_transactions()

    category_filter = request.GET.get('category')
    source_filter = request.GET.get('source')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    search_query = request.GET.get('search', '').strip()

    transactions_list = annotate_net_amount(
        Transaction.objects.all()
    ).order_by('-date')

    if search_query:
        transactions_list = transactions_list.filter(
            Q(description__icontains=search_query) |
            Q(notes__icontains=search_query) |
            Q(tags__icontains=search_query)
        )
    if category_filter:
        transactions_list = transactions_list.filter(
            Q(category__reporting_category_id=category_filter) | Q(category__id=category_filter)
        )
    if source_filter:
        transactions_list = transactions_list.filter(source__id=source_filter)
    if start_date:
        transactions_list = transactions_list.filter(date__gte=start_date)
    if end_date:
        transactions_list = transactions_list.filter(date__lte=end_date)

    paginator = Paginator(transactions_list, 25)

    page_number = request.GET.get('page',1)
    transactions = paginator.get_page(page_number)
    categories = Category.objects.all()
    sources = Source.objects.all()
    today = datetime.now().strftime("%Y-%m-%d")

    # Get upcoming recurring transactions (next 7 days)
    upcoming_transactions = get_upcoming_transactions()

    context = {
        'transactions': transactions,
        'categories': categories,
        'sources': sources,
        'today': today,
        'selected_category': category_filter,
        'selected_source': source_filter,
        'start_date': start_date,
        'end_date': end_date,
        'search_query': search_query,
        'upcoming_transactions': upcoming_transactions,
    }
    return render(request, "tracker/index.html", context)

def add_transaction(request):
    """
    View function for adding a transaction.
    This is a placeholder and will be implemented later.
    """
    # create a new transaction and handle the form submission
    # print(request.POST)
    if not request.POST:
        # If the request is not a POST, redirect to the index page
        return HttpResponseRedirect("/")
    if not 'description' in request.POST:
        # If the 'description' field is not in the POST data, redirect to the index page
        # This is a simple validation check
        return HttpResponseRedirect("/")
    if not 'amount' in request.POST:
        # If the 'amount' field is not in the POST data, redirect to the index page
        # This is a simple validation check
        return HttpResponseRedirect("/")
    if not 'date' in request.POST or not is_valid_date(request.POST['date']):
        # If the 'date' field is not in the POST data, redirect to the index page
        # This is a simple validation check
        return HttpResponseRedirect("/")
    if not 'category' in request.POST or not 'source' in request.POST:
        # If neither category nor source is provided, use default values
        # Redirect to the index page
        return HttpResponseRedirect("/")
    
    try:
        category = Category.objects.get(id=request.POST['category'])  # Get the category object from the database
    except Category.DoesNotExist:
        # If the category does not exist, set it to None
        # This will allow the transaction to be saved without a category
        category = None
    try:
        source = Source.objects.get(id=request.POST['source'])  # Get the source object from the database
    except Source.DoesNotExist:
        # If the source does not exist, set it to None
        # This will allow the transaction to be saved without a source
        source = None
    
    # implement math logic for transaction amount
    amount = 0
    if request.POST['amount']:
        try:
            amount = float(request.POST['amount'])
            print(f'Requested amount: {request.POST["amount"]}, Parsed amount: {amount}')
        except ValueError:
            # Handle invalid amount input
            print("Invalid amount value")
            return HttpResponseRedirect("/")

    reimbursement = 0
    reimbursement_value = request.POST.get('reimbursement')
    if reimbursement_value:
        try:
            reimbursement = float(reimbursement_value)
        except ValueError:
            print("Invalid reimbursement value")
            return HttpResponseRedirect("/")

    new_transaction = Transaction(
        description=request.POST['description'],
        amount=amount,
        reimbursement=reimbursement,
        date=request.POST['date'],
        category=category,
        source=source,
        tags=request.POST.get('tags', '').strip(),
        notes=request.POST.get('notes', '').strip(),
    )
    new_transaction.save()

    # Check for potential duplicates (same date + amount, excluding this transaction)
    duplicates = Transaction.objects.filter(
        date=new_transaction.date,
        amount=new_transaction.amount,
    ).exclude(id=new_transaction.id)
    if duplicates.exists():
        dup = duplicates.first()
        messages.warning(
            request,
            f'Possible duplicate: "{dup.description}" on {dup.date} for ${dup.amount:.2f} already exists.'
        )

    tip = get_better_card_tip(new_transaction)
    if tip:
        messages.info(
            request,
            f"Tip: {tip['best_source_name']} earns more on {new_transaction.category.name} "
            f"({tip['best_rate_label']} vs {tip['used_rate_label']}) — "
            f"~${tip['missed_value']:.2f} more in value on this ${tip['amount']:.2f} purchase."
        )

    referring_url = request.META.get('HTTP_REFERER', '/')
    return HttpResponseRedirect(referring_url)

def add_reward_credit(request):
    if request.method != "POST":
        return HttpResponseRedirect("/rewards/")

    source_id = request.POST.get("source")
    amount_value = request.POST.get("amount")
    date_value = request.POST.get("date")
    description = (request.POST.get("description") or "").strip()
    reward_reason = (request.POST.get("reward_reason") or "").strip()
    credit_type = (request.POST.get("credit_type") or "reward").strip()

    if not source_id or not amount_value or not date_value or not is_valid_date(date_value):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/rewards/"))

    try:
        source = Source.objects.get(id=source_id)
    except Source.DoesNotExist:
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/rewards/"))

    try:
        amount = abs(float(amount_value))
    except ValueError:
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/rewards/"))

    if not description:
        if credit_type == "card_cash":
            description = "Card cash credit"
        elif credit_type == "miles":
            description = "Miles credit"
        elif reward_reason == "signup":
            description = "Signup bonus credit"
        elif reward_reason == "anniversary":
            description = "Anniversary bonus credit"
        else:
            description = "Reward credit"

    if credit_type == "card_cash":
        category_prefix = "card-cash-"
    elif credit_type == "miles":
        category_prefix = "miles-credit-"
    else:
        category_prefix = "credit-"

    credit_category_name = f"{category_prefix}{source.name}"
    credit_category, _ = Category.objects.get_or_create(
        name=credit_category_name,
        defaults={"budget": 0},
    )
    # Credits should not be assigned to the income reporting category.
    # If a credit category was previously assigned to income, clear it.
    if credit_category.reporting_category_id is not None:
        income_category = Category.objects.filter(name__iexact="income").first()
        if income_category and credit_category.reporting_category_id == income_category.id:
            credit_category.reporting_category = None
            credit_category.save(update_fields=["reporting_category"])

    Transaction.objects.create(
        description=description,
        amount=amount,
        reimbursement=0,
        date=date_value,
        category=credit_category,
        source=source,
    )

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/rewards/"))

def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def add_months(d, months):
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def get_next_occurrence(recurring):
    if recurring.frequency == 'weekly':
        if recurring.last_generated:
            next_date = recurring.last_generated + timedelta(weeks=recurring.interval)
        else:
            next_date = recurring.start_date
            if recurring.day_of_week is not None:
                days_ahead = recurring.day_of_week - next_date.weekday()
                if days_ahead < 0:
                    days_ahead += 7
                next_date = next_date + timedelta(days=days_ahead)
        return next_date

    elif recurring.frequency == 'monthly':
        if recurring.last_generated:
            next_date = add_months(recurring.last_generated, recurring.interval)
        else:
            next_date = recurring.start_date
        day = min(recurring.day_of_month, calendar.monthrange(next_date.year, next_date.month)[1])
        next_date = next_date.replace(day=day)
        return next_date

    elif recurring.frequency == 'yearly':
        if recurring.last_generated:
            next_date = add_months(recurring.last_generated, recurring.interval * 12)
        else:
            next_date = recurring.start_date
        day = min(recurring.day_of_month, calendar.monthrange(next_date.year, next_date.month)[1])
        next_date = next_date.replace(day=day)
        return next_date

    return None


def generate_due_transactions():
    today = date.today()
    recurring_list = RecurringTransaction.objects.filter(is_active=True)

    for recurring in recurring_list:
        if recurring.end_date and recurring.end_date < today:
            recurring.is_active = False
            recurring.save()
            continue

        next_date = get_next_occurrence(recurring)

        while next_date and next_date <= today:
            if recurring.end_date and next_date > recurring.end_date:
                recurring.is_active = False
                recurring.save()
                break
            Transaction.objects.create(
                description=recurring.description,
                amount=recurring.amount,
                date=next_date,
                category=recurring.category,
                source=recurring.source,
                recurring_source=recurring,
            )
            recurring.last_generated = next_date
            recurring.save()
            next_date = get_next_occurrence(recurring)


def get_upcoming_transactions():
    today = date.today()
    upcoming = []
    for recurring in RecurringTransaction.objects.filter(is_active=True):
        next_date = get_next_occurrence(recurring)
        if next_date and today < next_date <= today + timedelta(days=7):
            upcoming.append({
                'recurring': recurring,
                'date': next_date,
            })
    upcoming.sort(key=lambda x: x['date'])
    return upcoming


def add_recurring_transaction(request):
    if request.method != "POST":
        return HttpResponseRedirect("/settings/")

    description = request.POST.get('description', '').strip()
    amount_str = request.POST.get('amount', '').strip()
    frequency = request.POST.get('frequency', 'monthly')
    interval_str = request.POST.get('interval', '1')
    day_of_month_str = request.POST.get('day_of_month', '1')
    day_of_week_str = request.POST.get('day_of_week', '')
    start_date_str = request.POST.get('start_date', '')
    end_date_str = request.POST.get('end_date', '')

    if not description or not amount_str or not start_date_str or not is_valid_date(start_date_str):
        return HttpResponseRedirect("/settings/")

    try:
        amount = float(amount_str)
    except ValueError:
        return HttpResponseRedirect("/settings/")

    try:
        interval = int(interval_str)
    except ValueError:
        interval = 1

    try:
        day_of_month = int(day_of_month_str)
    except ValueError:
        day_of_month = 1

    day_of_week = None
    if day_of_week_str:
        try:
            day_of_week = int(day_of_week_str)
        except ValueError:
            pass

    try:
        category = Category.objects.get(id=request.POST.get('category'))
    except (Category.DoesNotExist, ValueError, TypeError):
        category = None

    try:
        source = Source.objects.get(id=request.POST.get('source'))
    except (Source.DoesNotExist, ValueError, TypeError):
        source = None

    end_date = None
    if end_date_str and is_valid_date(end_date_str):
        end_date = end_date_str

    RecurringTransaction.objects.create(
        description=description,
        amount=amount,
        category=category,
        source=source,
        frequency=frequency,
        interval=interval,
        day_of_month=day_of_month,
        day_of_week=day_of_week,
        start_date=start_date_str,
        end_date=end_date,
    )

    return HttpResponseRedirect("/settings/")


def edit_recurring_transaction(request, recurring_id):
    try:
        recurring = RecurringTransaction.objects.get(id=recurring_id)
    except RecurringTransaction.DoesNotExist:
        return HttpResponseRedirect("/settings/")

    if request.method != "POST":
        return HttpResponseRedirect("/settings/")

    description = request.POST.get('description', '').strip()
    if description:
        recurring.description = description

    amount_str = request.POST.get('amount', '').strip()
    if amount_str:
        try:
            recurring.amount = float(amount_str)
        except ValueError:
            pass

    frequency = request.POST.get('frequency')
    if frequency in ('weekly', 'monthly', 'yearly'):
        recurring.frequency = frequency

    interval_str = request.POST.get('interval', '')
    if interval_str:
        try:
            recurring.interval = int(interval_str)
        except ValueError:
            pass

    day_of_month_str = request.POST.get('day_of_month', '')
    if day_of_month_str:
        try:
            recurring.day_of_month = int(day_of_month_str)
        except ValueError:
            pass

    day_of_week_str = request.POST.get('day_of_week', '')
    if day_of_week_str:
        try:
            recurring.day_of_week = int(day_of_week_str)
        except ValueError:
            pass
    elif recurring.frequency != 'weekly':
        recurring.day_of_week = None

    start_date_str = request.POST.get('start_date', '')
    if start_date_str and is_valid_date(start_date_str):
        recurring.start_date = start_date_str

    end_date_str = request.POST.get('end_date', '')
    if end_date_str and is_valid_date(end_date_str):
        recurring.end_date = end_date_str
    elif not end_date_str:
        recurring.end_date = None

    is_active = request.POST.get('is_active')
    if is_active is not None:
        recurring.is_active = is_active == 'on'

    try:
        recurring.category = Category.objects.get(id=request.POST.get('category'))
    except (Category.DoesNotExist, ValueError, TypeError):
        recurring.category = None

    try:
        recurring.source = Source.objects.get(id=request.POST.get('source'))
    except (Source.DoesNotExist, ValueError, TypeError):
        recurring.source = None

    recurring.save()
    return HttpResponseRedirect("/settings/")


def delete_recurring_transaction(request, recurring_id):
    try:
        recurring = RecurringTransaction.objects.get(id=recurring_id)
        recurring.delete()
    except RecurringTransaction.DoesNotExist:
        pass
    return HttpResponseRedirect("/settings/")


def add_from_recurring(request, recurring_id):
    if request.method != "POST":
        return HttpResponseRedirect("/")

    try:
        recurring = RecurringTransaction.objects.get(id=recurring_id)
    except RecurringTransaction.DoesNotExist:
        return HttpResponseRedirect("/")

    description = request.POST.get('description', '').strip() or recurring.description
    amount_str = request.POST.get('amount', '').strip()
    date_str = request.POST.get('date', '').strip()

    try:
        amount = float(amount_str) if amount_str else float(recurring.amount)
    except ValueError:
        amount = float(recurring.amount)

    if not date_str or not is_valid_date(date_str):
        next_date = get_next_occurrence(recurring)
        date_str = next_date.isoformat() if next_date else date.today().isoformat()

    try:
        category = Category.objects.get(id=request.POST.get('category'))
    except (Category.DoesNotExist, ValueError, TypeError):
        category = recurring.category

    try:
        source = Source.objects.get(id=request.POST.get('source'))
    except (Source.DoesNotExist, ValueError, TypeError):
        source = recurring.source

    Transaction.objects.create(
        description=description,
        amount=amount,
        date=date_str,
        category=category,
        source=source,
        recurring_source=recurring,
    )

    recurring.last_generated = date_str
    recurring.save()

    return HttpResponseRedirect("/")


def add_category(request):
    """
    View function for adding a new category.
    This is a placeholder and will be implemented later.
    """
    if not request.POST:
        # If the request is not a POST, redirect to the index page
        return HttpResponseRedirect("/")
    
    if not 'name' in request.POST:
        # If the 'name' field is not in the POST data, redirect to the index page
        return HttpResponseRedirect("/")

    budget_value = request.POST.get('budget')
    reporting_category_id = request.POST.get('reporting_category') or None
    if reporting_category_id:
        budget_value = None

    category_defaults = {}
    if budget_value not in (None, ""):
        try:
            category_defaults['budget'] = float(budget_value)
        except ValueError:
            category_defaults['budget'] = 0

    new_category, created = Category.objects.get_or_create(
        name=request.POST['name'],
        defaults=category_defaults
    )
    if reporting_category_id:
        new_category.reporting_category_id = int(reporting_category_id)
    elif budget_value not in (None, ""):
        new_category.budget = category_defaults.get('budget', new_category.budget)
    new_category.save()  # Save the new category to the database

    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)

def add_source(request):
    """
    View function for adding a new source.
    This is a placeholder and will be implemented later.
    """
    if not request.POST:
        # If the request is not a POST, redirect to the index page
        return HttpResponseRedirect("/")
    
    if not 'name' in request.POST:
        # If the 'name' field is not in the POST data, redirect to the index page
        return HttpResponseRedirect("/")
    
    new_source, created = Source.objects.get_or_create(name=request.POST['name'])
    new_source.save()  # Save the new source to the database

    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)

def dismiss_recurring_suggestion(request):
    if request.method == "POST":
        description = request.POST.get('description', '').strip()
        if description:
            DismissedSuggestion.objects.get_or_create(description=description)
    return HttpResponseRedirect("/settings/")


def settings_page(request):
    """
    View function for the settings page.
    This is a placeholder and will be implemented later.
    """
    categories = Category.objects.all()  # Retrieve all categories from the database
    reporting_categories = categories.filter(reporting_category__isnull=True)
    total_budget = 0
    for category in categories:
        category.annual_budget = category.budget * 12  # Calculate the annual budget for each category
        category.negative_budget = category.budget * -1
        category.annual_negative_budget = category.annual_budget * -1
        total_budget += category.budget  # Sum the annual budgets for all categories
    sources = Source.objects.all()
    categories_data = list(categories.values('id', 'name'))
    reward_category_map = {}
    reward_categories = list(RewardCategory.objects.select_related('source', 'category'))
    for reward_category in reward_categories:
        source_id = str(reward_category.source_id)
        category_id = str(reward_category.category_id)
        reward_category_map.setdefault(source_id, {})[category_id] = {
            "multiplier": float(reward_category.multiplier),
            "applicable_quarter": reward_category.applicable_quarter,
        }

    date_bounds = Transaction.objects.aggregate(
        min_date=Min('date'),
        max_date=Max('date')
    )
    min_date = date_bounds['min_date']
    max_date = date_bounds['max_date']
    for reward_category in reward_categories:
        if not reward_category.applicable_quarter:
            continue
        quarter_start, quarter_end = quarter_date_range(reward_category.applicable_quarter)
        if not quarter_start or not quarter_end:
            continue
        if not min_date or quarter_start < min_date:
            min_date = quarter_start
        if not max_date or quarter_end > max_date:
            max_date = quarter_end
    if not min_date or not max_date:
        current_year = datetime.now().year
        min_date = datetime(current_year, 1, 1).date()
        max_date = datetime(current_year, 12, 31).date()

    start_label = quarter_label(min_date)
    end_label = quarter_label(max_date)
    start_year, start_quarter = start_label.split("Q")
    end_year, end_quarter = end_label.split("Q")
    start_year = int(start_year)
    start_quarter = int(start_quarter)
    end_year = int(end_year)
    end_quarter = int(end_quarter)

    quarters = []
    year = start_year
    quarter = start_quarter
    while (year, quarter) <= (end_year, end_quarter):
        quarters.append(f"{year}Q{quarter}")
        quarter += 1
        if quarter > 4:
            quarter = 1
            year += 1
    today = datetime.now().strftime("%Y-%m-%d")
    total = ({'name': 'Total', 'budget': total_budget, 'annual_budget': total_budget*12, 'negative_budget':total_budget*-1, 'annual_negative_budget':total_budget*-12})

    recurring_transactions = RecurringTransaction.objects.filter(is_active=True)
    recurring_with_next = []
    for rt in recurring_transactions:
        next_date = get_next_occurrence(rt)
        recurring_with_next.append({
            'recurring': rt,
            'next_date': next_date,
        })

    recurring_suggestions = detect_recurring_patterns()

    context = {
        'categories': categories,
        'reporting_categories': reporting_categories,
        'sources': sources,
        'today': today,
        'total': total,
        'source_reward_map': reward_category_map,
        'quarters': quarters,
        'categories_data': categories_data,
        'recurring_transactions': recurring_with_next,
        'recurring_suggestions': recurring_suggestions,
    }
    return render(request, "tracker/settings.html", context)

def edit_source(request, source_id):
    """
    View function for editing an existing source.
    """
    try:
        source = Source.objects.get(id=source_id)  # Retrieve the source to edit
    except Source.DoesNotExist:
        # If the source does not exist, redirect to the settings page
        return HttpResponseRedirect("/settings/")

    if request.method == "POST":
        # If the request is a POST, update the source name
        if 'name' in request.POST and request.POST['name']:
            source.name = request.POST['name']
        if 'annual_fee' in request.POST and request.POST['annual_fee'] != "":
            try:
                source.annual_fee = float(request.POST['annual_fee'])
            except ValueError:
                print("Invalid annual fee value")
        if 'reward_type' in request.POST and request.POST['reward_type']:
            source.reward_type = request.POST['reward_type']
        if 'signup_bonus_miles' in request.POST and request.POST['signup_bonus_miles'] != "":
            try:
                source.signup_bonus_miles = int(float(request.POST['signup_bonus_miles']))
            except ValueError:
                print("Invalid signup bonus miles value")
        if 'signup_bonus_min_spend' in request.POST and request.POST['signup_bonus_min_spend'] != "":
            try:
                source.signup_bonus_min_spend = float(request.POST['signup_bonus_min_spend'])
            except ValueError:
                print("Invalid signup bonus min spend value")
        if 'opened_on' in request.POST:
            source.opened_on = request.POST['opened_on'] or None
        if 'closed_on' in request.POST:
            source.closed_on = request.POST['closed_on'] or None
        source.save()  # Save the updated source to the database
        posted_multiplier_keys = [key for key in request.POST if key.startswith("reward_multiplier_")]
        posted_category_ids = {key.split("reward_multiplier_")[-1] for key in posted_multiplier_keys}
        for reward_category in RewardCategory.objects.filter(source=source):
            if str(reward_category.category_id) not in posted_category_ids:
                reward_category.delete()
        for multiplier_key in posted_multiplier_keys:
            category_id = multiplier_key.split("reward_multiplier_")[-1]
            multiplier_value = request.POST.get(multiplier_key, "").strip()
            quarter_key = f"reward_quarter_{category_id}"
            quarter_label_value = request.POST.get(quarter_key, "").strip()
            if multiplier_value == "":
                RewardCategory.objects.filter(source=source, category_id=category_id).delete()
                continue
            try:
                multiplier = float(multiplier_value)
            except ValueError:
                print("Invalid reward multiplier value")
                continue
            reward_category, _ = RewardCategory.objects.get_or_create(source=source, category_id=category_id)
            reward_category.multiplier = multiplier
            reward_category.applicable_quarter = quarter_label_value or None
            reward_category.save()
        return HttpResponseRedirect("/settings/")
    
    # Render the edit source template with the current source details
    context = {'source': source}
    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)

def edit_category(request, category_id):
    """
    View function for editing an existing category.
    """
    try:
        category = Category.objects.get(id=category_id)  # Retrieve the category to edit
    except Category.DoesNotExist:
        # If the category does not exist, redirect to the settings page
        return HttpResponseRedirect("/settings/")

    # print(request.POST)

    if request.method == "POST":
        # If the request is a POST, update the category name
        if 'name' in request.POST:
            category.name = request.POST['name']
            category.save()  # Save the updated category to the database
            # print(category.name)
        if 'amount' in request.POST:
            try:
                # Attempt to convert the budget to a decimal
                category.budget = float(request.POST['amount'])
                category.save()  # Save the updated budget to the database
                # print(category.budget)
            except ValueError:
                print("Invalid budget value")  # Handle invalid budget input
        if 'reporting_category' in request.POST:
            reporting_category_id = request.POST.get('reporting_category') or None
            if reporting_category_id:
                category.reporting_category_id = int(reporting_category_id)
            else:
                category.reporting_category = None
            category.save()
        return HttpResponseRedirect("/settings/")  # Redirect to the settings page after saving

    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)

def edit_transaction(request, transaction_id):
    """
    View function for editing an existing transaction.
    """
    try:
        transaction = Transaction.objects.get(id=transaction_id)  # Retrieve the transaction to edit
    except Transaction.DoesNotExist:
        # If the transaction does not exist, redirect to the index page
        return HttpResponseRedirect("/")

    if request.method == "POST":
        # If the request is a POST, update the transaction details
        if 'description' in request.POST and request.POST['description']:
            transaction.description = request.POST['description']
        if 'amount' in request.POST and request.POST['amount']:
            try:
                amount = float(request.POST['amount'])
                transaction.amount = amount  # Update the transaction amount
            except ValueError:
            # Handle invalid amount input
                print(f"Invalid amount value {request.POST['amount']}")
                return HttpResponseRedirect("/")
        if 'reimbursement' in request.POST:
            reimbursement_value = request.POST['reimbursement']
            if reimbursement_value == "":
                transaction.reimbursement = 0
            else:
                try:
                    reimbursement = float(reimbursement_value)
                    transaction.reimbursement = reimbursement
                except ValueError:
                    print(f"Invalid reimbursement value {request.POST['reimbursement']}")
                    return HttpResponseRedirect("/")
        if 'date' in request.POST and is_valid_date(request.POST['date']):
            transaction.date = request.POST['date']
        if 'category' in request.POST:
            transaction.category_id = request.POST['category']
        if 'source' in request.POST:
            transaction.source_id = request.POST['source']
        if 'tags' in request.POST:
            transaction.tags = request.POST['tags'].strip()
        if 'notes' in request.POST:
            transaction.notes = request.POST['notes'].strip()

        transaction.save()
        referring_url = request.META.get('HTTP_REFERER', '/')
        return HttpResponseRedirect(referring_url)  # Redirect to the referring URL after saving
    
    # Render the edit transaction template with the current transaction details
    context = {'transaction': transaction}
    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)

def delete_source(request, source_id):
    """
    View function for deleting an existing source.
    """
    try:
        source = Source.objects.get(id=source_id)  # Retrieve the source to delete
        source.delete()  # Delete the source from the database
    except Source.DoesNotExist:
        # If the source does not exist, redirect to the settings page
        pass
    
    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)

def delete_category(request, category_id):
    """
    View function for deleting an existing category.
    """
    try:
        category = Category.objects.get(id=category_id)  # Retrieve the category to delete
        category.delete()  # Delete the category from the database
    except Category.DoesNotExist:
        # If the category does not exist, redirect to the settings page
        pass
    
    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)

def delete_transaction(request, transaction_id):
    """
    View function for deleting an existing transaction.
    """
    print(f'Deleting transaction id: {transaction_id}')
    try:
        transaction = Transaction.objects.get(id=transaction_id)  # Retrieve the transaction to delete
        print(f'Deleting transaction: {transaction}')
        transaction.delete()  # Delete the transaction from the database
    except Transaction.DoesNotExist:
        # If the transaction does not exist, redirect to the index page
        pass
    
    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)


def reports_view(request):
    income_total_amount = None
    final_line_data = None 
    year_values = (
        Transaction.objects.annotate(year=ExtractYear("date"))
        .values_list("year", flat=True)
        .distinct()
        .order_by("year")
    )
    # Handle month/year selection
    month = int(request.GET.get("month", datetime.today().month))
    year = int(request.GET.get("year", datetime.today().year))

    month_name = str(year) + '-' + str(month).zfill(2)

    daily_totals = []
    month_data = Month.objects.filter(name=month_name).first()
    if month_data:
        daily_totals = month_data.daily_spend
        income_total_amount = float(month_data.total_income)
        total_spent = float(month_data.total_spend)

        final_line_data = []
        for i, daily_total in enumerate(daily_totals):
            date = datetime(year, month, i + 1)
            final_line_data.append({
                "date": date.strftime("%Y-%m-%d"),
                "cumulative": float(daily_total)
            })
    
    base_transactions = annotate_net_amount(
        Transaction.objects.filter(date__month=month, date__year=year)
    )
    transactions = base_transactions.exclude(category__name__iexact='income').exclude(
        category__reporting_category__name__iexact='income'
    ).exclude(CREDIT_CATEGORY_Q)

    if income_total_amount is None:
        income_total = base_transactions.filter(
            Q(category__name__iexact='income') | Q(category__reporting_category__name__iexact='income')
        ).exclude(CREDIT_CATEGORY_Q).aggregate(total=Sum('net_amount'))

        income_total_amount = float(income_total['total']) if income_total['total'] else 0
    
    # Pie chart data (by category)
    pie_data = (
        annotate_reporting_category(transactions)
        .values("reporting_category_id", "reporting_category_name", "reporting_category_budget")
        .annotate(total=Sum("net_amount") * -1)
    )

    if final_line_data is None:
        # Line chart data (cumulative spend by date)
        daily_totals = (
            transactions.order_by("date")
            .values("date")
            .annotate(daily_total=Sum("net_amount"))
        )
        cumulative_total = 0
        line_data = [{"date": datetime(year, month, 1).strftime("%Y-%m-%d"), "cumulative": 0}]
        dates = {}
        for item in daily_totals:
            cumulative_total += item["daily_total"] * -1
            line_data.append({"date": item["date"].strftime("%Y-%m-%d"), "cumulative": float(cumulative_total)})
            dates[item["date"].isoformat()] = True

        new_line_data = []
        for data in line_data:
            tomorrow = datetime.strptime(data['date'], '%Y-%m-%d') + timedelta(days=1)
            first_of_next_month = datetime(year, month+1, 1) if month < 12 else datetime(year+1, 1, 1)
            while (tomorrow.strftime('%Y-%m-%d') not in dates) and tomorrow < first_of_next_month:
                new_line_data.append({"date": tomorrow.strftime('%Y-%m-%d'), 'cumulative': data['cumulative']})
                dates[tomorrow.isoformat()] = True
                tomorrow = tomorrow + timedelta(days=1)

        ld_idx = nld_idx = 0
        final_line_data = []
        while len(final_line_data) < (len(line_data) + len(new_line_data)):
            if nld_idx >= len(new_line_data) or (ld_idx < len(line_data) and line_data[ld_idx]['date'] < new_line_data[nld_idx]['date']):
                final_line_data.append(line_data[ld_idx])
                ld_idx += 1
            else:
                final_line_data.append(new_line_data[nld_idx])
                nld_idx += 1

    total_spent = float(sum([x["total"] for x in pie_data]))

    # Convert pie chart data to JSON-safe format
    pie_data_safe = []
    all_categories = []
    for item in pie_data:
        total = float(item["total"])
        percentage = total / total_spent * 100 if total_spent else 0
        budget = float(item["reporting_category_budget"] or 0)
        surplus = budget - total
        # print(item)
        # print(f'Category: {item["category__name"]}, Total: {total}, Percentage: {percentage:.2f}%, Budget: {budget}, Surplus: {surplus}')
        # print()
        pie_data_safe.append({
            "category__name": item["reporting_category_name"],
            "total": abs(total),
            "percentage": abs(percentage),
            "budget": abs(budget),
            "surplus": abs(surplus),
        })
        all_categories.append({
            "category__name": item["reporting_category_name"],
            "total": abs(total),
            "percentage": abs(percentage),
            "budget": abs(budget),
            "surplus": surplus,
            "is_surplus_negative": surplus < 0,
            "is_budget_negative": budget < 0,
            "is_negative": total < 0,
        })

    categories = Category.objects.filter(reporting_category__isnull=True)

    total_budget = float(sum(x for x in [cat.budget for cat in categories if cat.budget is not None])) * -1

    for category in categories:
        if category.name == "Income":
            continue
        # Find the category in pie_data_safe and append it
        for item in pie_data_safe:
            if item["category__name"] == category.name:
                break
        else:
            # If not found, append with zero values
            all_categories.append({
                "category__name": category.name,
                "total": 0,
                "percentage": 0,
                "budget": abs(category.budget),
                "surplus": abs(category.budget),
                "is_surplus_negative": category.budget < 0,
                "is_budget_negative": category.budget < 0,
                "is_negative": False,
            })

    all_categories.append({
        "category__name": "Savings",
        "total": float(income_total_amount) - total_spent,
        "percentage": 0,
        "budget": abs(total_budget),
        "surplus": -total_budget + (income_total_amount - total_spent),  
        "is_surplus_negative": total_budget > (income_total_amount - total_spent),
        "is_budget_negative": False,
        "is_negative": (float(income_total_amount) - total_spent) < 0,
    })
    all_categories.append({
        "category__name": "Income",
        "total": abs(float(income_total_amount)),
        "percentage": 0,
        "budget": abs(categories.get(name="Income").budget),
        "surplus": float(categories.get(name="Income").budget) + float(income_total_amount),  
        "is_surplus_negative": categories.get(name="Income").budget < (-1 *  float(income_total_amount)),
        "is_budget_negative": False,
        "is_negative": False,
    })

    table_data = sorted(
        all_categories,
        key=lambda item: (item.get("category__name") or "").lower(),
    )

    # Convert line chart data to JSON-safe format (day-of-month indexed)
    line_data_safe = [
        {"date": i + 1, "cumulative": float(final_line_data[i]["cumulative"])}
        for i in range(len(final_line_data))
    ]

    # Pad completed past months to 31 days so the x-axis aligns with the avg trend line
    is_current_month = (year == datetime.today().year and month == datetime.today().month)
    if not is_current_month and len(line_data_safe) < 31:
        last_val = line_data_safe[-1]["cumulative"] if line_data_safe else 0
        for extra_day in range(len(line_data_safe) + 1, 32):
            line_data_safe.append({"date": extra_day, "cumulative": last_val})

    # Compute average cumulative spend trend over past SPEND_TREND_LOOKBACK_MONTHS months
    avg_trend_values = [0.0] * 31
    months_with_data = 0
    lookback_y, lookback_m = year, month
    for _ in range(SPEND_TREND_LOOKBACK_MONTHS):
        lookback_m -= 1
        if lookback_m == 0:
            lookback_m = 12
            lookback_y -= 1
        monthly = get_monthly_cumulative_spend(lookback_y, lookback_m)
        if any(v > 0 for v in monthly):
            for i in range(31):
                avg_trend_values[i] += monthly[i]
            months_with_data += 1

    has_avg_trend = months_with_data > 0
    avg_trend = [round(v / months_with_data, 2) for v in avg_trend_values] if has_avg_trend else []

    if not month_data:
        # print(final_line_data)
        if final_line_data[1]['date'] == final_line_data[0]['date']:
            final_line_data = final_line_data[1:]  # Remove the first element if it's a duplicate
        # print(final_line_data)
        daily_totals = []
        for element in final_line_data:
            # Append daily spend to the list, defaulting to 0 if not present
            daily_totals.append(float(element["cumulative"]) if element["cumulative"] else 0)

        # print(len(daily_totals))
        if len(daily_totals) < 31:
            # Fill in the remaining days with 0 if there are less than 31 days
            daily_totals.extend([daily_totals[-1]] * (31 - len(daily_totals)))
        # Create a new Month entry if it doesn't exist (not saved — ArrayField requires PostgreSQL)
        month_data = Month(name=month_name, total_spend=total_spent, total_income=income_total_amount, daily_spend=daily_totals)

    context = {
        "selected_month": month,
        "selected_year": year,
        "income": income_total_amount,
        "total_spent": total_spent,
        "pie_data": pie_data_safe,
        "line_data": line_data_safe,
        "avg_trend": avg_trend,
        "has_avg_trend": has_avg_trend,
        "trend_lookback": SPEND_TREND_LOOKBACK_MONTHS,
        "table_data": table_data,
        "months": [(i, calendar.month_name[i]) for i in range(1, 13)],
        "years": list(year_values),
    }

    return render(request, "tracker/reports.html", context)

def import_csv_preview(request):
    if request.method == "POST" and request.FILES.get("csv_file"):
        csv_file = request.FILES["csv_file"]
        decoded_file = TextIOWrapper(csv_file.file, encoding="utf-8")
        reader = csv.DictReader(decoded_file)
        # print("HEADERS:", reader.fieldnames)


        column_aliases = {
            "description": ["description", "desc", "details"],
            "amount": ["amount", "amt", "value"],
            "date": ["date", "transaction date", "timestamp"],
            "category": ["category", "type", "label"],
            "source": ["source", "payment method", "account", "wallet", "account/card from"],
        }

        # Map lowercase header -> original header
        header_map = {header.lower().strip(): header for header in reader.fieldnames}

        # Map desired keys to original headers
        column_map = {}
        for key, aliases in column_aliases.items():
            for alias in aliases:
                if alias.lower() in header_map:
                    column_map[key] = header_map[alias.lower()]
                    break


        # Store headers + rows in session for confirmation step
        preview_rows = []
        rows = []
        for i, row in enumerate(reader):
            # print("RAW CSV ROW:", row)
            if i < 10:  # Show only first 10 rows
                preview_rows.append({k: row.get(v, "") for k, v in column_map.items()})
            rows.append({k: row.get(v, "") for k, v in column_map.items()})

        request.session["csv_data"] = {
            "rows": rows,
            "column_map": column_map,
        }

        # Flag potential duplicates in preview rows
        for row in preview_rows:
            try:
                date_str = row.get("date", "")
                amount_str = row.get("amount", "")
                if date_str and amount_str:
                    try:
                        parsed_date = datetime.strptime(date_str, "%m/%d/%y").date()
                    except ValueError:
                        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    parsed_amount = float(amount_str.replace("$", "").replace(",", ""))
                    if Transaction.objects.filter(date=parsed_date, amount=parsed_amount).exists():
                        row["duplicate"] = True
            except (ValueError, TypeError):
                pass

        return render(request, "tracker/import_preview.html", {
            "preview_rows": preview_rows,
            "column_map": column_map,
        })

    return HttpResponseRedirect("/")

def import_csv_confirm(request):
    data = request.session.get("csv_data", {})
    if not data:
        return HttpResponseRedirect("/")

    column_map = data.get("column_map", {})
    rows = data.get("rows", [])

    for row in rows:
        try:
            category = row.get("category")
            source = row.get("source")
            category_obj = Category.objects.get_or_create(name=category)[0] if category else None
            source_obj = Source.objects.get_or_create(name=source)[0] if source else None

            # Clean date format from m/d/yy to YYYY-MM-DD
            date_str = row.get("date")
            if date_str:
                try:
                    date_cleaned = datetime.strptime(date_str, "%m/%d/%y").date()
                except ValueError:
                    # Handle invalid date format
                    date_cleaned = datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                date_cleaned = None

            # Clean amount format from $1,234.56 to 1234.56
            amount_str = row.get("amount")
            if amount_str:
                amount_cleaned = float(amount_str.replace("$", "").replace(",", ""))
            else:
                amount_cleaned = None
            
            Transaction.objects.get_or_create(
                description=row["description"],
                amount=amount_cleaned,
                date=date_cleaned,
                category=category_obj,
                source=source_obj,
            )
        except Exception as e:
            print(f"Error importing row: {row} — {e}")

    # Clear session after import
    del request.session["csv_data"]

    return HttpResponseRedirect("/")

from django.db.models import Sum, F
from django.shortcuts import render
from datetime import datetime
from calendar import month_name
from .models import Transaction, Category

def ytd_report(request):
    def shift_month(year, month, delta):
        total = (year * 12) + (month - 1) + delta
        new_year = total // 12
        new_month = (total % 12) + 1
        return new_year, new_month

    now = datetime.now()
    year = now.year
    current_month = now.month
    view_mode = request.GET.get("view", "ytd")
    if view_mode not in {"ytd", "ttm"}:
        view_mode = "ytd"

    if view_mode == "ttm":
        months = [shift_month(year, current_month, -offset) for offset in range(11, -1, -1)]
        start_year, start_month = months[0]
        start_date = datetime(start_year, start_month, 1).date()
        end_date = now.date()
        months_count = 12
        transactions = annotate_net_amount(
            Transaction.objects.filter(date__gte=start_date, date__lte=end_date)
        )
    else:
        months = [(year, month) for month in range(1, current_month + 1)]
        months_count = current_month
        # Transactions for the year
        transactions = annotate_net_amount(
            Transaction.objects.filter(date__year=year, date__month__lte=current_month)
        )

    # Total spent per reporting category (excluding income)
    spending_data = (
        annotate_reporting_category(
            transactions.exclude(category__name__iexact='income').exclude(
                category__reporting_category__name__iexact='income'
            ).exclude(CREDIT_CATEGORY_Q)
        )
        .values('reporting_category_id', 'reporting_category_name', 'reporting_category_budget')
        .annotate(total=Sum('net_amount') * -1)
    )

    income_data = (
        annotate_reporting_category(
            transactions.filter(
                Q(category__name__iexact='income') | Q(category__reporting_category__name__iexact='income')
            ).exclude(CREDIT_CATEGORY_Q)
        )
        .values('reporting_category_id', 'reporting_category_name', 'reporting_category_budget')
        .annotate(total=Sum('net_amount'))
    )

    ytd_data = []
    total_spend = 0
    total_budget = 0
    category_pie_data = []
    for entry in spending_data:
        avg_per_month = entry['total'] / months_count
        budget = entry['reporting_category_budget'] or 0
        avg_surplus = budget - avg_per_month
        ytd_data.append({
            'category__name': entry['reporting_category_name'],
            'category__id': entry['reporting_category_id'],
            'total': entry['total'],
            'annual_budget': entry['reporting_category_budget'] * 12 if entry['reporting_category_budget'] else 0,
            'avg_per_month': avg_per_month,
            'budget': budget, 
            'avg_surplus': avg_surplus,
            'total_surplus': avg_surplus * months_count,
        })
        category_pie_data.append({
            "label": entry["reporting_category_name"],
            "total": float(entry["total"]),
            "category_id": entry["reporting_category_id"],
        })
        total_spend += avg_per_month
        total_budget += budget

    # Sort data by total spend descending for consistent display
    ytd_data = sorted(ytd_data, key=lambda x: x["total"], reverse=True)
    category_pie_data = sorted(category_pie_data, key=lambda x: x["total"], reverse=True)

    total_data = {
        'category__name': 'Total Spend',
        'total': (total_spend * months_count),
        'annual_budget': (total_budget * 12),
        'avg_per_month': total_spend,
        'budget': total_budget,
        'avg_surplus': (total_budget - total_spend),
        'total_surplus': ((total_budget - total_spend) * months_count)
    }

    # print(f'total_spend: {total_spend}')
    # print(f'total_budget: {total_budget}')
    for entry in income_data:
        avg_per_month = entry['total'] / months_count
        budget = -1*entry['reporting_category_budget'] or 0
        avg_surplus = -1*(budget - avg_per_month)
        # print(f'income: {avg_per_month}')
        income_data = {
            'category__name': entry['reporting_category_name'],
            'total': entry['total'],
            'category__id': entry['reporting_category_id'],
            'annual_budget': entry['reporting_category_budget'] * -12 if entry['reporting_category_budget'] else 0,
            'avg_per_month': avg_per_month,
            'budget': budget,
            'avg_surplus': avg_surplus,
            'total_surplus': avg_surplus * months_count,
        }
    savings_data = {
        'category__name': 'Savings',
        'total': income_data['total'] - (total_spend * months_count),
        'annual_budget': income_data['annual_budget'] - (total_budget * 12),
        'avg_per_month': income_data['avg_per_month'] - total_spend,
        'budget': income_data['budget'] - total_budget,
        'avg_surplus': (income_data['avg_surplus'] + (total_budget - total_spend)),
        'total_surplus': (income_data['total_surplus'] + ((total_budget - total_spend) * months_count))
    }
    # Net savings = income - spending for each month
    savings_chart_data = []
    savings_history = []

    for chart_year, chart_month in months:
        if view_mode == "ttm":
            month_label = f"{calendar.month_abbr[chart_month]} '{str(chart_year)[-2:]}"
        else:
            month_label = month_name[chart_month][:3]
        income = transactions.filter(
            date__year=chart_year,
            date__month=chart_month,
            category__name__iexact='income'
        ).exclude(CREDIT_CATEGORY_Q).aggregate(total=Sum('net_amount'))['total'] or 0

        spend = transactions.exclude(
            category__name__iexact='income'
        ).exclude(CREDIT_CATEGORY_Q).filter(
            date__year=chart_year,
            date__month=chart_month
        ).aggregate(total=Sum('net_amount'))['total'] or 0

        savings = income + spend
        savings_history.append(savings)
        recent_history = savings_history[-3:]

        running_average = sum(recent_history) / len(recent_history)

        savings_chart_data.append({
            'month': month_label,
            'income': float(round(income, 2)),
            'spend': float(round(spend, 2)),
            'savings': float(round(savings, 2)),
            'running': float(round(running_average, 2))
        })

    print(savings_chart_data)
    context = {
        'ytd_data': ytd_data,
        'savings_chart_data': savings_chart_data,
        'total_data': total_data,
        'income_data': income_data,
        'savings_data': savings_data,
        'year': year,
        'category_pie_data': category_pie_data,
        'view_mode': view_mode,
        'page_title': 'Trailing 12 Months Tracker' if view_mode == 'ttm' else 'Year-to-Date Tracker',
        'overview_title': 'Trailing 12 Months Spending Overview' if view_mode == 'ttm' else 'Year-to-Date Spending Overview',
        'period_label': 'Trailing 12 Months' if view_mode == 'ttm' else 'YTD',
    }

    return render(request, 'tracker/ytd.html', context)

def mtd_report(request):
    month = datetime.now().month
    year = datetime.now().year

    # Transactions for the current month
    transactions = annotate_net_amount(
        Transaction.objects.filter(date__year=year, date__month=month)
    )

    # Total spent per reporting category (excluding income)
    spending_data = (
        annotate_reporting_category(
            transactions.exclude(category__name__iexact='income').exclude(
                category__reporting_category__name__iexact='income'
            ).exclude(CREDIT_CATEGORY_Q)
        )
        .values('reporting_category_name', 'reporting_category_budget')
        .annotate(total=Sum('net_amount') * -1)
    )

    mtd_data = []
    for entry in spending_data:
        avg_per_month = entry['total'] / month
        budget = entry['reporting_category_budget'] or 0
        avg_surplus = budget - avg_per_month
        mtd_data.append({
            'category__name': entry['reporting_category_name'],
            'total': entry['total'],
            'avg_per_month': avg_per_month,
            'budget': budget,
            'avg_surplus': avg_surplus,
            'total_surplus': avg_surplus * month,
        })

    # Net savings = income - spending for each month
    savings_chart_data = []
    for day in range(1, 31):  # Assuming 30 days in a month for simplicity
        income = transactions.filter(
            date__day=day,
            category__name__iexact='income'
        ).exclude(CREDIT_CATEGORY_Q).aggregate(total=Sum('net_amount'))['total'] or 0

        spend = transactions.exclude(
            category__name__iexact='income'
        ).exclude(CREDIT_CATEGORY_Q).filter(date__day=day).aggregate(total=Sum('net_amount'))['total'] or 0

        savings_chart_data.append({
            'day': day,
            'income': float(round(income, 2)),
            'spend': float(round(spend, 2)),
            'savings': float(round(income + spend, 2))
        })

    print(savings_chart_data)
    context = {
        'mtd_data': mtd_data,
        'savings_chart_data': savings_chart_data
    }

    return render(request, 'tracker/mtd.html', context)


def rewards_tracker(request):
    selected_year = request.GET.get('year')
    selected_year = int(selected_year) if selected_year and selected_year.isdigit() else datetime.now().year

    transaction_years = (
        Transaction.objects.annotate(year=ExtractYear("date"))
        .values_list("year", flat=True)
        .distinct()
        .order_by("year")
    )
    quarter_years = RewardCategory.objects.exclude(applicable_quarter__isnull=True).exclude(
        applicable_quarter__exact=""
    ).values_list("applicable_quarter", flat=True)
    quarter_year_values = []
    for label in quarter_years:
        if "Q" in label:
            year_part = label.split("Q", 1)[0]
            if year_part.isdigit():
                quarter_year_values.append(int(year_part))
    years = sorted(set(list(transaction_years) + quarter_year_values))
    if not years:
        years = [selected_year]
    if selected_year not in years and years:
        selected_year = years[-1]

    sources = Source.objects.annotate(
        has_rewards=Case(
            When(Exists(RewardCategory.objects.filter(source=OuterRef('pk'))), then=Value(True)),
            When(~Q(reward_type='none'), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
    ).order_by('-has_rewards', 'name')
    sources_data = []
    total_miles_earned = 0
    total_cashback_earned = 0
    total_credits_earned = 0
    today = datetime.now().strftime("%Y-%m-%d")

    def cashback_label(value):
        percent = value * 100
        if percent.is_integer():
            return f"{int(percent)}%"
        return f"{percent:.2f}%"

    def signup_bonus_miles_for_year(source):
        bonus_miles = float(source.signup_bonus_miles or 0)
        min_spend = float(source.signup_bonus_min_spend or 0)
        if bonus_miles <= 0 or min_spend <= 0:
            return 0, None

        awarded_on = source.signup_bonus_awarded_on
        bonus_transactions = list(
            Transaction.objects.filter(source=source)
            .exclude(Q(category__name__iexact='income') | Q(category__reporting_category__name__iexact='income'))
            .exclude(CREDIT_CATEGORY_Q)
            .exclude(Q(category__name__icontains='rent') | Q(category__reporting_category__name__icontains='rent'))
            .order_by('date', 'id')
            .values_list('date', 'amount')
        )
        if awarded_on:
            total_spend = 0
            for txn_date, txn_amount in bonus_transactions:
                if txn_date > awarded_on:
                    break
                total_spend += abs(float(txn_amount))
            if total_spend < min_spend:
                awarded_on = None
                source.signup_bonus_awarded_on = None
                source.save(update_fields=['signup_bonus_awarded_on'])
        if not awarded_on:
            total_spend = 0
            for txn_date, txn_amount in bonus_transactions:
                total_spend += abs(float(txn_amount))
                if total_spend >= min_spend:
                    awarded_on = txn_date
                    source.signup_bonus_awarded_on = awarded_on
                    source.save(update_fields=['signup_bonus_awarded_on'])
                    break

        if awarded_on and awarded_on.year == selected_year:
            return bonus_miles, awarded_on
        return 0, awarded_on

    for source in sources:
        reward_entries = RewardCategory.objects.filter(source=source).select_related('category', 'category__reporting_category')
        reward_rows = []
        total_rewards = 0
        total_rewards_miles = 0
        total_rewards_cash = 0
        total_spend = 0
        covered_spend = 0

        base_transactions = Transaction.objects.filter(source=source, date__year=selected_year).exclude(
            Q(category__name__iexact='income') | Q(category__reporting_category__name__iexact='income')
        ).exclude(CREDIT_CATEGORY_Q)
        total_source_spend = abs(float(base_transactions.aggregate(total=Sum('amount'))['total'] or 0))

        if source.reward_type == 'card_cash_miles':
            rent_filter = Q(category__name__icontains='rent') | Q(category__reporting_category__name__icontains='rent')
            rent_transactions = base_transactions.filter(rent_filter)
            non_rent_transactions = base_transactions.exclude(rent_filter)

            rent_spend = abs(float(rent_transactions.aggregate(total=Sum('amount'))['total'] or 0))
            non_rent_spend = abs(float(non_rent_transactions.aggregate(total=Sum('amount'))['total'] or 0))

            non_rent_miles = non_rent_spend * 2.0
            card_cash_earned = non_rent_spend * 0.04
            card_cash_credit_total = Transaction.objects.filter(
                source=source,
                category__name__istartswith='card-cash-',
                date__year=selected_year
            ).aggregate(total=Sum('amount'))['total'] or 0
            card_cash_credits = abs(float(card_cash_credit_total))
            card_cash_pool = card_cash_earned + card_cash_credits
            rent_coverable = card_cash_pool / 0.03 if card_cash_pool else 0
            rent_points = min(rent_spend, rent_coverable)
            bilt_cash_used = rent_points * 0.03
            miles_credit_total = Transaction.objects.filter(
                source=source,
                category__name__istartswith='miles-credit-',
                date__year=selected_year
            ).aggregate(total=Sum('amount'))['total'] or 0
            miles_credit_amount = abs(float(miles_credit_total))
            credit_total = Transaction.objects.filter(
                source=source,
                category__name__istartswith='credit-',
                date__year=selected_year
            ).aggregate(total=Sum('amount'))['total'] or 0
            credit_amount = abs(float(credit_total))

            if non_rent_spend > 0:
                reward_rows.append({
                    'category_name': 'Non-Rent Spend (Miles)',
                    'reporting_category_name': None,
                    'multiplier': 2.0,
                    'multiplier_label': None,
                    'spend': non_rent_spend,
                    'rewards': non_rent_miles,
                    'rewards_format': 'miles',
                })
                reward_rows.append({
                    'category_name': 'Card Cash Earned',
                    'reporting_category_name': None,
                    'multiplier': 0.04,
                    'multiplier_label': cashback_label(0.04),
                    'spend': non_rent_spend,
                    'rewards': card_cash_earned,
                    'rewards_format': 'cash',
                })

            if card_cash_credits > 0:
                reward_rows.append({
                    'category_name': 'Card Cash Credits',
                    'reporting_category_name': None,
                    'multiplier': 0,
                    'multiplier_label': 'Credit',
                    'spend': 0,
                    'rewards': card_cash_credits,
                    'rewards_format': 'cash',
                })
            if miles_credit_amount > 0:
                reward_rows.append({
                    'category_name': 'Miles Credits',
                    'reporting_category_name': None,
                    'multiplier': 0,
                    'multiplier_label': 'Credit',
                    'spend': 0,
                    'rewards': miles_credit_amount,
                    'rewards_format': 'miles',
                })

            if rent_spend > 0:
                reward_rows.append({
                    'category_name': 'Rent (Redeemed Points)',
                    'reporting_category_name': None,
                    'multiplier': 1.0,
                    'multiplier_label': f'1x (${bilt_cash_used:.2f} Bilt Cash used)',
                    'spend': rent_points,
                    'rewards': rent_points,
                    'rewards_format': 'miles',
                })

            card_cash_remaining = card_cash_pool - bilt_cash_used
            reward_rows.append({
                'category_name': 'Bilt Cash Remaining',
                'reporting_category_name': None,
                'multiplier': 0,
                'multiplier_label': 'Balance',
                'spend': 0,
                'rewards': card_cash_remaining,
                'rewards_format': 'cash',
            })

            if credit_amount > 0:
                reward_rows.append({
                    'category_name': 'Credits',
                    'reporting_category_name': None,
                    'multiplier': 0,
                    'multiplier_label': 'Credit',
                    'spend': 0,
                    'rewards': credit_amount,
                    'rewards_format': 'cash',
                    'is_credit': True,
                })

            bonus_miles, _ = signup_bonus_miles_for_year(source)
            if bonus_miles > 0:
                reward_rows.append({
                    'category_name': 'Signup Bonus',
                    'reporting_category_name': None,
                    'multiplier': 0,
                    'multiplier_label': 'Bonus',
                    'spend': 0,
                    'rewards': bonus_miles,
                    'rewards_format': 'miles',
                })

            total_spend = non_rent_spend + rent_spend
            total_rewards_miles = non_rent_miles + rent_points + bonus_miles + miles_credit_amount
            total_rewards_cash = credit_amount
            total_rewards = total_rewards_miles + total_rewards_cash

            sources_data.append({
                'source': source,
                'reward_rows': reward_rows,
                'has_quarterly': False,
                'total_spend': total_spend,
                'total_rewards': total_rewards,
                'total_rewards_miles': total_rewards_miles,
                'total_rewards_cash': total_rewards_cash,
            })
            total_miles_earned += total_rewards_miles
            total_credits_earned += total_rewards_cash
            continue

        for entry in reward_entries:
            if entry.applicable_quarter:
                quarter_start, quarter_end = quarter_date_range(entry.applicable_quarter)
                if not quarter_start or not quarter_end or quarter_start.year != selected_year:
                    continue
            transaction_query = Transaction.objects.filter(
                source=source,
                category=entry.category
            )
            if entry.applicable_quarter:
                quarter_start, quarter_end = quarter_date_range(entry.applicable_quarter)
                if quarter_start and quarter_end:
                    transaction_query = transaction_query.filter(
                        date__gte=quarter_start,
                        date__lte=quarter_end
                    )
            else:
                transaction_query = transaction_query.filter(date__year=selected_year)
            transaction_total = transaction_query.aggregate(total=Sum('amount'))['total'] or 0
            spend = abs(float(transaction_total))
            multiplier = float(entry.multiplier)
            rewards = spend * multiplier

            reward_rows.append({
                'category_name': entry.category.name,
                'reporting_category_name': entry.category.reporting_category.name if entry.category.reporting_category else None,
                'multiplier': multiplier,
                'multiplier_label': cashback_label(multiplier) if source.reward_type == 'cashback' else None,
                'applicable_quarter': entry.applicable_quarter,
                'quarter_label': f"Q{entry.applicable_quarter.split('Q', 1)[1]}" if entry.applicable_quarter and "Q" in entry.applicable_quarter else None,
                'spend': spend,
                'rewards': rewards,
                'rewards_format': 'cash' if source.reward_type == 'cashback' else 'miles',
            })
            covered_spend += spend
            total_spend += spend
            total_rewards += rewards
            if source.reward_type == 'miles':
                total_rewards_miles += rewards
            elif source.reward_type == 'cashback':
                total_rewards_cash += rewards

        if source.reward_type == 'miles':
            bonus_miles, _ = signup_bonus_miles_for_year(source)
            if bonus_miles > 0:
                reward_rows.append({
                    'category_name': 'Signup Bonus',
                    'reporting_category_name': None,
                    'multiplier': 0,
                    'multiplier_label': 'Bonus',
                    'spend': 0,
                    'rewards': bonus_miles,
                    'rewards_format': 'miles',
                    'is_bonus': True,
                })
                total_rewards += bonus_miles
                total_rewards_miles += bonus_miles

        if source.reward_type == 'miles':
            miles_credit_total = Transaction.objects.filter(
                source=source,
                category__name__istartswith='miles-credit-',
                date__year=selected_year
            ).aggregate(total=Sum('amount'))['total'] or 0
            miles_credit_amount = abs(float(miles_credit_total))
            if miles_credit_amount > 0:
                reward_rows.append({
                    'category_name': 'Miles Credits',
                    'reporting_category_name': None,
                    'multiplier': 0,
                    'multiplier_label': 'Credit',
                    'spend': 0,
                    'rewards': miles_credit_amount,
                    'rewards_format': 'miles',
                    'is_credit': True,
                })
                total_rewards += miles_credit_amount
                total_rewards_miles += miles_credit_amount
            credit_total = Transaction.objects.filter(
                source=source,
                category__name__istartswith='credit-',
                date__year=selected_year
            ).aggregate(total=Sum('amount'))['total'] or 0
            credit_amount = abs(float(credit_total))
            if credit_amount > 0:
                reward_rows.append({
                    'category_name': 'Credits',
                    'reporting_category_name': None,
                    'multiplier': 0,
                    'multiplier_label': 'Credit',
                    'spend': 0,
                    'rewards': credit_amount,
                    'rewards_format': 'cash',
                    'is_credit': True,
                })
                total_rewards += credit_amount
                total_rewards_cash += credit_amount
        if source.reward_type == 'cashback':
            credit_total = Transaction.objects.filter(
                source=source,
                category__name__istartswith='credit-',
                date__year=selected_year
            ).aggregate(total=Sum('amount'))['total'] or 0
            credit_amount = abs(float(credit_total))
            if credit_amount > 0:
                reward_rows.append({
                    'category_name': 'Credits',
                    'reporting_category_name': None,
                    'multiplier': 0,
                    'multiplier_label': 'Credit',
                    'spend': 0,
                    'rewards': credit_amount,
                    'rewards_format': 'cash',
                    'is_credit': True,
                })
                total_rewards += credit_amount
                total_rewards_cash += credit_amount

        blanket_spend = max(total_source_spend - covered_spend, 0)
        blanket_multiplier = 0.01 if source.reward_type == 'cashback' else 1.0
        blanket_rewards = blanket_spend * blanket_multiplier
        if blanket_spend > 0 and source.reward_type != 'none':
            reward_rows.append({
                'category_name': 'All Other',
                'reporting_category_name': None,
                'multiplier': blanket_multiplier,
                'multiplier_label': '1%' if source.reward_type == 'cashback' else '1.00x',
                'spend': blanket_spend,
                'rewards': blanket_rewards,
                'rewards_format': 'cash' if source.reward_type == 'cashback' else 'miles',
                'is_blanket': True,
            })
            total_spend += blanket_spend
            total_rewards += blanket_rewards
            if source.reward_type == 'miles':
                total_rewards_miles += blanket_rewards
            elif source.reward_type == 'cashback':
                total_rewards_cash += blanket_rewards

        reward_rows.sort(key=lambda row: (row.get('is_blanket', False), row.get('is_credit', False), row['category_name']))
        sources_data.append({
            'source': source,
            'reward_rows': reward_rows,
            'has_quarterly': any(row.get('applicable_quarter') for row in reward_rows),
            'total_spend': total_spend,
            'total_rewards': total_rewards,
            'total_rewards_miles': total_rewards_miles,
            'total_rewards_cash': total_rewards_cash,
        })
        if source.reward_type == 'miles':
            total_miles_earned += total_rewards_miles
            total_credits_earned += total_rewards_cash
        elif source.reward_type == 'cashback':
            total_cashback_earned += total_rewards_cash

    context = {
        'sources_data': sources_data,
        'years': years,
        'selected_year': selected_year,
        'total_miles_earned': total_miles_earned,
        'total_cashback_earned': total_cashback_earned,
        'total_credits_earned': total_credits_earned,
        'sources': Source.objects.order_by('name'),
        'today': today,
        'card_recommendations': build_card_recommendations(),
    }

    return render(request, 'tracker/rewards.html', context)


def category_year_view(request):
    """
    Display a bar chart of a single category's monthly totals for the selected year
    or trailing 12 months.
    """
    categories = Category.objects.filter(reporting_category__isnull=True).order_by('name')
    now = datetime.now()
    current_year = now.year

    view_mode = request.GET.get("view", "ytd")
    if view_mode not in {"ytd", "ttm"}:
        view_mode = "ytd"

    is_ttm = view_mode == "ttm"

    year_values = (
        Transaction.objects.annotate(year=ExtractYear("date"))
        .values_list("year", flat=True)
        .distinct()
        .order_by("year")
    )
    years = list(year_values) or [current_year]
    year = int(request.GET.get("year", years[-1] if years else current_year))

    # Default to first non-income category if none chosen
    default_category = categories.exclude(name__iexact="income").first() or categories.first()
    category_id = request.GET.get("category") or (default_category.id if default_category else None)

    if is_ttm:
        # Build list of (year, month) tuples for trailing 12 months
        def shift_month(y, m, delta):
            total = (y * 12) + (m - 1) + delta
            return total // 12, (total % 12) + 1

        ttm_months = [shift_month(current_year, now.month, -offset) for offset in range(11, -1, -1)]
        start_year, start_month = ttm_months[0]
        start_date = datetime(start_year, start_month, 1).date()
        end_date = now.date()
        month_labels = [f"{calendar.month_abbr[m]} '{str(y)[-2:]}" for y, m in ttm_months]
    else:
        ttm_months = None
        month_labels = [calendar.month_abbr[i] for i in range(1, 13)]

    monthly_totals = [0 for _ in range(12)]
    selected_category = None

    if category_id:
        try:
            selected_category = Category.objects.get(id=category_id)
            base_filter = Q(category=selected_category) | Q(category__reporting_category=selected_category)

            if is_ttm:
                txns = annotate_net_amount(
                    Transaction.objects.filter(base_filter, date__gte=start_date, date__lte=end_date)
                )
                monthly_data = (
                    txns.annotate(m=ExtractMonth("date"), y=ExtractYear("date"))
                    .values("y", "m")
                    .annotate(total=Sum("net_amount"))
                )
                # Map each (year, month) to its TTM index
                ttm_index = {(y, m): idx for idx, (y, m) in enumerate(ttm_months)}
                for entry in monthly_data:
                    key = (int(entry["y"]), int(entry["m"]))
                    idx = ttm_index.get(key)
                    if idx is not None:
                        monthly_totals[idx] = abs(float(entry["total"])) if entry["total"] else 0
            else:
                monthly_data = (
                    annotate_net_amount(
                        Transaction.objects.filter(base_filter, date__year=year)
                    )
                    .annotate(month=ExtractMonth("date"))
                    .values("month")
                    .annotate(total=Sum("net_amount"))
                )
                for entry in monthly_data:
                    month_idx = int(entry["month"]) - 1
                    monthly_totals[month_idx] = abs(float(entry["total"])) if entry["total"] else 0
        except Category.DoesNotExist:
            selected_category = None

    monthly_budget = float(selected_category.budget) if selected_category and selected_category.budget else 0

    context = {
        "categories": categories,
        "selected_category": selected_category,
        "selected_category_id": int(category_id) if category_id else None,
        "year": year,
        "years": years,
        "view_mode": view_mode,
        "month_labels": month_labels,
        "monthly_totals": monthly_totals,
        "monthly_budget": monthly_budget,
        "total_for_year": sum(monthly_totals),
        "average_per_month": (sum(monthly_totals) / 12) if monthly_totals else 0,
        # Pass TTM month data for bar click navigation
        "ttm_months": [[y, m] for y, m in ttm_months] if is_ttm else None,
    }

    return render(request, "tracker/category_year.html", context)


def goals_view(request):
    goals = SavingsGoal.objects.filter(is_active=True)
    if not goals.exists():
        return render(request, "tracker/goals.html", {
            "goals": [],
            "avg_monthly_savings": 0,
            "today": datetime.now().strftime("%Y-%m-%d"),
        })

    # Find earliest goal creation date to scope savings calculation
    earliest = goals.order_by('created_at').first().created_at
    today = date.today()

    # Build list of (year, month) from earliest goal to now
    months_list = []
    current = date(earliest.year, earliest.month, 1)
    while current <= today:
        months_list.append((current.year, current.month))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    # Calculate monthly net savings using same pattern as ytd_report
    transactions = annotate_net_amount(Transaction.objects.all())
    total_positive = 0
    total_negative = 0

    for y, m in months_list:
        income = transactions.filter(
            date__year=y, date__month=m,
            category__name__iexact='income'
        ).exclude(CREDIT_CATEGORY_Q).aggregate(total=Sum('net_amount'))['total'] or 0

        spend = transactions.exclude(
            category__name__iexact='income'
        ).exclude(CREDIT_CATEGORY_Q).filter(
            date__year=y, date__month=m
        ).aggregate(total=Sum('net_amount'))['total'] or 0

        net = float(income) + float(spend)
        if net > 0:
            total_positive += net
        else:
            total_negative += abs(net)

    num_months = len(months_list) or 1
    avg_monthly_savings = (total_positive - total_negative) / num_months

    # Gather manual contributions per goal
    goal_data = []
    for goal in goals:
        manual_total = goal.contributions.aggregate(total=Sum('amount'))['total'] or 0
        goal_data.append({
            'goal': goal,
            'manual': float(manual_total),
            'auto_allocated': 0,
            'contributions': goal.contributions.all()[:10],
        })

    # Pass 1: Fill from positive months (by priority order)
    remaining_savings = total_positive
    fill_ordered = sorted(goal_data, key=lambda g: (g['goal'].priority, g['goal'].created_at))
    for g in fill_ordered:
        needed = max(0, float(g['goal'].target_amount) - g['manual'])
        auto = min(remaining_savings, needed)
        remaining_savings -= auto
        g['auto_allocated'] = auto

    # Pass 2: Withdraw from negative months (by withdrawal_priority order)
    remaining_deficit = total_negative
    withdraw_ordered = sorted(goal_data, key=lambda g: (g['goal'].withdrawal_priority, g['goal'].created_at))
    for g in withdraw_ordered:
        deduction = min(remaining_deficit, g['auto_allocated'])
        remaining_deficit -= deduction
        g['auto_allocated'] -= deduction

    # Compute final progress for each goal
    for g in goal_data:
        target = float(g['goal'].target_amount)
        progress = min(target, g['auto_allocated'] + g['manual'])
        g['progress'] = max(0, progress)
        g['percent'] = round((g['progress'] / target) * 100, 1) if target > 0 else 0
        g['remaining'] = max(0, target - g['progress'])

        # Projected completion date
        if g['remaining'] > 0 and avg_monthly_savings > 0:
            months_needed = g['remaining'] / avg_monthly_savings
            g['projected_date'] = add_months(today, int(months_needed) + 1)
        else:
            g['projected_date'] = None

    # Re-sort by priority for display
    goal_data.sort(key=lambda g: (g['goal'].priority, g['goal'].created_at))

    return render(request, "tracker/goals.html", {
        "goals": goal_data,
        "avg_monthly_savings": round(avg_monthly_savings, 2),
        "today": datetime.now().strftime("%Y-%m-%d"),
    })


def add_goal(request):
    if request.method != "POST":
        return HttpResponseRedirect("/goals/")
    name = request.POST.get("name", "").strip()
    target = request.POST.get("target_amount", "")
    priority = request.POST.get("priority", "1")
    withdrawal_priority = request.POST.get("withdrawal_priority", "1")
    deadline = request.POST.get("deadline", "")

    if not name or not target:
        return HttpResponseRedirect("/goals/")

    try:
        target = float(target)
        priority = int(priority)
        withdrawal_priority = int(withdrawal_priority)
    except ValueError:
        return HttpResponseRedirect("/goals/")

    goal = SavingsGoal(
        name=name,
        target_amount=target,
        priority=priority,
        withdrawal_priority=withdrawal_priority,
    )
    if deadline and is_valid_date(deadline):
        goal.deadline = deadline
    goal.save()
    return HttpResponseRedirect("/goals/")


def edit_goal(request, goal_id):
    try:
        goal = SavingsGoal.objects.get(id=goal_id)
    except SavingsGoal.DoesNotExist:
        return HttpResponseRedirect("/goals/")

    if request.method != "POST":
        return HttpResponseRedirect("/goals/")

    name = request.POST.get("name", "").strip()
    target = request.POST.get("target_amount", "")
    priority = request.POST.get("priority", "")
    withdrawal_priority = request.POST.get("withdrawal_priority", "")
    deadline = request.POST.get("deadline", "")
    is_active = request.POST.get("is_active", "on")

    if name:
        goal.name = name
    if target:
        try:
            goal.target_amount = float(target)
        except ValueError:
            pass
    if priority:
        try:
            goal.priority = int(priority)
        except ValueError:
            pass
    if withdrawal_priority:
        try:
            goal.withdrawal_priority = int(withdrawal_priority)
        except ValueError:
            pass

    if deadline and is_valid_date(deadline):
        goal.deadline = deadline
    elif not deadline:
        goal.deadline = None

    goal.is_active = is_active == "on"
    goal.save()
    return HttpResponseRedirect("/goals/")


def delete_goal(request, goal_id):
    if request.method == "POST":
        try:
            goal = SavingsGoal.objects.get(id=goal_id)
            goal.delete()
        except SavingsGoal.DoesNotExist:
            pass
    return HttpResponseRedirect("/goals/")


def add_contribution(request, goal_id):
    if request.method != "POST":
        return HttpResponseRedirect("/goals/")
    try:
        goal = SavingsGoal.objects.get(id=goal_id)
    except SavingsGoal.DoesNotExist:
        return HttpResponseRedirect("/goals/")

    amount = request.POST.get("amount", "")
    date_val = request.POST.get("date", "")
    note = request.POST.get("note", "").strip()

    if not amount or not date_val or not is_valid_date(date_val):
        return HttpResponseRedirect("/goals/")

    try:
        amount = float(amount)
    except ValueError:
        return HttpResponseRedirect("/goals/")

    GoalContribution.objects.create(
        goal=goal,
        amount=amount,
        date=date_val,
        note=note,
    )
    return HttpResponseRedirect("/goals/")
