import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import aliased 
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
import io


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = str(uuid.uuid4())

db = SQLAlchemy(app)

class Product(db.Model):
    __tablename__ = 'products'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False, unique=True)
    sku = db.Column(db.String(50), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    unit_of_measure = db.Column(db.String(20), nullable=False, default='unit')

class Location(db.Model):
    __tablename__ = 'locations'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False, unique=True)
    address = db.Column(db.Text, nullable=True)
    type = db.Column(db.String(50), nullable=False, default='Warehouse')

class ProductMovement(db.Model):
    __tablename__ = 'product_movements'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    product_id = db.Column(db.String(36), db.ForeignKey('products.id'), nullable=False)
    from_location_id = db.Column(db.String(36), db.ForeignKey('locations.id'), nullable=True)
    to_location_id = db.Column(db.String(36), db.ForeignKey('locations.id'), nullable=True)
    qty = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.String(50), nullable=False, default='SYSTEM_ADMIN')
    
    product = db.relationship('Product', backref='movements')
    from_location = db.relationship('Location', foreign_keys=[from_location_id], backref='outgoing_movements')
    to_location = db.relationship('Location', foreign_keys=[to_location_id], backref='incoming_movements')

def get_inventory_balances():
    # Subquery for incoming quantities
    incoming_subquery = db.session.query(
        ProductMovement.to_location_id.label('location_id'),
        ProductMovement.product_id.label('product_id'),
        func.sum(ProductMovement.qty).label('incoming_qty')
    ).filter(
        ProductMovement.to_location_id.isnot(None)
    ).group_by(
        ProductMovement.to_location_id, 
        ProductMovement.product_id
    ).subquery('incoming')
    
    # Subquery for outgoing quantities
    outgoing_subquery = db.session.query(
        ProductMovement.from_location_id.label('location_id'),
        ProductMovement.product_id.label('product_id'),
        func.sum(ProductMovement.qty).label('outgoing_qty')
    ).filter(
        ProductMovement.from_location_id.isnot(None)
    ).group_by(
        ProductMovement.from_location_id, 
        ProductMovement.product_id
    ).subquery('outgoing')
    
    # Get all product-location pairs that have movements
    product_location_pairs = db.session.query(
        ProductMovement.product_id.label('product_id'),
        ProductMovement.to_location_id.label('location_id')
    ).filter(
        ProductMovement.to_location_id.isnot(None)
    ).union(
        db.session.query(
            ProductMovement.product_id.label('product_id'),
            ProductMovement.from_location_id.label('location_id')
        ).filter(
            ProductMovement.from_location_id.isnot(None)
        )
    ).distinct().subquery('pairs')
    
    # Main query joining everything together
    query = db.session.query(
        Product.name.label('product_name'),
        Location.name.label('location_name'),
        Product.sku.label('sku'),
        func.coalesce(incoming_subquery.c.incoming_qty, 0).label('incoming'),
        func.coalesce(outgoing_subquery.c.outgoing_qty, 0).label('outgoing'),
        (func.coalesce(incoming_subquery.c.incoming_qty, 0) - func.coalesce(outgoing_subquery.c.outgoing_qty, 0)).label('balance')
    ).select_from(
        product_location_pairs
    ).join(
        Product, product_location_pairs.c.product_id == Product.id
    ).join(
        Location, product_location_pairs.c.location_id == Location.id
    ).outerjoin(
        incoming_subquery, 
        and_(
            incoming_subquery.c.product_id == product_location_pairs.c.product_id,
            incoming_subquery.c.location_id == product_location_pairs.c.location_id
        )
    ).outerjoin(
        outgoing_subquery,
        and_(
            outgoing_subquery.c.product_id == product_location_pairs.c.product_id,
            outgoing_subquery.c.location_id == product_location_pairs.c.location_id
        )
    ).order_by(
        Product.name, 
        Location.name
    )
    
    return query.all()

