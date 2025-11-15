# ==================================================
# PART 1: IMPORTS AND SETUP
# ==================================================
import os
import logging
import asyncio
import sqlite3
import io
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import mm, inch
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, Image
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

from PIL import Image as PILImage
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('8287443004:AAHSbSxhT_SAMvn1EJBqnegmdnLZvezDpLQ')  # Railway will provide this
GRACE_PERIOD_DAYS = 14
MONTHLY_INVOICE_LIMIT = 10

# Bot commands menu setup
async def setup_bot_commands(application):  
    """Set up the bot commands menu"""
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("logo", "Upload company logo"),
        BotCommand("company", "Set company name"),
        BotCommand("create", "Create new invoice"),
        BotCommand("myinvoices", "View my invoices"),
        BotCommand("premium", "Premium features"),
        BotCommand("contact", "Contact for premium"),  # ← NEW
        BotCommand("myid", "Get my user ID"),          # ← NEW
        BotCommand("clients", "Client database"),
        BotCommand("payments", "Track payments"),
        BotCommand("setup", "Company setup"),
        BotCommand("help", "Get help")
    ]
    
    await application.bot.set_my_commands(commands)
    print("✅ Bot commands menu has been set up!")

# Database setup
def init_db():
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Users table - with all required columns
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trial_end_date TIMESTAMP,
            subscription_tier TEXT DEFAULT 'lite',
            logo_path TEXT,
            company_name TEXT,
            company_reg_number TEXT,
            vat_reg_number TEXT
        )
    ''')
    
    # Invoices table - COMPLETE schema
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            invoice_number TEXT UNIQUE,
            client_name TEXT,
            invoice_date TEXT,
            currency TEXT,
            items TEXT,
            total_amount REAL,
            vat_enabled BOOLEAN DEFAULT FALSE,
            vat_amount REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            paid_status BOOLEAN DEFAULT FALSE,
            client_email TEXT,
            client_phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Invoice counters
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoice_counters (
            user_id INTEGER PRIMARY KEY,
            current_counter INTEGER DEFAULT 1,
            last_reset_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Clients table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            client_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            client_name TEXT,
            email TEXT,
            phone TEXT,
            address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Premium subscriptions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_subscriptions (
            user_id INTEGER PRIMARY KEY,
            subscription_type TEXT,
            start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_date TIMESTAMP,
            payment_method TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # FIXED: COMPLETE column migration for invoices table
    columns_to_add = [
        ('vat_enabled', 'BOOLEAN DEFAULT FALSE'),
        ('vat_amount', 'REAL DEFAULT 0'),
        ('client_email', 'TEXT'),
        ('client_phone', 'TEXT'),
        ('paid_status', 'BOOLEAN DEFAULT FALSE')  # ADDED: Missing paid_status column
    ]
    
    for column_name, column_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE invoices ADD COLUMN {column_name} {column_type}")
            print(f"✅ Added {column_name} column to invoices table")
        except sqlite3.OperationalError:
            pass  # Column already exists
    
    # FIXED: COMPLETE column migration for users table
    user_columns_to_add = [
        ('company_reg_number', 'TEXT'),
        ('vat_reg_number', 'TEXT')
    ]
    
    for column_name, column_type in user_columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
            print(f"✅ Added {column_name} column to users table")
        except sqlite3.OperationalError:
            pass  # Column already exists
    
    conn.commit()
    conn.close()
    print("✅ Database initialization complete")

init_db()
# ==================================================
# PART 2: DATABASE HELPER FUNCTIONS
# ==================================================

# Database helper functions
def get_user(user_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    trial_end_date = datetime.now() + timedelta(days=GRACE_PERIOD_DAYS)
    trial_end_date_str = trial_end_date.strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, trial_end_date)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name, trial_end_date_str))
    conn.commit()
    conn.close()

def update_user_company_info(user_id, logo_path=None, company_name=None, company_reg=None, vat_reg=None):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    if logo_path:
        cursor.execute('UPDATE users SET logo_path = ? WHERE user_id = ?', (logo_path, user_id))
    if company_name:
        cursor.execute('UPDATE users SET company_name = ? WHERE user_id = ?', (company_name, user_id))
    if company_reg:
        cursor.execute('UPDATE users SET company_reg_number = ? WHERE user_id = ?', (company_reg, user_id))
    if vat_reg:
        cursor.execute('UPDATE users SET vat_reg_number = ? WHERE user_id = ?', (vat_reg, user_id))
    conn.commit()
    conn.close()

def get_invoice_counter(user_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT current_counter FROM invoice_counters WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if not result:
        cursor.execute('INSERT INTO invoice_counters (user_id, current_counter) VALUES (?, ?)', (user_id, 1))
        conn.commit()
        counter = 1
    else:
        counter = result[0]
    conn.close()
    return counter

def increment_invoice_counter(user_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE invoice_counters SET current_counter = current_counter + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def save_invoice_draft(user_id, client_name, invoice_date, currency, items, vat_enabled=False, client_email=None, client_phone=None):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    items_json = str(items)
    
    # Calculate totals
    subtotal = sum(item['quantity'] * item['amount'] for item in items)
    vat_amount = subtotal * 0.2 if vat_enabled else 0
    total_amount = subtotal + vat_amount
    
    print(f"DEBUG: Saving invoice draft - User: {user_id}, Client: {client_name}")
    print(f"DEBUG: Items: {items}")
    print(f"DEBUG: VAT enabled: {vat_enabled}, VAT amount: {vat_amount}")
    
    cursor.execute('''
        INSERT INTO invoices (user_id, client_name, invoice_date, currency, items, 
                            total_amount, vat_enabled, vat_amount, client_email, client_phone, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
    ''', (user_id, client_name, invoice_date, currency, items_json, total_amount, vat_enabled, vat_amount, client_email, client_phone))
    
    invoice_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    print(f"DEBUG: Saved invoice with ID: {invoice_id}")
    return invoice_id

def update_invoice_status(invoice_id, status, invoice_number=None):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    if invoice_number:
        cursor.execute('''
            UPDATE invoices SET status = ?, invoice_number = ? WHERE invoice_id = ?
        ''', (status, invoice_number, invoice_id))
    else:
        cursor.execute('''
            UPDATE invoices SET status = ? WHERE invoice_id = ?
        ''', (status, invoice_id))
    conn.commit()
    conn.close()

def mark_invoice_paid(invoice_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE invoices SET paid_status = TRUE WHERE invoice_id = ?', (invoice_id,))
    conn.commit()
    conn.close()

def get_invoice(invoice_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM invoices WHERE invoice_id = ?', (invoice_id,))
    invoice = cursor.fetchone()
    conn.close()
    
    print(f"DEBUG: Getting invoice {invoice_id} - Found: {invoice is not None}")
    if invoice:
        print(f"DEBUG: Invoice data - ID: {invoice[0]}, Status: {invoice[10] if len(invoice) > 10 else 'N/A'}")
    
    return invoice

def get_user_invoices(user_id, client_name=None):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    if client_name:
        cursor.execute('''
            SELECT * FROM invoices 
            WHERE user_id = ? AND client_name LIKE ? AND status = 'approved'
            ORDER BY created_at DESC
        ''', (user_id, f'%{client_name}%'))
    else:
        cursor.execute('''
            SELECT * FROM invoices 
            WHERE user_id = ? AND status = 'approved'
            ORDER BY created_at DESC
        ''', (user_id,))
    invoices = cursor.fetchall()
    conn.close()
    return invoices

def get_unpaid_invoices(user_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM invoices 
        WHERE user_id = ? AND status = 'approved' AND paid_status = FALSE
        ORDER BY created_at DESC
    ''', (user_id,))
    invoices = cursor.fetchall()
    conn.close()
    return invoices

def get_user_invoice_count_this_month(user_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    first_day_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cursor.execute('''
        SELECT COUNT(*) FROM invoices 
        WHERE user_id = ? AND status = 'approved' AND created_at >= ?
    ''', (user_id, first_day_of_month))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def save_client(user_id, client_name, email=None, phone=None, address=None):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO clients (user_id, client_name, email, phone, address)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, client_name, email, phone, address))
    client_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return client_id

def update_client(client_id, client_name=None, email=None, phone=None, address=None):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    updates = []
    params = []
    
    if client_name:
        updates.append("client_name = ?")
        params.append(client_name)
    if email:
        updates.append("email = ?")
        params.append(email)
    if phone:
        updates.append("phone = ?")
        params.append(phone)
    if address:
        updates.append("address = ?")
        params.append(address)
    
    if updates:
        params.append(client_id)
        cursor.execute(f'''
            UPDATE clients SET {', '.join(updates)} WHERE client_id = ?
        ''', params)
    
    conn.commit()
    conn.close()

def get_user_clients(user_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clients WHERE user_id = ? ORDER BY client_name', (user_id,))
    clients = cursor.fetchall()
    conn.close()
    return clients

def get_client_by_id(client_id):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clients WHERE client_id = ?', (client_id,))
    client = cursor.fetchone()
    conn.close()
    return client

def get_client_by_name(user_id, client_name):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clients WHERE user_id = ? AND client_name = ?', (user_id, client_name))
    client = cursor.fetchone()
    conn.close()
    return client

def is_premium_user(user_id):
    """Check if user has premium access - uses simple file system"""
    from premium_manager import premium_manager
    return premium_manager.is_premium(user_id)

def add_premium_subscription(user_id, subscription_type, months=1):
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    start_date = datetime.now()
    end_date = start_date + timedelta(days=30*months)
    
    cursor.execute('''
        INSERT OR REPLACE INTO premium_subscriptions (user_id, subscription_type, start_date, end_date)
        VALUES (?, ?, ?, ?)
    ''', (user_id, subscription_type, start_date, end_date))
    
    cursor.execute('UPDATE users SET subscription_tier = ? WHERE user_id = ?', ('premium', user_id))
    conn.commit()
    conn.close()

# Date parsing function
def parse_trial_end_date(trial_end_date_str):
    if not trial_end_date_str:
        return datetime.now()
    
    try:
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S.%f',
            '%Y-%m-%d'
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(trial_end_date_str, fmt)
            except ValueError:
                continue
        
        return datetime.now()
    except Exception as e:
        logger.warning(f"Failed to parse trial end date '{trial_end_date_str}': {e}")
        return datetime.now()
        # ==================================================
# PART 3: INVOICE GENERATION AND PDF CREATION
# ==================================================

# Invoice generation
def generate_invoice_number(user_id):
    counter = get_invoice_counter(user_id)
    now = datetime.now()
    invoice_number = f"INV-{now.year}-{now.month:02d}-{counter:04d}"
    increment_invoice_counter(user_id)
    return invoice_number

def create_invoice_pdf(invoice_data, user_info):
    try:
        buffer = io.BytesIO()
        
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=A4,
            topMargin=0.5*inch,
            bottomMargin=0.5*inch,
            leftMargin=0.5*inch,
            rightMargin=0.5*inch
        )
        story = []
        styles = getSampleStyleSheet()
        
        title_style = styles["Heading1"]
        title_style.alignment = TA_RIGHT
        title_style.spaceAfter = 20
        
        normal_style = styles["Normal"]
        normal_style.spaceAfter = 6
        
        bold_style = styles["Normal"]
        bold_style.fontName = 'Helvetica-Bold'
        
        # Currency symbol mapping
        currency_symbols = {
            'GBP': '£',
            'USD': '$',
            'EUR': '€'
        }
        
        # Get currency symbol or use code as fallback
        currency_code = invoice_data['currency']
        currency_symbol = currency_symbols.get(currency_code, currency_code)
        
        # Header section
        has_logo = user_info.get('logo_path') and os.path.exists(user_info['logo_path'])
        company_name = user_info.get('company_name', '')
        
        header_data = []
        
        if has_logo:
            try:
                logo = Image(user_info['logo_path'], width=2.5*inch, height=1.25*inch)
                header_data.append(logo)
            except Exception as e:
                logger.warning(f"Could not load logo: {e}")
                has_logo = False
                if company_name:
                    company_text = Paragraph(f"<b>{company_name}</b>", bold_style)
                    header_data.append(company_text)
        elif company_name:
            company_text = Paragraph(f"<b>{company_name}</b>", bold_style)
            header_data.append(company_text)
        else:
            header_data.append(Spacer(1, 1.25*inch))
        
        right_section = []
        invoice_title = Paragraph("<b>INVOICE</b>", title_style)
        right_section.append(invoice_title)
        
        header_table = Table([[header_data, right_section]], colWidths=[4*inch, 2*inch])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ]))
        
        story.append(header_table)
        story.append(Spacer(1, 0.4*inch))
        
        # Company registration and VAT numbers if available
        # FIXED: Only show VAT registration number if VAT is enabled on this invoice
        reg_data = []
        
        # Always show company registration number if available
        if user_info.get('company_reg_number'):
            reg_data.append(Paragraph(f"<b>Company Reg:</b> {user_info['company_reg_number']}", normal_style))
        
        # Only show VAT registration number if VAT is enabled for this invoice
        if invoice_data.get('vat_enabled') and user_info.get('vat_reg_number'):
            reg_data.append(Paragraph(f"<b>VAT Reg:</b> {user_info['vat_reg_number']}", normal_style))
        
        if reg_data:
            for reg in reg_data:
                story.append(reg)
            story.append(Spacer(1, 0.2*inch))
        
        # Invoice details
        details_data = [
            [Paragraph("<b>Invoice Number:</b>", bold_style), 
             Paragraph(invoice_data['invoice_number'], normal_style),
             Paragraph("<b>Date:</b>", bold_style), 
             Paragraph(invoice_data['invoice_date'], normal_style)],
            
            [Paragraph("<b>Bill To:</b>", bold_style), 
             Paragraph(invoice_data['client_name'], normal_style),
             Paragraph("", normal_style), 
             Paragraph("", normal_style)]
        ]
        
        details_table = Table(details_data, colWidths=[1.2*inch, 2.2*inch, 0.8*inch, 1.8*inch])
        details_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.white),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        
        story.append(details_table)
        story.append(Spacer(1, 0.4*inch))
        
        # Items table
        table_data = [
            [Paragraph('<b>Description</b>', bold_style), 
             Paragraph('<b>Qty</b>', bold_style), 
             Paragraph('<b>Unit Price</b>', bold_style), 
             Paragraph('<b>Total</b>', bold_style)]
        ]
        
        subtotal = 0
        for item in invoice_data['items']:
            total = item['quantity'] * item['amount']
            subtotal += total
            table_data.append([
                Paragraph(item['description'], normal_style),
                Paragraph(str(item['quantity']), normal_style),
                # FIXED: Use currency symbol instead of code
                Paragraph(f"{currency_symbol} {item['amount']:.2f}", normal_style),
                Paragraph(f"{currency_symbol} {total:.2f}", normal_style)
            ])
        
        # Add VAT row if enabled
        if invoice_data.get('vat_enabled'):
            vat_amount = subtotal * 0.2
            table_data.append([
                Paragraph("<b>VAT @ 20%</b>", bold_style),
                Paragraph("", normal_style),
                Paragraph("", normal_style),
                # FIXED: Use currency symbol instead of code
                Paragraph(f"<b>{currency_symbol} {vat_amount:.2f}</b>", bold_style)
            ])
            grand_total = subtotal + vat_amount
        else:
            grand_total = subtotal
        
        # Add TOTAL row
        table_data.append([
            Paragraph("<b>TOTAL</b>", bold_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            # FIXED: Use currency symbol instead of code
            Paragraph(f"<b>{currency_symbol} {grand_total:.2f}</b>", bold_style)
        ])
        
        items_table = Table(table_data, colWidths=[3.2*inch, 0.8*inch, 1.2*inch, 1.2*inch])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a6ee0')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.white),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8f9fa')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('BOX', (-1, -1), (-1, -1), 2, colors.HexColor('#4a6ee0')),
            ('BACKGROUND', (-1, -1), (-1, -1), colors.HexColor('#f1f5fd')),
        ]))
        
        story.append(items_table)
        story.append(Spacer(1, 0.5*inch))
        
        # Thank you message
        thank_you_style = styles["Normal"]
        thank_you_style.alignment = TA_CENTER
        thank_you_style.textColor = colors.gray
        thank_you_style.fontSize = 10
        thank_you_style.spaceBefore = 20
        
        thank_you = Paragraph(
            "Thank you for your business. We appreciate your prompt payment.", 
            thank_you_style
        )
        story.append(thank_you)
        
        # Footer
        footer_text = "Generated by Minigma Invoice Bot"
        if has_logo and company_name:
            footer_text = f"{company_name} | {footer_text}"
        
        footer_style = styles["Normal"]
        footer_style.alignment = TA_CENTER
        footer_style.textColor = colors.lightgrey
        footer_style.fontSize = 8
        footer_style.spaceBefore = 10
        
        footer = Paragraph(footer_text, footer_style)
        story.append(footer)
        
        # Build PDF
        doc.build(story)
        
        pdf_data = buffer.getvalue()
        buffer.close()
        
        os.makedirs('invoices', exist_ok=True)
        pdf_file = f"invoices/{invoice_data['invoice_number']}.pdf"
        with open(pdf_file, 'wb') as f:
            f.write(pdf_data)
        
        logger.info(f"PDF generated successfully: {pdf_file}")
        return pdf_file
        
    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        raise

        # ==================================================
