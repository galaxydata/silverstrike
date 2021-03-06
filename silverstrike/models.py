import uuid
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from django.db import models
from django.urls import reverse
from django.utils.translation import ugettext as _


class Account(models.Model):
    PERSONAL = 1
    REVENUE = 2
    EXPENSE = 3
    SYSTEM = 4
    ACCOUNT_TYPES = (
        (PERSONAL, _('Personal')),
        (REVENUE, _('Revenue')),
        (EXPENSE, _('Expense')),
        (SYSTEM, _('System')),
    )

    name = models.CharField(max_length=64)
    account_type = models.IntegerField(choices=ACCOUNT_TYPES, default=PERSONAL)
    active = models.BooleanField(default=True)
    last_modified = models.DateTimeField(auto_now=True)
    show_on_dashboard = models.BooleanField(default=False)

    class Meta:
        ordering = ['-active', 'name']

    def __str__(self):
        return self.name

    @property
    def account_type_str(self):
        return Account.ACCOUNT_TYPES[self.account_type - 1][1]

    @property
    def is_personal(self):
        return self.account_type == Account.PERSONAL

    @property
    def transaction_num(self):
        """
        TODO do we really want the number of splits?
        """
        return Split.objects.filter(account=self).count()

    @property
    def balance(self):
        return self.balance_on(date.today())

    def balance_on(self, date):
        return Split.objects.filter(account=self, transaction__date__lte=date).aggregate(
            models.Sum('amount'))['amount__sum'] or 0

    def get_absolute_url(self):
        return reverse('account_view', args=[self.pk])

    def get_data_points(self, dstart=date.today() - timedelta(days=365),
                        dend=date.today(), steps=30):
        step = (dend - dstart) / steps
        if step < timedelta(days=1):
            step = timedelta(days=1)
            steps = int((dend - dstart) / step)
        data_points = []
        balance = self.balance_on(dstart)
        transactions = list(Split.objects.prefetch_related('transaction').filter(
            account_id=self.pk, transaction__date__gt=dstart,
            transaction__date__lte=dend).order_by('-transaction__date'))
        for i in range(steps):
            while len(transactions) > 0 and transactions[-1].transaction.date <= dstart.date():
                t = transactions.pop()
                balance += t.amount
            data_points.append((dstart, balance))
            dstart += step
        for t in transactions:
            balance += t.amount
        data_points.append((dend, balance))
        return data_points

    def set_initial_balance(self, amount):
        system = Account.objects.get(account_type=Account.SYSTEM)
        transaction = Transaction.objects.create(title=_('Initial Balance'),
                                                 transaction_type=Transaction.SYSTEM)
        Split.objects.create(transaction=transaction, amount=-amount,
                             account=system, opposing_account=self)
        Split.objects.create(transaction=transaction, amount=amount,
                             account=self, opposing_account=system)


class Transaction(models.Model):
    DEPOSIT = 1
    WITHDRAW = 2
    TRANSFER = 3
    SYSTEM = 4
    TRANSACTION_TYPES = (
        (DEPOSIT, 'Deposit'),
        (WITHDRAW, 'Withdrawl'),
        (TRANSFER, 'Transfer'),
        (SYSTEM, 'Reconcile'),
    )

    class Meta:
        ordering = ['-date', 'title']

    title = models.CharField(max_length=64)
    date = models.DateField(default=date.today)
    notes = models.TextField(blank=True, null=True)
    transaction_type = models.IntegerField(choices=TRANSACTION_TYPES)
    recurrence = models.ForeignKey('RecurringTransaction', related_name='recurrences',
                                   blank=True, null=True)

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('transaction_detail', args=[self.pk])

    def get_transaction_type_str(self):
        for i, name in self.TRANSACTION_TYPES:
            if i == self.transaction_type:
                return name

    @property
    def amount(self):
        """
        TODO that means that transfers dont have an amount?
        """
        return self.splits.filter(account__account_type=Account.PERSONAL).aggregate(
            models.Sum('amount'))['amount__sum'] or 0

    @property
    def is_split(self):
        return len(self.splits.all()) > 2

    @property
    def is_system(self):
        return self.transaction_type == self.SYSTEM


class SplitQuerySet(models.QuerySet):
    def personal(self):
        return self.filter(account__account_type=Account.PERSONAL)

    def income(self):
        return self.filter(opposing_account__account_type=Account.REVENUE)

    def expense(self):
        return self.filter(opposing_account__account_type=Account.EXPENSE)

    def date_range(self, dstart, dend):
        return self.filter(date__gte=dstart, date__lte=dend)

    def category(self, category):
        return self.filter(category=category)

    def transfers_once(self):
        return self.personal().exclude(opposing_account__account_type=Account.PERSONAL,
                                       amount__gte=0)

    def exclude_transfers(self):
        return self.exclude(account__account_type=Account.PERSONAL,
                            opposing_account__account_type=Account.PERSONAL)

    def upcoming(self):
        return self.filter(date__gt=date.today())

    def past(self):
        return self.filter(date__lte=date.today())