def get_movement_log(product_id=None, location_id=None):
    FromLocation = aliased(Location) 
    ToLocation = aliased(Location)   

    query = db.session.query(
        ProductMovement, 
        Product, 
        FromLocation, 
        ToLocation    
    ).outerjoin(
        Product, ProductMovement.product_id == Product.id
    ).outerjoin(
        FromLocation, ProductMovement.from_location_id == FromLocation.id 
    ).outerjoin(
        ToLocation, ProductMovement.to_location_id == ToLocation.id       
    )
    
    if product_id:
        query = query.filter(ProductMovement.product_id == product_id)
    
    if location_id:
        query = query.filter(
            or_(
                ProductMovement.from_location_id == location_id,
                ProductMovement.to_location_id == location_id
            )
        )
    
    return query.order_by(ProductMovement.timestamp.desc()).all()

@app.cli.command('seed')
def seed_database():
    db.create_all()
    
    products_data = [
        {'name': 'Laptop Pro X', 'sku': 'LPX-2025', 'description': 'High-performance laptop for professionals', 'unit_of_measure': 'unit'},
        {'name': 'Gaming Mouse', 'sku': 'GM-001', 'description': 'Precision gaming mouse with RGB lighting', 'unit_of_measure': 'unit'},
        {'name': 'Wireless Keyboard', 'sku': 'WK-2024', 'description': 'Ergonomic wireless keyboard', 'unit_of_measure': 'unit'},
        {'name': 'Monitor 4K', 'sku': 'M4K-2025', 'description': 'Ultra HD 4K monitor for professional use', 'unit_of_measure': 'unit'},
        {'name': 'Office Chair', 'sku': 'OC-2024', 'description': 'Ergonomic office chair with lumbar support', 'unit_of_measure': 'unit'}
    ]
    
    locations_data = [
        {'name': 'Zone 1A Shelf', 'address': '123 Industrial Blvd, City A', 'type': 'Warehouse'},
        {'name': 'Zone 2B Storage', 'address': '456 Commerce St, City B', 'type': 'Warehouse'},
        {'name': 'Retail Floor', 'address': '789 Main St, Downtown', 'type': 'Retail'},
        {'name': 'Fulfillment Center', 'address': '321 Logistics Ave, Industrial District', 'type': 'Fulfillment'},
        {'name': 'Back Stock Room', 'address': '654 Storage Lane, Warehouse District', 'type': 'Warehouse'}
    ]
    
    for product_data in products_data:
        existing_product = Product.query.filter_by(name=product_data['name']).first()
        if not existing_product:
            product = Product(**product_data)
            db.session.add(product)
    
    for location_data in locations_data:
        existing_location = Location.query.filter_by(name=location_data['name']).first()
        if not existing_location:
            location = Location(**location_data)
            db.session.add(location)
    
    db.session.commit()
    
    products = Product.query.all()
    locations = Location.query.all()
    
    movements_data = [
        {'product': products[0], 'to_location': locations[0], 'qty': 100, 'note': 'Initial stock receipt'},
        {'product': products[1], 'to_location': locations[0], 'qty': 200, 'note': 'Initial stock receipt'},
        {'product': products[2], 'to_location': locations[1], 'qty': 150, 'note': 'Initial stock receipt'},
        {'product': products[3], 'to_location': locations[2], 'qty': 75, 'note': 'Initial stock receipt'},
        {'product': products[4], 'to_location': locations[3], 'qty': 50, 'note': 'Initial stock receipt'},
        {'product': products[0], 'from_location': locations[0], 'to_location': locations[2], 'qty': 20, 'note': 'Transfer to retail floor'},
        {'product': products[1], 'from_location': locations[0], 'to_location': locations[2], 'qty': 30, 'note': 'Transfer to retail floor'},
        {'product': products[2], 'from_location': locations[1], 'to_location': locations[2], 'qty': 25, 'note': 'Transfer to retail floor'},
        {'product': products[3], 'from_location': locations[2], 'qty': 5, 'note': 'Customer sale'},
        {'product': products[4], 'from_location': locations[3], 'qty': 3, 'note': 'Customer sale'},
        {'product': products[0], 'from_location': locations[2], 'qty': 8, 'note': 'Customer sale'},
        {'product': products[1], 'from_location': locations[2], 'qty': 12, 'note': 'Customer sale'},
        {'product': products[2], 'from_location': locations[2], 'qty': 6, 'note': 'Customer sale'},
        {'product': products[0], 'to_location': locations[0], 'qty': 50, 'note': 'Restock from supplier'},
        {'product': products[1], 'to_location': locations[0], 'qty': 75, 'note': 'Restock from supplier'},
        {'product': products[2], 'to_location': locations[1], 'qty': 60, 'note': 'Restock from supplier'},
        {'product': products[3], 'to_location': locations[2], 'qty': 40, 'note': 'Restock from supplier'},
        {'product': products[4], 'to_location': locations[3], 'qty': 25, 'note': 'Restock from supplier'},
        {'product': products[0], 'from_location': locations[0], 'to_location': locations[1], 'qty': 30, 'note': 'Warehouse rebalancing'},
        {'product': products[1], 'from_location': locations[1], 'to_location': locations[0], 'qty': 40, 'note': 'Warehouse rebalancing'},
        {'product': products[2], 'from_location': locations[1], 'to_location': locations[0], 'qty': 20, 'note': 'Warehouse rebalancing'},
        {'product': products[3], 'from_location': locations[0], 'to_location': locations[1], 'qty': 15, 'note': 'Warehouse rebalancing'},
        {'product': products[4], 'from_location': locations[3], 'to_location': locations[4], 'qty': 10, 'note': 'Back stock transfer'},
        {'product': products[0], 'from_location': locations[1], 'to_location': locations[2], 'qty': 12, 'note': 'Additional retail stock'},
        {'product': products[1], 'from_location': locations[0], 'to_location': locations[2], 'qty': 18, 'note': 'Additional retail stock'},
        {'product': products[2], 'from_location': locations[0], 'to_location': locations[2], 'qty': 8, 'note': 'Additional retail stock'},
        {'product': products[3], 'from_location': locations[1], 'to_location': locations[2], 'qty': 6, 'note': 'Additional retail stock'},
        {'product': products[4], 'from_location': locations[4], 'to_location': locations[2], 'qty': 4, 'note': 'Additional retail stock'},
        {'product': products[0], 'from_location': locations[2], 'qty': 7, 'note': 'Customer sale'},
        {'product': products[1], 'from_location': locations[2], 'qty': 9, 'note': 'Customer sale'},
        {'product': products[2], 'from_location': locations[2], 'qty': 4, 'note': 'Customer sale'},
        {'product': products[3], 'from_location': locations[2], 'qty': 3, 'note': 'Customer sale'},
        {'product': products[4], 'from_location': locations[2], 'qty': 2, 'note': 'Customer sale'},
        {'product': products[0], 'to_location': locations[3], 'qty': 35, 'note': 'Direct fulfillment stock'},
        {'product': products[1], 'to_location': locations[3], 'qty': 45, 'note': 'Direct fulfillment stock'},
        {'product': products[2], 'to_location': locations[3], 'qty': 30, 'note': 'Direct fulfillment stock'},
        {'product': products[3], 'to_location': locations[3], 'qty': 20, 'note': 'Direct fulfillment stock'},
        {'product': products[4], 'to_location': locations[3], 'qty': 15, 'note': 'Direct fulfillment stock'},
        {'product': products[0], 'from_location': locations[3], 'qty': 15, 'note': 'Online order fulfillment'},
        {'product': products[1], 'from_location': locations[3], 'qty': 22, 'note': 'Online order fulfillment'},
        {'product': products[2], 'from_location': locations[3], 'qty': 18, 'note': 'Online order fulfillment'},
        {'product': products[3], 'from_location': locations[3], 'qty': 12, 'note': 'Online order fulfillment'},
        {'product': products[4], 'from_location': locations[3], 'qty': 8, 'note': 'Online order fulfillment'},
        {'product': products[0], 'from_location': locations[2], 'qty': 3, 'note': 'Final sale'},
        {'product': products[1], 'from_location': locations[2], 'qty': 5, 'note': 'Final sale'},
        {'product': products[2], 'from_location': locations[2], 'qty': 2, 'note': 'Final sale'},
        {'product': products[3], 'from_location': locations[2], 'qty': 1, 'note': 'Final sale'},
        {'product': products[4], 'from_location': locations[2], 'qty': 1, 'note': 'Final sale'},
        {'product': products[0], 'to_location': locations[4], 'qty': 25, 'note': 'End of period stock adjustment'},
        {'product': products[1], 'to_location': locations[4], 'qty': 35, 'note': 'End of period stock adjustment'},
        {'product': products[2], 'to_location': locations[4], 'qty': 28, 'note': 'End of period stock adjustment'},
        {'product': products[3], 'to_location': locations[4], 'qty': 18, 'note': 'End of period stock adjustment'},
        {'product': products[4], 'to_location': locations[4], 'qty': 12, 'note': 'End of period stock adjustment'}
    ]
    
    for movement_data in movements_data:
        movement = ProductMovement(
            product_id=movement_data['product'].id,
            from_location_id=movement_data.get('from_location').id if movement_data.get('from_location') else None,
            to_location_id=movement_data.get('to_location').id if movement_data.get('to_location') else None,
            qty=movement_data['qty'],
            note=movement_data['note'],
            user_id='SYSTEM_ADMIN'
        )
        db.session.add(movement)
    
    db.session.commit()
    print("Database seeded successfully!")

