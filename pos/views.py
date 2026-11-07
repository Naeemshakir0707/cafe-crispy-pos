from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db.models import Sum, Count, F, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import datetime
import pytz
from decimal import Decimal
import json
from .models import (
    Restaurant, UserProfile, Category, Product, Sale, SaleItem,
    Customer, Ingredient, RecipeItem, DealComponent
)


def get_user_restaurant(user):
    try:
        profile = UserProfile.objects.get(user=user)
        return profile.restaurant
    except UserProfile.DoesNotExist:
        return None


def user_is_manager(user):
    try:
        return user.userprofile.is_manager
    except:
        return False


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

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request'})

    try:
        data = json.loads(request.body)

        # ===== Device Time Handling =====
        client_time_str = data.get('client_time')
        client_time = timezone.now()

        if client_time_str:
            try:
                from django.utils.dateparse import parse_datetime
                parsed = parse_datetime(client_time_str)
                if parsed:
                    # Convert to Pakistan timezone
                    pakistan_tz = pytz.timezone('Asia/Karachi')
                    if timezone.is_naive(parsed):
                        client_time = pakistan_tz.localize(parsed)
                    else:
                        client_time = parsed.astimezone(pakistan_tz)
            except Exception as e:
                print("Time parse error:", e)
                client_time = timezone.now()
        # ================================

        cart = data.get('cart', [])
        if not cart:
            return JsonResponse({'success': False, 'message': 'Cart is empty'})

        subtotal = Decimal('0.00')
        for item in cart:
            subtotal += Decimal(str(item['price'])) * item['qty']

        discount = Decimal(str(data.get('discount', 0)))
        delivery_charge = Decimal(str(data.get('delivery_charge', 0)))
        total = max(Decimal('0.00'), subtotal - discount + delivery_charge)

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
        last_sale = Sale.objects.filter(
            restaurant=restaurant,
            invoice_no__startswith=prefix
        ).order_by('-id').first()

        new_number = 1
        if last_sale:
            try:
                new_number = int(last_sale.invoice_no.split('-')[-1]) + 1
            except:
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
            created_by=request.user,
            created_at=client_time
        )

        for item in cart:
            product = Product.objects.get(id=item['id'], restaurant=restaurant)
            qty = item['qty']

            SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=qty,
                price=Decimal(str(item['price'])),
                total=Decimal(str(item['price'])) * qty
            )

            # Stock Reduction
            recipe_items = product.recipe_items.all()
            if recipe_items.exists():
                for recipe in recipe_items:
                    ingredient = recipe.ingredient
                    used_qty = recipe.quantity * qty
                    ingredient.stock = max(0, ingredient.stock - used_qty)
                    ingredient.save()
            else:
                if product.is_deal:
                    for component in product.components.all():
                        component.product.stock = max(0, component.product.stock - (component.quantity * qty))
                        component.product.save()
                else:
                    product.stock = max(0, product.stock - qty)
                    product.save()

        return JsonResponse({
            'success': True,
            'message': 'Sale completed successfully!',
            'invoice_no': invoice_no,
            'total': str(total)
        })

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