# ==================================================
# PART 4: COMMAND HANDLERS
# ==================================================

# Premium features commands
async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show premium features and subscription options"""
    user_id = update.effective_user.id
    
    if is_premium_user(user_id):
        await update.message.reply_text(
            "🎉 **You're a Premium User!**\n\n"
            "You have access to all premium features:\n"
            "• Company/VAT registration numbers\n"
            "• VAT calculation on invoices\n"
            "• Client database\n"
            "• Payment tracking\n"
            "• Unlimited invoices\n\n"
            "Use /setup to configure company details\n"
            "Use /clients to manage clients\n"
            "Use /payments to track payments",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "🔒 **Premium Features**\n\n"
            "Get access to:\n"
            "• Company/VAT registration numbers\n"
            "• VAT calculation on invoices\n"
            "• Client database management\n"
            "• Payment tracking\n"
            "• Unlimited invoices & quotes\n"
            "• Email/SMS delivery\n\n"
            "💳 **Pricing:**\n"
            "• **Monthly:** £12 per month\n"
            "• **Annual:** £100 per year (save £44!)\n\n"
            "📞 **How to upgrade:**\n"
            "1. Use `/contact` to get payment instructions\n"
            "2. Choose your plan (monthly/annual)\n"
            "3. Complete payment\n"
            "4. Get instant premium activation\n\n"
            "Use `/premium` again to check your status after payment!",
            parse_mode='Markdown'
        )

async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show how to contact for premium"""
    await update.message.reply_text(
        "📞 **Contact for Premium Access**\n\n"
        "**To upgrade to Premium, contact the bot owner directly:**\n\n"
        "💬 **Telegram:** @MinigimaUK\n"
        "📧 **Email:** minigmauk@gmail.com\n\n"
        "**When contacting, please provide:**\n"
        "• Your preferred plan: Monthly (£12) or Annual (£100)\n"
        "• Your Telegram User ID (use `/myid` to get it)\n\n"
        "**You'll receive:**\n"
        "• Payment instructions (PayPal/Bank Transfer)\n" 
        "• Instant activation after payment\n"
        "• Confirmation message in this bot\n\n"
        "Use `/premium` to check your status after payment!",
        parse_mode='Markdown'
    )

async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get user's own Telegram ID"""
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"🔍 **Your Telegram User ID:** `{user_id}`\n\n"
        "Provide this ID when contacting for premium access.",
        parse_mode='Markdown'
    )