@app.route('/')
def index():
    product_count = Product.query.count()
    location_count = Location.query.count()
    movement_count = ProductMovement.query.count()
    
    total_inflow = db.session.query(func.sum(ProductMovement.qty)).filter(
        ProductMovement.to_location_id.isnot(None)
    ).scalar() or 0
    
    total_outflow = db.session.query(func.sum(ProductMovement.qty)).filter(
        ProductMovement.from_location_id.isnot(None)
    ).scalar() or 0
    
    return render_template('index.html', 
                           product_count=product_count,
                           location_count=location_count,
                           movement_count=movement_count,
                           total_inflow=total_inflow,
                           total_outflow=total_outflow)

@app.route('/products')
def products():
    products = Product.query.all()
    return render_template('products.html', products=products)

@app.route('/products/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        name = request.form['name']
        sku = request.form['sku']
        description = request.form['description']
        unit_of_measure = request.form['unit_of_measure']
        
        existing_product = Product.query.filter_by(name=name).first()
        if existing_product:
            flash('Product with this name already exists!', 'error')
            return redirect(url_for('add_product'))
        
        existing_sku = Product.query.filter_by(sku=sku).first()
        if existing_sku:
            flash('Product with this SKU already exists!', 'error')
            return redirect(url_for('add_product'))
        
        product = Product(name=name, sku=sku, description=description, unit_of_measure=unit_of_measure)
        db.session.add(product)
        db.session.commit()
        flash('Product added successfully!', 'success')
        return redirect(url_for('products'))
    
    return render_template('add_product.html')

@app.route('/products/edit/<id>', methods=['GET', 'POST'])
def edit_product(id):
    product = Product.query.get_or_404(id)
    
    if request.method == 'POST':
        name = request.form['name']
        sku = request.form['sku']
        description = request.form['description']
        unit_of_measure = request.form['unit_of_measure']
        
        existing_product = Product.query.filter(Product.name == name, Product.id != id).first()
        if existing_product:
            flash('Product with this name already exists!', 'error')
            return redirect(url_for('edit_product', id=id))
        
        existing_sku = Product.query.filter(Product.sku == sku, Product.id != id).first()
        if existing_sku:
            flash('Product with this SKU already exists!', 'error')
            return redirect(url_for('edit_product', id=id))
        
        product.name = name
        product.sku = sku
        product.description = description
        product.unit_of_measure = unit_of_measure
        db.session.commit()
        flash('Product updated successfully!', 'success')
        return redirect(url_for('products'))
    
    return render_template('edit_product.html', product=product)

@app.route('/products/delete/<id>', methods=['POST'])
def delete_product(id):
    product = Product.query.get_or_404(id)
    
    movement_count = ProductMovement.query.filter_by(product_id=id).count()
    if movement_count > 0:
        flash('Cannot delete product with existing movements!', 'error')
        return redirect(url_for('products'))
    
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted successfully!', 'success')
    return redirect(url_for('products'))

@app.route('/locations')
def locations():
    locations = Location.query.all()
    return render_template('locations.html', locations=locations)

@app.route('/locations/add', methods=['GET', 'POST'])
def add_location():
    if request.method == 'POST':
        name = request.form['name']
        address = request.form['address']
        type = request.form['type']
        
        existing_location = Location.query.filter_by(name=name).first()
        if existing_location:
            flash('Location with this name already exists!', 'error')
            return redirect(url_for('add_location'))
        
        location = Location(name=name, address=address, type=type)
        db.session.add(location)
        db.session.commit()
        flash('Location added successfully!', 'success')
        return redirect(url_for('locations'))
    
    return render_template('add_location.html')

@app.route('/locations/edit/<id>', methods=['GET', 'POST'])
def edit_location(id):
    location = Location.query.get_or_404(id)
    
    if request.method == 'POST':
        name = request.form['name']
        address = request.form['address']
        type = request.form['type']
        
        existing_location = Location.query.filter(Location.name == name, Location.id != id).first()
        if existing_location:
            flash('Location with this name already exists!', 'error')
            return redirect(url_for('edit_location', id=id))
        
        location.name = name
        location.address = address
        location.type = type
        db.session.commit()
        flash('Location updated successfully!', 'success')
        return redirect(url_for('locations'))
    
    return render_template('edit_location.html', location=location)

@app.route('/locations/delete/<id>', methods=['POST'])
def delete_location(id):
    location = Location.query.get_or_404(id)
    
    incoming_count = ProductMovement.query.filter_by(to_location_id=id).count()
    outgoing_count = ProductMovement.query.filter_by(from_location_id=id).count()
    
    if incoming_count > 0 or outgoing_count > 0:
        flash('Cannot delete location with existing movements!', 'error')
        return redirect(url_for('locations'))
    
    db.session.delete(location)
    db.session.commit()
    flash('Location deleted successfully!', 'success')
    return redirect(url_for('locations'))

@app.route('/movements')
def movements():
    FromLocation = aliased(Location) 
    ToLocation = aliased(Location)   

    movements = db.session.query(
        ProductMovement, 
        Product, 
        FromLocation, 
        ToLocation    
    ).outerjoin(
        Product, ProductMovement.product_id == Product.id
    ).outerjoin(
        FromLocation, ProductMovement.from_location_id == FromLocation.id 
    ).outerjoin(
        ToLocation, ProductMovement.to_location_id == ToLocation.id       
    ).order_by(ProductMovement.timestamp.desc()).all()
    
    return render_template('movements.html', movements=movements)

@app.route('/movements/add', methods=['GET', 'POST'])
def add_movement():
    if request.method == 'POST':
        product_id = request.form['product_id']
        from_location_id = request.form['from_location_id'] if request.form['from_location_id'] else None
        to_location_id = request.form['to_location_id'] if request.form['to_location_id'] else None
        qty = int(request.form['qty'])
        note = request.form['note']
        
        if not from_location_id and not to_location_id:
            flash('Either source or destination location must be specified!', 'error')
            return redirect(url_for('add_movement'))
        
        if qty <= 0:
            flash('Quantity must be greater than 0!', 'error')
            return redirect(url_for('add_movement'))
        
        if from_location_id:
            # Subquery for incoming quantity
            incoming_qty = db.session.query(func.coalesce(func.sum(ProductMovement.qty), 0)).filter(
                ProductMovement.product_id == product_id,
                ProductMovement.to_location_id == from_location_id
            ).scalar()
            
            # Subquery for outgoing quantity
            outgoing_qty = db.session.query(func.coalesce(func.sum(ProductMovement.qty), 0)).filter(
                ProductMovement.product_id == product_id,
                ProductMovement.from_location_id == from_location_id
            ).scalar()
            
            current_balance = incoming_qty - outgoing_qty
            
            if current_balance - qty < 0:
                product = Product.query.get(product_id)
                location = Location.query.get(from_location_id)
                flash(f'Transaction failed: Insufficient stock ({current_balance} units available) in {location.name} to move {qty} units of {product.name}.', 'error')
                return redirect(url_for('add_movement'))
        
        movement = ProductMovement(
            product_id=product_id,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            qty=qty,
            note=note,
            user_id='SYSTEM_ADMIN'
        )
        db.session.add(movement)
        db.session.commit()
        flash('Movement added successfully!', 'success')
        return redirect(url_for('movements'))
    
    products = Product.query.all()
    locations = Location.query.all()
    return render_template('add_movement.html', products=products, locations=locations)
@app.route('/download_report')
def download_report():
    """Generate PDF report of inventory balances"""
    balances = get_inventory_balances()
    
    # Create a buffer for the PDF
    buffer = io.BytesIO()
    
    # Create the PDF object
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1*inch)
    
    # Container for the 'Flowable' objects
    elements = []
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=30,
        alignment=1,  # Center alignment
    )
    
    # Add title
    title = Paragraph("Inventory Report - Arele", title_style)
    elements.append(title)
    
    # Add generation date
    date_style = ParagraphStyle(
        'CustomDate',
        parent=styles['Normal'],
        fontSize=10,
        alignment=1,
        spaceAfter=20,
    )
    date_text = Paragraph(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", date_style)
    elements.append(date_text)
    
    # Prepare table data
    table_data = [['Product', 'SKU', 'Location', 'Incoming', 'Outgoing', 'Balance']]
    
    for balance in balances:
        table_data.append([
            balance.product_name,
            balance.sku,
            balance.location_name,
            str(balance.incoming),
            str(balance.outgoing),
            str(balance.balance)
        ])
    
    # Create table
    table = Table(table_data, repeatRows=1)
    
    # Add style to table
    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f2937')),  # Dark gray header
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),  # Alternate row colors
    ])
    
    # Apply conditional formatting for balance column
    for i in range(1, len(table_data)):
        balance_value = balances[i-1].balance
        if balance_value > 100:
            table_style.add('BACKGROUND', (5, i), (5, i), colors.HexColor('#10b981'))  # Green
            table_style.add('TEXTCOLOR', (5, i), (5, i), colors.white)
        elif balance_value > 0:
            table_style.add('BACKGROUND', (5, i), (5, i), colors.HexColor('#0ea5e9'))  # Blue
            table_style.add('TEXTCOLOR', (5, i), (5, i), colors.white)
        elif balance_value == 0:
            table_style.add('BACKGROUND', (5, i), (5, i), colors.HexColor('#f59e0b'))  # Amber
            table_style.add('TEXTCOLOR', (5, i), (5, i), colors.white)
        else:
            table_style.add('BACKGROUND', (5, i), (5, i), colors.HexColor('#dc2626'))  # Red
            table_style.add('TEXTCOLOR', (5, i), (5, i), colors.white)
    
    table.setStyle(table_style)
    elements.append(table)
    
    # Add summary
    if balances:
        total_items = len(balances)
        positive_balance = sum(1 for b in balances if b.balance > 0)
        zero_balance = sum(1 for b in balances if b.balance == 0)
        negative_balance = sum(1 for b in balances if b.balance < 0)
        
        elements.append(Spacer(1, 20))
        
        summary_style = ParagraphStyle(
            'CustomSummary',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=5,
        )
        
        summary_text = f"""
        <b>Summary:</b><br/>
        Total Items: {total_items}<br/>
        Positive Balance: {positive_balance}<br/>
        Zero Balance: {zero_balance}<br/>
        Negative Balance: {negative_balance}
        """
        summary = Paragraph(summary_text, summary_style)
        elements.append(summary)
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF value from buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create response
    response = Response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=inventory_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
    
    return response

@app.route('/report')
def report():
    balances = get_inventory_balances()
    return render_template('report.html', balances=balances)

@app.route('/log')
def movement_log():
    product_id = request.args.get('product_id')
    location_id = request.args.get('location_id')
    movements = get_movement_log(product_id, location_id)
    
    products = Product.query.all()
    locations = Location.query.all()
    
    return render_template('log.html', movements=movements, products=products, locations=locations, 
                           selected_product_id=product_id, selected_location_id=location_id)

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)