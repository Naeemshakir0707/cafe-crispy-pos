from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from .models import Restaurant, UserProfile, Category, Product, Sale, SaleItem, Customer
from decimal import Decimal
import json
from datetime import datetime

def get_user_restaurant(user):
    """Helper function to get the restaurant of the logged-in user"""
    try:
        profile = UserProfile.objects.get(user=user)
        return profile.restaurant
    except UserProfile.DoesNotExist:
        return None

@login_required(login_url='login')
def pos_screen(request):
    restaurant = get_user_restaurant(request.user)
    
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    categories = Category.objects.filter(restaurant=restaurant, is_active=True)
    products = Product.objects.filter(restaurant=restaurant, is_active=True)

    context = {
        'categories': categories,
        'products': products,
        'restaurant': restaurant,
    }
    return render(request, 'pos/pos.html', context)

@login_required(login_url='login')
@csrf_exempt
def complete_sale(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return JsonResponse({'success': False, 'message': 'No restaurant assigned'})

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cart = data.get('cart', [])

            if not cart:
                return JsonResponse({'success': False, 'message': 'Cart is empty'})

            subtotal = Decimal('0.00')
            for item in cart:
                subtotal += Decimal(str(item['price'])) * item['qty']

            discount = Decimal(str(data.get('discount', 0)))
            delivery_charge = Decimal(str(data.get('delivery_charge', 0)))
            total = subtotal - discount + delivery_charge
            if total < 0:
                total = Decimal('0.00')

            # Handle Customer
            customer = None
            customer_name = data.get('customer_name', '').strip()
            customer_mobile = data.get('customer_mobile', '').strip()
            customer_address = data.get('customer_address', '').strip()

            if customer_name:
                customer, created = Customer.objects.get_or_create(
                    restaurant=restaurant,
                    name=customer_name,
                    mobile=customer_mobile,
                    defaults={'address': customer_address}
                )
                if not created and customer_address:
                    customer.address = customer_address
                    customer.save()

            # Generate Invoice Number
            today = datetime.now().strftime('%Y%m%d')
            prefix = f"{restaurant.name[:6].upper().replace(' ', '')}-{today}"
            last_sale = Sale.objects.filter(restaurant=restaurant, invoice_no__startswith=prefix).order_by('-id').first()

            if last_sale:
                last_number = int(last_sale.invoice_no.split('-')[-1])
                new_number = last_number + 1
            else:
                new_number = 1

            invoice_no = f"{prefix}-{new_number:04d}"

            sale = Sale.objects.create(
                restaurant=restaurant,
                invoice_no=invoice_no,
                order_type=data.get('order_type', 'take_away'),
                customer=customer,
                subtotal=subtotal,
                discount=discount,
                delivery_charge=delivery_charge,
                total=total,
                status='completed',
                created_by=request.user
            )

            for item in cart:
                product = Product.objects.get(id=item['id'], restaurant=restaurant)

                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    quantity=item['qty'],
                    price=Decimal(str(item['price'])),
                    total=Decimal(str(item['price'])) * item['qty']
                )

                # ===== Stock Reduction (Supports both simple stock + Recipe/Ingredients) =====
                product = Product.objects.get(id=item['id'], restaurant=restaurant)
                qty_sold = item['qty']

                # 1. If product has a Recipe → reduce ingredients
                recipe_items = product.recipe_items.all()
                if recipe_items.exists():
                    for recipe in recipe_items:
                        ingredient = recipe.ingredient
                        used_qty = recipe.quantity * qty_sold
                        ingredient.stock = max(0, ingredient.stock - used_qty)
                        ingredient.save()
                else:
                    # 2. Normal product or Deal (old logic)
                    if product.is_deal:
                        for component in product.components.all():
                            component.product.stock = max(0, component.product.stock - (component.quantity * qty_sold))
                            component.product.save()
                    else:
                        product.stock = max(0, product.stock - qty_sold)
                        product.save()

            return JsonResponse({
                'success': True,
                'message': 'Sale completed successfully!',
                'invoice_no': invoice_no,
                'total': str(total)
            })

        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    return JsonResponse({'success': False, 'message': 'Invalid request'})

