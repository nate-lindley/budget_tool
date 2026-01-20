from django.shortcuts import render, HttpResponseRedirect
from .models import *
from datetime import datetime, timedelta
from django.db.models import Sum, Q, Exists, OuterRef, Min, Max
import calendar
from django.db.models.functions import ExtractYear, ExtractMonth, Coalesce
import csv
from io import TextIOWrapper
from django.core.paginator import Paginator
import time


def annotate_reporting_category(queryset):
    return queryset.annotate(
        reporting_category_id=Coalesce('category__reporting_category_id', 'category_id'),
        reporting_category_name=Coalesce('category__reporting_category__name', 'category__name'),
        reporting_category_budget=Coalesce('category__reporting_category__budget', 'category__budget'),
    )


def quarter_label(date_value):
    if not date_value:
        return ""
    quarter = (date_value.month - 1) // 3 + 1
    return f"{date_value.year}Q{quarter}"


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
    category_filter = request.GET.get('category')
    source_filter = request.GET.get('source')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    transactions_list = Transaction.objects.all().order_by('-date')  # or your preferred ordering

    if category_filter:
        transactions_list = transactions_list.filter(category__id=category_filter)
    if source_filter:
        transactions_list = transactions_list.filter(source__id=source_filter)
    if start_date:
        transactions_list = transactions_list.filter(date__gte=start_date)
    if end_date:
        transactions_list = transactions_list.filter(date__lte=end_date)
    
    paginator = Paginator(transactions_list, 25)  # Show 25 per page

    page_number = request.GET.get('page',1)
    transactions = paginator.get_page(page_number)
    # Retrieve all categories from the database
    categories = Category.objects.all()  # Get all categories from the database
    sources = Source.objects.all()  # Retrieve all sources from the database, if needed for future use
    # Pass the transactions and categories to the template context
    today = datetime.now().strftime("%Y-%m-%d")  # Get today's date in the format YYYY-MM-DD for any future use in the template

    context = {
        'transactions': transactions,  # Pass the latest transactions to the template
        'categories': categories,  # Pass all categories to the template
        'sources': sources,  # Pass all sources to the template, if needed
        'today': today,  # Pass today's date to the template for any future use
        'selected_category': category_filter,
        'selected_source': source_filter,
        'start_date': start_date,
        'end_date': end_date,
    }
    # print(context)
    # Render the index.html template
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

    new_transaction = Transaction(description=request.POST['description'],amount=amount,date=request.POST['date'],category=category, source=source)
    new_transaction.save()  # Save the new transaction to the database

    # Get the referring URL or use a default if it's not available
    referring_url = request.META.get('HTTP_REFERER', '/')
    
    # Redirect to the referring URL
    return HttpResponseRedirect(referring_url)

