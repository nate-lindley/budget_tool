from django.db import models
from django.contrib.postgres.fields import ArrayField

# Create your models here.
class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    budget = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    reporting_category = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reporting_children"
    )

    class Meta:
        """
        Meta class to define ordering for the Category model.
        This will ensure that categories are ordered by name in ascending order.
        """
        ordering = ['name']

class Source(models.Model):
    name = models.CharField(max_length=100, unique=True)
    annual_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    reward_type = models.CharField(
        max_length=100,
        default='cashback',
        choices=[
            ('cashback', 'Cashback'),
            ('none', 'None'),
            ('miles', 'Miles'),
            ('card_cash_miles', 'Card Cash + Miles'),
        ],
    )
    signup_bonus_miles = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    signup_bonus_min_spend = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    signup_bonus_awarded_on = models.DateField(null=True, blank=True)

    class Meta:
        """
        Meta class to define ordering for the Source model.
        This will ensure that sources are ordered by name in ascending order.
        """
        ordering = ['name']

class RewardCategory(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=1.00)
    applicable_quarter = models.CharField(max_length=10, null=True, blank=True)

    class Meta:
        """
        Meta class to define ordering for the RewardCategory model.
        This will ensure that reward categories are ordered by name in ascending order.
        """
        ordering = ['source__name', 'category__name']

class RecurringTransaction(models.Model):
    FREQUENCY_CHOICES = [
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ]

    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True)
    frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, default='monthly')
    interval = models.PositiveIntegerField(default=1)
    day_of_month = models.PositiveIntegerField(default=1)
    day_of_week = models.PositiveIntegerField(null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    last_generated = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['description']

    def get_frequency_display_with_interval(self):
        if self.interval == 1:
            return self.get_frequency_display()
        unit = {'weekly': 'weeks', 'monthly': 'months', 'yearly': 'years'}[self.frequency]
        return f"Every {self.interval} {unit}"


class Transaction(models.Model):
    """
    Model representing a financial transaction.
    """
    date = models.DateField()
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reimbursement = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, default=None, blank=True, null=True)
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, default=None, blank=True, null=True)
    recurring_source = models.ForeignKey(RecurringTransaction, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    tags = models.CharField(max_length=500, blank=True, default='')

    def __str__(self):
        """
        String for representing the Transaction object (in admin site etc.)
        """
        return f"{self.date} - {self.description} - ${self.amount}"
    
    class Meta:
        """
        Meta class to define ordering for the Transaction model.
        """
        ordering = ['-date']

class DismissedSuggestion(models.Model):
    description = models.CharField(max_length=200, unique=True)
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-dismissed_at']


class SavingsGoal(models.Model):
    name = models.CharField(max_length=200)
    target_amount = models.DecimalField(max_digits=12, decimal_places=2)
    priority = models.PositiveIntegerField(default=1)
    withdrawal_priority = models.PositiveIntegerField(default=1)
    deadline = models.DateField(null=True, blank=True)
    created_at = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['priority', 'created_at']

    def __str__(self):
        return self.name


class GoalContribution(models.Model):
    goal = models.ForeignKey(SavingsGoal, on_delete=models.CASCADE, related_name='contributions')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()
    note = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.goal.name} - ${self.amount} on {self.date}"


class Month(models.Model):
    """
    Model representing a month for budget tracking.
    """
    name = models.CharField(max_length=50, unique=True)
    total_spend = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_income = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    daily_spend = ArrayField(models.DecimalField(max_digits=10, decimal_places=2, default=0.00), blank=True, default=list) # List of len 31

    def __str__(self):
        """
        String for representing the Month object (in admin site etc.)
        """
        return f'Name: {self.name} - Spend: ${self.total_spend} - Income: ${self.total_income}\n{self.daily_spend}\n{len(self.daily_spend)} days'
    
    class Meta:
        """
        Meta class to define ordering for the Month model.
        This will ensure that months are ordered by name in ascending order.
        """
        ordering = ['name']
