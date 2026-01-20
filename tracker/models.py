from django.db import models
from django.contrib.postgres.fields import ArrayField

# Create your models here.
class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    budget = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    class Meta:
        """
        Meta class to define ordering for the Category model.
        This will ensure that categories are ordered by name in ascending order.
        """
        ordering = ['name']

class Source(models.Model):
    name = models.CharField(max_length=100, unique=True)
    annual_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    reward_type = models.CharField(max_length=100, default='cashback', choices=[('cashback', 'Cashback'), ('none', 'None'), ('miles', 'Miles')])

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

    class Meta:
        """
        Meta class to define ordering for the RewardCategory model.
        This will ensure that reward categories are ordered by name in ascending order.
        """
        ordering = ['source__name', 'category__name']

class Transaction(models.Model):
    """
    Model representing a financial transaction.
    """
    date = models.DateField()
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, default=None, blank=True, null=True)
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, default=None, blank=True, null=True)

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
