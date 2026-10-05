from django.contrib import admin
from .models import Restaurant, UserProfile, Category, Product, Sale, SaleItem, Customer

admin.site.register(Restaurant)
admin.site.register(UserProfile)
admin.site.register(Category)
admin.site.register(Product)
admin.site.register(Sale)
admin.site.register(SaleItem)
admin.site.register(Customer)