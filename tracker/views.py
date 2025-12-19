from django.shortcuts import render, HttpResponseRedirect
from .models import *
from datetime import datetime, timedelta
from django.db.models import Sum
import calendar
from django.db.models.functions import ExtractYear
import csv
from io import TextIOWrapper
from django.core.paginator import Paginator
import time

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
    
    new_category, created = Category.objects.get_or_create(name=request.POST['name'], budget=request.POST['budget'])
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
    total_budget = 0
    for category in categories:
        category.annual_budget = category.budget * 12  # Calculate the annual budget for each category
        category.negative_budget = category.budget * -1
        category.annual_negative_budget = category.annual_budget * -1
        total_budget += category.budget  # Sum the annual budgets for all categories
    sources = Source.objects.all()
    today = datetime.now().strftime("%Y-%m-%d")  # Get today's date in the format YYYY-MM-DD for any future use in the template
    # categories = list(categories)  # Convert the queryset to a list for easier manipulation
    total = ({'name': 'Total', 'budget': total_budget, 'annual_budget': total_budget*12, 'negative_budget':total_budget*-1, 'annual_negative_budget':total_budget*-12})  # Append the total budget to the categories list
    # Pass the categories and sources to the template context
    context = {
        'categories': categories,  # Pass all categories to the template
        'sources': sources,  # Pass all sources to the template
        'today': today,  # Pass today's date to the template for any future use
        'total': total
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
            source.save()  # Save the updated source to the database
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
    
    transactions = Transaction.objects.filter(date__month=month, date__year=year).exclude(category__name='Income') #, amount__lt=0)

    if income_total_amount is None:
        income_total = Transaction.objects.filter(date__month=month, date__year=year, category__name='Income').aggregate(total=Sum('amount'))

        income_total_amount = float(income_total['total']) if income_total['total'] else 0
    
    # Pie chart data (by category)
    pie_data = transactions.values("category__name").annotate(total=Sum("amount")*-1)

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
        percentage = total / total_spent * 100
        budget = float(Category.objects.get(name=item["category__name"]).budget) if Category.objects.filter(name=item["category__name"]).exists() else 0
        surplus = budget - total
        # print(item)
        # print(f'Category: {item["category__name"]}, Total: {total}, Percentage: {percentage:.2f}%, Budget: {budget}, Surplus: {surplus}')
        # print()
        pie_data_safe.append({
            "category__name": item["category__name"],
            "total": abs(total),
            "percentage": abs(percentage),
            "budget": abs(budget),
            "surplus": abs(surplus),
        })
        all_categories.append({
            "category__name": item["category__name"],  
            "total": abs(total),
            "percentage": abs(percentage),
            "budget": abs(budget),
            "surplus": surplus,
            "is_surplus_negative": surplus < 0,
            "is_budget_negative": budget < 0,
            "is_negative": total < 0,
        })

    categories = Category.objects.all()

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
    year = datetime.now().year
    current_month = datetime.now().month

    # Transactions for the year
    transactions = Transaction.objects.filter(date__year=year, date__month__lte=current_month)

    # Total spent per category (excluding income)
    spending_data = (
        transactions.exclude(category__name__iexact='income')
        .values('category__name', 'category__budget')
        .annotate(total=Sum('amount') * -1)
    )

    income_data = (
        transactions.filter(category__name__iexact='income')
        .values('category__name', 'category__budget')
        .annotate(total=Sum('amount'))
    )

    ytd_data = []
    total_spend = 0
    total_budget = 0
    for entry in spending_data:
        avg_per_month = entry['total'] / current_month
        budget = entry['category__budget'] or 0
        avg_surplus = budget - avg_per_month
        ytd_data.append({
            'category__name': entry['category__name'],
            'total': entry['total'],
            'annual_budget': entry['category__budget'] * 12 if entry['category__budget'] else 0,
            'avg_per_month': avg_per_month,
            'budget': budget, 
            'avg_surplus': avg_surplus,
            'total_surplus': avg_surplus * current_month,
        })
        total_spend += avg_per_month
        total_budget += budget

    total_data = {
        'category__name': 'Total Spend',
        'total': (total_spend * current_month),
        'annual_budget': (total_budget * 12),
        'avg_per_month': total_spend,
        'budget': total_budget,
        'avg_surplus': (total_budget - total_spend),
        'total_surplus': ((total_budget - total_spend) * current_month)
    }

    # print(f'total_spend: {total_spend}')
    # print(f'total_budget: {total_budget}')
    for entry in income_data:
        avg_per_month = entry['total'] / current_month
        budget = -1*entry['category__budget'] or 0
        avg_surplus = -1*(budget - avg_per_month)
        # print(f'income: {avg_per_month}')
        income_data = {
            'category__name': entry['category__name'],
            'total': entry['total'],
            'annual_budget': entry['category__budget'] * -12 if entry['category__budget'] else 0,
            'avg_per_month': avg_per_month,
            'budget': budget,
            'avg_surplus': avg_surplus,
            'total_surplus': avg_surplus * current_month,
        }
    savings_data = {
        'category__name': 'Savings',
        'total': income_data['total'] - (total_spend * current_month),
        'annual_budget': income_data['annual_budget'] - (total_budget * 12),
        'avg_per_month': income_data['avg_per_month'] - total_spend,
        'budget': income_data['budget'] - total_budget,
        'avg_surplus': (income_data['avg_surplus'] + (total_budget - total_spend)),
        'total_surplus': (income_data['total_surplus'] + ((total_budget - total_spend) * current_month))
    }
    # Net savings = income - spending for each month
    savings_chart_data = []
    savings_history = []

    for month in range(1, current_month + 1):
        income = transactions.filter(
            date__month=month,
            category__name__iexact='income'
        ).aggregate(total=Sum('amount'))['total'] or 0 

        spend = transactions.exclude(
            category__name__iexact='income'
        ).filter(date__month=month).aggregate(total=Sum('amount'))['total'] or 0

        savings = income + spend
        savings_history.append(savings)
        recent_history = savings_history[-3:]

        running_average = sum(recent_history) / len(recent_history)

        savings_chart_data.append({
            'month': month_name[month][:3],
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
        'savings_data': savings_data
    }

    return render(request, 'tracker/ytd.html', context)

def mtd_report(request):
    month = datetime.now().month
    year = datetime.now().year

    # Transactions for the current month
    transactions = Transaction.objects.filter(date__year=year, date__month=month)

    # Total spent per category (excluding income)
    spending_data = (
        transactions.exclude(category__name__iexact='income')
        .values('category__name', 'category__budget')
        .annotate(total=Sum('amount') * -1)
    )

    mtd_data = []
    for entry in spending_data:
        avg_per_month = entry['total'] / month
        budget = entry['category__budget'] or 0
        avg_surplus = budget - avg_per_month
        mtd_data.append({
            'category__name': entry['category__name'],
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