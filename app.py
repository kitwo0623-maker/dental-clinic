from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response
from database import db, Patient, Appointment, MedicalRecord, Invoice, InvoiceItem, Staff, Shift, Product, SubRecord
from datetime import datetime, date
import json
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dental-clinic-secret-key-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///dental_clinic.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

with app.app_context():
    db.create_all()
    # SOAP列のマイグレーション（既存DBに列がない場合のみ追加）
    with db.engine.connect() as conn:
        existing_sub = [row[1] for row in conn.execute(db.text("PRAGMA table_info(sub_records)"))]
        for col, coldef in [
            ('soap_s', 'TEXT'),
            ('soap_o', 'TEXT'),
            ('soap_a', 'TEXT'),
            ('soap_p', 'TEXT'),
        ]:
            if col not in existing_sub:
                conn.execute(db.text(f"ALTER TABLE sub_records ADD COLUMN {col} {coldef}"))
        existing_pat = [row[1] for row in conn.execute(db.text("PRAGMA table_info(patients)"))]
        if 'systemic_diseases' not in existing_pat:
            conn.execute(db.text("ALTER TABLE patients ADD COLUMN systemic_diseases TEXT"))
        conn.commit()


@app.context_processor
def inject_globals():
    return {'now': datetime.utcnow}


def generate_patient_number():
    last = Patient.query.order_by(Patient.id.desc()).first()
    if last:
        num = int(last.patient_number.replace('P', '')) + 1
    else:
        num = 1
    return f'P{num:05d}'


def parse_systemic_diseases(form):
    result = []
    simple_list = ['糖尿病', '高血圧', '甲状腺機能低下', '甲状腺機能亢進', '骨粗鬆症', '慢性気管支炎', '喘息', 'てんかん', 'HIV']
    sub_map = [
        ('心臓病',         ['感染性心内膜炎', '心臓弁膜症', '心不全', 'その他']),
        ('副腎皮質機能不全', ['透析', '腎移植']),
        ('脳血管障害',      ['脳卒中', '狭心症', '心筋梗塞']),
        ('肝臓病',         ['B肝', 'C肝', 'その他']),
    ]
    for d in simple_list:
        if d in form.getlist('sys_s'):
            result.append(d)
    for parent, _ in sub_map:
        subs = form.getlist(f'sub_{parent}')
        if subs:
            result.append(f'{parent}（{"・".join(subs)}）')
    other_text = form.get('systemic_other', '').strip()
    if form.get('sys_other_check'):
        result.append(f'その他：{other_text}' if other_text else 'その他')
    return '、'.join(result)


def parse_systemic_for_form(sys_str):
    empty = {'simple': [], 'subs': {}, 'other': '', 'has_other': False}
    if not sys_str:
        return empty
    simple, subs, other, has_other = [], {}, '', False
    sub_parents = ['心臓病', '副腎皮質機能不全', '脳血管障害', '肝臓病']
    for part in sys_str.split('、'):
        part = part.strip()
        if not part:
            continue
        if part.startswith('その他：'):
            has_other, other = True, part[4:]
        elif part == 'その他':
            has_other = True
        else:
            matched = False
            for parent in sub_parents:
                if part.startswith(parent + '（') and part.endswith('）'):
                    subs[parent] = part[len(parent)+1:-1].split('・')
                    matched = True
                    break
                elif part == parent:
                    subs[parent] = []
                    matched = True
                    break
            if not matched:
                simple.append(part)
    return {'simple': simple, 'subs': subs, 'other': other, 'has_other': has_other}


def generate_invoice_number():
    last = Invoice.query.order_by(Invoice.id.desc()).first()
    if last:
        num = int(last.invoice_number.replace('INV', '')) + 1
    else:
        num = 1
    return f'INV{num:06d}'


# ==================== SEO ====================

SITE_URL = os.environ.get('SITE_URL', 'https://dental-clinic-wpg6.onrender.com')


