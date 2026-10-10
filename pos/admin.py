from django.contrib import admin
from .models import Restaurant, UserProfile, Category, Product, Sale, SaleItem, Customer, DealComponent, Ingredient, RecipeItem

class DealComponentInline(admin.TabularInline):
    model = DealComponent
    fk_name = 'deal'
    extra = 1

class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant', 'category', 'price', 'stock', 'is_deal')
    list_filter = ('restaurant', 'is_deal', 'category')
    inlines = [DealComponentInline]

admin.site.register(Restaurant)
admin.site.register(UserProfile)
admin.site.register(Category)
admin.site.register(Product, ProductAdmin)
admin.site.register(Sale)
admin.site.register(SaleItem)
admin.site.register(Customer)
admin.site.register(DealComponent)
admin.site.register(Ingredient)
admin.site.register(RecipeItem)