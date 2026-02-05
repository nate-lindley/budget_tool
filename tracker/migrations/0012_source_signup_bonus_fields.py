from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0011_transaction_reimbursement"),
    ]

    operations = [
        migrations.AddField(
            model_name="source",
            name="signup_bonus_miles",
            field=models.DecimalField(decimal_places=0, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="source",
            name="signup_bonus_min_spend",
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name="source",
            name="signup_bonus_awarded_on",
            field=models.DateField(blank=True, null=True),
        ),
    ]