@app.route('/robots.txt')
def robots_txt():
    content = f"""User-agent: *
Allow: /
Disallow: /admin/

Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{SITE_URL}/</loc>
    <changefreq>monthly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{SITE_URL}/shop</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{SITE_URL}/contact</loc>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>"""
    return Response(content, mimetype='application/xml')


# ==================== Public Pages ====================

@app.route('/')
def index():
    products = Product.query.filter_by(is_active=True).limit(6).all()
    return render_template('index.html', products=products)


@app.route('/shop')
def shop():
    category = request.args.get('category', '')
    sort = request.args.get('sort', 'newest')
    in_stock = request.args.get('in_stock', '')

    query = Product.query.filter_by(is_active=True)
    if category:
        query = query.filter_by(category=category)
    if in_stock:
        query = query.filter(Product.stock > 0)

    if sort == 'price_asc':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_desc':
        query = query.order_by(Product.price.desc())
    else:
        query = query.order_by(Product.created_at.desc())

    products = query.all()
    all_products = Product.query.filter_by(is_active=True).all()
    categories = db.session.query(Product.category).filter(
        Product.is_active == True, Product.category != None, Product.category != ''
    ).distinct().all()
    return render_template(
        'shop.html',
        products=products,
        all_products=all_products,
        categories=[c[0] for c in categories],
        selected_category=category,
        current_sort=sort,
        hide_out_of_stock=bool(in_stock)
    )


@app.route('/shop/<int:product_id>')
def shop_product(product_id):
    product = Product.query.filter_by(id=product_id, is_active=True).first_or_404()
    related = Product.query.filter(
        Product.is_active == True,
        Product.category == product.category,
        Product.id != product_id
    ).limit(4).all()
    return render_template('shop_product.html', product=product, related=related)


@app.route('/contact')
def contact():
    return render_template('contact.html')


# ==================== Admin Dashboard ====================

@app.route('/admin')
def admin():
    today = date.today()
    today_appointments = Appointment.query.filter(
        db.func.date(Appointment.appointment_date) == today
    ).order_by(Appointment.appointment_date).all()
    total_patients = Patient.query.count()
    monthly_revenue = db.session.query(db.func.sum(Invoice.total_amount)).filter(
        db.extract('month', Invoice.issue_date) == today.month,
        db.extract('year', Invoice.issue_date) == today.year,
        Invoice.status == '支払済'
    ).scalar() or 0
    unpaid_count = Invoice.query.filter_by(status='未払い').count()
    return render_template('admin/dashboard.html',
                           today_appointments=today_appointments,
                           total_patients=total_patients,
                           monthly_revenue=monthly_revenue,
                           unpaid_count=unpaid_count,
                           today=today)


# ==================== Patients ====================

@app.route('/admin/patients')
def patients_list():
    search = request.args.get('search', '')
    query = Patient.query
    if search:
        query = query.filter(
            db.or_(Patient.name.contains(search), Patient.name_kana.contains(search),
                   Patient.patient_number.contains(search), Patient.phone.contains(search))
        )
    patients = query.order_by(Patient.created_at.desc()).all()
    return render_template('admin/patients.html', patients=patients, search=search)


@app.route('/admin/patients/new', methods=['GET', 'POST'])
def patient_new():
    if request.method == 'POST':
        data = request.form
        patient = Patient(
            patient_number=generate_patient_number(),
            name=data['name'],
            name_kana=data.get('name_kana', ''),
            birth_date=datetime.strptime(data['birth_date'], '%Y-%m-%d').date() if data.get('birth_date') else None,
            systemic_diseases=parse_systemic_diseases(request.form),
            allergies=data.get('allergies', ''),
            notes=data.get('notes', '')
        )
        db.session.add(patient)
        db.session.commit()
        flash('患者を登録しました', 'success')
        return redirect(url_for('patient_detail', patient_id=patient.id))
    return render_template('admin/patient_form.html', patient=None, sys_data=parse_systemic_for_form(None))