@login_required(login_url='login')
def sales_history(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    from datetime import datetime

    sales = Sale.objects.filter(restaurant=restaurant).order_by('-created_at')

    # Filters
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    search = request.GET.get('search', '').strip()

    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            sales = sales.filter(created_at__date__gte=start)
        except:
            pass

    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            sales = sales.filter(created_at__date__lte=end)
        except:
            pass

    if search:
        sales = sales.filter(invoice_no__icontains=search)

    completed_sales = sales.filter(status='completed')
    total_amount = sum(sale.total for sale in completed_sales)
    total_orders = completed_sales.count()

    context = {
        'sales': sales,
        'restaurant': restaurant,
        'start_date': start_date or '',
        'end_date': end_date or '',
        'search': search,
        'total_amount': total_amount,
        'total_orders': total_orders,
    }
    return render(request, 'pos/sales_history.html', context)

@login_required(login_url='login')
def receipt(request, invoice_no):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    try:
        sale = Sale.objects.get(invoice_no=invoice_no, restaurant=restaurant)
        items = sale.items.all()
        context = {
            'sale': sale,
            'items': items,
            'restaurant': restaurant,
        }
        return render(request, 'pos/receipt.html', context)
    except Sale.DoesNotExist:
        return render(request, 'pos/receipt.html', {'error': 'Sale not found'})

def login_view(request):
    if request.user.is_authenticated:
        return redirect('pos_screen')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('pos_screen')
        else:
            error = "Invalid username or password"

    return render(request, 'pos/login.html', {'error': error})

def logout_view(request):
    logout(request)
    return redirect('login')

@login_required(login_url='login')
def dashboard(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    from django.utils import timezone
    from django.db.models import Sum
    from datetime import datetime

    today = timezone.now().date()

    # Get dates from filter
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except:
            start_date = today
            end_date = today
    else:
        start_date = today.replace(day=1)
        end_date = today

    # Only COMPLETED sales
    filtered_sales = Sale.objects.filter(
        restaurant=restaurant,
        status='completed',
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    total_sales = sum(sale.total for sale in filtered_sales)
    total_orders = filtered_sales.count()

    # Today's completed sales
    today_sales = Sale.objects.filter(
        restaurant=restaurant,
        status='completed',
        created_at__date=today
    )
    total_sales_today = sum(sale.total for sale in today_sales)
    total_orders_today = today_sales.count()

    # Top Selling Products (only completed sales)
    top_products = (
        SaleItem.objects
        .filter(
            sale__restaurant=restaurant,
            sale__status='completed',
            sale__created_at__date__gte=start_date,
            sale__created_at__date__lte=end_date
        )
        .values('product__name')
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')[:5]
    )

    # Low Stock Products
    # Low Stock Products (only products that do NOT have a recipe)
    from django.db.models import Count
    low_stock_products = Product.objects.filter(
        restaurant=restaurant,
        is_active=True,
        stock__lte=10
    ).annotate(
        recipe_count=Count('recipe_items')
    ).filter(
        recipe_count=0   # Only products without recipe
    ).order_by('stock')


    # Also get low stock Ingredients
    from django.db.models import Ingredient
    low_stock_ingredients = Ingredient.objects.filter(
        restaurant=restaurant,
        is_active=True,
        stock__lte=models.F('low_stock_threshold')
    ).order_by('stock')

    context = {
        'restaurant': restaurant,
        'total_sales': total_sales,
        'total_orders': total_orders,
        'total_sales_today': total_sales_today,
        'total_orders_today': total_orders_today,
        'top_products': top_products,
        'low_stock_products': low_stock_products,
        'start_date': start_date,
        'end_date': end_date,
        'today': today,
        'low_stock_products': low_stock_products,
        'low_stock_ingredients': low_stock_ingredients,
    }
    return render(request, 'pos/dashboard.html', context)

@login_required(login_url='login')
def manage_categories(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    categories = Category.objects.filter(restaurant=restaurant).order_by('name')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            name = request.POST.get('name')
            if name:
                Category.objects.create(restaurant=restaurant, name=name)

        elif action == 'edit':
            cat_id = request.POST.get('category_id')
            name = request.POST.get('name')
            category = Category.objects.filter(id=cat_id, restaurant=restaurant).first()
            if category and name:
                category.name = name
                category.save()

        elif action == 'delete':
            cat_id = request.POST.get('category_id')
            Category.objects.filter(id=cat_id, restaurant=restaurant).delete()

        return redirect('manage_categories')

    context = {
        'restaurant': restaurant,
        'categories': categories,
    }
    return render(request, 'pos/manage_categories.html', context)

@login_required(login_url='login')
def manage_products(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    from .models import DealComponent

    products = Product.objects.filter(restaurant=restaurant).select_related('category').order_by('name')
    categories = Category.objects.filter(restaurant=restaurant, is_active=True)
    all_products = Product.objects.filter(restaurant=restaurant, is_active=True)  # for deal components

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            name = request.POST.get('name')
            category_id = request.POST.get('category')
            price = request.POST.get('price')
            stock = request.POST.get('stock', 0)
            is_deal = True if request.POST.get('is_deal') == 'on' else False
            image = request.FILES.get('image')

            if name and category_id and price:
                Product.objects.create(
                    restaurant=restaurant,
                    name=name,
                    category_id=category_id,
                    price=price,
                    stock=0 if is_deal else (stock or 0),
                    is_deal=is_deal,
                    image=image
                )

        elif action == 'edit':
            product_id = request.POST.get('product_id')
            name = request.POST.get('name')
            category_id = request.POST.get('category')
            price = request.POST.get('price')
            stock = request.POST.get('stock', 0)
            is_deal = True if request.POST.get('is_deal') == 'on' else False
            image = request.FILES.get('image')

            product = Product.objects.filter(id=product_id, restaurant=restaurant).first()
            if product:
                product.name = name
                product.category_id = category_id
                product.price = price
                product.is_deal = is_deal

                if is_deal:
                    product.stock = 0
                else:
                    product.stock = stock or 0

                if image:
                    product.image = image

                product.save()

        elif action == 'delete':
            product_id = request.POST.get('product_id')
            Product.objects.filter(id=product_id, restaurant=restaurant).delete()

        elif action == 'add_component':
            deal_id = request.POST.get('deal_id')
            product_id = request.POST.get('component_product')
            quantity = request.POST.get('quantity', 1)

            deal = Product.objects.filter(id=deal_id, restaurant=restaurant, is_deal=True).first()
            component_product = Product.objects.filter(id=product_id, restaurant=restaurant).first()

            if deal and component_product:
                DealComponent.objects.create(
                    deal=deal,
                    product=component_product,
                    quantity=quantity
                )

        elif action == 'remove_component':
            component_id = request.POST.get('component_id')
            DealComponent.objects.filter(id=component_id, deal__restaurant=restaurant).delete()

        return redirect('manage_products')

    context = {
        'restaurant': restaurant,
        'products': products,
        'categories': categories,
        'all_products': all_products,
    }
    return render(request, 'pos/manage_products.html', context)

@login_required(login_url='login')
def last_10_sales(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    sales = Sale.objects.filter(restaurant=restaurant).order_by('-created_at')[:10]

    context = {
        'sales': sales,
        'restaurant': restaurant,
    }
    return render(request, 'pos/last_10_sales.html', context)

@login_required(login_url='login')
def cancel_sale(request, sale_id):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return redirect('dashboard')

    sale = Sale.objects.filter(id=sale_id, restaurant=restaurant).first()
    
    if sale and sale.status == 'completed':
        # Restore stock
        for item in sale.items.all():
            product = item.product
            product.stock += item.quantity
            product.save()
        
        # Mark as cancelled
        sale.status = 'cancelled'
        sale.save()

    return redirect(request.META.get('HTTP_REFERER', 'sales_history'))

@login_required(login_url='login')
def search_customers(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return JsonResponse({'customers': []})

    query = request.GET.get('q', '').strip()
    customers = Customer.objects.filter(restaurant=restaurant)

    if query:
        customers = customers.filter(name__icontains=query) | customers.filter(mobile__icontains=query)

    customers = customers[:10]

    data = [{
        'id': c.id,
        'name': c.name,
        'mobile': c.mobile,
        'address': c.address
    } for c in customers]

    return JsonResponse({'customers': data})

@login_required(login_url='login')
def daily_closing(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    from datetime import datetime
    from django.db.models import Sum, Count

    # Get selected date (default = today)
    date_str = request.GET.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except:
            selected_date = datetime.now().date()
    else:
        selected_date = datetime.now().date()

    # Only completed sales of selected date
    sales = Sale.objects.filter(
        restaurant=restaurant,
        status='completed',
        created_at__date=selected_date
    ).order_by('created_at')

    total_sales = sum(sale.total for sale in sales)
    total_orders = sales.count()
    total_discount = sum(sale.discount for sale in sales)
    total_delivery = sum(sale.delivery_charge for sale in sales)
    total_subtotal = sum(sale.subtotal for sale in sales)

    # Breakdown by order type
    dine_in = sales.filter(order_type='dine_in')
    take_away = sales.filter(order_type='take_away')
    delivery = sales.filter(order_type='delivery')

    context = {
        'restaurant': restaurant,
        'selected_date': selected_date,
        'sales': sales,
        'total_sales': total_sales,
        'total_orders': total_orders,
        'total_discount': total_discount,
        'total_delivery': total_delivery,
        'total_subtotal': total_subtotal,
        'dine_in_count': dine_in.count(),
        'dine_in_total': sum(s.total for s in dine_in),
        'take_away_count': take_away.count(),
        'take_away_total': sum(s.total for s in take_away),
        'delivery_count': delivery.count(),
        'delivery_total': sum(s.total for s in delivery),
    }
    return render(request, 'pos/daily_closing.html', context)

@login_required(login_url='login')
def stock_adjustment(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    products = Product.objects.filter(restaurant=restaurant, is_deal=False).order_by('name')

    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        adjustment = request.POST.get('adjustment')
        reason = request.POST.get('reason', '').strip()

        try:
            product = Product.objects.get(id=product_id, restaurant=restaurant)
            adjustment = int(adjustment)

            product.stock = max(0, product.stock + adjustment)
            product.save()

            # Optional: You can later log this adjustment if needed
        except:
            pass

        return redirect('stock_adjustment')

    context = {
        'restaurant': restaurant,
        'products': products,
    }
    return render(request, 'pos/stock_adjustment.html', context)

@login_required(login_url='login')
def restaurant_settings(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    if request.method == 'POST':
        restaurant.name = request.POST.get('name', restaurant.name).strip()
        restaurant.address = request.POST.get('address', '').strip()
        restaurant.phone = request.POST.get('phone', '').strip()
        restaurant.primary_color = request.POST.get('primary_color', '#0d6efd')
        restaurant.secondary_color = request.POST.get('secondary_color', '#198754')

        # Only update logo if a new file is uploaded
        if request.FILES.get('logo'):
            restaurant.logo = request.FILES.get('logo')

        restaurant.save()
        
        # Force refresh
        return redirect('restaurant_settings')

    context = {
        'restaurant': restaurant,
    }
    return render(request, 'pos/restaurant_settings.html', context)

@login_required(login_url='login')
def manage_staff(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    from django.contrib.auth.models import User
    from .models import UserProfile

    # Get all staff of this restaurant
    staff_list = UserProfile.objects.filter(restaurant=restaurant).select_related('user')

    message = None
    error = None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '').strip()
            is_manager = request.POST.get('is_manager') == 'on'

            if not username or not password:
                error = "Username and Password are required."
            elif User.objects.filter(username=username).exists():
                error = "This username is already taken. Please choose another."
            else:
                # Create user
                user = User.objects.create_user(username=username, password=password)
                # Link to restaurant
                UserProfile.objects.create(
                    user=user,
                    restaurant=restaurant,
                    is_manager=is_manager
                )
                message = f"Staff '{username}' created successfully."

        elif action == 'delete':
            profile_id = request.POST.get('profile_id')
            profile = UserProfile.objects.filter(id=profile_id, restaurant=restaurant).first()
            if profile and profile.user != request.user:  # Prevent deleting yourself
                user = profile.user
                profile.delete()
                user.delete()
                message = "Staff deleted successfully."
            else:
                error = "You cannot delete your own account."

    context = {
        'restaurant': restaurant,
        'staff_list': staff_list,
        'message': message,
        'error': error,
    }
    return render(request, 'pos/manage_staff.html', context)

@login_required(login_url='login')
def manage_ingredients(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    from .models import Ingredient
    ingredients = Ingredient.objects.filter(restaurant=restaurant).order_by('name')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            name = request.POST.get('name', '').strip()
            unit = request.POST.get('unit', 'gram')
            stock = request.POST.get('stock', 0)
            threshold = request.POST.get('low_stock_threshold', 100)

            if name:
                Ingredient.objects.create(
                    restaurant=restaurant,
                    name=name,
                    unit=unit,
                    stock=stock or 0,
                    low_stock_threshold=threshold or 100
                )

        elif action == 'edit':
            ing_id = request.POST.get('ingredient_id')
            ingredient = Ingredient.objects.filter(id=ing_id, restaurant=restaurant).first()
            if ingredient:
                ingredient.name = request.POST.get('name', ingredient.name)
                ingredient.unit = request.POST.get('unit', ingredient.unit)
                ingredient.stock = request.POST.get('stock', ingredient.stock)
                ingredient.low_stock_threshold = request.POST.get('low_stock_threshold', ingredient.low_stock_threshold)
                ingredient.save()

        elif action == 'delete':
            ing_id = request.POST.get('ingredient_id')
            Ingredient.objects.filter(id=ing_id, restaurant=restaurant).delete()

        return redirect('manage_ingredients')

    context = {
        'restaurant': restaurant,
        'ingredients': ingredients,
    }
    return render(request, 'pos/manage_ingredients.html', context)


@login_required(login_url='login')
def manage_recipe(request, product_id):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    from .models import Ingredient, RecipeItem, Product

    product = Product.objects.filter(id=product_id, restaurant=restaurant).first()
    if not product:
        return redirect('manage_products')

    ingredients = Ingredient.objects.filter(restaurant=restaurant, is_active=True)
    recipe_items = product.recipe_items.select_related('ingredient').all()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_recipe':
            ingredient_id = request.POST.get('ingredient_id')
            quantity = request.POST.get('quantity')
            if ingredient_id and quantity:
                RecipeItem.objects.create(
                    product=product,
                    ingredient_id=ingredient_id,
                    quantity=quantity
                )

        elif action == 'remove_recipe':
            recipe_id = request.POST.get('recipe_id')
            RecipeItem.objects.filter(id=recipe_id, product=product).delete()

        return redirect('manage_recipe', product_id=product.id)

    context = {
        'restaurant': restaurant,
        'product': product,
        'ingredients': ingredients,
        'recipe_items': recipe_items,
    }
    return render(request, 'pos/manage_recipe.html', context)