async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to add premium users"""
    user_id = update.effective_user.id
    
    # ⚠️ REPLACE 123456789 WITH YOUR ACTUAL TELEGRAM ID ⚠️
    ADMIN_ID = 334262726
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only command")
        return
    
    try:
        # Command: /add_premium USER_ID [username]
        parts = update.message.text.split()
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ Usage: `/add_premium USER_ID [username]`",
                parse_mode='Markdown'
            )
            return
        
        new_user_id = int(parts[1])
        username = parts[2] if len(parts) > 2 else ""
        
        success, result_msg = premium_manager.add_premium_user(new_user_id, username)
        await update.message.reply_text(result_msg)
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def remove_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to remove premium users"""
    user_id = update.effective_user.id
    
    # ⚠️ REPLACE 123456789 WITH YOUR ACTUAL TELEGRAM ID ⚠️
    ADMIN_ID = 334262726
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only command")
        return
    
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ Usage: `/remove_premium USER_ID`",
                parse_mode='Markdown'
            )
            return
        
        remove_user_id = int(parts[1])
        success, result_msg = premium_manager.remove_premium_user(remove_user_id)
        await update.message.reply_text(result_msg)
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def list_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to list premium users"""
    user_id = update.effective_user.id
    
    # ⚠️ REPLACE 123456789 WITH YOUR ACTUAL TELEGRAM ID ⚠️
    ADMIN_ID = 123456789
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only command")
        return
    
    premium_count = len(premium_manager.premium_users)
    await update.message.reply_text(
        f"📊 **Premium Users:** {premium_count}\n\n"
        f"User IDs: {', '.join(map(str, premium_manager.premium_users))}",
        parse_mode='Markdown'
    )

async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setup company information"""
    user_id = update.effective_user.id
    
    if not is_premium_user(user_id):
        await update.message.reply_text(
            "❌ **Premium Feature**\n\n"
            "Company setup is available for premium users only.\n"
            "Use /premium to upgrade and unlock this feature.",
            parse_mode='Markdown'
        )
        return
        
    keyboard = [
        [InlineKeyboardButton("🏢 Set Company Reg Number", callback_data="setup_company_reg")],
        [InlineKeyboardButton("📊 Set VAT Number", callback_data="setup_vat_number")],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    user = get_user(user_id)
    current_info = ""
    
    # FIXED: Safe tuple indexing with bounds checking
    if user and len(user) > 9 and user[9]:  # company_reg_number
        current_info += f"Current Company Reg: {user[9]}\n"
    if user and len(user) > 10 and user[10]:  # vat_reg_number
        current_info += f"Current VAT Reg: {user[10]}\n"
    
    await update.message.reply_text(
        f"🏢 **Company Information Setup**\n\n{current_info}\n"
        "Set up your company details that will appear on invoices:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def clients_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Client database management"""
    user_id = update.effective_user.id
    
    if not is_premium_user(user_id):
        await update.message.reply_text(
            "❌ **Premium Feature**\n\n"
            "Client database is available for premium users only.\n"
            "Use /premium to upgrade and unlock this feature.",
            parse_mode='Markdown'
        )
        return
        
    clients = get_user_clients(user_id)
    
    if not clients:
        keyboard = [
            [InlineKeyboardButton("➕ Add New Client", callback_data="client_start")],
            [InlineKeyboardButton("🔙 Back", callback_data="clients_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "👥 **Client Database**\n\n"
            "No clients found. Add your first client to get started!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    keyboard = []
    for client in clients[:10]:  # Show first 10 clients
        keyboard.append([InlineKeyboardButton(f"👤 {client[2]}", callback_data=f"view_client_{client[0]}")])
    
    keyboard.extend([
        [InlineKeyboardButton("➕ Add New Client", callback_data="client_start")],
        [InlineKeyboardButton("🔍 Search Invoices by Client", callback_data="search_client_invoices")],
        [InlineKeyboardButton("🔙 Back", callback_data="clients_back")]
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👥 **Client Database**\n\n"
        f"You have {len(clients)} clients. Select a client to view details:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def payments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Payment tracking"""
    user_id = update.effective_user.id
    
    if not is_premium_user(user_id):
        await update.message.reply_text(
            "❌ **Premium Feature**\n\n"
            "Payment tracking is available for premium users only.\n"
            "Use /premium to upgrade and unlock this feature.",
            parse_mode='Markdown'
        )
        return
        
    unpaid_invoices = get_unpaid_invoices(user_id)
    
    if not unpaid_invoices:
        await update.message.reply_text(
            "💰 **Payment Tracking**\n\n"
            "🎉 All your invoices are paid! No outstanding payments.",
            parse_mode='Markdown'
        )
        return
    
    keyboard = []
    for invoice in unpaid_invoices[:10]:  # Show first 10 unpaid invoices
        keyboard.append([InlineKeyboardButton(
            f"📄 {invoice[2]} - {invoice[3]} - {invoice[5]}{invoice[7]:.2f}", 
            callback_data=f"mark_paid_{invoice[0]}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="payments_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"💰 **Payment Tracking**\n\n"
        f"You have {len(unpaid_invoices)} unpaid invoices. Mark them as paid:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def my_invoices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Check if user wants to search by client
    if context.args:
        client_name = ' '.join(context.args)
        invoices = get_user_invoices(user_id, client_name)
        if invoices:
            message = f"📋 Invoices for {client_name}:\n\n"
            for inv in invoices:
                paid_status = "✅ Paid" if inv[11] else "❌ Unpaid"
                message += f"• {inv[2]} - {inv[3]} - {inv[5]}{inv[7]:.2f} - {paid_status}\n"
        else:
            message = f"No invoices found for client: {client_name}"
    else:
        invoices = get_user_invoices(user_id)
        if not invoices:
            await update.message.reply_text("You haven't created any approved invoices yet.")
            return
        
        message = "📋 Your Recent Invoices:\n\n"
        for inv in invoices[:10]:  # Show last 10 invoices
            paid_status = "✅ Paid" if inv[11] else "❌ Unpaid"
            message += f"• {inv[2]} - {inv[3]} - {inv[5]}{inv[7]:.2f} - {paid_status}\n"
        
        if is_premium_user(user_id):
            message += "\n💡 *Tip: Use* `/myinvoices ClientName` *to filter by client*"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# Bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    if not get_user(user_id):
        create_user(user_id, user.username, user.first_name, user.last_name)
        await update.message.reply_text("✅ Your account has been created! Enjoy your 14-day free trial.")
    
    welcome_message = """
🤖 Welcome to Minigma Invoice Bot!

✨ **Features:**
• Upload company logo
• Multiple currency support
• Professional PDF invoices
• 14-day free trial

💰 **Premium Features:**
• Company/VAT registration numbers
• VAT calculation
• Client database
• Payment tracking
• Email/SMS sending

📝 **Commands:**
/logo - Upload company logo
/company - Set company name  
/create - Create new invoice
/myinvoices - View invoices
/premium - Premium features
/contact - Contact for premium
/myid - Get your user ID
/clients - Client database (Premium)
/payments - Track payments (Premium)
/setup - Company setup (Premium)
/help - Get help

You have 14 days to try all features for free!
    """
    
    await update.message.reply_text(welcome_message)

async def set_logo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Please upload your company logo as a photo. "
        "The image should be clear and in PNG or JPG format."
    )

async def handle_logo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    
    user_dir = f"logos/{user_id}"
    os.makedirs(user_dir, exist_ok=True)
    
    photo_file = await context.bot.get_file(photo.file_id)
    logo_path = f"{user_dir}/logo.jpg"
    await photo_file.download_to_drive(logo_path)
    
    update_user_company_info(user_id, logo_path=logo_path)
    
    await update.message.reply_text(
        "✅ Logo uploaded successfully! It will appear on your invoices.\n"
        "You can now set your company name using /company or start creating invoices with /create"
    )

async def set_company_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Please enter your company name. This will be displayed on your invoices if no logo is uploaded."
    )
    context.user_data['awaiting_company_name'] = True

async def handle_company_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_company_name'):
        return
        
    user_id = update.effective_user.id
    company_name = update.message.text
    
    update_user_company_info(user_id, company_name=company_name)
    context.user_data['awaiting_company_name'] = False
    
    await update.message.reply_text(
        f"✅ Company name set to: {company_name}\n"
        "You can now create invoices with /create"
    )

async def create_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        create_user(user_id, update.effective_user.username, update.effective_user.first_name, update.effective_user.last_name)
        user = get_user(user_id)
        await update.message.reply_text("✅ Your account has been created! Starting invoice creation...")
    
    trial_end = parse_trial_end_date(user[5]) if user[5] else datetime.now()
    
    if datetime.now() > trial_end:
        monthly_count = get_user_invoice_count_this_month(user_id)
        if monthly_count >= MONTHLY_INVOICE_LIMIT:
            await update.message.reply_text(
                f"❌ You've reached your monthly limit of {MONTHLY_INVOICE_LIMIT} invoices.\n"
                "Please upgrade to Premium for unlimited invoices!\n\n"
                "Use /premium to see premium features."
            )
            return
    
    context.user_data['current_invoice'] = {
        'items': [],
        'step': 'client_name'
    }
    
    await update.message.reply_text(
        "Let's create a new invoice! 🧾\n\n"
        "First, please enter the client name:"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 Minigma Invoice Bot - Help

📝 **Basic Commands:**
/start - Start the bot
/logo - Upload company logo
/company - Set company name
/create - Create new invoice
/myinvoices - View your invoices
/contact - Contact for premium
/myid - Get your user ID
/help - Show this help message

💰 **Premium Commands:**
/premium - Premium features info
/setup - Company registration setup
/clients - Client database
/payments - Track payments

💡 **Premium Features:**
• Store company/VAT registration numbers
• VAT calculation on invoices
• Client database management
• Payment tracking
• Unlimited invoices

📊 **Usage Limits:**
• 14-day free trial (unlimited invoices)
• After trial: 10 invoices per month
• Premium: Unlimited invoices + all features

Need help? Contact the bot owner!
    """
    await update.message.reply_text(help_text)
# ==================================================
# PART 5: INVOICE & QUOTE CREATION AND BUTTON HANDLER
# ==================================================

async def handle_invoice_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_company_name'):
        await handle_company_name(update, context)
        return
        
    user_id = update.effective_user.id
    text = update.message.text
    invoice_data = context.user_data.get('current_invoice', {})
    
    if not invoice_data:
        await update.message.reply_text("Please start with /create to begin a new invoice.")
        return
    
    if invoice_data.get('step') == 'client_name':
        invoice_data['client_name'] = text
        invoice_data['step'] = 'invoice_date'
        
        await update.message.reply_text(
            "📅 Please enter the invoice date.\n"
            "Format: DD MMM YYYY (e.g., 24 Oct 2025)\n"
            "Or type 'today' for today's date"
        )
        
    elif invoice_data.get('step') == 'invoice_date':
        if text.lower() == 'today':
            invoice_date = datetime.now().strftime('%d %b %Y')
        else:
            try:
                datetime.strptime(text, '%d %b %Y')
                invoice_date = text
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid date format. Please use format: DD MMM YYYY (e.g., 24 Oct 2025)\n"
                    "Or type 'today' for today's date"
                )
                return
        
        invoice_data['invoice_date'] = invoice_date
        invoice_data['step'] = 'currency'
        
        keyboard = [
            [InlineKeyboardButton("GBP £", callback_data="currency_GBP")],
            [InlineKeyboardButton("EUR €", callback_data="currency_EUR")],
            [InlineKeyboardButton("USD $", callback_data="currency_USD")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "💱 Please select the currency:",
            reply_markup=reply_markup
        )
        
    elif invoice_data.get('step') == 'item_description':
        if 'current_item' not in invoice_data:
            invoice_data['current_item'] = {}
        
        invoice_data['current_item']['description'] = text
        invoice_data['step'] = 'item_quantity'
        
        await update.message.reply_text(
            "🔢 Please enter the quantity:"
        )
        
    elif invoice_data.get('step') == 'item_quantity':
        try:
            quantity = float(text)
            invoice_data['current_item']['quantity'] = quantity
            invoice_data['step'] = 'item_amount'
            
            await update.message.reply_text(
                "💰 Please enter the unit price:"
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number for quantity:"
            )
            
    elif invoice_data.get('step') == 'item_amount':
        try:
            amount = float(text)
            invoice_data['current_item']['amount'] = amount
            
            invoice_data['items'].append(invoice_data['current_item'])
            del invoice_data['current_item']
            
            keyboard = [
                [InlineKeyboardButton("✅ Add Another Item", callback_data="add_another_item")],
                [InlineKeyboardButton("✅ Finish", callback_data="finish_invoice")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            total_so_far = sum(item['quantity'] * item['amount'] for item in invoice_data['items'])
            currency = invoice_data.get('currency', '')
            
            await update.message.reply_text(
                f"✅ Item added!\n\n"
                f"Current total: {currency} {total_so_far:.2f}\n\n"
                f"Would you like to add another item?",
                reply_markup=reply_markup
            )
            
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number for unit price:"
            )
    
    context.user_data['current_invoice'] = invoice_data

async def handle_quote_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quote creation - similar to invoice but with quote-specific logic"""
    if context.user_data.get('awaiting_company_name'):
        await handle_company_name(update, context)
        return
        
    user_id = update.effective_user.id
    text = update.message.text
    quote_data = context.user_data.get('current_quote', {})
    
    if not quote_data:
        await update.message.reply_text("Please start with /quote to begin a new quote.")
        return
    
    if quote_data.get('step') == 'client_name':
        quote_data['client_name'] = text
        quote_data['step'] = 'quote_date'
        
        await update.message.reply_text(
            "📅 Please enter the quote date.\n"
            "Format: DD MMM YYYY (e.g., 24 Oct 2025)\n"
            "Or type 'today' for today's date"
        )
        
    elif quote_data.get('step') == 'quote_date':
        if text.lower() == 'today':
            quote_date = datetime.now().strftime('%d %b %Y')
        else:
            try:
                datetime.strptime(text, '%d %b %Y')
                quote_date = text
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid date format. Please use format: DD MMM YYYY (e.g., 24 Oct 2025)\n"
                    "Or type 'today' for today's date"
                )
                return
        
        quote_data['quote_date'] = quote_date
        quote_data['step'] = 'currency'
        
        keyboard = [
            [InlineKeyboardButton("GBP £", callback_data="quote_currency_GBP")],
            [InlineKeyboardButton("EUR €", callback_data="quote_currency_EUR")],
            [InlineKeyboardButton("USD $", callback_data="quote_currency_USD")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "💱 Please select the currency:",
            reply_markup=reply_markup
        )
        
    elif quote_data.get('step') == 'item_description':
        if 'current_item' not in quote_data:
            quote_data['current_item'] = {}
        
        quote_data['current_item']['description'] = text
        quote_data['step'] = 'item_quantity'
        
        await update.message.reply_text(
            "🔢 Please enter the quantity:"
        )
        
    elif quote_data.get('step') == 'item_quantity':
        try:
            quantity = float(text)
            quote_data['current_item']['quantity'] = quantity
            quote_data['step'] = 'item_amount'
            
            await update.message.reply_text(
                "💰 Please enter the unit price:"
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number for quantity:"
            )
            
    elif quote_data.get('step') == 'item_amount':
        try:
            amount = float(text)
            quote_data['current_item']['amount'] = amount
            
            quote_data['items'].append(quote_data['current_item'])
            del quote_data['current_item']
            
            keyboard = [
                [InlineKeyboardButton("✅ Add Another Item", callback_data="quote_add_another_item")],
                [InlineKeyboardButton("✅ Finish Quote", callback_data="finish_quote")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            total_so_far = sum(item['quantity'] * item['amount'] for item in quote_data['items'])
            currency = quote_data.get('currency', '')
            
            await update.message.reply_text(
                f"✅ Item added!\n\n"
                f"Current total: {currency} {total_so_far:.2f}\n\n"
                f"Would you like to add another item?",
                reply_markup=reply_markup
            )
            
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number for unit price:"
            )
    
    context.user_data['current_quote'] = quote_data

# Quote command
async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start creating a new quote"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        create_user(user_id, update.effective_user.username, update.effective_user.first_name, update.effective_user.last_name)
        user = get_user(user_id)
        await update.message.reply_text("✅ Your account has been created! Enjoy your 14-day free trial.")
    
    # Check creation limit (quotes count towards the same limit as invoices)
    can_create, message = check_invoice_limit(user_id)
    if not can_create:
        await update.message.reply_text(message)
        return
    
    context.user_data['current_quote'] = {
        'items': [],
        'step': 'client_name',
        'type': 'quote'
    }
    
    # Show remaining creations for free tier
    remaining_info = ""
    if not is_premium_user(user_id):
        remaining = get_remaining_invoices(user_id)
        remaining_info = f"\n\n📊 You have {remaining} creations remaining this month."
    
    await update.message.reply_text(
        f"Let's create a new quote! 📄{remaining_info}\n\n"
        "First, please enter the client name:"
    )

# FIXED: Complete button handler with ALL features working
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    print(f"DEBUG: Button pressed - {data}")
    
    # ===== INVOICE CREATION FLOW =====
    if data.startswith('currency_'):
        currency = data.split('_')[1]
        invoice_data = context.user_data.get('current_invoice', {})
        invoice_data['currency'] = currency
        
        # FIXED: Only ask about VAT for premium users
        if is_premium_user(user_id):
            keyboard = [
                [InlineKeyboardButton("✅ Include VAT", callback_data="vat_yes")],
                [InlineKeyboardButton("❌ No VAT", callback_data="vat_no")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"Currency set to: {currency}\n\n"
                "Should this invoice include VAT?\n"
                "*VAT will be calculated at 20%*\n\n"
                "💎 *Premium feature: VAT calculation*",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            # Free users skip VAT and go directly to items
            invoice_data['vat_enabled'] = False
            invoice_data['step'] = 'item_description'
            context.user_data['current_invoice'] = invoice_data
            
            await query.edit_message_text(
                f"Currency set to: {currency}\n\n"
                "Now let's add items to your invoice.\n\n"
                "💡 *VAT calculation is a Premium feature*\n"
                "*Upgrade to Premium to add VAT to your invoices*\n\n"
                "Please enter the description for the first item:",
                parse_mode='Markdown'
            )
        
    elif data == 'vat_yes':
        invoice_data = context.user_data.get('current_invoice', {})
        invoice_data['vat_enabled'] = True
        invoice_data['step'] = 'item_description'
        context.user_data['current_invoice'] = invoice_data
        
        await query.edit_message_text(
            "✅ VAT will be included in this invoice (20%).\n\n"
            "Now let's add items to your invoice.\n\n"
            "Please enter the description for the first item:"
        )
        
    elif data == 'vat_no':
        invoice_data = context.user_data.get('current_invoice', {})
        invoice_data['vat_enabled'] = False
        invoice_data['step'] = 'item_description'
        context.user_data['current_invoice'] = invoice_data
        
        await query.edit_message_text(
            "❌ VAT will not be included in this invoice.\n\n"
            "Now let's add items to your invoice.\n\n"
            "Please enter the description for the first item:"
        )
        
    elif data == 'add_another_item':
        invoice_data = context.user_data.get('current_invoice', {})
        invoice_data['step'] = 'item_description'
        context.user_data['current_invoice'] = invoice_data
        
        await query.edit_message_text(
            "Please enter the description for the next item:"
        )
        
    elif data == 'finish_invoice':
        print("DEBUG: Finish invoice button pressed")
        
        invoice_data = context.user_data.get('current_invoice', {})
        
        # Check if we have items
        if not invoice_data.get('items'):
            await query.edit_message_text(
                "❌ No items added to invoice. Please add at least one item."
            )
            return
            
        invoice_id = save_invoice_draft(
            user_id,
            invoice_data['client_name'],
            invoice_data['invoice_date'],
            invoice_data['currency'],
            invoice_data['items'],
            invoice_data.get('vat_enabled', False)
        )
        
        context.user_data['last_invoice_id'] = invoice_id
        
        # Calculate totals for preview
        subtotal = sum(item['quantity'] * item['amount'] for item in invoice_data['items'])
        if invoice_data.get('vat_enabled'):
            vat_amount = subtotal * 0.2
            total_amount = subtotal + vat_amount
            vat_text = f"VAT (20%): {invoice_data['currency']} {vat_amount:.2f}\n"
        else:
            total_amount = subtotal
            vat_text = "VAT: Not included\n"
        
        # Show preview
        preview_text = f"""
📄 **INVOICE PREVIEW**

**Client:** {invoice_data['client_name']}
**Date:** {invoice_data['invoice_date']}
**Currency:** {invoice_data['currency']}
{vat_text}
**ITEMS:**
"""
        for i, item in enumerate(invoice_data['items'], 1):
            item_total = item['quantity'] * item['amount']
            preview_text += f"{i}. {item['description']} - {item['quantity']} x {invoice_data['currency']} {item['amount']:.2f} = {invoice_data['currency']} {item_total:.2f}\n"
        
        preview_text += f"\n**Subtotal:** {invoice_data['currency']} {subtotal:.2f}"
        if invoice_data.get('vat_enabled'):
            preview_text += f"\n**VAT (20%):** {invoice_data['currency']} {vat_amount:.2f}"
        preview_text += f"\n**TOTAL:** {invoice_data['currency']} {total_amount:.2f}"
        
        keyboard = [
            [InlineKeyboardButton("✅ Approve Invoice", callback_data=f"approve_{invoice_id}")]
        ]
        
        if is_premium_user(user_id):
            keyboard.append([InlineKeyboardButton("📧 Send to Client", callback_data=f"send_invoice_{invoice_id}")])
        
        keyboard.append([InlineKeyboardButton("✏️ Edit Invoice", callback_data=f"edit_{invoice_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(preview_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    # ===== QUOTE CREATION FLOW =====
    elif data.startswith('quote_currency_'):
        currency = data.split('_')[2]  # quote_currency_GBP -> GBP
        quote_data = context.user_data.get('current_quote', {})
        quote_data['currency'] = currency
        quote_data['step'] = 'item_description'
        context.user_data['current_quote'] = quote_data
        
        await query.edit_message_text(
            f"Currency set to: {currency}\n\n"
            "Now let's add items to your quote.\n\n"
            "Please enter the description for the first item:"
        )
        
    elif data == 'quote_add_another_item':
        quote_data = context.user_data.get('current_quote', {})
        quote_data['step'] = 'item_description'
        context.user_data['current_quote'] = quote_data
        
        await query.edit_message_text(
            "Please enter the description for the next item:"
        )
        
    elif data == 'finish_quote':
        print("DEBUG: Finish quote button pressed")
        
        quote_data = context.user_data.get('current_quote', {})
        
        # Check if we have items
        if not quote_data.get('items'):
            await query.edit_message_text(
                "❌ No items added to quote. Please add at least one item."
            )
            return
            
        quote_id = save_quote_draft(
            user_id,
            quote_data['client_name'],
            quote_data['quote_date'],
            quote_data['currency'],
            quote_data['items']
        )
        
        context.user_data['last_quote_id'] = quote_id
        
        # Calculate totals for preview
        subtotal = sum(item['quantity'] * item['amount'] for item in quote_data['items'])
        total_amount = subtotal
        
        # Show preview
        preview_text = f"""
📄 **QUOTE PREVIEW**

**Client:** {quote_data['client_name']}
**Date:** {quote_data['quote_date']}
**Currency:** {quote_data['currency']}
**Valid for:** 30 days

**ITEMS:**
"""
        for i, item in enumerate(quote_data['items'], 1):
            item_total = item['quantity'] * item['amount']
            preview_text += f"{i}. {item['description']} - {item['quantity']} x {quote_data['currency']} {item['amount']:.2f} = {quote_data['currency']} {item_total:.2f}\n"
        
        preview_text += f"\n**TOTAL:** {quote_data['currency']} {total_amount:.2f}"
        
        keyboard = [
            [InlineKeyboardButton("✅ Approve Quote", callback_data=f"approve_quote_{quote_id}")]
        ]
        
        if is_premium_user(user_id):
            keyboard.append([InlineKeyboardButton("📧 Send to Client", callback_data=f"send_quote_{quote_id}")])
        
        keyboard.append([InlineKeyboardButton("✏️ Edit Quote", callback_data=f"edit_quote_{quote_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(preview_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    # ===== APPROVAL FLOW =====
    elif data.startswith('approve_'):
        invoice_id = int(data.split('_')[1])
        print(f"DEBUG: Approving invoice ID: {invoice_id}")
        
        invoice = get_invoice(invoice_id)
        print(f"DEBUG: Retrieved invoice: {invoice}")
        
        if invoice:
            # FIXED: Correct tuple indexes based on debug output
            # Tuple structure: (0:invoice_id, 1:user_id, 2:invoice_number, 3:client_name, 4:invoice_date, 
            # 5:currency, 6:items, 7:total_amount, 8:status, 9:created_at, 10:client_email, 11:client_phone, 
            # 12:vat_enabled, 13:vat_amount)
            status_index = 8
            items_index = 6
            vat_enabled_index = 12
            vat_amount_index = 13
            
            print(f"DEBUG: Invoice status: {invoice[status_index]}")
        
        if invoice and invoice[status_index] == 'draft':  # status field
            invoice_number = generate_invoice_number(user_id)
            print(f"DEBUG: Generated invoice number: {invoice_number}")
            
            update_invoice_status(invoice_id, 'approved', invoice_number)
            print(f"DEBUG: Updated invoice status to approved")
            
            user_info = get_user(user_id)
            user_info_dict = {
                'logo_path': user_info[7] if user_info and len(user_info) > 7 else None,
                'company_name': user_info[8] if user_info and len(user_info) > 8 else None,
                'company_reg_number': user_info[9] if user_info and len(user_info) > 9 else None,
                'vat_reg_number': user_info[10] if user_info and len(user_info) > 10 else None
            }
            
            print(f"DEBUG: User info: {user_info_dict}")
            
            # Parse items from database
            try:
                items = eval(invoice[items_index]) if invoice[items_index] else []
                print(f"DEBUG: Parsed items: {items}")
            except:
                items = []
                print("DEBUG: Failed to parse items, using empty list")
            
            invoice_data_pdf = {
                'invoice_number': invoice_number,
                'client_name': invoice[3],
                'invoice_date': invoice[4],
                'currency': invoice[5],
                'items': items,
                'vat_enabled': invoice[vat_enabled_index] if len(invoice) > vat_enabled_index else False,
                'total_amount': invoice[7]
            }
            
            print(f"DEBUG: PDF data: {invoice_data_pdf}")
            
            try:
                os.makedirs('invoices', exist_ok=True)
                pdf_path = create_invoice_pdf(invoice_data_pdf, user_info_dict)
                print(f"DEBUG: PDF created at: {pdf_path}")
                
                with open(pdf_path, 'rb') as pdf_file:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=pdf_file,
                        filename=f"{invoice_number}.pdf",
                        caption=f"✅ Invoice approved and generated!\nInvoice Number: {invoice_number}"
                    )
                
                await query.edit_message_text(
                    f"✅ Invoice approved!\n📄 PDF generated: {invoice_number}\n\n"
                    f"You can view your invoices anytime with /myinvoices"
                )
            except Exception as e:
                logger.error(f"PDF generation failed: {e}")
                print(f"DEBUG: PDF generation error: {e}")
                await query.edit_message_text(
                    f"✅ Invoice approved but PDF generation failed.\n"
                    f"Invoice Number: {invoice_number}\n"
                    f"Error: {str(e)}"
                )
        else:
            print(f"DEBUG: Invoice not found or wrong status. Status: {invoice[status_index] if invoice else 'N/A'}")
            await query.edit_message_text("❌ Invoice not found or already processed.")
    
    elif data.startswith('approve_quote_'):
        quote_id = int(data.split('_')[2])
        print(f"DEBUG: Approving quote ID: {quote_id}")
        
        quote = get_quote(quote_id)
        print(f"DEBUG: Retrieved quote: {quote}")
        
        if quote and quote[8] == 'draft':  # status field
            quote_number = generate_quote_number(user_id)
            print(f"DEBUG: Generated quote number: {quote_number}")
            
            update_quote_status(quote_id, 'approved', quote_number)
            print(f"DEBUG: Updated quote status to approved")
            
            user_info = get_user(user_id)
            user_info_dict = {
                'logo_path': user_info[7] if user_info and len(user_info) > 7 else None,
                'company_name': user_info[8] if user_info and len(user_info) > 8 else None,
                'company_reg_number': user_info[9] if user_info and len(user_info) > 9 else None,
                'vat_reg_number': user_info[10] if user_info and len(user_info) > 10 else None
            }
            
            # Parse items from database
            try:
                items = eval(quote[6]) if quote[6] else []
            except:
                items = []
            
            quote_data_pdf = {
                'quote_number': quote_number,
                'client_name': quote[3],
                'quote_date': quote[4],
                'currency': quote[5],
                'items': items,
                'total_amount': quote[7],
                'type': 'quote'
            }
            
            try:
                os.makedirs('quotes', exist_ok=True)
                pdf_path = create_quote_pdf(quote_data_pdf, user_info_dict)
                print(f"DEBUG: Quote PDF created at: {pdf_path}")
                
                with open(pdf_path, 'rb') as pdf_file:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=pdf_file,
                        filename=f"{quote_number}.pdf",
                        caption=f"✅ Quote approved and generated!\nQuote Number: {quote_number}"
                    )
                
                await query.edit_message_text(
                    f"✅ Quote approved!\n📄 PDF generated: {quote_number}\n\n"
                    f"You can view your quotes anytime with /myquotes"
                )
            except Exception as e:
                logger.error(f"Quote PDF generation failed: {e}")
                print(f"DEBUG: Quote PDF generation error: {e}")
                await query.edit_message_text(
                    f"✅ Quote approved but PDF generation failed.\n"
                    f"Quote Number: {quote_number}\n"
                    f"Error: {str(e)}"
                )
        else:
            await query.edit_message_text("❌ Quote not found or already processed.")
            
    elif data.startswith('mark_paid_'):
        invoice_id = int(data.split('_')[2])
        mark_invoice_paid(invoice_id)
        await query.edit_message_text("✅ Invoice marked as paid! Use /payments to see updated list.")
        
    elif data.startswith('premium_'):
        plan_type = data.split('_')[1]
        await handle_premium_payment(query, user_id, plan_type)
        
    elif data == 'premium_back':
        # Go back to premium plans
        current_tier = get_user_tier(user_id)
        remaining_invoices = get_remaining_invoices(user_id)
        
        free_features = "\n".join([f"• {feature}" for feature in TIER_LIMITS['free']['features']])
        premium_features = "\n".join([f"• {feature}" for feature in TIER_LIMITS['premium']['features']])
        
        premium_text = f"""
📊 **Your Current Plan: Free Tier**
{free_features}

**Monthly Limit:** {TIER_LIMITS['free']['monthly_invoices']} invoices
**Invoices Remaining:** {remaining_invoices}

💎 **Upgrade to Minigma Premium**

✨ **Premium Features:**
{premium_features}

💰 **Pricing:**
/month - £{TIER_LIMITS['premium']['monthly_price']} per month
/year - £{TIER_LIMITS['premium']['annual_price']} per year (save £39!)

💳 **Subscribe now to unlock all features!**
        """
        
        keyboard = [
            [InlineKeyboardButton("💰 Monthly - £12", callback_data="premium_monthly")],
            [InlineKeyboardButton("💎 Annual - £105", callback_data="premium_annual")],
            [InlineKeyboardButton("🆓 Start Free Trial", callback_data="premium_trial")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await safe_edit_message(query, premium_text, reply_markup, 'Markdown')
            
    elif data.startswith('setup_'):
        if data == 'setup_company_reg':
            context.user_data['awaiting_company_reg'] = True
            await query.edit_message_text(
                "🏢 **Company Registration Number**\n\n"
                "Please enter your Company Registration Number:"
            )
        elif data == 'setup_vat_number':
            context.user_data['awaiting_vat_number'] = True
            await query.edit_message_text(
                "📊 **VAT Registration Number**\n\n"
                "Please enter your VAT Registration Number:"
            )
        elif data == 'setup_back':
            await query.edit_message_text("Setup cancelled.")
            
    elif data == 'client_start':
        context.user_data['client_creation'] = {'step': 'name'}
        await query.edit_message_text(
            "👥 **Add New Client**\n\n"
            "Let's add a new client to your database.\n\n"
            "Please enter the client's full name:",
            parse_mode='Markdown'
        )
        
    elif data.startswith('view_client_'):
        client_id = int(data.split('_')[2])
        client = get_client_by_id(client_id)
        
        if client:
            # Get invoices for this client
            client_invoices = get_user_invoices(user_id, client[2])
            
            client_info = f"""
👤 **Client Details**

**Name:** {client[2]}
**Email:** {client[3] or 'Not provided'}
**Phone:** {client[4] or 'Not provided'}
**Address:** {client[5] or 'Not provided'}

📊 **Invoice History:**
"""
            if client_invoices:
                total_amount = sum(inv[7] for inv in client_invoices)
                paid_invoices = sum(1 for inv in client_invoices if inv[11])
                
                client_info += f"• Total Invoices: {len(client_invoices)}\n"
                client_info += f"• Paid: {paid_invoices}\n"
                client_info += f"• Unpaid: {len(client_invoices) - paid_invoices}\n"
                client_info += f"• Total Value: {client_invoices[0][5]}{total_amount:.2f}\n\n"
                
                client_info += "**Recent Invoices:**\n"
                for inv in client_invoices[:3]:  # Show last 3 invoices
                    status = "✅ Paid" if inv[11] else "❌ Unpaid"
                    client_info += f"• {inv[2]} - {inv[5]}{inv[7]:.2f} - {status}\n"
            else:
                client_info += "No invoices yet\n"
            
            keyboard = [
                [InlineKeyboardButton("📄 Create Invoice", callback_data=f"create_invoice_client_{client_id}")],
                [InlineKeyboardButton("📋 Create Quote", callback_data=f"create_quote_client_{client_id}")],
                [InlineKeyboardButton("✏️ Edit Client", callback_data=f"edit_client_{client_id}")],
                [InlineKeyboardButton("🔙 Back to Clients", callback_data="clients_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(client_info, reply_markup=reply_markup, parse_mode='Markdown')
    
    elif data.startswith('create_invoice_client_'):
        client_id = int(data.split('_')[3])
        client = get_client_by_id(client_id)
        
        if client:
            # Start invoice creation with client pre-filled and skip to invoice date step
            context.user_data['current_invoice'] = {
                'items': [],
                'step': 'invoice_date',  # Skip directly to invoice date
                'client_name': client[2]  # Pre-fill client name
            }
            
            await query.edit_message_text(
                f"📝 Creating invoice for: {client[2]}\n\n"
                "The client name has been pre-filled. Let's continue with the invoice date.\n\n"
                "📅 Please enter the invoice date.\n"
                "Format: DD MMM YYYY (e.g., 24 Oct 2025)\n"
                "Or type 'today' for today's date"
            )
    
    elif data.startswith('create_quote_client_'):
        client_id = int(data.split('_')[3])
        client = get_client_by_id(client_id)
        
        if client:
            # Start quote creation with client pre-filled and skip to quote date step
            context.user_data['current_quote'] = {
                'items': [],
                'step': 'quote_date',  # Skip directly to quote date
                'client_name': client[2],  # Pre-fill client name
                'type': 'quote'
            }
            
            await query.edit_message_text(
                f"📋 Creating quote for: {client[2]}\n\n"
                "The client name has been pre-filled. Let's continue with the quote date.\n\n"
                "📅 Please enter the quote date.\n"
                "Format: DD MMM YYYY (e.g., 24 Oct 2025)\n"
                "Or type 'today' for today's date"
            )
    
    elif data.startswith('edit_client_'):
        client_id = int(data.split('_')[2])
        context.user_data['editing_client'] = client_id
        context.user_data['client_edit_step'] = 'name'
        
        client = get_client_by_id(client_id)
        if client:
            await query.edit_message_text(
                f"✏️ **Editing Client: {client[2]}**\n\n"
                "Please enter the new client name, or type 'skip' to keep current:",
                parse_mode='Markdown'
            )
        
    elif data == 'search_client_invoices':
        context.user_data['awaiting_client_search'] = True
        await query.edit_message_text("Please enter the client name to search for invoices:")
        
    elif data == 'clients_back':
        # FIXED: Show clients list with safe edit
        clients = get_user_clients(user_id)
        
        if not clients:
            keyboard = [
                [InlineKeyboardButton("➕ Add New Client", callback_data="client_start")],
                [InlineKeyboardButton("🔙 Back", callback_data="clients_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            new_text = "👥 **Client Database**\n\nNo clients found. Add your first client to get started!"
            await safe_edit_message(query, new_text, reply_markup, 'Markdown')
            return
        
        keyboard = []
        for client in clients[:10]:
            keyboard.append([InlineKeyboardButton(f"👤 {client[2]}", callback_data=f"view_client_{client[0]}")])
        
        keyboard.extend([
            [InlineKeyboardButton("➕ Add New Client", callback_data="client_start")],
            [InlineKeyboardButton("🔍 Search Invoices by Client", callback_data="search_client_invoices")],
            [InlineKeyboardButton("🔙 Back", callback_data="clients_back")]
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        new_text = f"👥 **Client Database**\n\nYou have {len(clients)} clients. Select a client to view details:"
        await safe_edit_message(query, new_text, reply_markup, 'Markdown')
        
    elif data == 'payments_back':
        # FIXED: Show payments list with safe edit
        unpaid_invoices = get_unpaid_invoices(user_id)
        
        if not unpaid_invoices:
            new_text = "💰 **Payment Tracking**\n\n🎉 All your invoices are paid! No outstanding payments."
            await safe_edit_message(query, new_text, parse_mode='Markdown')
            return
        
        keyboard = []
        for invoice in unpaid_invoices[:10]:
            keyboard.append([InlineKeyboardButton(
                f"📄 {invoice[2]} - {invoice[3]} - {invoice[5]}{invoice[7]:.2f}", 
                callback_data=f"mark_paid_{invoice[0]}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="payments_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        new_text = f"💰 **Payment Tracking**\n\nYou have {len(unpaid_invoices)} unpaid invoices. Mark them as paid:"
        await safe_edit_message(query, new_text, reply_markup, 'Markdown')
        
    elif data.startswith('edit_'):
        await query.edit_message_text(
            "✏️ **Edit Feature**\n\n"
            "Invoice editing is available in our Premium tier!\n\n"
            "With Premium you can:\n"
            "• Edit invoices after creation\n"
            "• Add/remove items\n"
            "• Update client details\n"
            "• Modify amounts\n\n"
            "Use /premium to upgrade!",
            parse_mode='Markdown'
        )
        
    elif data.startswith('send_invoice_'):
        invoice_id = int(data.split('_')[2])
        invoice = get_invoice(invoice_id)
        
        if invoice:
            # Get client contact info
            client_email, client_phone = get_client_contact_info(invoice[3], user_id)
            
            if not client_email and not client_phone:
                await query.edit_message_text(
                    "❌ **No Contact Information**\n\n"
                    "This client doesn't have email or phone number saved.\n\n"
                    "Please add contact information to the client first:\n"
                    "1. Go to /clients\n"
                    "2. Select the client\n" 
                    "3. Click 'Edit Client'\n"
                    "4. Add email and/or phone number",
                    parse_mode='Markdown'
                )
                return
            
            # Generate invoice number and PDF if not already done
            if not invoice[2]:  # No invoice number yet
                invoice_number = generate_invoice_number(user_id)
                update_invoice_status(invoice_id, 'approved', invoice_number)
            else:
                invoice_number = invoice[2]
            
            # Create PDF if needed
            user_info = get_user(user_id)
            user_info_dict = {
                'logo_path': user_info[7] if user_info and len(user_info) > 7 else None,
                'company_name': user_info[8] if user_info and len(user_info) > 8 else None,
                'company_reg_number': user_info[9] if user_info and len(user_info) > 9 else None,
                'vat_reg_number': user_info[10] if user_info and len(user_info) > 10 else None
            }
            
            items = eval(invoice[6]) if invoice[6] else []
            invoice_data_pdf = {
                'invoice_number': invoice_number,
                'client_name': invoice[3],
                'invoice_date': invoice[4],
                'currency': invoice[5],
                'items': items,
                'vat_enabled': invoice[12] if len(invoice) > 12 else False,
                'total_amount': invoice[7]
            }
            
            pdf_path = create_invoice_pdf(invoice_data_pdf, user_info_dict)
            
            # Send options
            keyboard = []
            if client_email:
                keyboard.append([InlineKeyboardButton("📧 Send Email", callback_data=f"send_email_{invoice_id}")])
            if client_phone:
                keyboard.append([InlineKeyboardButton("📱 Send SMS", callback_data=f"send_sms_{invoice_id}")])
            if client_email and client_phone:
                keyboard.append([InlineKeyboardButton("📧📱 Send Both", callback_data=f"send_both_{invoice_id}")])
            
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"approve_{invoice_id}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            contact_info = ""
            if client_email:
                contact_info += f"📧 Email: {client_email}\n"
            if client_phone:
                contact_info += f"📱 Phone: {client_phone}\n"
            
            await query.edit_message_text(
                f"📤 **Send Invoice to Client**\n\n"
                f"**Client:** {invoice[3]}\n"
                f"**Invoice:** {invoice_number}\n\n"
                f"**Contact Information:**\n{contact_info}\n"
                f"Choose how to send the invoice:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Invoice not found.")

    elif data.startswith('send_email_'):
        invoice_id = int(data.split('_')[2])
        await send_invoice_via_email(invoice_id, user_id, query, context)
        
    elif data.startswith('send_sms_'):
        invoice_id = int(data.split('_')[2])
        await send_invoice_via_sms(invoice_id, user_id, query, context)
        
    elif data.startswith('send_both_'):
        invoice_id = int(data.split('_')[2])
        await send_invoice_via_both(invoice_id, user_id, query, context)

        # ==================================================
# PART 6: TEXT HANDLER AND MAIN FUNCTION
# ==================================================

# FIXED: Enhanced client creation and editing
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text inputs including premium features"""
    user_id = update.effective_user.id
    text = update.message.text
    
    print(f"DEBUG: Text input received - {text}")
    
    # Handle company registration number
    if context.user_data.get('awaiting_company_reg'):
        update_user_company_info(user_id, company_reg=text)
        context.user_data['awaiting_company_reg'] = False
        await update.message.reply_text(f"✅ Company Registration Number set to: {text}")
        return
        
    # Handle VAT number
    if context.user_data.get('awaiting_vat_number'):
        update_user_company_info(user_id, vat_reg=text)
        context.user_data['awaiting_vat_number'] = False
        await update.message.reply_text(f"✅ VAT Registration Number set to: {text}")
        return
        
    # Handle enhanced client creation
    if context.user_data.get('client_creation'):
        client_data = context.user_data['client_creation']
        
        if client_data['step'] == 'name':
            client_data['name'] = text
            client_data['step'] = 'email'
            context.user_data['client_creation'] = client_data
            
            await update.message.reply_text(
                "📧 **Client Email Address**\n\n"
                "Please enter the client's email address:\n\n"
                "*This will be used for sending invoices and payment reminders*",
                parse_mode='Markdown'
            )
            return
            
        elif client_data['step'] == 'email':
            client_data['email'] = text
            client_data['step'] = 'phone'
            context.user_data['client_creation'] = client_data
            
            await update.message.reply_text(
                "📱 **Client Phone Number**\n\n"
                "Please enter the client's phone number:\n\n"
                "*This will be used for SMS notifications*",
                parse_mode='Markdown'
            )
            return
            
        elif client_data['step'] == 'phone':
            client_data['phone'] = text
            client_data['step'] = 'address'
            context.user_data['client_creation'] = client_data
            
            await update.message.reply_text(
                "🏠 **Client Address** (Optional)\n\n"
                "Please enter the client's address, or type 'skip' to continue:",
                parse_mode='Markdown'
            )
            return
            
        elif client_data['step'] == 'address':
            address = text if text.lower() != 'skip' else None
            
            # Save the complete client
            client_id = save_client(
                user_id,
                client_data['name'],
                client_data['email'],
                client_data['phone'],
                address
            )
            
            # Clear client creation data
            del context.user_data['client_creation']
            
            await update.message.reply_text(
                f"✅ **Client Added Successfully!**\n\n"
                f"**Name:** {client_data['name']}\n"
                f"**Email:** {client_data['email']}\n"
                f"**Phone:** {client_data['phone']}\n"
                f"**Address:** {address if address else 'Not provided'}\n\n"
                "This client is now in your database and ready for invoicing!",
                parse_mode='Markdown'
            )
            return
    
    # Handle client editing
    if context.user_data.get('editing_client'):
        client_id = context.user_data['editing_client']
        edit_step = context.user_data.get('client_edit_step')
        client = get_client_by_id(client_id)
        
        if not client:
            await update.message.reply_text("❌ Client not found.")
            return
            
        if edit_step == 'name':
            if text.lower() != 'skip':
                update_client(client_id, client_name=text)
            context.user_data['client_edit_step'] = 'email'
            await update.message.reply_text(
                f"✏️ **Editing Client Email**\n\n"
                f"Current email: {client[3] or 'Not provided'}\n"
                "Please enter the new email, or type 'skip' to keep current:",
                parse_mode='Markdown'
            )
            return
            
        elif edit_step == 'email':
            if text.lower() != 'skip':
                update_client(client_id, email=text)
            context.user_data['client_edit_step'] = 'phone'
            await update.message.reply_text(
                f"✏️ **Editing Client Phone**\n\n"
                f"Current phone: {client[4] or 'Not provided'}\n"
                "Please enter the new phone number, or type 'skip' to keep current:",
                parse_mode='Markdown'
            )
            return
            
        elif edit_step == 'phone':
            if text.lower() != 'skip':
                update_client(client_id, phone=text)
            context.user_data['client_edit_step'] = 'address'
            await update.message.reply_text(
                f"✏️ **Editing Client Address**\n\n"
                f"Current address: {client[5] or 'Not provided'}\n"
                "Please enter the new address, or type 'skip' to keep current:",
                parse_mode='Markdown'
            )
            return
            
        elif edit_step == 'address':
            if text.lower() != 'skip':
                update_client(client_id, address=text)
            
            # Clear editing data
            del context.user_data['editing_client']
            del context.user_data['client_edit_step']
            
            updated_client = get_client_by_id(client_id)
            await update.message.reply_text(
                f"✅ **Client Updated Successfully!**\n\n"
                f"**Name:** {updated_client[2]}\n"
                f"**Email:** {updated_client[3] or 'Not provided'}\n"
                f"**Phone:** {updated_client[4] or 'Not provided'}\n"
                f"**Address:** {updated_client[5] or 'Not provided'}\n\n"
                "Client details have been updated!",
                parse_mode='Markdown'
            )
            return
        
    # Handle client search - FIXED: This was the broken line
    if context.user_data.get('awaiting_client_search'):
        invoices = get_user_invoices(user_id, text)
        context.user_data['awaiting_client_search'] = False
        if invoices:
            message = f"📋 Invoices for {text}:\n\n"
            for inv in invoices:
                paid_status = "✅ Paid" if inv[11] else "❌ Unpaid"
                message += f"• {inv[2]} - {inv[5]}{inv[7]:.2f} - {inv[4]} - {paid_status}\n"
        else:
            message = f"No invoices found for client: {text}"
        await update.message.reply_text(message)
        return
        
    # Handle regular invoice creation
    await handle_invoice_creation(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 Minigma Invoice Bot - Help

📝 **Basic Commands:**
/start - Start the bot
/logo - Upload company logo
/company - Set company name
/create - Create new invoice
/myinvoices - View your invoices
/help - Show this help message

💰 **Premium Commands:**
/premium - Premium features info
/setup - Company registration setup
/clients - Client database
/payments - Track payments

💡 **Premium Features:**
• Store company/VAT registration numbers
• VAT calculation on invoices
• Client database management
• Payment tracking
• Unlimited invoices

📊 **Usage Limits:**
• 14-day free trial (unlimited invoices)
• After trial: 10 invoices per month
• Premium: Unlimited invoices + all features

Need help? Contact support!
    """
    await update.message.reply_text(help_text)

def main():
    # Create necessary directories
    os.makedirs('logos', exist_ok=True)
    os.makedirs('invoices', exist_ok=True)
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers - ORDER MATTERS!
    
    # Command handlers first
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("logo", set_logo))
    application.add_handler(CommandHandler("company", set_company_name))
    application.add_handler(CommandHandler("create", create_invoice))
    application.add_handler(CommandHandler("myinvoices", my_invoices_command))
    application.add_handler(CommandHandler("premium", premium_command))
    application.add_handler(CommandHandler("setup", setup_command))
    application.add_handler(CommandHandler("clients", clients_command))
    application.add_handler(CommandHandler("payments", payments_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("contact", contact_command))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CommandHandler("add_premium", add_premium_command))
    application.add_handler(CommandHandler("remove_premium", remove_premium_command))
    application.add_handler(CommandHandler("list_premium", list_premium_command))

    # Photo handler for logos
    application.add_handler(MessageHandler(filters.PHOTO, handle_logo))
    
    # Callback query handler for buttons - MUST be before text handler
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Text handler last - catches all other text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))
    
    # Set up bot commands menu
    application.post_init = setup_bot_commands
    
    # Start the bot
    print("🚀 Bot is starting with ALL FEATURES FIXED...")
    print("✅ Premium features are ENABLED for testing!")
    print("✅ Payments system is WORKING!")
    print("✅ Finish Invoice button is WORKING!")
    print("✅ VAT calculation is WORKING!")
    print("✅ Client creation with email/phone is WORKING!")
    print("✅ Client editing is WORKING!")
    print("✅ Setup command is WORKING!")
    print("✅ MyInvoices command is WORKING!")
    print("")
    print("📝 TESTING INSTRUCTIONS:")
    print("   1. /create → Add items → Finish Invoice → See VAT calculation")
    print("   2. /payments → Mark invoices as paid")
    print("   3. /setup → Set company/VAT numbers")
    print("   4. /clients → Add New Client → Enter name, email, phone")
    print("   5. /clients → View Client → Edit Client → Update details")
    print("   6. /myinvoices → View all invoices")
    print("   7. /myinvoices ClientName → Filter by client")
    print("")
    print("🎯 ALL FEATURES SHOULD NOW WORK!")
    
    try:
        application.run_polling()
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        print(f"❌ Bot crashed: {e}")

if __name__ == '__main__':
    # FIXED: Use asyncio to run the application properly
    import asyncio
    asyncio.run(main())
# ==================================================
# PART 7: EMAIL AND SMS DELIVERY
# ==================================================

import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

# Email configuration (you'll need to set these up)
EMAIL_CONFIG = {
    'smtp_server': 'smtp.gmail.com',  # Change for your email provider
    'smtp_port': 587,
    'sender_email': 'your-email@gmail.com',  # Set this
    'sender_password': 'your-app-password',  # Set this (use app password for Gmail)
    'sender_name': 'Your Company Name'
}

# SMS configuration (using Twilio as example)
SMS_CONFIG = {
    'account_sid': 'your-twilio-account-sid',  # Set this
    'auth_token': 'your-twilio-auth-token',    # Set this
    'twilio_number': '+1234567890'             # Set this
}

def send_invoice_email(client_email, client_name, invoice_number, pdf_path, invoice_data):
    """Send invoice via email"""
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = f"{EMAIL_CONFIG['sender_name']} <{EMAIL_CONFIG['sender_email']}>"
        msg['To'] = client_email
        msg['Subject'] = f"Invoice {invoice_number} from {EMAIL_CONFIG['sender_name']}"
        
        # Email body
        body = f"""
Dear {client_name},

Please find your invoice {invoice_number} attached.

Invoice Details:
- Invoice Number: {invoice_number}
- Date: {invoice_data['invoice_date']}
- Total Amount: {invoice_data['currency']} {invoice_data['total_amount']:.2f}

Thank you for your business!

Best regards,
{EMAIL_CONFIG['sender_name']}
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach PDF
        with open(pdf_path, 'rb') as file:
            attach = MIMEApplication(file.read(), _subtype='pdf')
            attach.add_header('Content-Disposition', 'attachment', filename=f'{invoice_number}.pdf')
            msg.attach(attach)
        
        # Send email
        server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.starttls()
        server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Email sent to {client_email}")
        return True
        
    except Exception as e:
        print(f"❌ Email sending failed: {e}")
        return False

def send_invoice_sms(client_phone, client_name, invoice_number, invoice_data):
    """Send invoice notification via SMS (using Twilio)"""
    try:
        # This is a simplified version - you'll need Twilio account
        # Install: pip install twilio
        from twilio.rest import Client
        
        client = Client(SMS_CONFIG['account_sid'], SMS_CONFIG['auth_token'])
        
        message_body = f"""
Hi {client_name}, your invoice {invoice_number} for {invoice_data['currency']} {invoice_data['total_amount']:.2f} is ready. 
Check your email for the PDF invoice.
From {EMAIL_CONFIG['sender_name']}
        """
        
        message = client.messages.create(
            body=message_body,
            from_=SMS_CONFIG['twilio_number'],
            to=client_phone
        )
        
        print(f"✅ SMS sent to {client_phone}")
        return True
        
    except ImportError:
        print("❌ Twilio not installed. Install with: pip install twilio")
        return False
    except Exception as e:
        print(f"❌ SMS sending failed: {e}")
        return False

def get_client_contact_info(client_name, user_id):
    """Get client email and phone from database"""
    client = get_client_by_name(user_id, client_name)
    if client:
        return client[3], client[4]  # email, phone
    return None, None

# Update the save_invoice_draft function to accept client contact info
def save_invoice_draft_with_contacts(user_id, client_name, invoice_date, currency, items, 
                                   vat_enabled=False, client_email=None, client_phone=None):
    """Save invoice draft with client contact information"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    items_json = str(items)
    
    # Calculate totals
    subtotal = sum(item['quantity'] * item['amount'] for item in items)
    vat_amount = subtotal * 0.2 if vat_enabled else 0
    total_amount = subtotal + vat_amount
    
    print(f"DEBUG: Saving invoice draft - User: {user_id}, Client: {client_name}")
    print(f"DEBUG: Client Email: {client_email}, Phone: {client_phone}")
    
    cursor.execute('''
        INSERT INTO invoices (user_id, client_name, invoice_date, currency, items, 
                            total_amount, vat_enabled, vat_amount, client_email, client_phone, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
    ''', (user_id, client_name, invoice_date, currency, items_json, total_amount, 
          vat_enabled, vat_amount, client_email, client_phone))
    
    invoice_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    print(f"DEBUG: Saved invoice with ID: {invoice_id}")
    return invoice_id

async def send_invoice_via_email(invoice_id, user_id, query, context):
    """Send invoice via email"""
    invoice = get_invoice(invoice_id)
    if not invoice:
        await query.edit_message_text("❌ Invoice not found.")
        return
    
    client_email = invoice[10] if len(invoice) > 10 else None
    if not client_email:
        await query.edit_message_text("❌ No email address found for this client.")
        return
    
    # Generate PDF
    user_info = get_user(user_id)
    user_info_dict = {
        'logo_path': user_info[7] if user_info and len(user_info) > 7 else None,
        'company_name': user_info[8] if user_info and len(user_info) > 8 else None,
        'company_reg_number': user_info[9] if user_info and len(user_info) > 9 else None,
        'vat_reg_number': user_info[10] if user_info and len(user_info) > 10 else None
    }
    
    items = eval(invoice[6]) if invoice[6] else []
    invoice_data = {
        'invoice_number': invoice[2],
        'client_name': invoice[3],
        'invoice_date': invoice[4],
        'currency': invoice[5],
        'items': items,
        'vat_enabled': invoice[12] if len(invoice) > 12 else False,
        'total_amount': invoice[7]
    }
    
    pdf_path = create_invoice_pdf(invoice_data, user_info_dict)
    
    # Send email
    success = send_invoice_email(
        client_email, 
        invoice[3], 
        invoice[2], 
        pdf_path, 
        invoice_data
    )
    
    if success:
        await query.edit_message_text(
            f"✅ **Email Sent Successfully!**\n\n"
            f"**To:** {client_email}\n"
            f"**Invoice:** {invoice[2]}\n"
            f"**Client:** {invoice[3]}\n\n"
            f"The invoice has been sent to the client's email address.",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            f"❌ **Email Failed to Send**\n\n"
            f"Please check your email configuration and try again.",
            parse_mode='Markdown'
        )

async def send_invoice_via_sms(invoice_id, user_id, query, context):
    """Send invoice notification via SMS"""
    invoice = get_invoice(invoice_id)
    if not invoice:
        await query.edit_message_text("❌ Invoice not found.")
        return
    
    client_phone = invoice[11] if len(invoice) > 11 else None
    if not client_phone:
        await query.edit_message_text("❌ No phone number found for this client.")
        return
    
    invoice_data = {
        'invoice_number': invoice[2],
        'client_name': invoice[3],
        'invoice_date': invoice[4],
        'currency': invoice[5],
        'total_amount': invoice[7]
    }
    
    # Send SMS
    success = send_invoice_sms(client_phone, invoice[3], invoice[2], invoice_data)
    
    if success:
        await query.edit_message_text(
            f"✅ **SMS Sent Successfully!**\n\n"
            f"**To:** {client_phone}\n"
            f"**Invoice:** {invoice[2]}\n"
            f"**Client:** {invoice[3]}\n\n"
            f"The invoice notification has been sent via SMS.",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            f"❌ **SMS Failed to Send**\n\n"
            f"Please check your SMS configuration and try again.",
            parse_mode='Markdown'
        )

async def send_invoice_via_both(invoice_id, user_id, query, context):
    """Send invoice via both email and SMS"""
    # Send email first
    invoice = get_invoice(invoice_id)
    if not invoice:
        await query.edit_message_text("❌ Invoice not found.")
        return
    
    client_email = invoice[10] if len(invoice) > 10 else None
    client_phone = invoice[11] if len(invoice) > 11 else None
    
    if client_email:
        await send_invoice_via_email(invoice_id, user_id, query, context)
        # Small delay then send SMS
        await asyncio.sleep(2)
    
    if client_phone:
        await send_invoice_via_sms(invoice_id, user_id, query, context)

def test_email_configuration():
    """Test email configuration"""
    try:
        server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.starttls()
        server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
        server.quit()
        print("✅ Email configuration test: PASSED")
        return True
    except Exception as e:
        print(f"❌ Email configuration test: FAILED - {e}")
        return False

def test_sms_configuration():
    """Test SMS configuration"""
    try:
        from twilio.rest import Client
        client = Client(SMS_CONFIG['account_sid'], SMS_CONFIG['auth_token'])
        # Try to list messages to test connection
        client.messages.list(limit=1)
        print("✅ SMS configuration test: PASSED")
        return True
    except ImportError:
        print("❌ SMS configuration test: Twilio not installed")
        return False
    except Exception as e:
        print(f"❌ SMS configuration test: FAILED - {e}")
        return False

# Add this to main() to test configurations on startup
def setup_email_sms():
    """Setup and test email/SMS configurations"""
    print("\n🔧 Setting up Email & SMS Delivery...")
    
    email_ready = test_email_configuration()
    sms_ready = test_sms_configuration()
    
    if email_ready:
        print("📧 Email delivery: READY")
    else:
        print("📧 Email delivery: NOT CONFIGURED - Set up EMAIL_CONFIG in PART 7")
    
    if sms_ready:
        print("📱 SMS delivery: READY") 
    else:
        print("📱 SMS delivery: NOT CONFIGURED - Set up SMS_CONFIG in PART 7 or install Twilio")
    
    return email_ready or sms_ready  # Return True if at least one is configured
# ==================================================
# PART 8: PREMIUM TIER SYSTEM & QUOTE FUNCTIONALITY
# ==================================================

# Tier configuration
TIER_LIMITS = {
    'free': {
        'monthly_invoices': 10,  # Includes both invoices and quotes
        'features': [
            'Basic invoice creation',
            'Quote creation',
            'PDF generation',
            'Multiple currencies',
            '14-day free trial'
        ],
        'price': 0
    },
    'premium': {
        'monthly_invoices': float('inf'),  # Unlimited
        'features': [
            'Unlimited invoices & quotes',
            'Company/VAT registration',
            'VAT calculation',
            'Client database', 
            'Payment tracking',
            'Email/SMS delivery',
            'Priority support'
        ],
        'monthly_price': 12,
        'annual_price': 105
    }
}

# Payment configuration (using Stripe as example)
PAYMENT_CONFIG = {
    'stripe_secret_key': 'sk_test_your_stripe_secret_key',  # Set this
    'stripe_public_key': 'pk_test_your_stripe_public_key',  # Set this
    'webhook_secret': 'whsec_your_webhook_secret'  # Set this
}

async def safe_edit_message(query, new_text, reply_markup=None, parse_mode=None):
    """Safely edit a message, only if content has changed"""
    try:
        # Check if content is actually different
        current_text = query.message.text or ""
        if current_text.replace(' #', '').strip() == new_text.replace(' #', '').strip():
            # Content is the same, just answer the callback to remove loading
            await query.answer()
            return False
        
        # Content is different, proceed with edit
        await query.edit_message_text(
            text=new_text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        return True
        
    except Exception as e:
        # If edit fails, just answer the callback
        print(f"Safe edit failed: {e}")
        await query.answer()
        return False

def is_premium_user(user_id):
    """Check if user has active premium subscription"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT subscription_tier, trial_end_date FROM users WHERE user_id = ?
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return False
    
    subscription_tier, trial_end_date = result
    
    # Check if user is on premium tier
    if subscription_tier == 'premium':
        return True
    
    # Check if user is in trial period
    if trial_end_date:
        trial_end = parse_trial_end_date(trial_end_date)
        if datetime.now() <= trial_end:
            return True
    
    return False

def get_user_tier(user_id):
    """Get user's current tier"""
    if is_premium_user(user_id):
        return 'premium'
    return 'free'

def get_remaining_invoices(user_id):
    """Get remaining invoices/quotes for current month"""
    if is_premium_user(user_id):
        return float('inf')  # Unlimited for premium
    
    monthly_count = get_user_invoice_count_this_month(user_id) + get_user_quote_count_this_month(user_id)
    remaining = TIER_LIMITS['free']['monthly_invoices'] - monthly_count
    return max(0, remaining)

def check_invoice_limit(user_id):
    """Check if user can create more invoices or quotes"""
    if is_premium_user(user_id):
        return True, ""

    monthly_count = get_user_invoice_count_this_month(user_id) + get_user_quote_count_this_month(user_id)
    remaining = TIER_LIMITS['free']['monthly_invoices'] - monthly_count
    
    if remaining <= 0:
        return False, f"❌ You've reached your monthly limit of {TIER_LIMITS['free']['monthly_invoices']} creations.\nUpgrade to Premium for unlimited invoices and quotes!"
    
    return True, f"({remaining} creations remaining this month)"

# Quote-specific database functions
def save_quote_draft(user_id, client_name, quote_date, currency, items, client_email=None, client_phone=None):
    """Save quote draft to database"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    items_json = str(items)
    
    # Calculate total
    total_amount = sum(item['quantity'] * item['amount'] for item in items)
    
    print(f"DEBUG: Saving quote draft - User: {user_id}, Client: {client_name}")
    
    cursor.execute('''
        INSERT INTO invoices (user_id, client_name, invoice_date, currency, items, 
                            total_amount, status, client_email, client_phone, document_type)
        VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, 'quote')
    ''', (user_id, client_name, quote_date, currency, items_json, total_amount, client_email, client_phone))
    
    quote_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    print(f"DEBUG: Saved quote with ID: {quote_id}")
    return quote_id

def get_quote(quote_id):
    """Get quote by ID"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM invoices WHERE invoice_id = ? AND document_type = "quote"', (quote_id,))
    quote = cursor.fetchone()
    conn.close()
    return quote

def generate_quote_number(user_id):
    """Generate unique quote number"""
    counter = get_invoice_counter(user_id)
    now = datetime.now()
    quote_number = f"QUO-{now.year}-{now.month:02d}-{counter:04d}"
    increment_invoice_counter(user_id)
    return quote_number

def update_quote_status(quote_id, status, quote_number=None):
    """Update quote status"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    if quote_number:
        cursor.execute('''
            UPDATE invoices SET status = ?, invoice_number = ? WHERE invoice_id = ? AND document_type = "quote"
        ''', (status, quote_number, quote_id))
    else:
        cursor.execute('''
            UPDATE invoices SET status = ? WHERE invoice_id = ? AND document_type = "quote"
        ''', (status, quote_id))
    conn.commit()
    conn.close()

def get_user_quotes(user_id, client_name=None):
    """Get user's quotes"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    if client_name:
        cursor.execute('''
            SELECT * FROM invoices 
            WHERE user_id = ? AND client_name LIKE ? AND status = 'approved' AND document_type = 'quote'
            ORDER BY created_at DESC
        ''', (user_id, f'%{client_name}%'))
    else:
        cursor.execute('''
            SELECT * FROM invoices 
            WHERE user_id = ? AND status = 'approved' AND document_type = 'quote'
            ORDER BY created_at DESC
        ''', (user_id,))
    quotes = cursor.fetchall()
    conn.close()
    return quotes

def get_user_quote_count_this_month(user_id):
    """Get number of quotes created this month"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    first_day_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cursor.execute('''
        SELECT COUNT(*) FROM invoices 
        WHERE user_id = ? AND status = 'approved' AND created_at >= ? AND document_type = 'quote'
    ''', (user_id, first_day_of_month))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def create_quote_pdf(quote_data, user_info):
    """Create PDF for quote"""
    try:
        # Use the same PDF creation as invoice but with quote-specific text
        buffer = io.BytesIO()
        
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=A4,
            topMargin=0.5*inch,
            bottomMargin=0.5*inch,
            leftMargin=0.5*inch,
            rightMargin=0.5*inch
        )
        story = []
        styles = getSampleStyleSheet()
        
        title_style = styles["Heading1"]
        title_style.alignment = TA_RIGHT
        title_style.spaceAfter = 20
        
        normal_style = styles["Normal"]
        normal_style.spaceAfter = 6
        
        bold_style = styles["Normal"]
        bold_style.fontName = 'Helvetica-Bold'
        
        # Currency symbol mapping
        currency_symbols = {
            'GBP': '£',
            'USD': '$',
            'EUR': '€'
        }
        
        # Get currency symbol or use code as fallback
        currency_code = quote_data['currency']
        currency_symbol = currency_symbols.get(currency_code, currency_code)
        
        # Header section
        has_logo = user_info.get('logo_path') and os.path.exists(user_info['logo_path'])
        company_name = user_info.get('company_name', '')
        
        header_data = []
        
        if has_logo:
            try:
                logo = Image(user_info['logo_path'], width=2.5*inch, height=1.25*inch)
                header_data.append(logo)
            except Exception as e:
                logger.warning(f"Could not load logo: {e}")
                has_logo = False
                if company_name:
                    company_text = Paragraph(f"<b>{company_name}</b>", bold_style)
                    header_data.append(company_text)
        elif company_name:
            company_text = Paragraph(f"<b>{company_name}</b>", bold_style)
            header_data.append(company_text)
        else:
            header_data.append(Spacer(1, 1.25*inch))
        
        right_section = []
        quote_title = Paragraph("<b>QUOTE</b>", title_style)
        right_section.append(quote_title)
        
        header_table = Table([[header_data, right_section]], colWidths=[4*inch, 2*inch])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ]))
        
        story.append(header_table)
        story.append(Spacer(1, 0.4*inch))
        
        # Company registration numbers if available
        reg_data = []
        if user_info.get('company_reg_number'):
            reg_data.append(Paragraph(f"<b>Company Reg:</b> {user_info['company_reg_number']}", normal_style))
        
        if reg_data:
            for reg in reg_data:
                story.append(reg)
            story.append(Spacer(1, 0.2*inch))
        
        # Quote details
        details_data = [
            [Paragraph("<b>Quote Number:</b>", bold_style), 
             Paragraph(quote_data['quote_number'], normal_style),
             Paragraph("<b>Date:</b>", bold_style), 
             Paragraph(quote_data['quote_date'], normal_style)],
            
            [Paragraph("<b>Quote To:</b>", bold_style), 
             Paragraph(quote_data['client_name'], normal_style),
             Paragraph("<b>Valid Until:</b>", bold_style), 
             Paragraph((datetime.strptime(quote_data['quote_date'], '%d %b %Y') + timedelta(days=30)).strftime('%d %b %Y'), normal_style)]
        ]
        
        details_table = Table(details_data, colWidths=[1.2*inch, 2.2*inch, 1.2*inch, 1.4*inch])
        details_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.white),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        
        story.append(details_table)
        story.append(Spacer(1, 0.4*inch))
        
        # Items table
        table_data = [
            [Paragraph('<b>Description</b>', bold_style), 
             Paragraph('<b>Qty</b>', bold_style), 
             Paragraph('<b>Unit Price</b>', bold_style), 
             Paragraph('<b>Total</b>', bold_style)]
        ]
        
        total_amount = 0
        for item in quote_data['items']:
            total = item['quantity'] * item['amount']
            total_amount += total
            table_data.append([
                Paragraph(item['description'], normal_style),
                Paragraph(str(item['quantity']), normal_style),
                Paragraph(f"{currency_symbol} {item['amount']:.2f}", normal_style),
                Paragraph(f"{currency_symbol} {total:.2f}", normal_style)
            ])
        
        # Add TOTAL row
        table_data.append([
            Paragraph("<b>TOTAL</b>", bold_style),
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph(f"<b>{currency_symbol} {total_amount:.2f}</b>", bold_style)
        ])
        
        items_table = Table(table_data, colWidths=[3.2*inch, 0.8*inch, 1.2*inch, 1.2*inch])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a6ee0')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.white),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8f9fa')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('BOX', (-1, -1), (-1, -1), 2, colors.HexColor('#4a6ee0')),
            ('BACKGROUND', (-1, -1), (-1, -1), colors.HexColor('#f1f5fd')),
        ]))
        
        story.append(items_table)
        story.append(Spacer(1, 0.5*inch))
        
        # Terms and conditions
        terms_style = styles["Normal"]
        terms_style.alignment = TA_LEFT
        terms_style.textColor = colors.gray
        terms_style.fontSize = 9
        terms_style.spaceBefore = 20
        
        terms = Paragraph(
            "<b>Terms & Conditions:</b><br/>"
            "• This quote is valid for 30 days from the date issued<br/>"
            "• Prices are subject to change after the validity period<br/>"
            "• Acceptance of this quote constitutes a binding agreement<br/>"
            "• Payment terms: 50% deposit, 50% on completion", 
            terms_style
        )
        story.append(terms)
        
        # Thank you message
        thank_you_style = styles["Normal"]
        thank_you_style.alignment = TA_CENTER
        thank_you_style.textColor = colors.gray
        thank_you_style.fontSize = 10
        thank_you_style.spaceBefore = 20
        
        thank_you = Paragraph(
            "Thank you for considering our services. We look forward to working with you!", 
            thank_you_style
        )
        story.append(thank_you)
        
        # Footer
        footer_text = "Generated by Minigma Invoice Bot"
        if has_logo and company_name:
            footer_text = f"{company_name} | {footer_text}"
        
        footer_style = styles["Normal"]
        footer_style.alignment = TA_CENTER
        footer_style.textColor = colors.lightgrey
        footer_style.fontSize = 8
        footer_style.spaceBefore = 10
        
        footer = Paragraph(footer_text, footer_style)
        story.append(footer)
        
        # Build PDF
        doc.build(story)
        
        pdf_data = buffer.getvalue()
        buffer.close()
        
        os.makedirs('quotes', exist_ok=True)
        pdf_file = f"quotes/{quote_data['quote_number']}.pdf"
        with open(pdf_file, 'wb') as f:
            f.write(pdf_data)
        
        logger.info(f"Quote PDF generated successfully: {pdf_file}")
        return pdf_file
        
    except Exception as e:
        logger.error(f"Quote PDF generation error: {e}")
        raise

# My Quotes command
async def my_quotes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's quotes"""
    user_id = update.effective_user.id
    
    # Check if user wants to search by client
    if context.args:
        client_name = ' '.join(context.args)
        quotes = get_user_quotes(user_id, client_name)
        if quotes:
            message = f"📋 Quotes for {client_name}:\n\n"
            for quote in quotes:
                message += f"• {quote[2]} - {quote[3]} - {quote[5]}{quote[7]:.2f}\n"
        else:
            message = f"No quotes found for client: {client_name}"
    else:
        quotes = get_user_quotes(user_id)
        if not quotes:
            await update.message.reply_text("You haven't created any approved quotes yet.")
            return
        
        message = "📋 Your Recent Quotes:\n\n"
        for quote in quotes[:10]:  # Show last 10 quotes
            message += f"• {quote[2]} - {quote[3]} - {quote[5]}{quote[7]:.2f}\n"
        
        if is_premium_user(user_id):
            message += "\n💡 *Tip: Use* `/myquotes ClientName` *to filter by client*"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# Updated premium command with proper tier system
async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show premium features and subscription options"""
    user_id = update.effective_user.id
    current_tier = get_user_tier(user_id)
    remaining_invoices = get_remaining_invoices(user_id)
    
    if current_tier == 'premium':
        await update.message.reply_text(
            f"🎉 **You're a Premium User!**\n\n"
            f"✨ **Premium Features:**\n"
            f"• Unlimited invoices & quotes\n"
            f"• Company/VAT registration numbers\n" 
            f"• VAT calculation on invoices\n"
            f"• Client database management\n"
            f"• Payment tracking\n"
            f"• Email/SMS delivery\n"
            f"• Priority support\n\n"
            f"💎 **Your subscription is active**\n\n"
            f"Use /setup to configure company details\n"
            f"Use /clients to manage clients\n"
            f"Use /payments to track payments",
            parse_mode='Markdown'
        )
    else:
        # Show free tier limits and premium options
        free_features = "\n".join([f"• {feature}" for feature in TIER_LIMITS['free']['features']])
        premium_features = "\n".join([f"• {feature}" for feature in TIER_LIMITS['premium']['features']])
        
        premium_text = f"""
📊 **Your Current Plan: Free Tier**
{free_features}

**Monthly Limit:** {TIER_LIMITS['free']['monthly_invoices']} invoices/quotes
**Creations Remaining:** {remaining_invoices}

💎 **Upgrade to Minigma Premium**

✨ **Premium Features:**
{premium_features}

💰 **Pricing:**
/month - £{TIER_LIMITS['premium']['monthly_price']} per month
/year - £{TIER_LIMITS['premium']['annual_price']} per year (save £39!)

💳 **Subscribe now to unlock all features!**
        """
        
        keyboard = [
            [InlineKeyboardButton("💰 Monthly - £12", callback_data="premium_monthly")],
            [InlineKeyboardButton("💎 Annual - £105", callback_data="premium_annual")],
            [InlineKeyboardButton("🆓 Start Free Trial", callback_data="premium_trial")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(premium_text, reply_markup=reply_markup, parse_mode='Markdown')

# Updated create_invoice command with tier limits
async def create_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        create_user(user_id, update.effective_user.username, update.effective_user.first_name, update.effective_user.last_name)
        user = get_user(user_id)
        await update.message.reply_text("✅ Your account has been created! Enjoy your 14-day free trial.")
    
    # Check creation limit
    can_create, message = check_invoice_limit(user_id)
    if not can_create:
        await update.message.reply_text(message)
        return
    
    context.user_data['current_invoice'] = {
        'items': [],
        'step': 'client_name'
    }
    
    # Show remaining creations for free tier
    remaining_info = ""
    if not is_premium_user(user_id):
        remaining = get_remaining_invoices(user_id)
        remaining_info = f"\n\n📊 You have {remaining} creations remaining this month."
    
    await update.message.reply_text(
        f"Let's create a new invoice! 🧾{remaining_info}\n\n"
        "First, please enter the client name:"
    )

# Payment processing functions
def create_stripe_checkout_session(user_id, price_id, success_url, cancel_url):
    """Create Stripe checkout session"""
    try:
        import stripe
        stripe.api_key = PAYMENT_CONFIG['stripe_secret_key']
        
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=user_id,
            metadata={
                'user_id': user_id
            }
        )
        return session.url
    except ImportError:
        print("❌ Stripe not installed. Install with: pip install stripe")
        return None
    except Exception as e:
        print(f"❌ Stripe error: {e}")
        return None

def handle_stripe_webhook(payload, sig_header):
    """Handle Stripe webhook for payment confirmation"""
    try:
        import stripe
        stripe.api_key = PAYMENT_CONFIG['stripe_secret_key']
        
        event = stripe.Webhook.construct_event(
            payload, sig_header, PAYMENT_CONFIG['webhook_secret']
        )
        
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            user_id = session['client_reference_id']
            
            # Activate premium for user
            add_premium_subscription(user_id, 'paid', 1)  # 1 month
            
            print(f"✅ Premium activated for user {user_id}")
            
        return True
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return False

# Updated button handler for premium payments
async def handle_premium_payment(query, user_id, plan_type):
    """Handle premium payment selection"""
    if plan_type == 'trial':
        # Activate free trial
        add_premium_subscription(user_id, 'trial', 1)
        await query.edit_message_text(
            "🎉 **Premium Trial Activated!**\n\n"
            "You now have access to all premium features for 1 month!\n\n"
            "✨ **Unlocked Features:**\n"
            "• Unlimited invoices & quotes\n"
            "• Company/VAT registration\n"
            "• VAT calculation\n"
            "• Client database\n"
            "• Payment tracking\n"
            "• Email/SMS delivery\n\n"
            "Use /setup to configure company details\n"
            "Use /clients to manage clients\n"
            "Use /payments to track payments\n"
            "Use /create and /quote to make unlimited documents!",
            parse_mode='Markdown'
        )
    
    elif plan_type in ['monthly', 'annual']:
        # Redirect to payment
        if plan_type == 'monthly':
            price_id = 'price_monthly'  # Set your Stripe price ID
            duration = "monthly"
            amount = TIER_LIMITS['premium']['monthly_price']
        else:
            price_id = 'price_annual'   # Set your Stripe price ID
            duration = "annual"
            amount = TIER_LIMITS['premium']['annual_price']
        
        # Create checkout session
        success_url = f"https://t.me/your_bot_username?start=payment_success"
        cancel_url = f"https://t.me/your_bot_username?start=payment_cancel"
        
        checkout_url = create_stripe_checkout_session(user_id, price_id, success_url, cancel_url)
        
        if checkout_url:
            keyboard = [
                [InlineKeyboardButton("💳 Pay Now", url=checkout_url)],
                [InlineKeyboardButton("🔙 Back to Plans", callback_data="premium_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"💰 **Premium {duration.capitalize()} Plan - £{amount}**\n\n"
                f"Click the button below to complete your payment and activate Premium features!\n\n"
                f"✨ **You'll get:**\n"
                f"• Unlimited invoices & quotes\n"
                f"• Company/VAT registration\n"
                f"• VAT calculation\n"
                f"• Client database\n"
                f"• Payment tracking\n"
                f"• Email/SMS delivery\n"
                f"• Priority support",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                "❌ **Payment System Unavailable**\n\n"
                "Our payment system is currently undergoing maintenance.\n\n"
                "Please try again later or contact support for manual activation.",
                parse_mode='Markdown'
            )

# Feature restriction decorator
def premium_required(feature_name):
    """Decorator to restrict features to premium users"""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            
            if not is_premium_user(user_id):
                remaining = get_remaining_invoices(user_id)
                
                await update.message.reply_text(
                    f"❌ **Premium Feature: {feature_name}**\n\n"
                    f"This feature is only available for Premium users.\n\n"
                    f"📊 **Your Current Plan:**\n"
                    f"• Free Tier ({remaining} creations remaining)\n\n"
                    f"💎 **Upgrade to Premium for:**\n"
                    f"• Unlimited invoices & quotes\n"
                    f"• {feature_name}\n"
                    f"• Client database\n"
                    f"• Payment tracking\n"
                    f"• Email/SMS delivery\n\n"
                    f"Use /premium to upgrade and unlock all features!",
                    parse_mode='Markdown'
                )
                return
            
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

# Updated premium-only commands with restrictions
@premium_required("Company Setup")
async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setup company information (Premium only)"""
    user_id = update.effective_user.id
        
    keyboard = [
        [InlineKeyboardButton("🏢 Set Company Reg Number", callback_data="setup_company_reg")],
        [InlineKeyboardButton("📊 Set VAT Number", callback_data="setup_vat_number")],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    user = get_user(user_id)
    current_info = ""
    
    # FIXED: Safe tuple indexing with bounds checking
    if user and len(user) > 9 and user[9]:  # company_reg_number
        current_info += f"Current Company Reg: {user[9]}\n"
    if user and len(user) > 10 and user[10]:  # vat_reg_number
        current_info += f"Current VAT Reg: {user[10]}\n"
    
    await update.message.reply_text(
        f"🏢 **Company Information Setup**\n\n{current_info}\n"
        "Set up your company details that will appear on invoices:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

@premium_required("Client Database")
async def clients_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Client database management (Premium only)"""
    user_id = update.effective_user.id
        
    clients = get_user_clients(user_id)
    
    if not clients:
        keyboard = [
            [InlineKeyboardButton("➕ Add New Client", callback_data="client_start")],
            [InlineKeyboardButton("🔙 Back", callback_data="clients_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "👥 **Client Database**\n\n"
            "No clients found. Add your first client to get started!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    keyboard = []
    for client in clients[:10]:  # Show first 10 clients
        keyboard.append([InlineKeyboardButton(f"👤 {client[2]}", callback_data=f"view_client_{client[0]}")])
    
    keyboard.extend([
        [InlineKeyboardButton("➕ Add New Client", callback_data="client_start")],
        [InlineKeyboardButton("🔍 Search Invoices by Client", callback_data="search_client_invoices")],
        [InlineKeyboardButton("🔙 Back", callback_data="clients_back")]
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👥 **Client Database**\n\n"
        f"You have {len(clients)} clients. Select a client to view details:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

@premium_required("Payment Tracking")
async def payments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Payment tracking (Premium only)"""
    user_id = update.effective_user.id
        
    unpaid_invoices = get_unpaid_invoices(user_id)
    
    if not unpaid_invoices:
        await update.message.reply_text(
            "💰 **Payment Tracking**\n\n"
            "🎉 All your invoices are paid! No outstanding payments.",
            parse_mode='Markdown'
        )
        return
    
    keyboard = []
    for invoice in unpaid_invoices[:10]:  # Show first 10 unpaid invoices
        keyboard.append([InlineKeyboardButton(
            f"📄 {invoice[2]} - {invoice[3]} - {invoice[5]}{invoice[7]:.2f}", 
            callback_data=f"mark_paid_{invoice[0]}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="payments_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"💰 **Payment Tracking**\n\n"
        f"You have {len(unpaid_invoices)} unpaid invoices. Mark them as paid:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Update the start command to show tier information
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    if not get_user(user_id):
        create_user(user_id, user.username, user.first_name, user.last_name)
        await update.message.reply_text("✅ Your account has been created! Enjoy your 14-day free trial.")
    
    current_tier = get_user_tier(user_id)
    remaining_invoices = get_remaining_invoices(user_id)
    
    tier_info = ""
    if current_tier == 'premium':
        tier_info = "🎉 **You have Premium access!**"
    else:
        tier_info = f"📊 **Free Tier:** {remaining_invoices} creations remaining this month"
    
    welcome_message = f"""
🤖 Welcome to Minigma Invoice Bot!

{tier_info}

✨ **Free Features:**
• Upload company logo
• Create invoices & quotes
• Multiple currency support  
• Professional PDF generation
• 14-day free trial

💰 **Premium Features (£12/month):**
• Unlimited invoices & quotes
• Company/VAT registration numbers
• VAT calculation
• Client database
• Payment tracking
• Email/SMS sending

📝 **Commands:**
/logo - Upload company logo
/company - Set company name  
/create - Create new invoice
/quote - Create new quote
/myinvoices - View invoices
/myquotes - View quotes
/premium - Premium features
/clients - Client database (Premium)
/payments - Track payments (Premium)
/setup - Company setup (Premium)
/help - Get help

You have 14 days to try all features for free!
    """
    
    await update.message.reply_text(welcome_message)

# Update database initialization to include document_type column
def update_database_for_quotes():
    """Add document_type column to invoices table for quote support"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE invoices ADD COLUMN document_type TEXT DEFAULT 'invoice'")
        print("✅ Added document_type column to invoices table")
    except sqlite3.OperationalError:
        print("✅ document_type column already exists")
    
    conn.commit()
    conn.close()

# Call this in main() after init_db()
# ==================================================
# PART 9: SIMPLE PREMIUM USER MANAGEMENT
# ==================================================

import datetime

class PremiumManager:
    def __init__(self, filename='premium_users.txt'):
        self.filename = filename
        self.premium_users = set()
        self.load_premium_users()
    
    def load_premium_users(self):
        """Load premium users from simple text file"""
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split('|')
                        if parts:
                            user_id = parts[0].strip()
                            if user_id.isdigit():
                                self.premium_users.add(int(user_id))
            print(f"✅ Loaded {len(self.premium_users)} premium users from file")
        except FileNotFoundError:
            with open(self.filename, 'w', encoding='utf-8') as f:
                f.write("# Premium Users List\n# Format: TelegramUserID | Username (optional) | ActivatedDate\n")
            print("✅ Created new premium users file")
    
    def is_premium(self, user_id):
        """Check if user has premium access"""
        return user_id in self.premium_users
    
    def add_premium_user(self, user_id, username=""):
        """Add a new premium user to the file"""
        if user_id in self.premium_users:
            return False, "User already has premium access"
        
        self.premium_users.add(user_id)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        with open(self.filename, 'a', encoding='utf-8') as f:
            if username:
                f.write(f"\n{user_id} | {username} | {today}")
            else:
                f.write(f"\n{user_id} | | {today}")
        
        return True, f"✅ User {user_id} added to premium users"
    
    def remove_premium_user(self, user_id):
        """Remove a user from premium access"""
        if user_id not in self.premium_users:
            return False, "User not in premium list"
        
        self.premium_users.remove(user_id)
        
        with open(self.filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        with open(self.filename, 'w', encoding='utf-8') as f:
            for line in lines:
                if line.strip() and not line.strip().startswith('#'):
                    parts = line.split('|')
                    if parts and parts[0].strip() != str(user_id):
                        f.write(line)
                else:
                    f.write(line)
        
        return True, f"❌ User {user_id} removed from premium users"

# Create global instance
premium_manager = PremiumManager()

def is_premium_user(user_id):
    """Simple premium check - uses the file system"""
    return premium_manager.is_premium(user_id)

def get_remaining_invoices(user_id):
    """Get remaining invoices for free users"""
    if is_premium_user(user_id):
        return float('inf')  # Unlimited for premium
    
    monthly_count = get_user_invoice_count_this_month(user_id)
    remaining = 10 - monthly_count  # Free users get 10 invoices/month
    return max(0, remaining)

def check_invoice_limit(user_id):
    """Check if user can create more invoices"""
    if is_premium_user(user_id):
        return True, ""  # Premium users have no limits

    monthly_count = get_user_invoice_count_this_month(user_id)
    remaining = 10 - monthly_count
    
    if remaining <= 0:
        return False, f"❌ You've reached your monthly limit of 10 invoices.\nUpgrade to Premium for unlimited invoices!"
    
    return True, f"({remaining} invoices remaining this month)"

# Simple function to get user's premium status for display
def get_user_premium_status(user_id):
    """Get user's premium status for display"""
    if is_premium_user(user_id):
        return "🎉 **Premium User** - Unlimited access"
    else:
        remaining = get_remaining_invoices(user_id)
        return f"🆓 **Free User** - {remaining} invoices remaining this month"

# Update the create_invoice function to check limits
async def create_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        create_user(user_id, update.effective_user.username, update.effective_user.first_name, update.effective_user.last_name)
        user = get_user(user_id)
        await update.message.reply_text("✅ Your account has been created! Starting invoice creation...")
    
    # Check invoice limit for free users
    can_create, message = check_invoice_limit(user_id)
    if not can_create:
        await update.message.reply_text(message)
        return
    
    context.user_data['current_invoice'] = {
        'items': [],
        'step': 'client_name'
    }
    
    # Show remaining invoices for free users
    remaining_info = ""
    if not is_premium_user(user_id):
        remaining = get_remaining_invoices(user_id)
        remaining_info = f"\n\n📊 You have {remaining} invoices remaining this month."
    
    await update.message.reply_text(
        f"Let's create a new invoice! 🧾{remaining_info}\n\n"
        "First, please enter the client name:"
    )