@app.route('/admin/patients/<int:patient_id>')
def patient_detail(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    records = MedicalRecord.query.filter_by(patient_id=patient_id).order_by(MedicalRecord.visit_date.desc()).all()
    appointments = Appointment.query.filter_by(patient_id=patient_id).order_by(Appointment.appointment_date.desc()).all()
    invoices = Invoice.query.filter_by(patient_id=patient_id).order_by(Invoice.issue_date.desc()).all()
    staff_list = Staff.query.filter_by(is_active=True).all()
    return render_template('admin/patient_detail.html',
                           patient=patient, records=records,
                           appointments=appointments, invoices=invoices,
                           staff_list=staff_list)


@app.route('/admin/patients/<int:patient_id>/edit', methods=['GET', 'POST'])
def patient_edit(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        data = request.form
        patient.name = data['name']
        patient.name_kana = data.get('name_kana', '')
        patient.birth_date = datetime.strptime(data['birth_date'], '%Y-%m-%d').date() if data.get('birth_date') else None
        patient.systemic_diseases = parse_systemic_diseases(request.form)
        patient.allergies = data.get('allergies', '')
        patient.notes = data.get('notes', '')
        db.session.commit()
        flash('患者情報を更新しました', 'success')
        return redirect(url_for('patient_detail', patient_id=patient.id))
    return render_template('admin/patient_form.html', patient=patient, sys_data=parse_systemic_for_form(patient.systemic_diseases))


# ==================== Medical Records ====================

@app.route('/admin/patients/<int:patient_id>/records/new', methods=['GET', 'POST'])
def record_new(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    staff_list = Staff.query.filter_by(is_active=True).all()
    if request.method == 'POST':
        data = request.form
        record = MedicalRecord(
            patient_id=patient_id,
            staff_id=data.get('staff_id') or None,
            visit_date=datetime.strptime(data['visit_date'], '%Y-%m-%dT%H:%M') if data.get('visit_date') else datetime.utcnow(),
            chief_complaint=data.get('chief_complaint', ''),
            diagnosis=data.get('diagnosis', ''),
            treatment=data.get('treatment', ''),
            tooth_chart=data.get('tooth_chart', ''),
            prescription=data.get('prescription', ''),
            next_visit_notes=data.get('next_visit_notes', '')
        )
        db.session.add(record)
        db.session.commit()
        flash('カルテを保存しました', 'success')
        return redirect(url_for('patient_detail', patient_id=patient_id))
    return render_template('admin/record_form.html', patient=patient, record=None, staff_list=staff_list)


@app.route('/admin/records/<int:record_id>/edit', methods=['GET', 'POST'])
def record_edit(record_id):
    record = MedicalRecord.query.get_or_404(record_id)
    patient = record.patient
    staff_list = Staff.query.filter_by(is_active=True).all()
    if request.method == 'POST':
        data = request.form
        record.staff_id = data.get('staff_id') or None
        record.visit_date = datetime.strptime(data['visit_date'], '%Y-%m-%dT%H:%M') if data.get('visit_date') else record.visit_date
        record.chief_complaint = data.get('chief_complaint', '')
        record.diagnosis = data.get('diagnosis', '')
        record.treatment = data.get('treatment', '')
        record.tooth_chart = data.get('tooth_chart', '')
        record.prescription = data.get('prescription', '')
        record.next_visit_notes = data.get('next_visit_notes', '')
        db.session.commit()
        flash('カルテを更新しました', 'success')
        return redirect(url_for('patient_detail', patient_id=patient.id))
    return render_template('admin/record_form.html', patient=patient, record=record, staff_list=staff_list)


# ==================== Sub Records ====================

@app.route('/admin/patients/<int:patient_id>/sub/new')
def patient_sub_record_new(patient_id):
    """カルテ記録フォームをスキップしてサブカルテを直接作成"""
    patient = Patient.query.get_or_404(patient_id)
    record = MedicalRecord(
        patient_id=patient_id,
        visit_date=datetime.utcnow()
    )
    db.session.add(record)
    db.session.commit()
    return redirect(url_for('sub_record_new', record_id=record.id))


@app.route('/admin/records/<int:record_id>/sub/new', methods=['GET', 'POST'])
def sub_record_new(record_id):
    record = MedicalRecord.query.get_or_404(record_id)
    if request.method == 'GET':
        return render_template('admin/sub_record_form.html', record=record, sub=None)
    data = request.form
    sub = SubRecord(
        medical_record_id=record_id,
        tooth_number=data.get('tooth_number', ''),
        soap_s=data.get('soap_s', ''),
        soap_o=data.get('soap_o', ''),
        soap_a=data.get('soap_a', ''),
        soap_p=data.get('soap_p', ''),
        xray_taken=bool(data.get('xray_taken')),
        xray_note=data.get('xray_note', '')
    )
    db.session.add(sub)
    db.session.commit()
    flash('サブカルテを追加しました', 'success')
    return redirect(url_for('patient_detail', patient_id=record.patient_id))


@app.route('/admin/records/<int:record_id>/sub/<int:sub_id>/edit', methods=['GET', 'POST'])
def sub_record_edit(record_id, sub_id):
    record = MedicalRecord.query.get_or_404(record_id)
    sub = SubRecord.query.get_or_404(sub_id)
    if request.method == 'POST':
        data = request.form
        sub.tooth_number = data.get('tooth_number', '')
        sub.soap_s = data.get('soap_s', '')
        sub.soap_o = data.get('soap_o', '')
        sub.soap_a = data.get('soap_a', '')
        sub.soap_p = data.get('soap_p', '')
        sub.xray_taken = bool(data.get('xray_taken'))
        sub.xray_note = data.get('xray_note', '')
        db.session.commit()
        flash('サブカルテを更新しました', 'success')
        return redirect(url_for('patient_detail', patient_id=record.patient_id))
    return render_template('admin/sub_record_form.html', record=record, sub=sub)


@app.route('/admin/records/<int:record_id>/sub/<int:sub_id>/delete', methods=['POST'])
def sub_record_delete(record_id, sub_id):
    record = MedicalRecord.query.get_or_404(record_id)
    sub = SubRecord.query.get_or_404(sub_id)
    db.session.delete(sub)
    db.session.commit()
    flash('サブカルテを削除しました', 'success')
    return redirect(url_for('patient_detail', patient_id=record.patient_id))


# ==================== Appointments ====================

@app.route('/admin/appointments')
def appointments():
    target_date = request.args.get('date', date.today().isoformat())
    try:
        target = datetime.strptime(target_date, '%Y-%m-%d').date()
    except ValueError:
        target = date.today()
    appts = Appointment.query.filter(
        db.func.date(Appointment.appointment_date) == target
    ).order_by(Appointment.appointment_date).all()
    patients = Patient.query.all()
    staff_list = Staff.query.filter_by(is_active=True).all()
    return render_template('admin/appointments.html',
                           appointments=appts, patients=patients,
                           staff_list=staff_list, target_date=target)


@app.route('/admin/appointments/new', methods=['POST'])
def appointment_new():
    data = request.form
    appt = Appointment(
        patient_id=data['patient_id'],
        staff_id=data.get('staff_id') or None,
        appointment_date=datetime.strptime(data['appointment_date'], '%Y-%m-%dT%H:%M'),
        duration_minutes=int(data.get('duration_minutes', 30)),
        treatment_type=data.get('treatment_type', ''),
        status='予約済',
        notes=data.get('notes', '')
    )
    db.session.add(appt)
    db.session.commit()
    flash('予約を追加しました', 'success')
    return redirect(url_for('appointments', date=appt.appointment_date.date().isoformat()))


@app.route('/admin/appointments/<int:appt_id>/status', methods=['POST'])
def appointment_status(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    appt.status = request.form.get('status', appt.status)
    db.session.commit()
    return jsonify({'success': True})


# ==================== Billing ====================

@app.route('/admin/billing')
def billing():
    status_filter = request.args.get('status', '')
    query = Invoice.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    invoices = query.order_by(Invoice.issue_date.desc()).all()
    total_paid = db.session.query(db.func.sum(Invoice.total_amount)).filter_by(status='支払済').scalar() or 0
    total_unpaid = db.session.query(db.func.sum(Invoice.total_amount)).filter_by(status='未払い').scalar() or 0
    return render_template('admin/billing.html',
                           invoices=invoices, total_paid=total_paid,
                           total_unpaid=total_unpaid, status_filter=status_filter)


@app.route('/admin/billing/new', methods=['GET', 'POST'])
def invoice_new():
    patients = Patient.query.all()
    if request.method == 'POST':
        data = request.form
        items_desc = request.form.getlist('item_desc')
        items_qty = request.form.getlist('item_qty')
        items_price = request.form.getlist('item_price')

        subtotal = 0
        invoice_items = []
        for desc, qty, price in zip(items_desc, items_qty, items_price):
            if desc:
                amount = int(qty) * int(price)
                subtotal += amount
                invoice_items.append(InvoiceItem(description=desc, quantity=int(qty), unit_price=int(price), amount=amount))

        insurance = int(data.get('insurance_covered', 0))
        patient_portion = subtotal - insurance
        tax = int(patient_portion * 0.1)
        total = patient_portion + tax

        invoice = Invoice(
            invoice_number=generate_invoice_number(),
            patient_id=data['patient_id'],
            issue_date=datetime.strptime(data['issue_date'], '%Y-%m-%d').date(),
            due_date=datetime.strptime(data['due_date'], '%Y-%m-%d').date() if data.get('due_date') else None,
            subtotal=subtotal,
            insurance_covered=insurance,
            patient_portion=patient_portion,
            tax_amount=tax,
            total_amount=total,
            status='未払い',
            notes=data.get('notes', '')
        )
        db.session.add(invoice)
        db.session.flush()
        for item in invoice_items:
            item.invoice_id = invoice.id
            db.session.add(item)
        db.session.commit()
        flash('請求書を作成しました', 'success')
        return redirect(url_for('invoice_detail', invoice_id=invoice.id))
    return render_template('admin/invoice_form.html', patients=patients, invoice=None, today=date.today())


@app.route('/admin/billing/<int:invoice_id>')
def invoice_detail(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    return render_template('admin/invoice_detail.html', invoice=invoice)


@app.route('/admin/billing/<int:invoice_id>/pay', methods=['POST'])
def invoice_pay(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    invoice.status = '支払済'
    db.session.commit()
    flash('支払い処理が完了しました', 'success')
    return redirect(url_for('invoice_detail', invoice_id=invoice_id))


# ==================== Staff & Shifts ====================

@app.route('/admin/staff')
def staff_list():
    staff = Staff.query.filter_by(is_active=True).order_by(Staff.role).all()
    return render_template('admin/staff.html', staff=staff)


@app.route('/admin/staff/new', methods=['GET', 'POST'])
def staff_new():
    if request.method == 'POST':
        data = request.form
        member = Staff(
            name=data['name'],
            name_kana=data.get('name_kana', ''),
            role=data.get('role', ''),
            email=data.get('email', ''),
            phone=data.get('phone', ''),
            hire_date=datetime.strptime(data['hire_date'], '%Y-%m-%d').date() if data.get('hire_date') else None,
        )
        db.session.add(member)
        db.session.commit()
        flash('スタッフを登録しました', 'success')
        return redirect(url_for('staff_list'))
    return render_template('admin/staff_form.html', member=None)


@app.route('/admin/staff/<int:staff_id>/edit', methods=['GET', 'POST'])
def staff_edit(staff_id):
    member = Staff.query.get_or_404(staff_id)
    if request.method == 'POST':
        data = request.form
        member.name = data['name']
        member.name_kana = data.get('name_kana', '')
        member.role = data.get('role', '')
        member.email = data.get('email', '')
        member.phone = data.get('phone', '')
        member.hire_date = datetime.strptime(data['hire_date'], '%Y-%m-%d').date() if data.get('hire_date') else None
        db.session.commit()
        flash('スタッフ情報を更新しました', 'success')
        return redirect(url_for('staff_list'))
    return render_template('admin/staff_form.html', member=member)


@app.route('/admin/shifts')
def shifts():
    year = int(request.args.get('year', date.today().year))
    month = int(request.args.get('month', date.today().month))
    staff_all = Staff.query.filter_by(is_active=True).all()
    shift_data = Shift.query.filter(
        db.extract('year', Shift.shift_date) == year,
        db.extract('month', Shift.shift_date) == month
    ).all()

    # Build shift map: {staff_id: {day: shift}}
    shift_map = {}
    for s in staff_all:
        shift_map[s.id] = {}
    for sh in shift_data:
        shift_map[sh.staff_id][sh.shift_date.day] = sh

    import calendar
    cal = calendar.monthcalendar(year, month)
    days_in_month = calendar.monthrange(year, month)[1]

    return render_template('admin/shifts.html',
                           staff_all=staff_all, shift_map=shift_map,
                           year=year, month=month, cal=cal,
                           days_in_month=days_in_month)


@app.route('/admin/shifts/save', methods=['POST'])
def shift_save():
    data = request.get_json()
    staff_id = data['staff_id']
    shift_date = datetime.strptime(data['shift_date'], '%Y-%m-%d').date()
    shift_type = data.get('shift_type', '通常')
    start_time = data.get('start_time', '09:00')
    end_time = data.get('end_time', '18:00')

    existing = Shift.query.filter_by(staff_id=staff_id, shift_date=shift_date).first()
    if existing:
        existing.shift_type = shift_type
        existing.start_time = start_time
        existing.end_time = end_time
    else:
        shift = Shift(staff_id=staff_id, shift_date=shift_date,
                      shift_type=shift_type, start_time=start_time, end_time=end_time)
        db.session.add(shift)
    db.session.commit()
    return jsonify({'success': True})


# ==================== Products (EC) ====================

@app.route('/admin/products')
def products_admin():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('admin/products.html', products=products)


@app.route('/admin/products/new', methods=['GET', 'POST'])
def product_new():
    if request.method == 'POST':
        data = request.form
        product = Product(
            name=data['name'],
            description=data.get('description', ''),
            price=int(data['price']),
            stock=int(data.get('stock', 0)),
            category=data.get('category', ''),
            image_url=data.get('image_url', ''),
            is_active=bool(data.get('is_active'))
        )
        db.session.add(product)
        db.session.commit()
        flash('商品を登録しました', 'success')
        return redirect(url_for('products_admin'))
    return render_template('admin/product_form.html', product=None)


@app.route('/admin/products/<int:product_id>/edit', methods=['GET', 'POST'])
def product_edit(product_id):
    product = Product.query.get_or_404(product_id)
    if request.method == 'POST':
        data = request.form
        product.name = data['name']
        product.description = data.get('description', '')
        product.price = int(data['price'])
        product.stock = int(data.get('stock', 0))
        product.category = data.get('category', '')
        product.image_url = data.get('image_url', '')
        product.is_active = bool(data.get('is_active'))
        db.session.commit()
        flash('商品情報を更新しました', 'success')
        return redirect(url_for('products_admin'))
    return render_template('admin/product_form.html', product=product)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)
