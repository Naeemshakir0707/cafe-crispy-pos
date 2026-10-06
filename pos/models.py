from django.db import models
from django.contrib.auth.models import User

class Restaurant(models.Model):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    logo = models.ImageField(upload_to='logos/', blank=True, null=True)
    primary_color = models.CharField(max_length=7, default='#0d6efd')   # Example: #0d6efd
    secondary_color = models.CharField(max_length=7, default='#198754')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, null=True, blank=True)
    is_manager = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} - {self.restaurant.name if self.restaurant else 'No Restaurant'}"

class Customer(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    mobile = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.mobile})"

class Category(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"

class Product(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"

class Sale(models.Model):
    ORDER_TYPES = (
        ('dine_in', 'Dine In'),
        ('take_away', 'Take Away'),
        ('delivery', 'Delivery'),
    )
    
    STATUS_CHOICES = (
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    )

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    invoice_no = models.CharField(max_length=50)
    order_type = models.CharField(max_length=20, choices=ORDER_TYPES)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('restaurant', 'invoice_no')

    def __str__(self):
        return f"{self.invoice_no} - {self.restaurant.name}"

class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"