class Split(models.Model):
    account = models.ForeignKey(Account, models.CASCADE, related_name='incoming_transactions')
    opposing_account = models.ForeignKey(Account, models.CASCADE,
                                         related_name='outgoing_transactions')
    title = models.CharField(max_length=64)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=date.today)
    category = models.ForeignKey('Category', blank=True, null=True, related_name='splits')
    transaction = models.ForeignKey(Transaction, models.CASCADE, related_name='splits',
                                    blank=True, null=True)

    objects = SplitQuerySet.as_manager()

    class Meta:
        ordering = ['-date', 'title']

    def __str__(self):
        return self.title

    @property
    def is_transfer(self):
        return self.transaction.transaction_type == Transaction.TRANSFER

    @property
    def is_withdraw(self):
        return self.transaction.transaction_type == Transaction.WITHDRAW

    @property
    def is_deposit(self):
        return self.transaction.transaction_type == Transaction.DEPOSIT

    @property
    def is_system(self):
        return self.transaction.transaction_type == Transaction.SYSTEM

    def get_absolute_url(self):
        return self.transaction.get_absolute_url()


class Category(models.Model):
    name = models.CharField(max_length=64)
    last_modified = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name

    @property
    def money_spent(self):
        return abs(Split.objects.filter(
                category=self, account__account_type=Account.PERSONAL,
                transaction__transaction_type=Transaction.WITHDRAW).aggregate(
            models.Sum('amount'))['amount__sum'] or 0)

    def get_absolute_url(self):
        return reverse('category_detail', args=[self.id])


class BudgetQuerySet(models.QuerySet):
    def for_month(self, month):
        return self.filter(month=month)


class Budget(models.Model):
    category = models.ForeignKey(Category)
    month = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    objects = BudgetQuerySet.as_manager()


class ImportFile(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to='imports')


class ImportConfiguration(models.Model):
    DO_NOT_USE = 0
    SOURCE_ACCOUNT = 1
    DESTINATION_ACCOUNT = 2
    AMOUNT = 3
    DATE = 4
    NOTES = 5
    CATEGORY = 6
    TITLE = 7

    FIELD_TYPES = (
        (DO_NOT_USE, _('Do not use')),
        (SOURCE_ACCOUNT, _('Source Account')),
        (DESTINATION_ACCOUNT, _('Destination Account')),
        (AMOUNT, _('Amount')),
        (DATE, _('Date')),
        (NOTES, _('Notes')),
        (CATEGORY, _('Category')),
        (TITLE, _('Title')),
        )

    name = models.CharField(max_length=64)
    headers = models.BooleanField(help_text=_('First line contains headers'))
    default_account = models.ForeignKey(Account,
                                        limit_choices_to={'account_type': Account.PERSONAL},
                                        null=True, blank=True)
    dateformat = models.CharField(max_length=32)
    config = models.TextField()

    def __str__(self):
        return self.name


class RecurringTransactionManager(models.Manager):
    def due_in_month(self, month=None):
        from .lib import last_day_of_month
        if not month:
            month = date.today()
        month = last_day_of_month(month)
        queryset = self.get_queryset().filter(date__lte=month)
        return queryset.exclude(recurrence=RecurringTransaction.DISABLED)


class RecurringTransaction(models.Model):
    DISABLED = 0
    WEEKLY = 1
    MONTHLY = 2
    YEARLY = 3
    RECCURENCE_OPTIONS = (
        (DISABLED, _('Disabled')),
        (WEEKLY, _('Weekly')),
        (MONTHLY, _('Monthly')),
        (YEARLY, _('Yearly')))

    class Meta:
        ordering = ['date']

    objects = RecurringTransactionManager()

    title = models.CharField(max_length=64)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()
    src = models.ForeignKey(Account)
    dst = models.ForeignKey(Account, related_name='opposing_recurring_transactions')
    recurrence = models.IntegerField(choices=RECCURENCE_OPTIONS)
    transaction_type = models.IntegerField(choices=Transaction.TRANSACTION_TYPES[:3])
    category = models.ForeignKey(Category, null=True, blank=True)

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('recurrence_detail', args=[self.pk])

    @property
    def is_due(self):
        return date.today() >= self.date

    def update_date(self):
        if self.recurrence == self.WEEKLY:
            self.date += timedelta(days=7)
        elif self.recurrence == self.MONTHLY:
            self.date += relativedelta(months=+1)
        else:
            self.date += relativedelta(years=+1)

    @property
    def is_disabled(self):
        return self.recurrence == self.DISABLED

    @property
    def get_recurrence(self):
        for r, name in self.RECCURENCE_OPTIONS:
            if r == self.recurrence:
                return name
        return ''

    @property
    def signed_amount(self):
        if self.transaction_type == Transaction.WITHDRAW:
            return -self.amount
        else:
            return self.amount

    @property
    def is_withdraw(self):
        return self.transaction_type == Transaction.WITHDRAW

    @property
    def is_deposit(self):
        return self.transaction_type == Transaction.DEPOSIT

    @classmethod
    def outstanding_transaction_sum(cls):
        from .lib import last_day_of_month
        outstanding = 0
        dend = last_day_of_month(date.today())
        transactions = cls.objects.due_in_month().exclude(
            transaction_type=Transaction.TRANSFER)
        for t in transactions:
            while t.date <= dend:
                if t.transaction_type == Transaction.WITHDRAW:
                    outstanding -= t.amount
                else:
                    outstanding += t.amount
                t.update_date()
        return outstanding