def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False
    
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
    today = datetime.now().strftime("%Y-%m-%d")  # Get today's date in the format YYYY-MM-DD for any future use in the template
    # categories = list(categories)  # Convert the queryset to a list for easier manipulation
    total = ({'name': 'Total', 'budget': total_budget, 'annual_budget': total_budget*12, 'negative_budget':total_budget*-1, 'annual_negative_budget':total_budget*-12})  # Append the total budget to the categories list
    # Pass the categories and sources to the template context
    context = {
        'categories': categories,  # Pass all categories to the template
        'reporting_categories': reporting_categories,
        'sources': sources,  # Pass all sources to the template
        'today': today,  # Pass today's date to the template for any future use
        'total': total,
        'source_reward_map': reward_category_map,
        'quarters': quarters,
        'categories_data': categories_data
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
        if 'date' in request.POST and is_valid_date(request.POST['date']):
            transaction.date = request.POST['date']
        if 'category' in request.POST:
            transaction.category_id = request.POST['category']
        if 'source' in request.POST:
            transaction.source_id = request.POST['source']
        
        transaction.save()  # Save the updated transaction to the database
        return HttpResponseRedirect("/")  # Redirect to the index page after saving
    
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
    
    base_transactions = Transaction.objects.filter(date__month=month, date__year=year)
    transactions = base_transactions.exclude(category__name__iexact='income').exclude(
        category__reporting_category__name__iexact='income'
    )

    if income_total_amount is None:
        income_total = base_transactions.filter(
            Q(category__name__iexact='income') | Q(category__reporting_category__name__iexact='income')
        ).aggregate(total=Sum('amount'))

        income_total_amount = float(income_total['total']) if income_total['total'] else 0
    
    # Pie chart data (by category)
    pie_data = (
        annotate_reporting_category(transactions)
        .values("reporting_category_id", "reporting_category_name", "reporting_category_budget")
        .annotate(total=Sum("amount") * -1)
    )

    if final_line_data is None:
        # Line chart data (cumulative spend by date)
        daily_totals = (
            transactions.order_by("date")
            .values("date")
            .annotate(daily_total=Sum("amount"))
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

    # Convert line chart data to JSON-safe format
    line_data_safe = [
        {"date": item["date"], "cumulative": float(item["cumulative"])}
        for item in final_line_data
    ]

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
        # Create a new Month entry if it doesn't exist
        month_data = Month(name=month_name, total_spend=total_spent, total_income=income_total_amount, daily_spend=daily_totals)
        # print(month_data)
        # month_data.save()

    context = {
        "selected_month": month,
        "selected_year": year,
        "income": income_total_amount,
        "total_spent": total_spent,
        "pie_data": pie_data_safe,
        "line_data": line_data_safe,
        "table_data": all_categories,
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
        # print(preview_rows)

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
        transactions = Transaction.objects.filter(date__gte=start_date, date__lte=end_date)
    else:
        months = [(year, month) for month in range(1, current_month + 1)]
        months_count = current_month
        # Transactions for the year
        transactions = Transaction.objects.filter(date__year=year, date__month__lte=current_month)

    # Total spent per reporting category (excluding income)
    spending_data = (
        annotate_reporting_category(
            transactions.exclude(category__name__iexact='income').exclude(
                category__reporting_category__name__iexact='income'
            )
        )
        .values('reporting_category_id', 'reporting_category_name', 'reporting_category_budget')
        .annotate(total=Sum('amount') * -1)
    )

    income_data = (
        annotate_reporting_category(
            transactions.filter(
                Q(category__name__iexact='income') | Q(category__reporting_category__name__iexact='income')
            )
        )
        .values('reporting_category_id', 'reporting_category_name', 'reporting_category_budget')
        .annotate(total=Sum('amount'))
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
        ).aggregate(total=Sum('amount'))['total'] or 0 

        spend = transactions.exclude(
            category__name__iexact='income'
        ).filter(
            date__year=chart_year,
            date__month=chart_month
        ).aggregate(total=Sum('amount'))['total'] or 0

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
    transactions = Transaction.objects.filter(date__year=year, date__month=month)

    # Total spent per reporting category (excluding income)
    spending_data = (
        annotate_reporting_category(
            transactions.exclude(category__name__iexact='income').exclude(
                category__reporting_category__name__iexact='income'
            )
        )
        .values('reporting_category_name', 'reporting_category_budget')
        .annotate(total=Sum('amount') * -1)
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
        ).aggregate(total=Sum('amount'))['total'] or 0

        spend = transactions.exclude(
            category__name__iexact='income'
        ).filter(date__day=day).aggregate(total=Sum('amount'))['total'] or 0

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
        has_rewards=Exists(RewardCategory.objects.filter(source=OuterRef('pk')))
    ).order_by('-has_rewards', 'name')
    sources_data = []

    for source in sources:
        reward_entries = RewardCategory.objects.filter(source=source).select_related('category', 'category__reporting_category')
        reward_rows = []
        total_rewards = 0
        total_spend = 0
        covered_spend = 0

        base_transactions = Transaction.objects.filter(source=source, date__year=selected_year).exclude(
            Q(category__name__iexact='income') | Q(category__reporting_category__name__iexact='income')
        )
        total_source_spend = abs(float(base_transactions.aggregate(total=Sum('amount'))['total'] or 0))

        for entry in reward_entries:
            transaction_query = Transaction.objects.filter(
                source=source,
                category=entry.category
            )
            if entry.applicable_quarter:
                quarter_start, quarter_end = quarter_date_range(entry.applicable_quarter)
                if quarter_start and quarter_end:
                    if quarter_start.year != selected_year:
                        transaction_query = transaction_query.none()
                    else:
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
                'spend': spend,
                'rewards': rewards,
            })
            covered_spend += spend
            total_spend += spend
            total_rewards += rewards

        blanket_spend = max(total_source_spend - covered_spend, 0)
        blanket_multiplier = 0.01 if source.reward_type == 'cashback' else 1.0
        blanket_rewards = blanket_spend * blanket_multiplier
        if blanket_spend > 0:
            reward_rows.append({
                'category_name': 'All Other',
                'reporting_category_name': None,
                'multiplier': blanket_multiplier,
                'multiplier_label': '1%' if source.reward_type == 'cashback' else '1.00x',
                'spend': blanket_spend,
                'rewards': blanket_rewards,
                'is_blanket': True,
            })
            total_spend += blanket_spend
            total_rewards += blanket_rewards

        reward_rows.sort(key=lambda row: (row.get('is_blanket', False), row['category_name']))
        sources_data.append({
            'source': source,
            'reward_rows': reward_rows,
            'total_spend': total_spend,
            'total_rewards': total_rewards,
        })

    context = {
        'sources_data': sources_data,
        'years': years,
        'selected_year': selected_year,
    }

    return render(request, 'tracker/rewards.html', context)


def category_year_view(request):
    """
    Display a bar chart of a single category's monthly totals for the selected year.
    """
    categories = Category.objects.filter(reporting_category__isnull=True).order_by('name')
    current_year = datetime.now().year
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

    monthly_totals = [0 for _ in range(12)]
    selected_category = None

    if category_id:
        try:
            selected_category = Category.objects.get(id=category_id)
            monthly_data = (
                Transaction.objects.filter(
                    Q(category=selected_category) | Q(category__reporting_category=selected_category),
                    date__year=year
                )
                .annotate(month=ExtractMonth("date"))
                .values("month")
                .annotate(total=Sum("amount"))
            )
            for entry in monthly_data:
                month_idx = int(entry["month"]) - 1
                monthly_totals[month_idx] = abs(float(entry["total"])) if entry["total"] else 0
        except Category.DoesNotExist:
            selected_category = None

    context = {
        "categories": categories,
        "selected_category": selected_category,
        "selected_category_id": int(category_id) if category_id else None,
        "year": year,
        "years": years,
        "month_labels": [calendar.month_abbr[i] for i in range(1, 13)],
        "monthly_totals": monthly_totals,
        "total_for_year": sum(monthly_totals),
        "average_per_month": (sum(monthly_totals) / 12) if monthly_totals else 0,
    }

    return render(request, "tracker/category_year.html", context)