@login_required(login_url='login')
def sales_history(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    if not user_is_manager(request.user):
        return redirect('pos')

    sales = Sale.objects.filter(restaurant=restaurant).order_by('-created_at')

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
        context = {
            'sale': sale,
            'items': sale.items.all(),
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

    if not user_is_manager(request.user):
        return redirect('pos')

    today = timezone.now().date()
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except:
            start_date = today.replace(day=1)
            end_date = today
    else:
        start_date = today.replace(day=1)
        end_date = today

    filtered_sales = Sale.objects.filter(
        restaurant=restaurant,
        status='completed',
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    total_sales = sum(s.total for s in filtered_sales)
    total_orders = filtered_sales.count()

    today_sales = Sale.objects.filter(
        restaurant=restaurant,
        status='completed',
        created_at__date=today
    )
    total_sales_today = sum(s.total for s in today_sales)
    total_orders_today = today_sales.count()

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
        .order_by('-total_qty')[:100]
    )

    low_stock_products = Product.objects.filter(
        restaurant=restaurant,
        is_active=True,
        stock__lte=10
    ).annotate(
        recipe_count=Count('recipe_items')
    ).filter(recipe_count=0).order_by('stock')

    low_stock_ingredients = Ingredient.objects.filter(
        restaurant=restaurant,
        is_active=True,
        stock__lte=F('low_stock_threshold')
    ).order_by('stock')

    context = {
        'restaurant': restaurant,
        'total_sales': total_sales,
        'total_orders': total_orders,
        'total_sales_today': total_sales_today,
        'total_orders_today': total_orders_today,
        'top_products': top_products,
        'low_stock_products': low_stock_products,
        'low_stock_ingredients': low_stock_ingredients,
        'start_date': start_date,
        'end_date': end_date,
        'today': today,
    }
    return render(request, 'pos/dashboard.html', context)


@login_required(login_url='login')
def manage_categories(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')
    if not user_is_manager(request.user):
        return redirect('pos')

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

    return render(request, 'pos/manage_categories.html', {
        'restaurant': restaurant,
        'categories': categories,
    })


@login_required(login_url='login')
def manage_products(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')
    if not user_is_manager(request.user):
        return redirect('pos')

    products = Product.objects.filter(restaurant=restaurant).select_related('category').order_by('name')
    categories = Category.objects.filter(restaurant=restaurant, is_active=True)
    all_products = Product.objects.filter(restaurant=restaurant, is_active=True)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            name = request.POST.get('name', '').strip()
            category_id = request.POST.get('category')
            price = request.POST.get('price')
            stock = request.POST.get('stock', 0)
            is_deal = request.POST.get('is_deal') == 'on'
            image = request.FILES.get('image')

            if name and category_id and price:
                try:
                    Product.objects.create(
                        restaurant=restaurant,
                        name=name,
                        category_id=category_id,
                        price=price,
                        stock=0 if is_deal else (stock or 0),
                        is_deal=is_deal,
                        is_active=True,
                        image=image
                    )
                except Exception as e:
                    print("ERROR CREATING PRODUCT:", str(e))

        elif action == 'edit':
            product_id = request.POST.get('product_id')
            prod = Product.objects.filter(id=product_id, restaurant=restaurant).first()
            if prod:
                prod.name = request.POST.get('name', prod.name)
                prod.category_id = request.POST.get('category', prod.category_id)
                prod.price = request.POST.get('price', prod.price)
                prod.is_deal = request.POST.get('is_deal') == 'on'
                prod.is_active = True
                prod.stock = 0 if prod.is_deal else request.POST.get('stock', prod.stock)
                if request.FILES.get('image'):
                    prod.image = request.FILES.get('image')
                prod.save()

        elif action == 'delete':
            product_id = request.POST.get('product_id')
            Product.objects.filter(id=product_id, restaurant=restaurant).delete()

        elif action == 'add_component':
            deal_id = request.POST.get('deal_id')
            component_id = request.POST.get('component_product')
            quantity = request.POST.get('quantity', 1)
            deal = Product.objects.filter(id=deal_id, restaurant=restaurant, is_deal=True).first()
            component = Product.objects.filter(id=component_id, restaurant=restaurant).first()
            if deal and component:
                DealComponent.objects.create(deal=deal, product=component, quantity=quantity)

        elif action == 'remove_component':
            component_id = request.POST.get('component_id')
            DealComponent.objects.filter(id=component_id, deal__restaurant=restaurant).delete()

        return redirect('manage_products')

    return render(request, 'pos/manage_products.html', {
        'restaurant': restaurant,
        'products': products,
        'categories': categories,
        'all_products': all_products,
    })


@login_required(login_url='login')
def last_10_sales(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')

    sales = Sale.objects.filter(restaurant=restaurant).order_by('-created_at')[:10]
    return render(request, 'pos/last_10_sales.html', {
        'sales': sales,
        'restaurant': restaurant,
    })


@login_required(login_url='login')
def cancel_sale(request, sale_id):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return redirect('dashboard')

    sale = Sale.objects.filter(id=sale_id, restaurant=restaurant).first()
    if sale and sale.status == 'completed':
        for item in sale.items.all():
            product = item.product
            # Restore stock (simple version)
            product.stock += item.quantity
            product.save()
        sale.status = 'cancelled'
        sale.save()

    return redirect(request.META.get('HTTP_REFERER', 'sales_history'))


@login_required(login_url='login')
def search_customers(request):
    restaurant = get_user_restaurant(request.user)
    q = request.GET.get('q', '').strip()
    customers = []

    if restaurant and len(q) >= 2:
        results = Customer.objects.filter(restaurant=restaurant).filter(
            Q(name__icontains=q) | Q(mobile__icontains=q)
        ).order_by('-id')[:10]

        customers = [
            {'name': c.name or '', 'mobile': c.mobile or '', 'address': c.address or ''}
            for c in results
        ]

    return JsonResponse({'customers': customers})


@login_required(login_url='login')
def daily_closing(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')
    if not user_is_manager(request.user):
        return redirect('pos')

    date_str = request.GET.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except:
            selected_date = timezone.now().date()
    else:
        selected_date = timezone.now().date()

    sales = Sale.objects.filter(
        restaurant=restaurant,
        status='completed',
        created_at__date=selected_date
    ).order_by('created_at')

    context = {
        'restaurant': restaurant,
        'selected_date': selected_date,
        'sales': sales,
        'total_sales': sum(s.total for s in sales),
        'total_orders': sales.count(),
        'total_discount': sum(s.discount for s in sales),
        'total_delivery': sum(s.delivery_charge for s in sales),
        'total_subtotal': sum(s.subtotal for s in sales),
        'dine_in_count': sales.filter(order_type='dine_in').count(),
        'dine_in_total': sum(s.total for s in sales.filter(order_type='dine_in')),
        'take_away_count': sales.filter(order_type='take_away').count(),
        'take_away_total': sum(s.total for s in sales.filter(order_type='take_away')),
        'delivery_count': sales.filter(order_type='delivery').count(),
        'delivery_total': sum(s.total for s in sales.filter(order_type='delivery')),
    }
    return render(request, 'pos/daily_closing.html', context)


@login_required(login_url='login')
def stock_adjustment(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')
    if not user_is_manager(request.user):
        return redirect('pos')

    products = Product.objects.filter(restaurant=restaurant, is_deal=False).order_by('name')

    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        adjustment = request.POST.get('adjustment')
        try:
            product = Product.objects.get(id=product_id, restaurant=restaurant)
            product.stock = max(0, product.stock + int(adjustment))
            product.save()
        except:
            pass
        return redirect('stock_adjustment')

    return render(request, 'pos/stock_adjustment.html', {
        'restaurant': restaurant,
        'products': products,
    })


@login_required(login_url='login')
def restaurant_settings(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')
    if not user_is_manager(request.user):
        return redirect('pos')

    if request.method == 'POST':
        restaurant.name = request.POST.get('name', restaurant.name).strip()
        restaurant.address = request.POST.get('address', '').strip()
        restaurant.phone = request.POST.get('phone', '').strip()
        restaurant.primary_color = request.POST.get('primary_color', '#0d6efd')
        restaurant.secondary_color = request.POST.get('secondary_color', '#198754')
        if request.FILES.get('logo'):
            restaurant.logo = request.FILES.get('logo')
        restaurant.save()
        return redirect('restaurant_settings')

    return render(request, 'pos/restaurant_settings.html', {'restaurant': restaurant})


@login_required(login_url='login')
def manage_staff(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')
    if not user_is_manager(request.user):
        return redirect('pos')

    staff_list = UserProfile.objects.filter(restaurant=restaurant).select_related('user')
    message = error = None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '').strip()
            is_manager_flag = request.POST.get('is_manager') == 'on'

            if not username or not password:
                error = "Username and Password are required."
            elif User.objects.filter(username=username).exists():
                error = "This username is already taken."
            else:
                user = User.objects.create_user(username=username, password=password)
                UserProfile.objects.create(user=user, restaurant=restaurant, is_manager=is_manager_flag)
                message = f"Staff '{username}' created successfully."

        elif action == 'delete':
            profile_id = request.POST.get('profile_id')
            profile = UserProfile.objects.filter(id=profile_id, restaurant=restaurant).first()
            if profile and profile.user != request.user:
                user = profile.user
                profile.delete()
                user.delete()
                message = "Staff deleted successfully."
            else:
                error = "You cannot delete your own account."

    return render(request, 'pos/manage_staff.html', {
        'restaurant': restaurant,
        'staff_list': staff_list,
        'message': message,
        'error': error,
    })


@login_required(login_url='login')
def manage_ingredients(request):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')
    if not user_is_manager(request.user):
        return redirect('pos')

    ingredients = Ingredient.objects.filter(restaurant=restaurant).order_by('name')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            name = request.POST.get('name', '').strip()
            if name:
                Ingredient.objects.create(
                    restaurant=restaurant,
                    name=name,
                    unit=request.POST.get('unit', 'gram'),
                    stock=request.POST.get('stock', 0) or 0,
                    low_stock_threshold=request.POST.get('low_stock_threshold', 100) or 100
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

    return render(request, 'pos/manage_ingredients.html', {
        'restaurant': restaurant,
        'ingredients': ingredients,
    })


@login_required(login_url='login')
def manage_recipe(request, product_id):
    restaurant = get_user_restaurant(request.user)
    if not restaurant:
        return render(request, 'pos/no_restaurant.html')
    if not user_is_manager(request.user):
        return redirect('pos')

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
                RecipeItem.objects.create(product=product, ingredient_id=ingredient_id, quantity=quantity)
        elif action == 'remove_recipe':
            recipe_id = request.POST.get('recipe_id')
            RecipeItem.objects.filter(id=recipe_id, product=product).delete()
        return redirect('manage_recipe', product_id=product.id)

    return render(request, 'pos/manage_recipe.html', {
        'restaurant': restaurant,
        'product': product,
        'ingredients': ingredients,
        'recipe_items': recipe_items,
    })