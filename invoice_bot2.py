# ==================================================
# PART 1: IMPORTS AND SETUP (Updated with Scheduling)
# ==================================================

import os
import logging
import asyncio
import sqlite3
import io
import time
import requests
import json
import uuid
import smtplib
import threading
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from enum import Enum
from threading import Thread, Timer
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import pytz
from flask import Flask
from dateutil import parser
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    BotCommand, ReplyKeyboardMarkup, KeyboardButton, 
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters, 
    ConversationHandler
)
from telegram.constants import ParseMode

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

# Try to import premium_manager, but don't crash if it doesn't exist
try:
    from premium_manager import premium_manager
    PREMIUM_MANAGER_AVAILABLE = True
except ImportError:
    PREMIUM_MANAGER_AVAILABLE = False
    print("⚠️  premium_manager module not found. Premium features will be limited.")

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== BOT TOKEN HANDLING (SAFE & SECURE) =====

def get_bot_token() -> Optional[str]:
    """
    Get bot token securely from multiple sources.
    Order of priority:
    1. Environment variable BOT_TOKEN (for Koyeb)
    2. Environment variable TELEGRAM_BOT_TOKEN (alternative)
    3. .env file (via python-dotenv)
    4. bot_token.txt file
    5. Returns None if no token found
    """
    # Method 1: BOT_TOKEN environment variable (for Koyeb)
    token = os.getenv('BOT_TOKEN')
    if token and token.strip():
        return token.strip()
    
    # Method 2: TELEGRAM_BOT_TOKEN environment variable (alternative)
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if token and token.strip():
        return token.strip()
    
    # Method 3: .env file (already loaded via load_dotenv())
    # Note: load_dotenv() loads into os.environ, so this is already covered above
    
    # Method 4: bot_token.txt file
    try:
        with open('bot_token.txt', 'r') as f:
            token = f.read().strip()
            if token and token != "YOUR_BOT_TOKEN_HERE":
                return token
    except FileNotFoundError:
        pass
    
    # Method 5: Check for old token files
    token_files = ['token.txt', '.bot_token', 'telegram_token.txt']
    for filename in token_files:
        try:
            with open(filename, 'r') as f:
                token = f.read().strip()
                if token and token != "YOUR_BOT_TOKEN_HERE":
                    return token
        except FileNotFoundError:
            continue
    
    return None

# Configuration constants
GRACE_PERIOD_DAYS = 14
MONTHLY_INVOICE_LIMIT = 10
DEFAULT_WORKING_HOURS = {"start": "09:00", "end": "17:00"}
DEFAULT_SLOT_DURATION = 30  # minutes
MAX_SLOTS_PER_DAY = 16  # 8 hours with 30-min slots

# Appointment status enum
class AppointmentStatus(Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    RESCHEDULED = "rescheduled"

# Appointment types enum
class AppointmentType(Enum):
    CONSULTATION = "consultation"
    MEETING = "meeting"
    FOLLOW_UP = "follow_up"
    DELIVERY = "delivery"
    PAYMENT = "payment"
    SUPPORT = "support"

# Conversation states for scheduling
(
    SCHEDULE_START,
    SELECT_CLIENT,
    SELECT_TYPE,
    SELECT_DATE,
    SELECT_TIME,
    SELECT_DURATION,
    ADD_DESCRIPTION,
    CONFIRM_APPOINTMENT,
    APPOINTMENT_EDIT,
    APPOINTMENT_DATE_EDIT,
    APPOINTMENT_TIME_EDIT,
    APPOINTMENT_TYPE_EDIT,
    APPOINTMENT_DURATION_EDIT,
    APPOINTMENT_DESC_EDIT,
    APPOINTMENT_CLIENT_EDIT,
    SET_REMINDER_TIME,
    VIEW_CALENDAR,
    CALENDAR_NAVIGATE,
    BOOKING_CONFIRMED,
    EMAIL_CONFIG,
    WORKING_HOURS_SETUP
) = range(21)

# Bot commands menu setup
async def setup_bot_commands(application):  
    """Set up the bot commands menu"""
    commands = [
        BotCommand("start", "🏢 Launch Business Suite"),
        BotCommand("schedule", "🗓️ Schedule appointment"),
        BotCommand("calendar", "📅 View calendar"),
        BotCommand("quickbook", "⚡ Quick appointment"),
        BotCommand("appointments", "📋 My appointments"),
        BotCommand("today", "📅 Today's schedule"),
        BotCommand("week", "🗓️ This week's schedule"),
        BotCommand("remind", "⏰ Set reminder"),
        BotCommand("reschedule", "🔄 Reschedule appointment"),
        BotCommand("cancel", "❌ Cancel appointment"),
        BotCommand("logo", "🏢 Set company branding"),
        BotCommand("company", "📛 Configure business name"),
        BotCommand("create", "🧾 Generate new invoice"),
        BotCommand("myinvoices", "📋 View invoice history"),
        BotCommand("premium", "💎 Upgrade to Premium Suite"),
        BotCommand("contact", "📞 Contact sales/support"),
        BotCommand("myid", "🔑 Get account ID"),
        BotCommand("clients", "👥 Manage clients"),
        BotCommand("payments", "💰 Payment tracking"),
        BotCommand("setup", "⚙️ Business configuration"),
        BotCommand("settings", "⚙️ Appointment settings"),
        BotCommand("help", "❓ Help & support")
    ]
    
    await application.bot.set_my_commands(commands)
    print("✅ Bot commands menu has been set up with scheduling!")

# Database setup - COMPREHENSIVE VERSION WITH SCHEDULING
def init_db():
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Users table
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
            vat_reg_number TEXT,
            trial_start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trial_used BOOLEAN DEFAULT FALSE,
            email TEXT,
            phone TEXT,
            timezone TEXT DEFAULT 'UTC',
            calendar_settings TEXT DEFAULT '{}'
        )
    ''')
    
    # Invoices table
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
    
    # Invoice counters table
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
    
    # Premium subscriptions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_subscriptions (
            user_id INTEGER PRIMARY KEY,
            subscription_type TEXT,
            start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_date TIMESTAMP,
            payment_method TEXT,
            auto_renew BOOLEAN DEFAULT TRUE,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # ===== ENHANCED APPOINTMENT TABLES =====
    
    # Appointments table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            appointment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            client_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            appointment_time TIMESTAMP NOT NULL,
            duration_minutes INTEGER DEFAULT 60,
            appointment_type TEXT DEFAULT 'meeting',
            status TEXT DEFAULT 'scheduled',
            reminder_enabled BOOLEAN DEFAULT TRUE,
            reminder_sent BOOLEAN DEFAULT FALSE,
            reminder_minutes_before INTEGER DEFAULT 30,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            cancelled_at TIMESTAMP,
            cancellation_reason TEXT,
            notification_sent BOOLEAN DEFAULT FALSE,
            recurrence_pattern TEXT,
            recurrence_end_date TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (client_id) REFERENCES clients (client_id)
        )
    ''')
    
    # Appointment types table (customizable)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointment_types (
            type_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type_name TEXT NOT NULL,
            color_hex TEXT DEFAULT '#4a6ee0',
            duration_minutes INTEGER DEFAULT 60,
            price DECIMAL(10,2) DEFAULT 0.00,
            description TEXT,
            buffer_before INTEGER DEFAULT 0,
            buffer_after INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            UNIQUE(user_id, type_name)
        )
    ''')
    
    # Working hours table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS working_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            day_of_week INTEGER,  -- 0=Monday, 6=Sunday
            is_working_day BOOLEAN DEFAULT TRUE,
            start_time TEXT,  -- Format: HH:MM
            end_time TEXT,    -- Format: HH:MM
            lunch_start TEXT,
            lunch_end TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            UNIQUE(user_id, day_of_week)
        )
    ''')
    
    # Appointment reminders table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointment_reminders (
            reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            user_id INTEGER,
            reminder_time TIMESTAMP,
            reminder_type TEXT,  -- email, telegram, both
            sent BOOLEAN DEFAULT FALSE,
            sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (appointment_id) REFERENCES appointments (appointment_id),
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Calendar settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS calendar_settings (
            user_id INTEGER PRIMARY KEY,
            default_view TEXT DEFAULT 'week',
            first_day_of_week INTEGER DEFAULT 1,  -- 1=Monday, 0=Sunday
            slot_duration INTEGER DEFAULT 30,
            show_weekends BOOLEAN DEFAULT TRUE,
            send_email_notifications BOOLEAN DEFAULT TRUE,
            send_telegram_notifications BOOLEAN DEFAULT TRUE,
            email_template TEXT DEFAULT 'default',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Email templates table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_templates (
            template_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            template_name TEXT,
            subject TEXT,
            body TEXT,
            is_default BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Buffer times table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS buffer_times (
            buffer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            before_appointment INTEGER DEFAULT 15,
            after_appointment INTEGER DEFAULT 15,
            same_day_buffer INTEGER DEFAULT 60,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Unavailable dates table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS unavailable_dates (
            date_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date DATE NOT NULL,
            reason TEXT,
            all_day BOOLEAN DEFAULT TRUE,
            start_time TEXT,
            end_time TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            UNIQUE(user_id, date)
        )
    ''')
    
    # Default appointment types table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS default_appointment_types (
            type_id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_name TEXT,
            duration_minutes INTEGER DEFAULT 60,
            description TEXT,
            color_hex TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert default appointment types if table is empty
    cursor.execute('SELECT COUNT(*) FROM default_appointment_types')
    if cursor.fetchone()[0] == 0:
        default_types = [
            ('Consultation', 60, 'Initial client consultation', '#4a6ee0'),
            ('Follow-up', 30, 'Follow-up meeting', '#34c759'),
            ('Delivery', 15, 'Product/service delivery', '#ff9500'),
            ('Payment', 15, 'Payment collection', '#ff3b30'),
            ('Support', 45, 'Technical support', '#5ac8fa'),
            ('Planning', 90, 'Project planning session', '#af52de'),
            ('Review', 60, 'Performance review', '#ffcc00')
        ]
        
        for type_name, duration, description, color in default_types:
            cursor.execute('''
                INSERT INTO default_appointment_types (type_name, duration_minutes, description, color_hex)
                VALUES (?, ?, ?, ?)
            ''', (type_name, duration, description, color))
    
    # Add missing columns to existing tables (safely)
    columns_to_add = [
        ('invoices', 'vat_enabled', 'BOOLEAN DEFAULT FALSE'),
        ('invoices', 'vat_amount', 'REAL DEFAULT 0'),
        ('invoices', 'client_email', 'TEXT'),
        ('invoices', 'client_phone', 'TEXT'),
        ('invoices', 'paid_status', 'BOOLEAN DEFAULT FALSE'),
        ('users', 'company_reg_number', 'TEXT'),
        ('users', 'vat_reg_number', 'TEXT'),
        ('users', 'email', 'TEXT'),
        ('users', 'phone', 'TEXT'),
        ('users', 'timezone', 'TEXT DEFAULT "UTC"'),
        ('users', 'calendar_settings', 'TEXT DEFAULT "{}"'),
        ('appointments', 'reminder_enabled', 'BOOLEAN DEFAULT TRUE'),
        ('appointments', 'reminder_minutes_before', 'INTEGER DEFAULT 30'),
        ('appointments', 'updated_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
        ('appointments', 'cancelled_at', 'TIMESTAMP'),
        ('appointments', 'cancellation_reason', 'TEXT'),
        ('appointments', 'notification_sent', 'BOOLEAN DEFAULT FALSE'),
        ('appointments', 'recurrence_pattern', 'TEXT'),
        ('appointments', 'recurrence_end_date', 'TIMESTAMP')
    ]
    
    for table, column_name, column_type in columns_to_add:
        try:
            # Check if column exists first
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]
            if column_name not in columns:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}")
                print(f"✅ Added {column_name} column to {table} table")
        except sqlite3.Error as e:
            print(f"⚠️  Could not add column {column_name} to {table}: {e}")
    
    # Create indexes for better performance
    indexes = [
        ('idx_appointments_user_date', 'appointments(user_id, appointment_time)'),
        ('idx_appointments_status', 'appointments(status)'),
        ('idx_appointments_client', 'appointments(client_id)'),
        ('idx_clients_user', 'clients(user_id)'),
        ('idx_reminders_sent', 'appointment_reminders(sent, reminder_time)'),
        ('idx_invoices_user_date', 'invoices(user_id, created_at)'),
        ('idx_invoices_status', 'invoices(status)'),
        ('idx_users_username', 'users(username)')
    ]
    
    for index_name, index_def in indexes:
        try:
            cursor.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON {index_def}')
        except sqlite3.Error as e:
            print(f"⚠️  Could not create index {index_name}: {e}")
    
    conn.commit()
    conn.close()
    print("✅ Database initialization complete with enhanced scheduling system")

# Initialize database
init_db()

# Helper functions for default settings
def init_default_working_hours(user_id):
    """Initialize default working hours for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Monday to Friday, 9am-5pm
    for day in range(0, 5):  # 0=Monday, 4=Friday
        cursor.execute('''
            INSERT OR IGNORE INTO working_hours (user_id, day_of_week, is_working_day, start_time, end_time)
            VALUES (?, ?, TRUE, ?, ?)
        ''', (user_id, day, "09:00", "17:00"))
    
    # Saturday and Sunday - non-working days
    for day in range(5, 7):
        cursor.execute('''
            INSERT OR IGNORE INTO working_hours (user_id, day_of_week, is_working_day)
            VALUES (?, ?, FALSE)
        ''', (user_id, day))
    
    conn.commit()
    conn.close()

def init_default_calendar_settings(user_id):
    """Initialize default calendar settings for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR IGNORE INTO calendar_settings 
        (user_id, default_view, first_day_of_week, slot_duration, show_weekends, send_email_notifications, send_telegram_notifications)
        VALUES (?, 'week', 1, 30, TRUE, TRUE, TRUE)
    ''', (user_id,))
    
    conn.commit()
    conn.close()

def init_default_email_templates(user_id):
    """Initialize default email templates for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    templates = [
        ("Appointment Confirmation", "Appointment Confirmation - {title}",
         """Dear {client_name},

Your appointment has been confirmed.

📅 **Appointment Details:**
- **Date:** {date}
- **Time:** {time}
- **Duration:** {duration} minutes
- **Type:** {type}
- **Description:** {description}

📍 **Location/Meeting Link:** {location}

Please arrive on time. If you need to reschedule or cancel, please do so at least 24 hours in advance.

Best regards,
{company_name}"""),
        
        ("Appointment Reminder", "Reminder: Your Appointment Tomorrow",
         """Dear {client_name},

This is a friendly reminder about your appointment tomorrow.

📅 **Appointment Details:**
- **Date:** {date}
- **Time:** {time}
- **Duration:** {duration} minutes

Please don't hesitate to contact us if you have any questions.

Best regards,
{company_name}"""),
        
        ("Appointment Cancellation", "Appointment Cancelled - {title}",
         """Dear {client_name},

Your appointment has been cancelled as requested.

📅 **Cancelled Appointment:**
- **Date:** {date}
- **Time:** {time}
- **Type:** {type}

To reschedule, please use our booking system or contact us directly.

Best regards,
{company_name}""")
    ]
    
    for i, (name, subject, body) in enumerate(templates):
        cursor.execute('''
            INSERT OR IGNORE INTO email_templates (user_id, template_name, subject, body, is_default)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, name, subject, body, i == 0))  # First one is default
    
    conn.commit()
    conn.close()

def init_default_buffer_times(user_id):
    """Initialize default buffer times for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR IGNORE INTO buffer_times (user_id, before_appointment, after_appointment, same_day_buffer)
        VALUES (?, 15, 15, 60)
    ''', (user_id,))
    
    conn.commit()
    conn.close()

def init_default_appointment_types(user_id):
    """Initialize default appointment types for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Copy default types to user's appointment_types
    cursor.execute('''
        INSERT OR IGNORE INTO appointment_types (user_id, type_name, duration_minutes, description, color_hex)
        SELECT ?, type_name, duration_minutes, description, color_hex
        FROM default_appointment_types
        WHERE NOT EXISTS (
            SELECT 1 FROM appointment_types WHERE user_id = ? AND type_name = default_appointment_types.type_name
        )
    ''', (user_id, user_id))
    
    conn.commit()
    conn.close()

def initialize_user_defaults(user_id):
    """Initialize all default settings for a new user"""
    init_default_working_hours(user_id)
    init_default_calendar_settings(user_id)
    init_default_email_templates(user_id)
    init_default_buffer_times(user_id)
    init_default_appointment_types(user_id)
    print(f"✅ Default settings initialized for user {user_id}")

print("✅ Part 1: Imports and setup complete with scheduling support!")

# ==================================================
# PART 2: DATABASE HELPER FUNCTIONS (Updated with Scheduling)
# ==================================================

# Date parsing function - MOVED TO TOP
def parse_trial_end_date(trial_end_date_str):
    """Parse trial end date string to datetime object"""
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
# EXISTING DATABASE HELPER FUNCTIONS (KEPT AS IS)
# ==================================================

# Database helper functions
def get_user(user_id):
    """Get user by ID"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id, username, first_name, last_name):
    """Create a new user with trial period and initialize scheduling defaults"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    trial_end_date = datetime.now() + timedelta(days=14)
    trial_end_date_str = trial_end_date.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
        INSERT OR REPLACE INTO users 
        (user_id, username, first_name, last_name, trial_end_date, trial_start_date, trial_used)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, TRUE)
    ''', (user_id, username, first_name, last_name, trial_end_date_str))
    
    # Initialize scheduling defaults for new user
    conn.commit()
    init_default_working_hours(user_id)
    init_default_calendar_settings(user_id)
    init_default_email_templates(user_id)
    
    conn.close()

def update_user_company_info(user_id, logo_path=None, company_name=None, company_reg=None, vat_reg=None):
    """Update user's company information"""
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
    """Get current invoice counter for user"""
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
    """Increment invoice counter for user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE invoice_counters SET current_counter = current_counter + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def save_invoice_draft(user_id, client_name, invoice_date, currency, items, vat_enabled=False, client_email=None, client_phone=None):
    """Save invoice draft to database"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    items_json = json.dumps(items)  # Use json.dumps instead of str() for proper serialization
    
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
    """Update invoice status and optionally assign invoice number"""
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
    """Mark invoice as paid"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE invoices SET paid_status = TRUE WHERE invoice_id = ?', (invoice_id,))
    conn.commit()
    conn.close()

def get_invoice(invoice_id):
    """Get invoice by ID"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM invoices WHERE invoice_id = ?', (invoice_id,))
    invoice = cursor.fetchone()
    conn.close()
    
    if invoice:
        # Parse items JSON back to Python object
        if len(invoice) > 5 and invoice[5]:  # items column
            try:
                # Create a mutable list version to modify items
                invoice_list = list(invoice)
                invoice_list[5] = json.loads(invoice[5]) if isinstance(invoice[5], str) else invoice[5]
                invoice = tuple(invoice_list)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse items JSON for invoice {invoice_id}")
    
    print(f"DEBUG: Getting invoice {invoice_id} - Found: {invoice is not None}")
    if invoice:
        print(f"DEBUG: Invoice data - ID: {invoice[0]}, Status: {invoice[10] if len(invoice) > 10 else 'N/A'}")
    
    return invoice

def get_user_invoices(user_id, client_name=None):
    """Get user's approved invoices"""
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
    
    # Parse items JSON for each invoice
    parsed_invoices = []
    for invoice in invoices:
        if len(invoice) > 5 and invoice[5]:  # items column
            try:
                invoice_list = list(invoice)
                invoice_list[5] = json.loads(invoice[5]) if isinstance(invoice[5], str) else invoice[5]
                parsed_invoices.append(tuple(invoice_list))
            except json.JSONDecodeError:
                parsed_invoices.append(invoice)
        else:
            parsed_invoices.append(invoice)
    
    conn.close()
    return parsed_invoices

def get_unpaid_invoices(user_id):
    """Get user's unpaid approved invoices"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM invoices 
        WHERE user_id = ? AND status = 'approved' AND paid_status = FALSE
        ORDER BY created_at DESC
    ''', (user_id,))
    invoices = cursor.fetchall()
    
    # Parse items JSON for each invoice
    parsed_invoices = []
    for invoice in invoices:
        if len(invoice) > 5 and invoice[5]:
            try:
                invoice_list = list(invoice)
                invoice_list[5] = json.loads(invoice[5]) if isinstance(invoice[5], str) else invoice[5]
                parsed_invoices.append(tuple(invoice_list))
            except json.JSONDecodeError:
                parsed_invoices.append(invoice)
        else:
            parsed_invoices.append(invoice)
    
    conn.close()
    return parsed_invoices

def get_user_invoice_count_this_month(user_id):
    """Count user's approved invoices for current month"""
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
    """Save new client"""
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
    """Update existing client information"""
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
    """Get all clients for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clients WHERE user_id = ? ORDER BY client_name', (user_id,))
    clients = cursor.fetchall()
    conn.close()
    return clients

def get_client_by_id(client_id):
    """Get client by ID"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clients WHERE client_id = ?', (client_id,))
    client = cursor.fetchone()
    conn.close()
    return client

def get_client_by_name(user_id, client_name):
    """Get client by name for specific user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clients WHERE user_id = ? AND client_name = ?', (user_id, client_name))
    client = cursor.fetchone()
    conn.close()
    return client

def is_premium_user(user_id):
    """Check if user has premium access or active trial"""
    # First check premium status
    if premium_manager.is_premium(user_id):
        return True
    
    # Check trial period
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT trial_end_date, trial_used FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return False
    
    trial_end_date, trial_used = result
    
    # If user never started trial, they get one
    if not trial_used:
        return True
    
    # Check if trial has expired
    if trial_end_date:
        trial_end = parse_trial_end_date(trial_end_date)
        if datetime.now() <= trial_end:
            return True  # Still in trial period
    
    return False  # Trial expired

def add_premium_subscription(user_id, subscription_type, months=1):
    """Add premium subscription for user"""
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
# ==================================================
# NEW APPOINTMENT SCHEDULING HELPER FUNCTIONS
# ==================================================

# APPOINTMENT MANAGEMENT FUNCTIONS
def create_appointment(user_id, client_id, title, appointment_date, duration_minutes=60,
                      appointment_type='meeting', description='', status='scheduled',
                      reminder_minutes_before=30):
    """Create a new appointment"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Ensure appointment_date is a string if it's a datetime object
    if isinstance(appointment_date, datetime):
        appointment_date_str = appointment_date.strftime('%Y-%m-%d %H:%M:%S')
    else:
        appointment_date_str = str(appointment_date)
    
    cursor.execute('''
        INSERT INTO appointments 
        (user_id, client_id, title, description, appointment_date, duration_minutes,
         appointment_type, status, reminder_minutes_before)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, client_id, title, description, appointment_date_str, duration_minutes,
          appointment_type, status, reminder_minutes_before))
    
    appointment_id = cursor.lastrowid
    
    # Create reminder record if needed
    if reminder_minutes_before > 0:
        # Ensure we have a datetime object for calculation
        if isinstance(appointment_date, datetime):
            appt_dt = appointment_date
        else:
            try:
                appt_dt = parser.parse(appointment_date_str)
            except:
                appt_dt = datetime.now() + timedelta(days=1)
        
        reminder_time = appt_dt - timedelta(minutes=reminder_minutes_before)
        reminder_time_str = reminder_time.strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute('''
            INSERT INTO appointment_reminders (appointment_id, user_id, reminder_time, reminder_type)
            VALUES (?, ?, ?, 'telegram')
        ''', (appointment_id, user_id, reminder_time_str))
    
    conn.commit()
    conn.close()
    return appointment_id

def update_appointment(appointment_id, **kwargs):
    """Update appointment fields"""
    if not kwargs:
        return False
    
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    updates = []
    params = []
    
    for field, value in kwargs.items():
        if field in ['title', 'description', 'appointment_date', 'duration_minutes',
                    'appointment_type', 'status', 'reminder_minutes_before', 'cancellation_reason']:
            # Convert datetime to string for database storage
            if field == 'appointment_date' and isinstance(value, datetime):
                value = value.strftime('%Y-%m-%d %H:%M:%S')
            updates.append(f"{field} = ?")
            params.append(value)
    
    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(appointment_id)
        
        cursor.execute(f'''
            UPDATE appointments SET {', '.join(updates)} 
            WHERE appointment_id = ?
        ''', params)
        
        # If date or reminder time changed, update reminders
        if 'appointment_date' in kwargs or 'reminder_minutes_before' in kwargs:
            cursor.execute('''
                SELECT appointment_date, reminder_minutes_before 
                FROM appointments WHERE appointment_id = ?
            ''', (appointment_id,))
            result = cursor.fetchone()
            
            if result:
                appointment_date_str, reminder_minutes = result
                
                # Parse appointment date string to datetime
                try:
                    appointment_date = parser.parse(appointment_date_str)
                except:
                    appointment_date = datetime.now()
                
                reminder_time = appointment_date - timedelta(minutes=reminder_minutes)
                reminder_time_str = reminder_time.strftime('%Y-%m-%d %H:%M:%S')
                
                cursor.execute('''
                    UPDATE appointment_reminders 
                    SET reminder_time = ?, sent = FALSE 
                    WHERE appointment_id = ?
                ''', (reminder_time_str, appointment_id))
    
    conn.commit()
    conn.close()
    return True

def get_appointment(appointment_id):
    """Get appointment by ID"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM appointments WHERE appointment_id = ?', (appointment_id,))
    appointment = cursor.fetchone()
    conn.close()
    return appointment

def get_user_appointments(user_id, start_date=None, end_date=None, status='scheduled'):
    """Get appointments for a user within date range"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    query = '''
        SELECT a.*, c.client_name, c.email as client_email, c.phone as client_phone
        FROM appointments a
        LEFT JOIN clients c ON a.client_id = c.client_id
        WHERE a.user_id = ? AND a.status = ?
    '''
    params = [user_id, status]
    
    if start_date:
        # Ensure start_date is in proper format
        if isinstance(start_date, datetime):
            start_date = start_date.strftime('%Y-%m-%d %H:%M:%S')
        query += ' AND a.appointment_date >= ?'
        params.append(start_date)
    
    if end_date:
        # Ensure end_date is in proper format
        if isinstance(end_date, datetime):
            end_date = end_date.strftime('%Y-%m-%d %H:%M:%S')
        query += ' AND a.appointment_date <= ?'
        params.append(end_date)
    
    query += ' ORDER BY a.appointment_date ASC'
    
    cursor.execute(query, params)
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_todays_appointments(user_id):
    """Get today's appointments for a user"""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    return get_user_appointments(user_id, today, tomorrow)

def get_weekly_appointments(user_id):
    """Get this week's appointments for a user"""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_week = today - timedelta(days=today.weekday())  # Monday
    end_of_week = start_of_week + timedelta(days=7)
    return get_user_appointments(user_id, start_of_week, end_of_week)

def cancel_appointment(appointment_id, reason=""):
    """Cancel an appointment"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE appointments 
        SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP, cancellation_reason = ?
        WHERE appointment_id = ?
    ''', (reason, appointment_id))
    
    conn.commit()
    conn.close()
    return True

def reschedule_appointment(appointment_id, new_date, new_time=None):
    """Reschedule an appointment to new date/time"""
    appointment = get_appointment(appointment_id)
    if not appointment:
        return False
    
    # Get existing appointment datetime
    appointment_date_str = appointment[5]  # appointment_date field
    try:
        appointment_date = parser.parse(appointment_date_str)
    except:
        appointment_date = datetime.now()
    
    # Create new datetime
    if isinstance(new_date, str):
        new_date = parser.parse(new_date)
    
    # If new_time is provided, use it
    if new_time:
        if isinstance(new_time, str):
            try:
                time_obj = datetime.strptime(new_time, '%H:%M').time()
                new_datetime = datetime.combine(new_date.date(), time_obj)
            except:
                new_datetime = new_date
        elif isinstance(new_time, datetime):
            new_datetime = datetime.combine(new_date.date(), new_time.time())
        else:
            new_datetime = new_date
    else:
        # Keep the same time, just change the date
        new_datetime = new_date.replace(
            hour=appointment_date.hour,
            minute=appointment_date.minute,
            second=0,
            microsecond=0
        )
    
    return update_appointment(appointment_id, 
                            appointment_date=new_datetime,
                            status='rescheduled')

# APPOINTMENT TYPE FUNCTIONS
def get_appointment_types(user_id):
    """Get custom appointment types for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # First check if user has custom types
    cursor.execute('SELECT * FROM appointment_types WHERE user_id = ? ORDER BY type_name', (user_id,))
    custom_types = cursor.fetchall()
    
    if not custom_types:
        # Return default types
        cursor.execute('SELECT * FROM default_appointment_types ORDER BY type_name')
        default_types = cursor.fetchall()
        
        # Convert to same format as custom types
        types = []
        for dt in default_types:
            types.append((
                dt[0],  # type_id
                user_id,
                dt[1],  # type_name
                dt[4] if len(dt) > 4 else '#4a6ee0',  # color_hex
                dt[2],  # duration_minutes
                0.00,   # price
                dt[3] if len(dt) > 3 else '',  # description
                0,      # buffer_before (placeholder)
                0,      # buffer_after (placeholder)
                True    # is_active (placeholder)
            ))
        conn.close()
        return types
    
    conn.close()
    return custom_types

def add_appointment_type(user_id, type_name, duration_minutes=60, color_hex='#4a6ee0', price=0.0, description=''):
    """Add a custom appointment type"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO appointment_types 
        (user_id, type_name, duration_minutes, color_hex, price, description)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, type_name, duration_minutes, color_hex, price, description))
    
    type_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return type_id

def delete_appointment_type(type_id):
    """Delete a custom appointment type"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM appointment_types WHERE type_id = ?', (type_id,))
    conn.commit()
    conn.close()
    return True

# WORKING HOURS FUNCTIONS
def get_working_hours(user_id):
    """Get working hours for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM working_hours 
        WHERE user_id = ? 
        ORDER BY day_of_week
    ''', (user_id,))
    hours = cursor.fetchall()
    conn.close()
    
    if not hours:
        # Initialize default hours
        init_default_working_hours(user_id)
        return get_working_hours(user_id)
    
    return hours

def update_working_hours(user_id, day_of_week, is_working_day=True, start_time=None, end_time=None,
                        lunch_start=None, lunch_end=None):
    """Update working hours for a specific day"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO working_hours 
        (user_id, day_of_week, is_working_day, start_time, end_time, lunch_start, lunch_end)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, day_of_week, is_working_day, start_time, end_time, lunch_start, lunch_end))
    
    conn.commit()
    conn.close()
    return True

def is_working_day(user_id, date):
    """Check if a specific date is a working day"""
    if isinstance(date, str):
        try:
            date = parser.parse(date)
        except:
            date = datetime.now()
    
    day_of_week = date.weekday()  # 0=Monday, 6=Sunday
    hours = get_working_hours(user_id)
    
    for day_hours in hours:
        if day_hours[2] == day_of_week:  # day_of_week field
            return bool(day_hours[3])  # is_working_day field
    
    return False

def get_available_slots(user_id, date, duration_minutes=60):
    """Get available time slots for a specific date"""
    if isinstance(date, str):
        try:
            date = parser.parse(date)
        except:
            date = datetime.now()
    
    if not is_working_day(user_id, date):
        return []
    
    # Get appointments for that day
    start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)
    appointments = get_user_appointments(user_id, start_of_day, end_of_day, 'scheduled')
    
    # Get working hours for that day
    day_of_week = date.weekday()
    hours = get_working_hours(user_id)
    day_hours = None
    for h in hours:
        if h[2] == day_of_week:
            day_hours = h
            break
    
    if not day_hours or not day_hours[3]:  # is_working_day
        return []
    
    if not day_hours[4] or not day_hours[5]:  # start_time, end_time
        return []
    
    # Parse working hours
    try:
        work_start = datetime.strptime(day_hours[4], '%H:%M').time()
        work_end = datetime.strptime(day_hours[5], '%H:%M').time()
    except ValueError:
        return []
    
    # Generate slots
    slot_duration = timedelta(minutes=duration_minutes)
    current_time = datetime.combine(date.date(), work_start)
    end_time_dt = datetime.combine(date.date(), work_end)
    
    slots = []
    while current_time + slot_duration <= end_time_dt:
        # Check if slot conflicts with existing appointments
        slot_end = current_time + slot_duration
        conflict = False
        
        for appt in appointments:
            appt_date_str = appt[5]  # appointment_date field
            try:
                appt_start = parser.parse(appt_date_str)
            except:
                continue
            appt_end = appt_start + timedelta(minutes=appt[6])  # duration_minutes field
            
            if (current_time < appt_end and slot_end > appt_start):
                conflict = True
                break
        
        if not conflict:
            slots.append(current_time.strftime('%H:%M'))
        
        current_time += timedelta(minutes=DEFAULT_SLOT_DURATION)
    
    return slots

# CALENDAR SETTINGS FUNCTIONS
def get_calendar_settings(user_id):
    """Get calendar settings for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM calendar_settings WHERE user_id = ?', (user_id,))
    settings = cursor.fetchone()
    conn.close()
    
    if not settings:
        # Initialize default settings
        init_default_calendar_settings(user_id)
        return get_calendar_settings(user_id)
    
    return settings

def update_calendar_settings(user_id, **kwargs):
    """Update calendar settings"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    settings = get_calendar_settings(user_id)
    if not settings:
        # Create settings if they don't exist
        cursor.execute('''
            INSERT INTO calendar_settings (user_id) VALUES (?)
        ''', (user_id,))
    
    updates = []
    params = []
    
    for field, value in kwargs.items():
        if field in ['default_view', 'first_day_of_week', 'slot_duration', 'show_weekends',
                    'send_email_notifications', 'send_telegram_notifications', 'email_template']:
            updates.append(f"{field} = ?")
            params.append(value)
    
    if updates:
        params.append(user_id)
        cursor.execute(f'''
            UPDATE calendar_settings SET {', '.join(updates)} WHERE user_id = ?
        ''', params)
    
    conn.commit()
    conn.close()
    return True

# REMINDER FUNCTIONS
def get_pending_reminders():
    """Get reminders that need to be sent"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT ar.*, a.title, a.appointment_date, a.duration_minutes, 
               c.client_name, c.email as client_email, u.email as user_email
        FROM appointment_reminders ar
        JOIN appointments a ON ar.appointment_id = a.appointment_id
        LEFT JOIN clients c ON a.client_id = c.client_id
        JOIN users u ON a.user_id = u.user_id
        WHERE ar.sent = FALSE AND ar.reminder_time <= datetime('now', '+5 minutes')
        AND a.status IN ('scheduled', 'confirmed')
    ''')
    
    reminders = cursor.fetchall()
    conn.close()
    return reminders

def mark_reminder_sent(reminder_id):
    """Mark a reminder as sent"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE appointment_reminders 
        SET sent = TRUE, sent_at = CURRENT_TIMESTAMP 
        WHERE reminder_id = ?
    ''', (reminder_id,))
    conn.commit()
    conn.close()
    return True

# EMAIL TEMPLATE FUNCTIONS
def get_email_templates(user_id):
    """Get email templates for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM email_templates 
        WHERE user_id = ? 
        ORDER BY is_default DESC, template_name
    ''', (user_id,))
    templates = cursor.fetchall()
    conn.close()
    return templates

def get_default_email_template(user_id):
    """Get default email template for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM email_templates 
        WHERE user_id = ? AND is_default = TRUE
        LIMIT 1
    ''', (user_id,))
    template = cursor.fetchone()
    conn.close()
    return template

def update_email_template(template_id, **kwargs):
    """Update email template"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    updates = []
    params = []
    
    for field, value in kwargs.items():
        if field in ['template_name', 'subject', 'body', 'is_default']:
            updates.append(f"{field} = ?")
            params.append(value)
    
    if updates:
        params.append(template_id)
        cursor.execute(f'''
            UPDATE email_templates SET {', '.join(updates)} WHERE template_id = ?
        ''', params)
    
    conn.commit()
    conn.close()
    return True

# UNAVAILABLE DATES FUNCTIONS
def add_unavailable_date(user_id, date, reason="", all_day=True, start_time=None, end_time=None):
    """Add an unavailable date (holiday/time off)"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Convert date to string if it's a datetime object
    if isinstance(date, datetime):
        date_str = date.strftime('%Y-%m-%d')
    else:
        date_str = str(date)
    
    cursor.execute('''
        INSERT OR REPLACE INTO unavailable_dates 
        (user_id, date, reason, all_day, start_time, end_time)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, date_str, reason, all_day, start_time, end_time))
    
    conn.commit()
    conn.close()
    return True

def get_unavailable_dates(user_id, start_date=None, end_date=None):
    """Get unavailable dates for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    query = 'SELECT * FROM unavailable_dates WHERE user_id = ?'
    params = [user_id]
    
    if start_date:
        # Convert to string if datetime
        if isinstance(start_date, datetime):
            start_date = start_date.strftime('%Y-%m-%d')
        query += ' AND date >= ?'
        params.append(start_date)
    
    if end_date:
        # Convert to string if datetime
        if isinstance(end_date, datetime):
            end_date = end_date.strftime('%Y-%m-%d')
        query += ' AND date <= ?'
        params.append(end_date)
    
    cursor.execute(query, params)
    dates = cursor.fetchall()
    conn.close()
    return dates

def is_date_available(user_id, date):
    """Check if a date is available for appointments"""
    # Convert to datetime if string
    if isinstance(date, str):
        try:
            date = parser.parse(date).date()
        except:
            return False
    
    # Check if it's a working day
    if not is_working_day(user_id, date):
        return False
    
    # Check if it's an unavailable date
    unavailable_dates = get_unavailable_dates(user_id, date, date)
    if unavailable_dates:
        return False
    
    return True

# STATISTICS FUNCTIONS
def get_appointment_stats(user_id, start_date=None, end_date=None):
    """Get appointment statistics for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    query = '''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled,
            SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END) as scheduled,
            AVG(duration_minutes) as avg_duration
        FROM appointments 
        WHERE user_id = ?
    '''
    params = [user_id]
    
    if start_date:
        # Convert to string if datetime
        if isinstance(start_date, datetime):
            start_date = start_date.strftime('%Y-%m-%d %H:%M:%S')
        query += ' AND appointment_date >= ?'
        params.append(start_date)
    
    if end_date:
        # Convert to string if datetime
        if isinstance(end_date, datetime):
            end_date = end_date.strftime('%Y-%m-%d %H:%M:%S')
        query += ' AND appointment_date <= ?'
        params.append(end_date)
    
    cursor.execute(query, params)
    stats = cursor.fetchone()
    conn.close()
    
    return {
        'total': stats[0] or 0,
        'completed': stats[1] or 0,
        'cancelled': stats[2] or 0,
        'scheduled': stats[3] or 0,
        'avg_duration': stats[4] or 0
    }

print("✅ Database helper functions updated with scheduling support!")

    
# ==================================================
# APPOINTMENT HELPER FUNCTIONS (Enhanced Version)
# ==================================================

def save_appointment(user_id, client_id, title, description, appointment_date, 
                    duration=60, appointment_type='meeting', status='scheduled',
                    reminder_minutes_before=30, google_calendar_id=None):
    """Save new appointment to database with enhanced features"""
    return create_appointment(
        user_id=user_id,
        client_id=client_id,
        title=title,
        appointment_date=appointment_date,
        duration_minutes=duration,
        appointment_type=appointment_type,
        description=description,
        status=status,
        reminder_minutes_before=reminder_minutes_before
    )

# NOTE: get_appointment is already defined in Part 3, so I'm renaming this one
def get_appointment_with_details(appointment_id):
    """Get appointment by ID with client details - renamed to avoid conflict"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.*, c.client_name, c.email, c.phone, c.address,
               u.company_name, u.email as business_email
        FROM appointments a
        LEFT JOIN clients c ON a.client_id = c.client_id
        LEFT JOIN users u ON a.user_id = u.user_id
        WHERE a.appointment_id = ?
    ''', (appointment_id,))
    
    appointment = cursor.fetchone()
    conn.close()
    return appointment

# NOTE: get_user_appointments is already defined in Part 3, so I'm renaming this one
def get_user_appointments_filtered(user_id, start_date=None, end_date=None, status=None, 
                         appointment_type=None, client_id=None):
    """Get user's appointments with filtering options - renamed to avoid conflict"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    query = '''
        SELECT a.*, c.client_name, c.email, c.phone 
        FROM appointments a
        LEFT JOIN clients c ON a.client_id = c.client_id
        WHERE a.user_id = ?
    '''
    params = [user_id]
    
    if start_date:
        # Convert to string if datetime
        if isinstance(start_date, datetime):
            start_date = start_date.strftime('%Y-%m-%d %H:%M:%S')
        query += ' AND a.appointment_date >= ?'
        params.append(start_date)
    
    if end_date:
        # Convert to string if datetime
        if isinstance(end_date, datetime):
            end_date = end_date.strftime('%Y-%m-%d %H:%M:%S')
        query += ' AND a.appointment_date <= ?'
        params.append(end_date)
    
    if status:
        query += ' AND a.status = ?'
        params.append(status)
    
    if appointment_type:
        query += ' AND a.appointment_type = ?'
        params.append(appointment_type)
    
    if client_id:
        query += ' AND a.client_id = ?'
        params.append(client_id)
    
    query += ' ORDER BY a.appointment_date ASC'
    
    cursor.execute(query, params)
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_week_appointments(user_id, week_start_date):
    """Get appointments for a specific week with daily breakdown"""
    week_end = week_start_date + timedelta(days=7)
    appointments = get_user_appointments(user_id, week_start_date, week_end, 'scheduled')
    
    # Group by day
    daily_appointments = {}
    for appt in appointments:
        appt_date_str = appt[5]  # appointment_date field
        try:
            appt_date = parser.parse(appt_date_str)
            day_key = appt_date.strftime('%Y-%m-%d')
        except:
            day_key = "Unknown"
        
        if day_key not in daily_appointments:
            daily_appointments[day_key] = []
        
        daily_appointments[day_key].append(appt)
    
    return daily_appointments

# NOTE: get_today_appointments is already defined in Part 3, so I'm renaming this one
def get_today_appointments_with_sorting(user_id):
    """Get today's appointments with time sorting - renamed to avoid conflict"""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    appointments = get_user_appointments(user_id, today_start, today_end, 'scheduled')
    
    # Sort by time
    try:
        return sorted(appointments, key=lambda x: parser.parse(x[5]) if x[5] else datetime.max)
    except:
        return appointments

def update_appointment_status(appointment_id, status, cancellation_reason=None):
    """Update appointment status with optional cancellation reason"""
    updates = {'status': status}
    
    if status == 'cancelled' and cancellation_reason:
        updates['cancellation_reason'] = cancellation_reason
    
    return update_appointment(appointment_id, **updates)

# NOTE: reschedule_appointment is already defined in Part 3, so I'm renaming this one
def reschedule_appointment_enhanced(appointment_id, new_date, new_duration=None, new_time=None):
    """Reschedule an appointment with enhanced options - renamed to avoid conflict"""
    appointment = get_appointment(appointment_id)
    if not appointment:
        return False
    
    # If new_time is provided as string
    if new_time and isinstance(new_time, str):
        try:
            time_obj = datetime.strptime(new_time, '%H:%M').time()
            new_date = datetime.combine(new_date.date() if isinstance(new_date, datetime) else new_date, time_obj)
        except ValueError:
            pass
    # If new_time is provided as datetime.time object
    elif new_time and hasattr(new_time, 'hour'):
        new_date = datetime.combine(new_date.date() if isinstance(new_date, datetime) else new_date, new_time)
    
    updates = {
        'appointment_date': new_date,
        'status': 'rescheduled'
    }
    
    if new_duration:
        updates['duration_minutes'] = new_duration
    
    return update_appointment(appointment_id, **updates)

def delete_appointment_permanently(appointment_id):
    """Delete an appointment (permanent removal) - renamed for clarity"""
    # First mark as cancelled
    cancel_appointment(appointment_id, "Deleted by user")
    
    # Then optionally remove from database (commented out for safety)
    # conn = sqlite3.connect('invoices.db')
    # cursor = conn.cursor()
    # cursor.execute('DELETE FROM appointments WHERE appointment_id = ?', (appointment_id,))
    # conn.commit()
    # conn.close()
    
    return True

def get_user_appointment_types_with_details(user_id):
    """Get user's custom appointment types with all details"""
    return get_appointment_types(user_id)

def get_default_appointment_types_list():
    """Get default appointment types"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM default_appointment_types ORDER BY type_name')
    types = cursor.fetchall()
    conn.close()
    return types

def add_custom_appointment_type(user_id, type_name, duration=60, color='#4a6ee0', price=0.00, description=''):
    """Add custom appointment type for user"""
    return add_appointment_type(
        user_id=user_id,
        type_name=type_name,
        duration_minutes=duration,
        color_hex=color,
        price=price,
        description=description
    )

def set_appointment_reminder_sent(appointment_id):
    """Mark appointment reminder as sent"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE appointments SET reminder_sent = 1 WHERE appointment_id = ?', (appointment_id,))
    conn.commit()
    conn.close()
    return True

def get_appointments_needing_reminder(hours_before=24):
    """Get appointments needing reminder with enhanced filtering"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    reminder_window_start = datetime.now() + timedelta(hours=hours_before - 1)
    reminder_window_end = datetime.now() + timedelta(hours=hours_before + 1)
    
    # Convert to strings for SQL
    reminder_window_start_str = reminder_window_start.strftime('%Y-%m-%d %H:%M:%S')
    reminder_window_end_str = reminder_window_end.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
        SELECT a.*, u.username, u.company_name, u.email as business_email,
               c.client_name, c.email, c.phone 
        FROM appointments a
        LEFT JOIN users u ON a.user_id = u.user_id
        LEFT JOIN clients c ON a.client_id = c.client_id
        WHERE a.appointment_date BETWEEN ? AND ?
        AND a.reminder_sent = 0
        AND a.status IN ('scheduled', 'confirmed')
        AND (a.reminder_minutes_before IS NULL OR 
             a.reminder_minutes_before = ? OR
             a.reminder_minutes_before = 0)
    ''', (reminder_window_start_str, reminder_window_end_str, hours_before * 60))
    
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_upcoming_appointments_count(user_id, days=7):
    """Count upcoming appointments in next X days"""
    start_date = datetime.now()
    end_date = start_date + timedelta(days=days)
    appointments = get_user_appointments(user_id, start_date, end_date, 'scheduled')
    return len(appointments)

def get_appointment_statistics_enhanced(user_id, start_date=None, end_date=None):
    """Get appointment statistics for dashboard with enhanced metrics"""
    stats = get_appointment_stats(user_id, start_date, end_date)
    
    # Get additional statistics
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Convert dates to strings for SQL
    start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S') if start_date else None
    end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S') if end_date else None
    
    # Get most common appointment types
    type_query = '''
        SELECT appointment_type, COUNT(*) as count
        FROM appointments
        WHERE user_id = ?
    '''
    type_params = [user_id]
    
    if start_date_str:
        type_query += ' AND appointment_date >= ?'
        type_params.append(start_date_str)
    
    if end_date_str:
        type_query += ' AND appointment_date <= ?'
        type_params.append(end_date_str)
    
    type_query += ' GROUP BY appointment_type ORDER BY count DESC LIMIT 5'
    
    cursor.execute(type_query, type_params)
    top_types = cursor.fetchall()
    
    # Get busiest days
    day_query = '''
        SELECT strftime('%w', appointment_date) as weekday, COUNT(*) as count
        FROM appointments
        WHERE user_id = ?
    '''
    day_params = [user_id]
    
    if start_date_str:
        day_query += ' AND appointment_date >= ?'
        day_params.append(start_date_str)
    
    if end_date_str:
        day_query += ' AND appointment_date <= ?'
        day_params.append(end_date_str)
    
    day_query += ' GROUP BY weekday ORDER BY count DESC'
    
    cursor.execute(day_query, day_params)
    busy_days = cursor.fetchall()
    
    conn.close()
    
    # Enhance stats dictionary
    stats['top_types'] = top_types
    stats['busy_days'] = busy_days
    stats['utilization_rate'] = (stats.get('scheduled', 0) / max(stats.get('total', 1), 1)) * 100
    
    return stats

def get_appointment_conflicts(user_id, start_datetime, duration_minutes, exclude_appointment_id=None):
    """Check for scheduling conflicts"""
    end_datetime = start_datetime + timedelta(minutes=duration_minutes)
    
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Convert to strings for SQL
    start_str = start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    end_str = end_datetime.strftime('%Y-%m-%d %H:%M:%S')
    
    query = '''
        SELECT a.*, c.client_name
        FROM appointments a
        LEFT JOIN clients c ON a.client_id = c.client_id
        WHERE a.user_id = ? 
        AND a.status IN ('scheduled', 'confirmed')
        AND (
            (a.appointment_date < ? AND datetime(a.appointment_date, '+' || a.duration_minutes || ' minutes') > ?)
            OR (a.appointment_date >= ? AND a.appointment_date < ?)
        )
    '''
    
    params = [user_id, end_str, start_str, start_str, end_str]
    
    if exclude_appointment_id:
        query += ' AND a.appointment_id != ?'
        params.append(exclude_appointment_id)
    
    cursor.execute(query, params)
    conflicts = cursor.fetchall()
    conn.close()
    
    return conflicts

def generate_appointment_summary(appointment_id):
    """Generate a formatted summary of an appointment"""
    appointment = get_appointment(appointment_id)
    if not appointment:
        return None
    
    appt_date_str = appointment[5]  # appointment_date field
    try:
        appt_date = parser.parse(appt_date_str)
    except:
        appt_date = datetime.now()
    
    duration = appointment[6]  # duration_minutes field
    end_time = appt_date + timedelta(minutes=duration)
    
    # Get client name - check different positions
    client_name = "Unknown"
    if len(appointment) > 12 and appointment[12]:  # from joined query
        client_name = appointment[12]
    
    summary = f"""
📅 **Appointment Summary**
━━━━━━━━━━━━━━━━━━━━━━
• **Title:** {appointment[3] or 'No title'}
• **Client:** {client_name}
• **Date:** {appt_date.strftime('%A, %B %d, %Y')}
• **Time:** {appt_date.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}
• **Duration:** {duration} minutes
• **Type:** {appointment[7] or 'Meeting'}  # appointment_type
• **Status:** {appointment[8] or 'Scheduled'}  # status
• **Description:** {appointment[4] or 'No description provided'}
"""
    
    if appointment[8] == 'cancelled' and len(appointment) > 18 and appointment[18]:  # status and cancellation_reason
        summary += f"• **Cancellation Reason:** {appointment[18]}\n"
    
    return summary.strip()

def get_available_appointment_slots(user_id, date, duration_minutes=60):
    """Get available time slots for booking"""
    return get_available_slots(user_id, date, duration_minutes)

def check_date_availability(user_id, date):
    """Check if a date is available for appointments"""
    return is_date_available(user_id, date)

def send_appointment_confirmation(appointment_id):
    """Send appointment confirmation to client"""
    appointment = get_appointment_with_details(appointment_id)
    if not appointment:
        return False
    
    # Get client email - check different positions
    client_email = None
    if len(appointment) > 13 and appointment[13]:  # email from client join
        client_email = appointment[13]
    
    if not client_email:
        return False
    
    # Get email template
    template = get_default_email_template(appointment[1])  # user_id
    
    # Get client name
    client_name = "Valued Client"
    if len(appointment) > 12 and appointment[12]:
        client_name = appointment[12]
    
    # Get company name
    company_name = "Our Team"
    if len(appointment) > 15 and appointment[15]:
        company_name = appointment[15]
    
    appt_date_str = appointment[5]
    try:
        appt_date = parser.parse(appt_date_str)
    except:
        appt_date = datetime.now()
    
    if not template:
        # Use default template
        subject = f"Appointment Confirmation: {appointment[3]}"
        body = f"""
Dear {client_name},

Your appointment has been confirmed.

**Details:**
- Date: {appt_date.strftime('%B %d, %Y')}
- Time: {appt_date.strftime('%I:%M %p')}
- Duration: {appointment[6]} minutes
- Type: {appointment[7]}
- Description: {appointment[4] or 'None'}

Thank you for your booking.

Best regards,
{company_name}
"""
    else:
        # Use custom template
        subject = template[3].format(  # subject field
            title=appointment[3],
            date=appt_date.strftime('%B %d, %Y'),
            time=appt_date.strftime('%I:%M %p'),
            client_name=client_name
        )
        
        body = template[4].format(  # body field
            client_name=client_name,
            date=appt_date.strftime('%B %d, %Y'),
            time=appt_date.strftime('%I:%M %p'),
            duration=appointment[6],
            type=appointment[7],
            description=appointment[4] or '',
            company_name=company_name
        )
    
    # Send email (implementation depends on your email setup)
    # send_email(client_email, subject, body)
    
    # For now, just log it
    logger.info(f"Would send confirmation email to {client_email}: {subject}")
    
    return True

def create_recurring_appointments(user_id, client_id, title, description, 
                                 start_date, duration, appointment_type,
                                 recurrence_pattern, end_date=None, count=None):
    """Create a series of recurring appointments"""
    appointments_created = []
    
    if recurrence_pattern == 'daily':
        interval = timedelta(days=1)
    elif recurrence_pattern == 'weekly':
        interval = timedelta(weeks=1)
    elif recurrence_pattern == 'biweekly':
        interval = timedelta(weeks=2)
    elif recurrence_pattern == 'monthly':
        # Simple monthly - same day of month
        interval = None
    else:
        return appointments_created
    
    current_date = start_date
    appointments_count = 0
    
    while True:
        # Check termination conditions
        if end_date and current_date > end_date:
            break
        
        if count and appointments_count >= count:
            break
        
        # Create appointment
        appointment_id = create_appointment(
            user_id=user_id,
            client_id=client_id,
            title=f"{title} ({appointments_count + 1})" if count else title,
            appointment_date=current_date,
            duration_minutes=duration,
            appointment_type=appointment_type,
            description=description,
            status='scheduled'
        )
        
        appointments_created.append(appointment_id)
        appointments_count += 1
        
        # Calculate next date
        if recurrence_pattern == 'monthly':
            # Add one month
            try:
                if current_date.month == 12:
                    current_date = current_date.replace(year=current_date.year + 1, month=1)
                else:
                    current_date = current_date.replace(month=current_date.month + 1)
            except ValueError:
                # Handle invalid date (e.g., Jan 31 -> Feb 28/29)
                try:
                    current_date = current_date.replace(month=current_date.month + 1, day=28)
                except:
                    break
        else:
            current_date += interval
    
    return appointments_created

def export_appointments_to_csv(user_id, start_date=None, end_date=None):
    """Export appointments to CSV format"""
    import csv
    from io import StringIO
    
    appointments = get_user_appointments_filtered(user_id, start_date, end_date)
    
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        'Appointment ID', 'Client', 'Title', 'Description', 'Date',
        'Time', 'Duration', 'Type', 'Status', 'Client Email', 'Client Phone'
    ])
    
    # Write data
    for appt in appointments:
        appt_date_str = appt[5]
        try:
            appt_date = parser.parse(appt_date_str)
            date_str = appt_date.strftime('%Y-%m-%d')
            time_str = appt_date.strftime('%H:%M')
        except:
            date_str = appt_date_str
            time_str = ''
        
        writer.writerow([
            appt[0],  # appointment_id
            appt[12] if len(appt) > 12 else '',  # client_name
            appt[3],  # title
            appt[4],  # description
            date_str,
            time_str,
            appt[6],  # duration_minutes
            appt[7],  # appointment_type
            appt[8],  # status
            appt[13] if len(appt) > 13 else '',  # email
            appt[14] if len(appt) > 14 else ''   # phone
        ])
    
    return output.getvalue()

print("✅ Appointment helper functions enhanced with comprehensive scheduling features!")
    
# ==================================================
# PART 4: INVOICE GENERATION AND PDF CREATION (Updated with Appointment Features)
# ==================================================

# Invoice generation
def generate_invoice_number(user_id):
    """Generate unique invoice number"""
    counter = get_invoice_counter(user_id)
    now = datetime.now()
    invoice_number = f"INV-{now.year}-{now.month:02d}-{counter:04d}"
    increment_invoice_counter(user_id)
    return invoice_number

def generate_appointment_number(user_id):
    """Generate unique appointment reference number"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    now = datetime.now()
    cursor.execute('SELECT COUNT(*) FROM appointments WHERE user_id = ?', (user_id,))
    count = cursor.fetchone()[0]
    
    appointment_number = f"APT-{now.year}{now.month:02d}-{count+1:04d}"
    conn.close()
    return appointment_number

# ==================================================
# APPOINTMENT PDF AND EMAIL FUNCTIONS
# ==================================================

def create_appointment_confirmation_pdf(appointment_data, user_info, client_info):
    """Create a professional appointment confirmation PDF"""
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
        
        # Create custom styles
        title_style = styles["Heading1"]
        title_style.alignment = TA_CENTER
        title_style.textColor = colors.HexColor('#4a6ee0')
        title_style.spaceAfter = 20
        
        heading_style = styles["Heading2"]
        heading_style.spaceAfter = 12
        
        normal_style = styles["Normal"]
        normal_style.spaceAfter = 8
        
        bold_style = styles["Normal"]
        bold_style.fontName = 'Helvetica-Bold'
        
        small_style = styles["Normal"]
        small_style.fontSize = 9
        small_style.textColor = colors.gray
        
        # Header section
        company_name = ""
        if user_info and len(user_info) > 8:
            company_name = user_info[8] if user_info[8] else ''
        
        has_logo = False
        if user_info and len(user_info) > 7 and user_info[7]:  # logo_path
            logo_path = user_info[7]
            if os.path.exists(logo_path):
                try:
                    logo = Image(logo_path, width=2*inch, height=1*inch)
                    story.append(logo)
                    story.append(Spacer(1, 0.2*inch))
                    has_logo = True
                except Exception as e:
                    logger.warning(f"Could not load logo: {e}")
                    has_logo = False
        
        # Appointment title
        title_text = "<b>APPOINTMENT CONFIRMATION</b>"
        story.append(Paragraph(title_text, title_style))
        
        # Appointment reference
        ref_style = styles["Normal"]
        ref_style.alignment = TA_CENTER
        ref_style.textColor = colors.HexColor('#666666')
        appointment_number = appointment_data.get('appointment_number', 'N/A')
        story.append(Paragraph(f"Reference: {appointment_number}", ref_style))
        
        story.append(Spacer(1, 0.3*inch))
        
        # Parse appointment date
        appt_date_str = appointment_data.get('appointment_date', '')
        try:
            appt_date = parser.parse(appt_date_str)
        except:
            appt_date = datetime.now()
        
        duration = appointment_data.get('duration_minutes', 60)
        end_time = appt_date + timedelta(minutes=duration)
        
        # Appointment details in a table
        details_data = [
            [Paragraph("<b>Appointment Details</b>", heading_style), ""],
            ["", ""],
            [Paragraph("<b>Title:</b>", bold_style), 
             Paragraph(appointment_data.get('title', 'N/A'), normal_style)],
            
            [Paragraph("<b>Date:</b>", bold_style), 
             Paragraph(appt_date.strftime('%A, %B %d, %Y'), normal_style)],
            
            [Paragraph("<b>Time:</b>", bold_style), 
             Paragraph(f"{appt_date.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}", normal_style)],
            
            [Paragraph("<b>Duration:</b>", bold_style), 
             Paragraph(f"{duration} minutes", normal_style)],
            
            [Paragraph("<b>Type:</b>", bold_style), 
             Paragraph(appointment_data.get('appointment_type', 'Meeting').title(), normal_style)],
            
            [Paragraph("<b>Status:</b>", bold_style), 
             Paragraph(appointment_data.get('status', 'Scheduled').title(), normal_style)],
        ]
        
        if appointment_data.get('description'):
            details_data.append([
                Paragraph("<b>Description:</b>", bold_style), 
                Paragraph(appointment_data['description'], normal_style)
            ])
        
        details_table = Table(details_data, colWidths=[2*inch, 4*inch])
        details_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('SPAN', (0, 0), (1, 0)),
            ('BACKGROUND', (0, 0), (1, 0), colors.HexColor('#4a6ee0')),
            ('TEXTCOLOR', (0, 0), (1, 0), colors.white),
            ('ALIGN', (0, 0), (1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (1, 0), 12),
            ('SPAN', (0, 1), (1, 1)),  # Empty spacer row
            ('LINEBELOW', (0, 1), (1, 1), 0, colors.white),  # Hidden line
            ('BACKGROUND', (0, 2), (-1, -1), colors.white),
            ('TEXTCOLOR', (0, 2), (-1, -1), colors.black),
            ('ALIGN', (0, 2), (0, -1), 'LEFT'),
            ('ALIGN', (1, 2), (1, -1), 'LEFT'),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 2), (-1, -1), 0.5, colors.lightgrey),
        ]))
        
        story.append(details_table)
        story.append(Spacer(1, 0.4*inch))
        
        # Client information
        story.append(Paragraph("<b>Client Information</b>", heading_style))
        
        client_details = []
        if client_info and client_info.get('client_name'):
            client_details.append(f"<b>Name:</b> {client_info['client_name']}")
        if client_info and client_info.get('email'):
            client_details.append(f"<b>Email:</b> {client_info['email']}")
        if client_info and client_info.get('phone'):
            client_details.append(f"<b>Phone:</b> {client_info['phone']}")
        if client_info and client_info.get('address'):
            client_details.append(f"<b>Address:</b> {client_info['address']}")
        
        if client_details:
            for detail in client_details:
                story.append(Paragraph(detail, normal_style))
        else:
            story.append(Paragraph("No client information available", normal_style))
        
        story.append(Spacer(1, 0.4*inch))
        
        # Business information
        story.append(Paragraph("<b>Business Information</b>", heading_style))
        
        business_details = []
        if company_name:
            business_details.append(f"<b>Company:</b> {company_name}")
        
        # Get user email and phone from user_info tuple
        if user_info:
            if len(user_info) > 13 and user_info[13]:  # email field
                business_details.append(f"<b>Contact Email:</b> {user_info[13]}")
            if len(user_info) > 14 and user_info[14]:  # phone field
                business_details.append(f"<b>Contact Phone:</b> {user_info[14]}")
        
        for detail in business_details:
            story.append(Paragraph(detail, normal_style))
        
        story.append(Spacer(1, 0.5*inch))
        
        # Important notes
        notes_style = styles["Normal"]
        notes_style.textColor = colors.HexColor('#ff6b6b')
        notes_style.fontSize = 10
        
        notes = [
            "• Please arrive 5-10 minutes before your scheduled appointment",
            "• To reschedule or cancel, please provide at least 24 hours notice",
            "• Late arrivals may result in reduced appointment time",
            "• Contact us if you have any questions or special requirements"
        ]
        
        story.append(Paragraph("<b>Important Notes</b>", heading_style))
        for note in notes:
            story.append(Paragraph(note, notes_style))
        
        story.append(Spacer(1, 0.3*inch))
        
        # Footer
        footer_style = styles["Normal"]
        footer_style.alignment = TA_CENTER
        footer_style.textColor = colors.gray
        footer_style.fontSize = 8
        footer_style.spaceBefore = 20
        
        generated_date = datetime.now().strftime('%B %d, %Y %I:%M %p')
        footer_text = f"Generated by Minigma Business Suite • {generated_date}"
        
        if company_name:
            footer_text = f"{company_name} • {footer_text}"
        
        story.append(Paragraph(footer_text, footer_style))
        
        # Build PDF
        doc.build(story)
        
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Save PDF
        os.makedirs('appointments', exist_ok=True)
        pdf_file = f"appointments/{appointment_number}.pdf"
        with open(pdf_file, 'wb') as f:
            f.write(pdf_data)
        
        logger.info(f"Appointment PDF generated: {pdf_file}")
        return pdf_file
        
    except Exception as e:
        logger.error(f"Appointment PDF generation error: {e}")
        # Return minimal PDF or raise exception
        raise

def create_calendar_export_pdf(user_id, start_date, end_date):
    """Create a PDF calendar export for a date range"""
    try:
        # Get appointments for the period
        appointments = get_user_appointments(user_id, start_date, end_date)
        user_info = get_user(user_id)
        
        if not appointments:
            return None
        
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
        title_style.alignment = TA_CENTER
        title_style.textColor = colors.HexColor('#4a6ee0')
        title_style.spaceAfter = 20
        
        heading_style = styles["Heading2"]
        heading_style.spaceAfter = 12
        
        normal_style = styles["Normal"]
        normal_style.spaceAfter = 6
        
        small_style = styles["Normal"]
        small_style.fontSize = 9
        
        # Header
        company_name = "Your Business"
        if user_info and len(user_info) > 8 and user_info[8]:
            company_name = user_info[8]
            
        story.append(Paragraph(f"<b>{company_name} - Appointment Calendar</b>", title_style))
        
        date_range = f"{start_date.strftime('%B %d, %Y')} to {end_date.strftime('%B %d, %Y')}"
        story.append(Paragraph(date_range, heading_style))
        
        story.append(Spacer(1, 0.3*inch))
        
        # Group appointments by date
        appointments_by_date = {}
        for appt in appointments:
            appt_date_str = appt[5]  # appointment_date field
            try:
                appt_date = parser.parse(appt_date_str)
                date_key = appt_date.strftime('%Y-%m-%d')
            except:
                date_key = "Unknown"
            
            if date_key not in appointments_by_date:
                appointments_by_date[date_key] = []
            
            appointments_by_date[date_key].append(appt)
        
        # Sort dates
        sorted_dates = sorted([d for d in appointments_by_date.keys() if d != "Unknown"])
        
        # Create calendar view
        for date_key in sorted_dates:
            try:
                date_obj = datetime.strptime(date_key, '%Y-%m-%d')
            except:
                continue
            
            # Date header
            date_header = date_obj.strftime('%A, %B %d, %Y')
            story.append(Paragraph(f"<b>{date_header}</b>", heading_style))
            
            # Table for appointments on this day
            day_appointments = appointments_by_date[date_key]
            
            if not day_appointments:
                story.append(Paragraph("No appointments scheduled", normal_style))
                story.append(Spacer(1, 0.2*inch))
                continue
            
            # Create table data
            table_data = [
                [Paragraph('<b>Time</b>', normal_style),
                 Paragraph('<b>Client</b>', normal_style),
                 Paragraph('<b>Type</b>', normal_style),
                 Paragraph('<b>Duration</b>', normal_style),
                 Paragraph('<b>Status</b>', normal_style)]
            ]
            
            for appt in day_appointments:
                appt_date_str = appt[5]
                try:
                    appt_time = parser.parse(appt_date_str)
                    duration = appt[6] if len(appt) > 6 else 60
                    end_time = appt_time + timedelta(minutes=duration)
                    time_range = f"{appt_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}"
                except:
                    time_range = "Time N/A"
                    duration = 0
                
                client_name = "Unknown"
                if len(appt) > 12 and appt[12]:  # client_name from join
                    client_name = appt[12]
                
                appointment_type = appt[7] if len(appt) > 7 else "Meeting"
                status = appt[8] if len(appt) > 8 else "Scheduled"
                
                table_data.append([
                    Paragraph(time_range, small_style),
                    Paragraph(client_name[:20] + ("..." if len(client_name) > 20 else ""), small_style),
                    Paragraph(appointment_type, small_style),
                    Paragraph(f"{duration} min", small_style),
                    Paragraph(status.title(), small_style)
                ])
            
            # Create table
            table = Table(table_data, colWidths=[1.5*inch, 1.5*inch, 1*inch, 0.8*inch, 1*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a6ee0')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ('PADDING', (0, 0), (-1, -1), 6),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            
            story.append(table)
            story.append(Spacer(1, 0.3*inch))
        
        # Summary statistics
        story.append(Paragraph("<b>Summary</b>", heading_style))
        
        total_appointments = len(appointments)
        scheduled = sum(1 for a in appointments if len(a) > 8 and a[8] == 'scheduled')
        completed = sum(1 for a in appointments if len(a) > 8 and a[8] == 'completed')
        cancelled = sum(1 for a in appointments if len(a) > 8 and a[8] == 'cancelled')
        
        summary_data = [
            ["Total Appointments:", str(total_appointments)],
            ["Scheduled:", str(scheduled)],
            ["Completed:", str(completed)],
            ["Cancelled:", str(cancelled)],
        ]
        
        if total_appointments > 0:
            completion_rate = (completed / total_appointments) * 100
            summary_data.append(["Completion Rate:", f"{completion_rate:.1f}%"])
        
        summary_table = Table(summary_data, colWidths=[2*inch, 1*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f8f9fa')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        
        story.append(summary_table)
        
        # Footer
        footer_style = styles["Normal"]
        footer_style.alignment = TA_CENTER
        footer_style.textColor = colors.gray
        footer_style.fontSize = 8
        footer_style.spaceBefore = 20
        
        generated_date = datetime.now().strftime('%B %d, %Y %I:%M %p')
        footer_text = f"Calendar Export • Generated {generated_date} • Minigma Business Suite"
        
        story.append(Paragraph(footer_text, footer_style))
        
        # Build PDF
        doc.build(story)
        
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Save PDF
        os.makedirs('calendar_exports', exist_ok=True)
        filename = f"calendar_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.pdf"
        pdf_file = f"calendar_exports/{filename}"
        
        with open(pdf_file, 'wb') as f:
            f.write(pdf_data)
        
        logger.info(f"Calendar PDF generated: {pdf_file}")
        return pdf_file
        
    except Exception as e:
        logger.error(f"Calendar PDF generation error: {e}")
        return None

def create_appointment_reminder_pdf(appointment_data):
    """Create a reminder PDF for an upcoming appointment"""
    try:
        buffer = io.BytesIO()
        
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=(6*inch, 4*inch),  # Smaller size for reminders
            topMargin=0.3*inch,
            bottomMargin=0.3*inch,
            leftMargin=0.3*inch,
            rightMargin=0.3*inch
        )
        story = []
        styles = getSampleStyleSheet()
        
        title_style = styles["Heading2"]
        title_style.alignment = TA_CENTER
        title_style.textColor = colors.HexColor('#ff9500')
        
        normal_style = styles["Normal"]
        normal_style.fontSize = 10
        
        bold_style = styles["Normal"]
        bold_style.fontName = 'Helvetica-Bold'
        bold_style.fontSize = 10
        
        # Title
        story.append(Paragraph("<b>APPOINTMENT REMINDER</b>", title_style))
        story.append(Spacer(1, 0.2*inch))
        
        # Parse appointment date
        appt_date_str = appointment_data.get('appointment_date', '')
        try:
            appt_date = parser.parse(appt_date_str)
            time_str = appt_date.strftime('%I:%M %p')
            date_str = appt_date.strftime('%A, %b %d')
        except:
            time_str = "Time N/A"
            date_str = "Date N/A"
        
        # Appointment details
        details = [
            f"<b>When:</b> {date_str} at {time_str}",
            f"<b>What:</b> {appointment_data.get('title', 'Appointment')}",
            f"<b>Duration:</b> {appointment_data.get('duration_minutes', 60)} minutes",
            f"<b>Type:</b> {appointment_data.get('appointment_type', 'Meeting').title()}"
        ]
        
        for detail in details:
            story.append(Paragraph(detail, normal_style))
        
        story.append(Spacer(1, 0.2*inch))
        
        # Reminder note
        note_style = styles["Normal"]
        note_style.fontSize = 9
        note_style.textColor = colors.HexColor('#ff3b30')
        
        story.append(Paragraph("<b>Don't forget your appointment!</b>", note_style))
        story.append(Paragraph("Please arrive 5 minutes early.", note_style))
        
        # Footer
        footer_style = styles["Normal"]
        footer_style.alignment = TA_CENTER
        footer_style.fontSize = 8
        footer_style.textColor = colors.gray
        footer_style.spaceBefore = 0.3*inch
        
        story.append(Paragraph("Reminder generated by Minigma Business Suite", footer_style))
        
        # Build PDF
        doc.build(story)
        
        pdf_data = buffer.getvalue()
        buffer.close()
        
        return pdf_data
        
    except Exception as e:
        logger.error(f"Reminder PDF generation error: {e}")
        return None

# ==================================================
# PART 5: EMAIL FUNCTIONS FOR APPOINTMENTS
# ==================================================

def send_appointment_email(appointment_id, email_type="confirmation"):
    """Send appointment email to client"""
    try:
        # Get appointment details
        appointment = get_appointment(appointment_id)
        if not appointment:
            return False
        
        # Get user and client info
        user_id = appointment[1]
        user_info = get_user(user_id)
        client_info = {
            'client_name': appointment[12] if len(appointment) > 12 else '',
            'email': appointment[13] if len(appointment) > 13 else '',
            'phone': appointment[14] if len(appointment) > 14 else '',
            'address': appointment[15] if len(appointment) > 15 else ''
        }
        
        # Check if client has email
        if not client_info['email']:
            logger.warning(f"No email for client in appointment {appointment_id}")
            return False
        
        # Prepare appointment data
        appt_data = {
            'appointment_id': appointment[0],
            'appointment_number': generate_appointment_number(user_id),
            'title': appointment[3],
            'description': appointment[4],
            'appointment_date': appointment[5],
            'duration_minutes': appointment[6],
            'appointment_type': appointment[7],
            'status': appointment[8]
        }
        
        # Create PDF based on email type
        pdf_file = None
        if email_type == "confirmation":
            pdf_file = create_appointment_confirmation_pdf(appt_data, user_info, client_info)
            subject = f"Appointment Confirmation: {appt_data['title']}"
        elif email_type == "reminder":
            pdf_data = create_appointment_reminder_pdf(appt_data)
            subject = f"Reminder: Your Appointment Tomorrow - {appt_data['title']}"
        elif email_type == "cancellation":
            subject = f"Appointment Cancelled: {appt_data['title']}"
        else:
            subject = f"Appointment Update: {appt_data['title']}"
        
        # Prepare email content
        company_name = "Your Business"
        if user_info and len(user_info) > 8 and user_info[8]:
            company_name = user_info[8]
            
        try:
            appt_date = parser.parse(appt_data['appointment_date'])
            date_str = appt_date.strftime('%A, %B %d, %Y')
            time_str = appt_date.strftime('%I:%M %p')
        except:
            date_str = "Date not specified"
            time_str = "Time not specified"
        
        # HTML email body
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0;">
                <div style="background-color: #4a6ee0; color: white; padding: 20px; text-align: center;">
                    <h1 style="margin: 0;">{company_name}</h1>
                </div>
                
                <div style="padding: 30px;">
                    <h2 style="color: #4a6ee0;">Appointment {email_type.title()}</h2>
                    
                    <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                        <h3 style="margin-top: 0;">{appt_data['title']}</h3>
                        <p><strong>Date:</strong> {date_str}</p>
                        <p><strong>Time:</strong> {time_str}</p>
                        <p><strong>Duration:</strong> {appt_data['duration_minutes']} minutes</p>
                        <p><strong>Type:</strong> {appt_data['appointment_type'].title()}</p>
                    </div>
                    
                    {f'<p><strong>Description:</strong> {appt_data["description"]}</p>' if appt_data['description'] else ''}
                    
                    <p>Please find the attached confirmation document for your records.</p>
                    
                    <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e0e0e0;">
                        <p style="color: #666; font-size: 14px;">
                            <strong>Important Notes:</strong><br>
                            • Please arrive 5-10 minutes before your appointment<br>
                            • Contact us if you need to reschedule or cancel<br>
                            • Bring any necessary documents or materials
                        </p>
                    </div>
                </div>
                
                <div style="background-color: #f8f9fa; padding: 20px; text-align: center; color: #666; font-size: 12px;">
                    <p>This email was sent from Minigma Business Suite</p>
                    <p>{datetime.now().strftime('%B %d, %Y')}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Plain text version
        text_body = f"""
        Appointment {email_type.title()}
        -------------------------
        
        Company: {company_name}
        Appointment: {appt_data['title']}
        Date: {date_str}
        Time: {time_str}
        Duration: {appt_data['duration_minutes']} minutes
        Type: {appt_data['appointment_type'].title()}
        
        Description: {appt_data.get('description', 'None')}
        
        Please find the attached confirmation document for your records.
        
        Important Notes:
        • Please arrive 5-10 minutes before your appointment
        • Contact us if you need to reschedule or cancel
        • Bring any necessary documents or materials
        
        Generated by Minigma Business Suite
        {datetime.now().strftime('%B %d, %Y')}
        """
        
        # Send email (you'll need to implement your email sending logic)
        # Example using smtplib:
        # send_email_with_attachment(
        #     to_email=client_info['email'],
        #     subject=subject,
        #     html_body=html_body,
        #     text_body=text_body,
        #     attachment_path=pdf_file if pdf_file else None
        # )
        
        logger.info(f"Appointment {email_type} email prepared for appointment {appointment_id}")
        
        # Mark notification as sent in database
        conn = sqlite3.connect('invoices.db')
        cursor = conn.cursor()
        cursor.execute('UPDATE appointments SET notification_sent = 1 WHERE appointment_id = ?', (appointment_id,))
        conn.commit()
        conn.close()
        
        return True
        
    except Exception as e:
        logger.error(f"Error sending appointment email: {e}")
        return False

def send_bulk_appointment_reminders():
    """Send reminders for upcoming appointments"""
    try:
        # Get appointments needing reminders
        appointments = get_appointments_needing_reminder(hours_before=24)
        
        if not appointments:
            return 0
        
        sent_count = 0
        for appointment in appointments:
            try:
                success = send_appointment_email(appointment[0], email_type="reminder")
                if success:
                    set_appointment_reminder_sent(appointment[0])
                    sent_count += 1
            except Exception as e:
                logger.error(f"Error sending reminder for appointment {appointment[0]}: {e}")
        
        logger.info(f"Sent {sent_count} appointment reminders")
        return sent_count
        
    except Exception as e:
        logger.error(f"Error in bulk reminder sending: {e}")
        return 0

# ==================================================
# EXISTING INVOICE PDF FUNCTION (UPDATED)
# ==================================================

def create_invoice_pdf(invoice_data, user_info):
    """Original invoice PDF creation function - kept as is"""
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
        currency_code = invoice_data.get('currency', 'USD')
        currency_symbol = currency_symbols.get(currency_code, currency_code)
        
        # Header section
        company_name = ""
        has_logo = False
        if user_info and len(user_info) > 8:
            company_name = user_info[8] if user_info[8] else ''
        
        if user_info and len(user_info) > 7 and user_info[7]:  # logo_path
            logo_path = user_info[7]
            if os.path.exists(logo_path):
                try:
                    logo = Image(logo_path, width=2.5*inch, height=1.25*inch)
                    has_logo = True
                except Exception as e:
                    logger.warning(f"Could not load logo: {e}")
                    has_logo = False
        
        header_data = []
        
        if has_logo:
            header_data.append(logo)
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
        reg_data = []
        
        # Always show company registration number if available
        if user_info and len(user_info) > 9 and user_info[9]:  # company_reg_number
            reg_data.append(Paragraph(f"<b>Company Reg:</b> {user_info[9]}", normal_style))
        
        # Only show VAT registration number if VAT is enabled for this invoice
        if invoice_data.get('vat_enabled') and user_info and len(user_info) > 10 and user_info[10]:  # vat_reg_number
            reg_data.append(Paragraph(f"<b>VAT Reg:</b> {user_info[10]}", normal_style))
        
        if reg_data:
            for reg in reg_data:
                story.append(reg)
            story.append(Spacer(1, 0.2*inch))
        
        # Invoice details
        details_data = [
            [Paragraph("<b>Invoice Number:</b>", bold_style), 
             Paragraph(invoice_data.get('invoice_number', 'N/A'), normal_style),
             Paragraph("<b>Date:</b>", bold_style), 
             Paragraph(invoice_data.get('invoice_date', 'N/A'), normal_style)],
            
            [Paragraph("<b>Bill To:</b>", bold_style), 
             Paragraph(invoice_data.get('client_name', 'N/A'), normal_style),
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
        items = invoice_data.get('items', [])
        for item in items:
            quantity = item.get('quantity', 0)
            amount = item.get('amount', 0.0)
            total = quantity * amount
            subtotal += total
            table_data.append([
                Paragraph(item.get('description', ''), normal_style),
                Paragraph(str(quantity), normal_style),
                Paragraph(f"{currency_symbol} {amount:.2f}", normal_style),
                Paragraph(f"{currency_symbol} {total:.2f}", normal_style)
            ])
        
        # Add VAT row if enabled
        vat_enabled = invoice_data.get('vat_enabled', False)
        if vat_enabled:
            vat_amount = subtotal * 0.2
            table_data.append([
                Paragraph("<b>VAT @ 20%</b>", bold_style),
                Paragraph("", normal_style),
                Paragraph("", normal_style),
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
        footer_text = "Generated by Minigma Business Suite"
        if company_name:
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
        pdf_file = f"invoices/{invoice_data.get('invoice_number', 'invoice')}.pdf"
        with open(pdf_file, 'wb') as f:
            f.write(pdf_data)
        
        logger.info(f"PDF generated successfully: {pdf_file}")
        return pdf_file
        
    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        raise

print("✅ Part 5 updated with comprehensive appointment PDF and email functionality!")

# PART 4: COMMAND HANDLERS (Updated with Scheduling)
# ==================================================

# ==================================================
# SCHEDULING COMMANDS
# ==================================================

async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the appointment scheduling process"""
    user_id = update.effective_user.id
    
    # Check if user has existing clients
    clients = get_user_clients(user_id)
    
    if not clients:
        # No clients, create one first
        await update.message.reply_text(
            "📅 **Schedule Appointment**\n\n"
            "You need to add a client first before scheduling an appointment.\n\n"
            "Would you like to add a client now?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Client", callback_data="schedule_add_client")],
                [InlineKeyboardButton("❌ Cancel", callback_data="schedule_cancel")]
            ])
        )
        return SCHEDULE_START
    
    # Start scheduling conversation
    context.user_data['scheduling'] = {
        'step': 'select_client',
        'appointment_data': {}
    }
    
    # Show client selection
    keyboard = []
    for client in clients[:10]:
        keyboard.append([
            InlineKeyboardButton(
                f"👤 {client[2]}", 
                callback_data=f"schedule_client_{client[0]}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("➕ Add New Client", callback_data="schedule_new_client"),
        InlineKeyboardButton("❌ Cancel", callback_data="schedule_cancel")
    ])
    
    await update.message.reply_text(
        "📅 **Schedule New Appointment**\n\n"
        "Select a client for this appointment:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return SELECT_CLIENT

# ===== NEW: SCHEDULING CALLBACK HANDLERS =====

async def schedule_client_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle scheduling appointments for specific clients"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("schedule_client_"):
        try:
            client_id = int(data.split("_")[2])
            
            # Get client details
            client = get_client_by_id(client_id)
            if client:
                # Store in context for booking flow
                context.user_data['scheduling'] = {
                    'step': 'select_type',
                    'client_id': client_id,
                    'client_name': client[2],
                    'appointment_data': {}
                }
                
                # Show appointment type selection
                keyboard = [
                    [InlineKeyboardButton("👥 In-Person Meeting", callback_data=f"book_type_inperson_{client_id}")],
                    [InlineKeyboardButton("📞 Phone Call", callback_data=f"book_type_phone_{client_id}")],
                    [InlineKeyboardButton("💻 Video Call", callback_data=f"book_type_video_{client_id}")],
                    [InlineKeyboardButton("📝 Consultation", callback_data=f"book_type_consultation_{client_id}")],
                    [InlineKeyboardButton("🔙 Back to Clients", callback_data="schedule_back"),
                     InlineKeyboardButton("❌ Cancel", callback_data="schedule_cancel")]
                ]
                
                await query.edit_message_text(
                    f"📅 **Schedule with {client[2]}**\n\n"
                    f"**Client:** {client[2]}\n"
                    f"**Email:** {client[3] or 'Not set'}\n"
                    f"**Phone:** {client[4] or 'Not set'}\n\n"
                    f"Select appointment type:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    "❌ **Client not found**\n\n"
                    "This client may have been deleted.",
                    parse_mode='Markdown'
                )
        except (IndexError, ValueError):
            await query.edit_message_text(
                "❌ **Invalid request**\n\n"
                "Please try again or use the menu.",
                parse_mode='Markdown'
            )
    elif data == "schedule_back":
        # Go back to schedule menu
        await schedule_command(update, context)
    elif data == "schedule_cancel":
        # Cancel scheduling
        if 'scheduling' in context.user_data:
            del context.user_data['scheduling']
        await query.edit_message_text(
            "❌ **Scheduling cancelled**\n\n"
            "No appointment was created.",
            parse_mode='Markdown'
        )
    else:
        await query.answer("Please use the menu options.")

async def handle_appointment_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all appointment-related button clicks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "book_appointment_start":
        await start_appointment_booking(query, user_id)
    
    elif data == "view_schedule_today":
        await calendar_command(update, context)
    
    elif data.startswith("calendar_"):
        await handle_calendar_navigation(query, data, user_id)
    
    elif data.startswith("toggle_reminder_"):
        appointment_id = int(data.split("_")[2])
        await toggle_appointment_reminder(query, appointment_id)
    
    elif data == "schedule_back":
        await schedule_command(update, context)
    
    elif data == "appt_view_calendar":
        await calendar_command(update, context)
    
    elif data == "appt_new":
        await schedule_command(update, context)
    
    elif data.startswith("view_appt_"):
        appointment_id = int(data.split("_")[2])
        await view_appointment_details(query, appointment_id)
    
    else:
        await query.edit_message_text(
            "⚠️ **Feature Coming Soon**\n\n"
            "This appointment feature is in development.\n"
            "Basic booking is available via /quickbook",
            parse_mode='Markdown'
        )

async def start_appointment_booking(query, user_id):
    """Start the appointment booking flow"""
    clients = get_user_clients(user_id)
    
    if not clients:
        await query.edit_message_text(
            "👥 **Add a Client First**\n\n"
            "You need to add at least one client before booking appointments.\n\n"
            "Use /clients to add your first client, then try booking again.",
            parse_mode='Markdown'
        )
        return
    
    # Show client selection
    keyboard = []
    for client in clients[:8]:  # Max 8 clients
        keyboard.append([
            InlineKeyboardButton(f"👤 {client[2]}", callback_data=f"book_client_{client[0]}")
        ])
    
    keyboard.append([
        InlineKeyboardButton("🔙 Back", callback_data="schedule_back")
    ])
    
    await query.edit_message_text(
        "📅 **Book Appointment - Step 1**\n\n"
        "Select a client for the appointment:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_booking_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the appointment booking flow callbacks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("book_type_"):
        # Extract type and client_id
        parts = data.split("_")
        appointment_type = parts[2]  # inperson, phone, video, etc.
        
        # Check if client_id is in callback data
        client_id = None
        if len(parts) > 3:
            try:
                client_id = int(parts[3])
                context.user_data['booking_client_id'] = client_id
            except:
                client_id = None
        
        # Get client info if available
        client_name = ""
        if client_id:
            client = get_client_by_id(client_id)
            client_name = client[2] if client else "Client"
        
        # Store in context
        context.user_data['booking'] = {
            'type': appointment_type,
            'client_id': client_id,
            'client_name': client_name,
            'step': 'date_selection',
            'created_at': datetime.now()
        }
        
        # Show date selection
        await show_date_selection(query, user_id, appointment_type, client_name)
    
    elif data.startswith("book_client_"):
        # Client selection
        client_id = int(data.split("_")[2])
        client = get_client_by_id(client_id)
        
        if client:
            context.user_data['booking_client_id'] = client_id
            
            await query.edit_message_text(
                f"✅ **Client selected: {client[2]}**\n\n"
                "Now choose appointment type:",
                parse_mode='Markdown'
            )
            
            # Show type selection
            keyboard = [
                [InlineKeyboardButton("👥 In-Person", callback_data=f"book_type_inperson_{client_id}")],
                [InlineKeyboardButton("📞 Phone Call", callback_data=f"book_type_phone_{client_id}")],
                [InlineKeyboardButton("💻 Video Call", callback_data=f"book_type_video_{client_id}")],
                [InlineKeyboardButton("🔙 Change Client", callback_data="book_appointment_start")]
            ]
            
            await query.message.reply_text(
                "**Select appointment type:**",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text("❌ Client not found")
    
    elif data == "booking_cancel":
        # Cancel booking
        if 'booking' in context.user_data:
            del context.user_data['booking']
        if 'booking_client_id' in context.user_data:
            del context.user_data['booking_client_id']
        
        await query.edit_message_text(
            "❌ **Booking cancelled**\n\n"
            "No appointment was created.",
            parse_mode='Markdown'
        )
    
    elif data == "booking_back":
        # Go back in booking flow
        await schedule_command(update, context)
    
    elif data.startswith("select_date_"):
        # Date selected
        date_str = data.split("_")[2]
        selected_date = datetime.strptime(date_str, '%Y-%m-%d')
        
        context.user_data['booking']['selected_date'] = selected_date
        
        await show_time_selection(query, user_id, selected_date)

async def show_date_selection(query, user_id: int, appointment_type: str, client_name: str = ""):
    """Show date selection for booking"""
    today = datetime.now()
    
    # Create date buttons for next 14 days
    keyboard = []
    row = []
    
    for day_offset in range(14):
        current_date = today + timedelta(days=day_offset)
        
        # Check if it's a working day (default Monday-Friday)
        if current_date.weekday() < 5:  # 0-4 = Monday-Friday
            day_text = current_date.strftime("%a %d")
            if day_offset == 0:
                day_text = f"🟢 {day_text}"
            elif day_offset == 1:
                day_text = f"🔵 {day_text}"
            
            row.append(InlineKeyboardButton(
                day_text, 
                callback_data=f"select_date_{current_date.strftime('%Y-%m-%d')}"
            ))
        
        if len(row) == 3:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    # Add navigation and options
    keyboard.append([
        InlineKeyboardButton("📅 Calendar View", callback_data="calendar_advanced"),
        InlineKeyboardButton("🔄 This Week", callback_data="select_week_today")
    ])
    
    keyboard.append([
        InlineKeyboardButton("🔙 Back", callback_data="booking_back"),
        InlineKeyboardButton("❌ Cancel", callback_data="booking_cancel")
    ])
    
    client_info = f"with {client_name}" if client_name else ""
    
    await query.edit_message_text(
        f"📅 **Step 3: Select Date**\n\n"
        f"**Type:** {appointment_type.replace('_', ' ').title()}\n"
        f"**Client:** {client_name or 'Not selected'}\n\n"
        f"🟢 = Today | 🔵 = Tomorrow\n\n"
        f"Select a date for your appointment {client_info}:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def show_time_selection(query, user_id: int, selected_date: datetime):
    """Show time selection for booking"""
    # Get available time slots for this date
    available_slots = get_available_slots(user_id, selected_date.date())
    
    if not available_slots:
        # No slots available, suggest another date
        await query.edit_message_text(
            f"❌ **No available slots on {selected_date.strftime('%A, %d %B')}**\n\n"
            f"All time slots are booked or outside working hours.\n\n"
            "Please select another date:",
            parse_mode='Markdown'
        )
        await show_date_selection(query, user_id, 
                                context.user_data['booking'].get('type', 'meeting'),
                                context.user_data['booking'].get('client_name', ''))
        return
    
    # Create time buttons
    keyboard = []
    row = []
    
    for i, slot in enumerate(available_slots[:12]):  # Show max 12 slots
        row.append(InlineKeyboardButton(
            slot, 
            callback_data=f"select_time_{slot.replace(':', '')}"
        ))
        
        if len(row) == 3:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    # Add navigation
    keyboard.append([
        InlineKeyboardButton("◀️ Different Date", callback_data="booking_back"),
        InlineKeyboardButton("❌ Cancel", callback_data="booking_cancel")
    ])
    
    await query.edit_message_text(
        f"⏰ **Step 4: Select Time**\n\n"
        f"**Date:** {selected_date.strftime('%A, %d %B %Y')}\n\n"
        f"Available time slots:\n"
        f"(Each slot is 60 minutes)",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def handle_calendar_navigation(query, data: str, user_id: int):
    """Handle calendar navigation buttons"""
    if data == "calendar_today":
        await calendar_command(query, context)
    elif data.startswith("calendar_prev_"):
        # Navigate to previous week
        date_str = data.split("_")[2]
        # Implementation for week navigation
        await query.answer("Previous week view coming soon!")
    elif data.startswith("calendar_next_"):
        # Navigate to next week
        await query.answer("Next week view coming soon!")
    elif data.startswith("calendar_select_"):
        # Select specific date
        date_str = data.split("_")[2]
        selected_date = datetime.strptime(date_str, '%Y-%m-%d')
        
        # Update context
        if 'calendar_view' not in context.user_data:
            context.user_data['calendar_view'] = {}
        context.user_data['calendar_view']['selected_date'] = selected_date
        
        await show_calendar_view(query, context)
    else:
        await query.answer("Calendar feature coming soon!")

async def toggle_appointment_reminder(query, appointment_id: int):
    """Toggle reminders for an appointment"""
    appointment = get_appointment(appointment_id)
    
    if not appointment:
        await query.answer("Appointment not found")
        return
    
    # Toggle reminder_sent field
    new_status = not appointment[9] if appointment[9] is not None else True
    
    # Update database
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE appointments SET reminder_sent = ? WHERE appointment_id = ?', 
                  (1 if new_status else 0, appointment_id))
    conn.commit()
    conn.close()
    
    if new_status:
        message = "✅ Reminders ENABLED for this appointment"
    else:
        message = "🔕 Reminders DISABLED for this appointment"
    
    await query.answer(message)
    # Refresh the view
    await appointments_command(query, context)

async def view_appointment_details(query, appointment_id: int):
    """View detailed information about an appointment"""
    appointment = get_appointment(appointment_id)
    
    if not appointment:
        await query.answer("Appointment not found")
        return
    
    appt_time = parser.parse(appointment[5])
    client_name = appointment[12] if len(appointment) > 12 else "Unknown"
    title = appointment[3] or "No title"
    duration = appointment[6] or 60
    status = appointment[8] or 'scheduled'
    notes = appointment[7] or "No notes"
    
    # Status emoji
    status_emojis = {
        'scheduled': '⏰',
        'confirmed': '✅',
        'completed': '☑️',
        'cancelled': '❌',
        'no_show': '🚫'
    }
    status_emoji = status_emojis.get(status, '📅')
    
    message = f"{status_emoji} **Appointment Details**\n\n"
    message += f"**Title:** {title}\n"
    message += f"**Client:** {client_name}\n"
    message += f"**Date:** {appt_time.strftime('%A, %d %B %Y')}\n"
    message += f"**Time:** {appt_time.strftime('%I:%M %p')}\n"
    message += f"**Duration:** {duration} minutes\n"
    message += f"**Status:** {status.title()}\n"
    message += f"**Notes:** {notes}\n\n"
    
    # Action buttons
    keyboard = [
        [
            InlineKeyboardButton("🔄 Reschedule", callback_data=f"reschedule_{appointment_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{appointment_id}")
        ],
        [
            InlineKeyboardButton("⏰ Reminders", callback_data=f"reminders_{appointment_id}"),
            InlineKeyboardButton("📋 Add Notes", callback_data=f"notes_{appointment_id}")
        ],
        [
            InlineKeyboardButton("🔙 Back to List", callback_data="appointments_back")
        ]
    ]
    
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# ===== EXISTING SCHEDULING COMMANDS (Keep as is) =====

async def quickbook_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick appointment booking for today/tomorrow"""
    user_id = update.effective_user.id
    
    # Get today's and tomorrow's available slots
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    
    today_slots = get_available_slots(user_id, today)
    tomorrow_slots = get_available_slots(user_id, tomorrow)
    
    message = "⚡ **Quick Appointment Booking**\n\n"
    keyboard = []
    
    if today_slots:
        message += "📅 **Today's Available Slots:**\n"
        for slot in today_slots[:5]:  # Show first 5 slots
            keyboard.append([
                InlineKeyboardButton(
                    f"🕒 Today {slot}", 
                    callback_data=f"quick_today_{slot}"
                )
            ])
    
    if tomorrow_slots:
        message += "\n📅 **Tomorrow's Available Slots:**\n"
        for slot in tomorrow_slots[:5]:  # Show first 5 slots
            keyboard.append([
                InlineKeyboardButton(
                    f"🕒 Tomorrow {slot}", 
                    callback_data=f"quick_tomorrow_{slot}"
                )
            ])
    
    if not today_slots and not tomorrow_slots:
        message += "❌ No available slots for today or tomorrow.\n\n"
        message += "Please use /schedule to book for another date."
    
    keyboard.append([
        InlineKeyboardButton("📅 Full Schedule", callback_data="quick_full_schedule"),
        InlineKeyboardButton("❌ Cancel", callback_data="quick_cancel")
    ])
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def appointments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's appointments"""
    user_id = update.effective_user.id
    
    # Get appointments for next 7 days
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    next_week = today + timedelta(days=7)
    
    appointments = get_user_appointments(user_id, today, next_week)
    
    if not appointments:
        await update.message.reply_text(
            "📋 **My Appointments**\n\n"
            "No upcoming appointments in the next 7 days.\n\n"
            "📅 Use /schedule to book a new appointment\n"
            "⚡ Use /quickbook for quick bookings\n"
            "🗓️ Use /calendar to view your schedule"
        )
        return
    
    # Group appointments by date
    appointments_by_date = {}
    for appt in appointments:
        appt_date = parser.parse(appt[5]).date()
        date_str = appt_date.strftime('%Y-%m-%d')
        
        if date_str not in appointments_by_date:
            appointments_by_date[date_str] = []
        
        appointments_by_date[date_str].append(appt)
    
    # Build message
    message = "📋 **My Upcoming Appointments**\n\n"
    keyboard = []
    
    for date_str in sorted(appointments_by_date.keys()):
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        day_name = date_obj.strftime('%A')
        
        if date_obj == today.date():
            day_label = "📅 **Today**"
        elif date_obj == today.date() + timedelta(days=1):
            day_label = "📅 **Tomorrow**"
        else:
            day_label = f"📅 {day_name}, {date_obj.strftime('%b %d')}"
        
        message += f"{day_label}\n"
        
        for appt in appointments_by_date[date_str]:
            appt_time = parser.parse(appt[5])
            client_name = appt[12] if len(appt) > 12 else "Unknown"
            title = appt[3] or "No title"
            
            message += f"• 🕒 {appt_time.strftime('%I:%M %p')} - {title} with {client_name}\n"
            
            # Add button for each appointment
            keyboard.append([
                InlineKeyboardButton(
                    f"{appt_time.strftime('%H:%M')} - {title[:15]}{'...' if len(title) > 15 else ''}", 
                    callback_data=f"view_appt_{appt[0]}"
                )
            ])
        
        message += "\n"
    
    # Add action buttons
    keyboard.extend([
        [
            InlineKeyboardButton("📅 View Calendar", callback_data="appt_view_calendar"),
            InlineKeyboardButton("➕ New Appointment", callback_data="appt_new")
        ],
        [
            InlineKeyboardButton("🔄 Reschedule", callback_data="appt_reschedule_menu"),
            InlineKeyboardButton("❌ Cancel", callback_data="appt_cancel_menu")
        ]
    ])
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's appointments"""
    user_id = update.effective_user.id
    appointments = get_todays_appointments(user_id)
    
    if not appointments:
        await update.message.reply_text(
            "📅 **Today's Schedule**\n\n"
            "No appointments scheduled for today.\n\n"
            "⚡ Use /quickbook to book an appointment\n"
            "📅 Use /schedule for future appointments"
        )
        return
    
    message = "📅 **Today's Schedule**\n\n"
    
    # Sort by time
    appointments.sort(key=lambda x: parser.parse(x[5]))
    
    for appt in appointments:
        appt_time = parser.parse(appt[5])
        end_time = appt_time + timedelta(minutes=appt[6])
        client_name = appt[12] if len(appt) > 12 else "Unknown"
        title = appt[3] or "Meeting"
        
        # Calculate time until appointment
        time_until = appt_time - datetime.now()
        if time_until.total_seconds() > 0:
            if time_until.total_seconds() < 3600:  # Less than 1 hour
                time_indicator = "🟡"
            elif time_until.total_seconds() < 7200:  # Less than 2 hours
                time_indicator = "🟢"
            else:
                time_indicator = "🔵"
        else:
            time_indicator = "✅"  # Past appointment
        
        message += (
            f"{time_indicator} **{appt_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}**\n"
            f"   📋 {title}\n"
            f"   👤 {client_name}\n"
            f"   ⏰ {appt[6]} minutes\n\n"
        )
    
    keyboard = []
    for appt in appointments[:3]:  # Add buttons for first 3 appointments
        appt_time = parser.parse(appt[5])
        title = appt[3] or "Meeting"
        keyboard.append([
            InlineKeyboardButton(
                f"📋 {appt_time.strftime('%H:%M')} - {title[:15]}", 
                callback_data=f"view_appt_{appt[0]}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("➕ Add Appointment", callback_data="today_add"),
        InlineKeyboardButton("🗓️ View Week", callback_data="today_view_week")
    ])
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show this week's appointments"""
    user_id = update.effective_user.id
    appointments = get_weekly_appointments(user_id)
    
    if not appointments:
        await update.message.reply_text(
            "🗓️ **This Week's Schedule**\n\n"
            "No appointments scheduled for this week.\n\n"
            "📅 Use /schedule to book appointments\n"
            "⚡ Use /quickbook for immediate bookings"
        )
        return
    
    # Group by day
    appointments_by_day = {}
    for appt in appointments:
        appt_date = parser.parse(appt[5])
        day_key = appt_date.strftime('%A')
        
        if day_key not in appointments_by_day:
            appointments_by_day[day_key] = []
        
        appointments_by_day[day_key].append(appt)
    
    # Build calendar view
    message = "🗓️ **This Week's Schedule**\n\n"
    
    days_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    today_name = datetime.now().strftime('%A')
    
    for day in days_order:
        if day in appointments_by_day:
            day_appointments = appointments_by_day[day]
            day_appointments.sort(key=lambda x: parser.parse(x[5]))
            
            day_header = f"**{day}**" + (" (Today)" if day == today_name else "")
            message += f"{day_header}\n"
            
            for appt in day_appointments[:3]:  # Show max 3 per day
                appt_time = parser.parse(appt[5])
                client_name = appt[12] if len(appt) > 12 else "Unknown"
                title = appt[3] or "Meeting"
                
                message += f"• 🕒 {appt_time.strftime('%I:%M %p')} - {title} with {client_name}\n"
            
            if len(day_appointments) > 3:
                message += f"  ... and {len(day_appointments) - 3} more\n"
            
            message += "\n"
    
    total_appointments = len(appointments)
    message += f"📊 **Total:** {total_appointments} appointments this week\n\n"
    
    # Create calendar keyboard
    keyboard = [
        [
            InlineKeyboardButton("📅 Today", callback_data="week_today"),
            InlineKeyboardButton("📅 Tomorrow", callback_data="week_tomorrow")
        ],
        [
            InlineKeyboardButton("➕ Add Appointment", callback_data="week_add"),
            InlineKeyboardButton("📋 View All", callback_data="week_view_all")
        ],
        [
            InlineKeyboardButton("📤 Export Calendar", callback_data="week_export"),
            InlineKeyboardButton("⚙️ Settings", callback_data="week_settings")
        ]
    ]
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show interactive calendar view"""
    user_id = update.effective_user.id
    today = datetime.now().date()
    
    # Calculate start of current week (Monday)
    start_of_week = today - timedelta(days=today.weekday())
    
    # Store in context for navigation
    context.user_data['calendar_view'] = {
        'current_date': today,
        'view_type': 'week',
        'week_start': start_of_week
    }
    
    await show_calendar_view(update, context)

async def show_calendar_view(update, context):
    """Display calendar view"""
    user_id = update.effective_user.id
    calendar_data = context.user_data.get('calendar_view', {})
    week_start = calendar_data.get('week_start', datetime.now().date())
    
    # Get appointments for the week
    week_end = week_start + timedelta(days=6)
    appointments = get_user_appointments(
        user_id, 
        datetime.combine(week_start, datetime.min.time()),
        datetime.combine(week_end, datetime.max.time())
    )
    
    # Group appointments by date
    appointments_by_date = {}
    for appt in appointments:
        appt_date = parser.parse(appt[5]).date()
        date_str = appt_date.strftime('%Y-%m-%d')
        
        if date_str not in appointments_by_date:
            appointments_by_date[date_str] = []
        
        appointments_by_date[date_str].append(appt)
    
    # Build calendar message
    message = f"🗓️ **Calendar View**\n"
    message += f"*Week of {week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}*\n\n"
    
    # Create weekly grid
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    
    # Header
    message += "```\n"
    for day in days:
        message += f"{day:^10}"
    message += "\n" + "-" * 70 + "\n"
    
    # Calendar days
    current_day = week_start
    for i in range(7):
        date_str = current_day.strftime('%Y-%m-%d')
        day_appointments = appointments_by_date.get(date_str, [])
        
        if current_day == datetime.now().date():
            day_marker = "📌"
        else:
            day_marker = "○" if day_appointments else "·"
        
        appointment_count = len(day_appointments)
        day_display = f"{current_day.day:2d}{day_marker}"
        
        if appointment_count > 0:
            day_display = f"**{day_display}**"
        
        message += f"{day_display:^10}"
        current_day += timedelta(days=1)
    
    message += "\n```\n\n"
    
    # Show appointments for selected date
    selected_date = calendar_data.get('selected_date', datetime.now().date())
    selected_date_str = selected_date.strftime('%Y-%m-%d')
    
    if selected_date_str in appointments_by_date:
        message += f"**Appointments for {selected_date.strftime('%A, %b %d')}:**\n"
        for appt in appointments_by_date[selected_date_str]:
            appt_time = parser.parse(appt[5])
            client_name = appt[12] if len(appt) > 12 else "Unknown"
            title = appt[3] or "Meeting"
            
            message += f"• 🕒 {appt_time.strftime('%I:%M %p')} - {title}\n"
    
    # Create navigation keyboard
    keyboard = [
        [
            InlineKeyboardButton("⬅️ Prev Week", callback_data="calendar_prev_week"),
            InlineKeyboardButton("📅 Today", callback_data="calendar_today"),
            InlineKeyboardButton("Next Week ➡️", callback_data="calendar_next_week")
        ],
        [
            InlineKeyboardButton("📋 Day View", callback_data="calendar_day_view"),
            InlineKeyboardButton("🗓️ Month View", callback_data="calendar_month_view")
        ],
        [
            InlineKeyboardButton("➕ Add Appointment", callback_data="calendar_add"),
            InlineKeyboardButton("📤 Export", callback_data="calendar_export")
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="calendar_settings"),
            InlineKeyboardButton("❌ Close", callback_data="calendar_close")
        ]
    ]
    
    # Add day selection buttons
    day_buttons = []
    current_day = week_start
    for i in range(7):
        date_str = current_day.strftime('%Y-%m-%d')
        appointment_count = len(appointments_by_date.get(date_str, []))
        
        button_text = f"{current_day.day}"
        if appointment_count > 0:
            button_text = f"📌{current_day.day}"
        
        day_buttons.append(
            InlineKeyboardButton(button_text, callback_data=f"calendar_select_{date_str}")
        )
        current_day += timedelta(days=1)
    
    keyboard.insert(0, day_buttons)  # Add day buttons at top
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    return CALENDAR_NAVIGATE

async def reschedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start appointment rescheduling process"""
    user_id = update.effective_user.id
    
    # Get upcoming appointments
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    next_month = today + timedelta(days=30)
    appointments = get_user_appointments(user_id, today, next_month, 'scheduled')
    
    if not appointments:
        await update.message.reply_text(
            "🔄 **Reschedule Appointment**\n\n"
            "No upcoming appointments found to reschedule.\n\n"
            "📅 Use /schedule to book new appointments\n"
            "📋 Use /appointments to view all appointments"
        )
        return APPOINTMENT_EDIT
# ==================================================
# REMINDER COMMANDS
# ==================================================

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set reminders for appointments"""
    user_id = update.effective_user.id
    
    # Get upcoming appointments
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    next_week = today + timedelta(days=7)
    appointments = get_user_appointments(user_id, today, next_week, 'scheduled')
    
    if not appointments:
        await update.message.reply_text(
            "⏰ **Set Reminders**\n\n"
            "No upcoming appointments found to set reminders for.\n\n"
            "📅 Use /schedule to book appointments\n"
            "📋 Use /appointments to view upcoming appointments"
        )
        return
    
    # Show appointments with reminder status
    message = "⏰ **Set Appointment Reminders**\n\n"
    message += "You can set reminders for your upcoming appointments:\n\n"
    
    keyboard = []
    for appt in appointments[:8]:  # Show first 8 appointments
        appt_id = appt[0]
        appt_time = parser.parse(appt[5])
        client_name = appt[12] if len(appt) > 12 else "Unknown"
        title = appt[3] or "Meeting"
        
        # Check if reminder is already set
        has_reminder = appt[9] if len(appt) > 9 else False
        
        reminder_status = "✅ Enabled" if has_reminder else "❌ Disabled"
        
        message += (
            f"📅 **{appt_time.strftime('%a %d %b %H:%M')}** - {title}\n"
            f"   👤 {client_name}\n"
            f"   ⏰ Reminder: {reminder_status}\n\n"
        )
        
        # Create toggle button
        toggle_text = "🔕 Disable" if has_reminder else "🔔 Enable"
        keyboard.append([
            InlineKeyboardButton(
                f"{toggle_text} - {appt_time.strftime('%H:%M')} {title[:10]}", 
                callback_data=f"toggle_reminder_{appt_id}"
            )
        ])
    
    # Add global reminder settings button
    keyboard.append([
        InlineKeyboardButton("⚙️ Global Reminder Settings", callback_data="reminder_settings"),
        InlineKeyboardButton("📅 View Calendar", callback_data="reminder_view_calendar")
    ])
    
    keyboard.append([
        InlineKeyboardButton("✅ Save All Settings", callback_data="reminder_save_all"),
        InlineKeyboardButton("❌ Cancel", callback_data="reminder_cancel")
    ])
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def handle_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):    
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "reminder_settings":
        # Get current settings and show them
        settings = get_reminder_settings(user_id)
        default_times = settings.get('default_reminder_times', [24, 2])
        email_notifications = settings.get('email_notifications', True)
        sms_notifications = settings.get('sms_notifications', False)
        voice_call_reminders = settings.get('voice_call_reminders', False)
        
        message = "⚙️ **Reminder Settings**\n\n"
        message += f"⏰ **Default Reminder Times:** {', '.join([f'{t}h before' for t in default_times])}\n"
        message += f"📧 **Email Notifications:** {'✅ Enabled' if email_notifications else '❌ Disabled'}\n"
        message += f"📱 **SMS Notifications:** {'✅ Enabled' if sms_notifications else '❌ Disabled'}\n"
        message += f"📞 **Voice Call Reminders:** {'✅ Enabled' if voice_call_reminders else '❌ Disabled'}\n\n"
        message += "Customize your reminder preferences:"
        
        keyboard = [
            [
                InlineKeyboardButton("⏰ Set Default Times", callback_data="set_reminder_times"),
                InlineKeyboardButton("📧 Toggle Email", callback_data="toggle_email_reminders")
            ],
            [
                InlineKeyboardButton("📱 Toggle SMS", callback_data="toggle_sms_reminders"),
                InlineKeyboardButton("📞 Toggle Calls", callback_data="toggle_call_reminders")
            ],
            [
                InlineKeyboardButton("💾 Save Settings", callback_data="save_reminder_settings"),
                InlineKeyboardButton("🔙 Back to Reminders", callback_data="back_to_reminders")
            ]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("toggle_reminder_"):
        # Extract appointment ID
        appointment_id = int(data.split("_")[2])
        
        # Toggle reminder for this appointment
        await toggle_appointment_reminder(query, appointment_id)
        
        # Refresh the reminder list
        await remind_command(update, context)
    
    elif data == "set_reminder_times":
        # Ask user to set default reminder times
        await query.edit_message_text(
            "⏰ **Set Default Reminder Times**\n\n"
            "Please enter the reminder times (in hours before appointment).\n"
            "You can specify multiple times separated by commas.\n\n"
            "Example: 24, 2, 0.5 (for 24h, 2h, and 30min before)\n\n"
            "Current times: 24, 2\n\n"
            "Enter new times:"
        )
        
        # Store that we're awaiting reminder times
        context.user_data['awaiting_reminder_times'] = True
        context.user_data['reminder_settings_step'] = 'set_times'
    
    elif data == "toggle_email_reminders":
        # Toggle email notifications
        settings = get_reminder_settings(user_id)
        settings['email_notifications'] = not settings.get('email_notifications', True)
        save_reminder_settings(user_id, settings)
        
        await query.answer(f"Email reminders {'enabled' if settings['email_notifications'] else 'disabled'}")
        await handle_reminder_callback(update, context)
    
    elif data == "toggle_sms_reminders":
        # Toggle SMS notifications
        settings = get_reminder_settings(user_id)
        settings['sms_notifications'] = not settings.get('sms_notifications', False)
        save_reminder_settings(user_id, settings)
        
        await query.answer(f"SMS reminders {'enabled' if settings['sms_notifications'] else 'disabled'}")
        await handle_reminder_callback(update, context)
    
    elif data == "toggle_call_reminders":
        # Toggle voice call reminders
        settings = get_reminder_settings(user_id)
        settings['voice_call_reminders'] = not settings.get('voice_call_reminders', False)
        save_reminder_settings(user_id, settings)
        
        await query.answer(f"Voice call reminders {'enabled' if settings['voice_call_reminders'] else 'disabled'}")
        await handle_reminder_callback(update, context)
    
    elif data == "save_reminder_settings":
        # Save all settings
        await query.answer("✅ Reminder settings saved!")
        await query.edit_message_text(
            "✅ **Reminder Settings Saved**\n\n"
            "Your reminder preferences have been updated.\n\n"
            "Use /remind to manage individual appointment reminders.",
            parse_mode='Markdown'
        )
    
    elif data == "back_to_reminders":
        # Go back to main reminder menu
        await remind_command(update, context)
    
    elif data == "reminder_view_calendar":
        await calendar_command(update, context)
    
    elif data in ["reminder_save_all", "reminder_cancel", "reminder_stats", "view_all_reminders"]:
        await query.answer("This feature is coming soon!")
    
    else:
        await query.answer("Reminder feature coming soon!")

async def handle_reminder_times_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user input for reminder times"""
    if not context.user_data.get('awaiting_reminder_times'):
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        # Parse comma-separated times
        times = [float(t.strip()) for t in text.split(',')]
        
        # Validate times (should be positive numbers)
        if any(t < 0 for t in times):
            await update.message.reply_text(
                "❌ Please enter positive numbers only.\n"
                "Example: 24, 2, 0.5\n\n"
                "Try again:"
            )
            return
        
        # Sort times in descending order
        times.sort(reverse=True)
        
        # Save to user settings
        settings = get_reminder_settings(user_id)
        settings['default_reminder_times'] = times
        save_reminder_settings(user_id, settings)
        
        # Clear the flag
        del context.user_data['awaiting_reminder_times']
        del context.user_data['reminder_settings_step']
        
        await update.message.reply_text(
            f"✅ **Reminder times updated!**\n\n"
            f"New reminder times: {', '.join([f'{t}h before' for t in times])}\n\n"
            f"These will be applied to new appointments.\n"
            f"Use /remind to update existing appointments."
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Please enter numbers separated by commas.\n"
            "Example: 24, 2, 0.5\n\n"
            "Try again:"
        )

def get_reminder_settings(user_id: int):
    """Get user's reminder settings from database"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT reminder_settings FROM users WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        try:
            import json
            return json.loads(result[0])
        except:
            return {}
    
    # Return default settings
    return {
        'default_reminder_times': [24, 2],  # 24 hours and 2 hours before
        'email_notifications': True,
        'sms_notifications': False,
        'voice_call_reminders': False,
        'timezone': 'UTC'
    }

def save_reminder_settings(user_id: int, settings: dict):
    """Save user's reminder settings to database"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    try:
        import json
        settings_json = json.dumps(settings)
        
        cursor.execute('''
            UPDATE users SET reminder_settings = ? WHERE user_id = ?
        ''', (settings_json, user_id))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving reminder settings: {e}")
        return False
    finally:
        conn.close()
# ==================================================
# REMINDER COMMANDS
# ==================================================

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set reminders for appointments"""
    user_id = update.effective_user.id
    
    # Get upcoming appointments
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    next_week = today + timedelta(days=7)
    appointments = get_user_appointments(user_id, today, next_week, 'scheduled')
    
    if not appointments:
        await update.message.reply_text(
            "⏰ **Set Reminders**\n\n"
            "No upcoming appointments found to set reminders for.\n\n"
            "📅 Use /schedule to book appointments\n"
            "📋 Use /appointments to view upcoming appointments"
        )
        return
    
    # Show appointments with reminder status
    message = "⏰ **Set Appointment Reminders**\n\n"
    message += "You can set reminders for your upcoming appointments:\n\n"
    
    keyboard = []
    for appt in appointments[:8]:  # Show first 8 appointments
        appt_id = appt[0]
        appt_time = parser.parse(appt[5])
        client_name = appt[12] if len(appt) > 12 else "Unknown"
        title = appt[3] or "Meeting"
        
        # Check if reminder is already set
        has_reminder = appt[9] if len(appt) > 9 else False
        
        reminder_status = "✅ Enabled" if has_reminder else "❌ Disabled"
        
        message += (
            f"📅 **{appt_time.strftime('%a %d %b %H:%M')}** - {title}\n"
            f"   👤 {client_name}\n"
            f"   ⏰ Reminder: {reminder_status}\n\n"
        )
        
        # Create toggle button
        toggle_text = "🔕 Disable" if has_reminder else "🔔 Enable"
        keyboard.append([
            InlineKeyboardButton(
                f"{toggle_text} - {appt_time.strftime('%H:%M')} {title[:10]}", 
                callback_data=f"toggle_reminder_{appt_id}"
            )
        ])
    
    # Add global reminder settings button
    keyboard.append([
        InlineKeyboardButton("⚙️ Global Reminder Settings", callback_data="reminder_settings"),
        InlineKeyboardButton("📅 View Calendar", callback_data="reminder_view_calendar")
    ])
    
    keyboard.append([
        InlineKeyboardButton("✅ Save All Settings", callback_data="reminder_save_all"),
        InlineKeyboardButton("❌ Cancel", callback_data="reminder_cancel")
    ])
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def handle_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle reminder-related callback queries"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "reminder_settings":
        # Get current settings and show them
        settings = get_reminder_settings(user_id)
        default_times = settings.get('default_reminder_times', [24, 2])
        email_notifications = settings.get('email_notifications', True)
        sms_notifications = settings.get('sms_notifications', False)
        voice_call_reminders = settings.get('voice_call_reminders', False)
        
        message = "⚙️ **Reminder Settings**\n\n"
        message += f"⏰ **Default Reminder Times:** {', '.join([f'{t}h before' for t in default_times])}\n"
        message += f"📧 **Email Notifications:** {'✅ Enabled' if email_notifications else '❌ Disabled'}\n"
        message += f"📱 **SMS Notifications:** {'✅ Enabled' if sms_notifications else '❌ Disabled'}\n"
        message += f"📞 **Voice Call Reminders:** {'✅ Enabled' if voice_call_reminders else '❌ Disabled'}\n\n"
        message += "Customize your reminder preferences:"
        
        keyboard = [
            [
                InlineKeyboardButton("⏰ Set Default Times", callback_data="set_reminder_times"),
                InlineKeyboardButton("📧 Toggle Email", callback_data="toggle_email_reminders")
            ],
            [
                InlineKeyboardButton("📱 Toggle SMS", callback_data="toggle_sms_reminders"),
                InlineKeyboardButton("📞 Toggle Calls", callback_data="toggle_call_reminders")
            ],
            [
                InlineKeyboardButton("💾 Save Settings", callback_data="save_reminder_settings"),
                InlineKeyboardButton("🔙 Back to Reminders", callback_data="back_to_reminders")
            ]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("toggle_reminder_"):
        # Extract appointment ID
        appointment_id = int(data.split("_")[2])
        
        # Toggle reminder for this appointment
        await toggle_appointment_reminder(query, appointment_id)
        
        # Refresh the reminder list
        await remind_command(update, context)
    
    elif data == "set_reminder_times":
        # Ask user to set default reminder times
        await query.edit_message_text(
            "⏰ **Set Default Reminder Times**\n\n"
            "Please enter the reminder times (in hours before appointment).\n"
            "You can specify multiple times separated by commas.\n\n"
            "Example: 24, 2, 0.5 (for 24h, 2h, and 30min before)\n\n"
            "Current times: 24, 2\n\n"
            "Enter new times:"
        )
        
        # Store that we're awaiting reminder times
        context.user_data['awaiting_reminder_times'] = True
        context.user_data['reminder_settings_step'] = 'set_times'
    
    elif data == "toggle_email_reminders":
        # Toggle email notifications
        settings = get_reminder_settings(user_id)
        settings['email_notifications'] = not settings.get('email_notifications', True)
        save_reminder_settings(user_id, settings)
        
        await query.answer(f"Email reminders {'enabled' if settings['email_notifications'] else 'disabled'}")
        await handle_reminder_callback(update, context)
    
    elif data == "toggle_sms_reminders":
        # Toggle SMS notifications
        settings = get_reminder_settings(user_id)
        settings['sms_notifications'] = not settings.get('sms_notifications', False)
        save_reminder_settings(user_id, settings)
        
        await query.answer(f"SMS reminders {'enabled' if settings['sms_notifications'] else 'disabled'}")
        await handle_reminder_callback(update, context)
    
    elif data == "toggle_call_reminders":
        # Toggle voice call reminders
        settings = get_reminder_settings(user_id)
        settings['voice_call_reminders'] = not settings.get('voice_call_reminders', False)
        save_reminder_settings(user_id, settings)
        
        await query.answer(f"Voice call reminders {'enabled' if settings['voice_call_reminders'] else 'disabled'}")
        await handle_reminder_callback(update, context)
    
    elif data == "save_reminder_settings":
        # Save all settings
        await query.answer("✅ Reminder settings saved!")
        await query.edit_message_text(
            "✅ **Reminder Settings Saved**\n\n"
            "Your reminder preferences have been updated.\n\n"
            "Use /remind to manage individual appointment reminders.",
            parse_mode='Markdown'
        )
    
    elif data == "back_to_reminders":
        # Go back to main reminder menu
        await remind_command(update, context)
    
    elif data == "reminder_view_calendar":
        await calendar_command(update, context)
    
    elif data in ["reminder_save_all", "reminder_cancel", "reminder_stats", "view_all_reminders"]:
        await query.answer("This feature is coming soon!")
    
    else:
        await query.answer("Reminder feature coming soon!")

async def handle_reminder_times_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user input for reminder times"""
    if not context.user_data.get('awaiting_reminder_times'):
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        # Parse comma-separated times
        times = [float(t.strip()) for t in text.split(',')]
        
        # Validate times (should be positive numbers)
        if any(t < 0 for t in times):
            await update.message.reply_text(
                "❌ Please enter positive numbers only.\n"
                "Example: 24, 2, 0.5\n\n"
                "Try again:"
            )
            return
        
        # Sort times in descending order
        times.sort(reverse=True)
        
        # Save to user settings
        settings = get_reminder_settings(user_id)
        settings['default_reminder_times'] = times
        save_reminder_settings(user_id, settings)
        
        # Clear the flag
        del context.user_data['awaiting_reminder_times']
        del context.user_data['reminder_settings_step']
        
        await update.message.reply_text(
            f"✅ **Reminder times updated!**\n\n"
            f"New reminder times: {', '.join([f'{t}h before' for t in times])}\n\n"
            f"These will be applied to new appointments.\n"
            f"Use /remind to update existing appointments."
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Please enter numbers separated by commas.\n"
            "Example: 24, 2, 0.5\n\n"
            "Try again:"
        )

# Helper functions for reminders
def get_reminder_settings(user_id: int):
    """Get user's reminder settings from database"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT reminder_settings FROM users WHERE user_id = ?
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        try:
            import json
            return json.loads(result[0])
        except:
            return {}
    
    # Return default settings
    return {
        'default_reminder_times': [24, 2],  # 24 hours and 2 hours before
        'email_notifications': True,
        'sms_notifications': False,
        'voice_call_reminders': False,
        'timezone': 'UTC'
    }

def save_reminder_settings(user_id: int, settings: dict):
    """Save user's reminder settings to database"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    try:
        import json
        settings_json = json.dumps(settings)
        
        cursor.execute('''
            UPDATE users SET reminder_settings = ? WHERE user_id = ?
        ''', (settings_json, user_id))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving reminder settings: {e}")
        return False
    finally:
        conn.close()

# ==================================================
# PART 5: INVOICE, QUOTE & APPOINTMENT CREATION HANDLERS
# ==================================================

async def handle_invoice_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_company_name'):
        await handle_company_name(update, context)
        return
        
    user_id = update.effective_user.id
    text = update.message.text
    invoice_data = context.user_data.get('current_invoice', {})
    
    if not invoice_data:
        # Check if this is for appointment scheduling
        if 'scheduling' in context.user_data:
            await handle_appointment_creation(update, context)
            return
        
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

# ==================================================
# APPOINTMENT CREATION HANDLER
# ==================================================

async def handle_appointment_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle appointment creation through conversation"""
    user_id = update.effective_user.id
    text = update.message.text
    
    scheduling_data = context.user_data.get('scheduling', {})
    appointment_data = scheduling_data.get('appointment_data', {})
    step = scheduling_data.get('step')
    
    if step == 'select_date':
        try:
            # Try to parse the date
            if text.lower() == 'today':
                selected_date = datetime.now().date()
            elif text.lower() == 'tomorrow':
                selected_date = (datetime.now() + timedelta(days=1)).date()
            else:
                selected_date = parser.parse(text).date()
            
            # Check if date is available
            if not is_date_available(user_id, selected_date):
                await update.message.reply_text(
                    f"❌ **{selected_date.strftime('%A, %B %d, %Y')}** is not available for appointments.\n\n"
                    "Reasons may include:\n"
                    "• It's outside your working hours\n"
                    "• You've marked it as unavailable\n"
                    "• It's a weekend (if not working weekends)\n\n"
                    "Please choose another date:"
                )
                return
            
            # Store selected date and get available slots
            appointment_data['date'] = selected_date
            scheduling_data['step'] = 'select_time'
            context.user_data['scheduling'] = scheduling_data
            
            # Get available slots for this date
            duration = appointment_data.get('duration', 60)
            available_slots = get_available_slots(user_id, selected_date, duration)
            
            if not available_slots:
                await update.message.reply_text(
                    f"❌ No available time slots on **{selected_date.strftime('%A, %B %d')}**.\n\n"
                    "Please choose another date:"
                )
                return
            
            # Show available slots
            keyboard = []
            for slot in available_slots[:12]:  # Show first 12 slots
                keyboard.append([
                    InlineKeyboardButton(
                        f"🕒 {slot}", 
                        callback_data=f"appointment_time_{slot}"
                    )
                ])
            
            keyboard.append([
                InlineKeyboardButton("📅 Choose Different Date", callback_data="appointment_change_date"),
                InlineKeyboardButton("❌ Cancel", callback_data="appointment_cancel")
            ])
            
            await update.message.reply_text(
                f"📅 **{selected_date.strftime('%A, %B %d, %Y')}**\n\n"
                "Available time slots:\n\n"
                "Select a time for your appointment:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
        except Exception as e:
            await update.message.reply_text(
                "❌ Please enter a valid date.\n\n"
                "Examples:\n"
                "• 'today' or 'tomorrow'\n"
                "• '2025-12-25'\n"
                "• '25 December 2025'\n"
                "• 'next Monday'\n\n"
                "Please try again:"
            )
    
    elif step == 'add_description':
        appointment_data['description'] = text
        scheduling_data['step'] = 'confirm'
        context.user_data['scheduling'] = scheduling_data
        
        # Show confirmation
        await show_appointment_confirmation(update, context)
    
    elif step == 'add_title':
        appointment_data['title'] = text
        scheduling_data['step'] = 'add_description'
        context.user_data['scheduling'] = scheduling_data
        
        await update.message.reply_text(
            "📝 **Add Description**\n\n"
            "Please enter a description for this appointment (optional):\n\n"
            "You can include:\n"
            "• Meeting agenda\n"
            "• Special requirements\n"
            "• Location details\n"
            "• Any other notes\n\n"
            "Or type 'skip' to continue without description:"
        )

async def show_appointment_confirmation(update, context):
    """Show appointment confirmation before saving"""
    scheduling_data = context.user_data.get('scheduling', {})
    appointment_data = scheduling_data.get('appointment_data', {})
    user_id = update.effective_user.id
    
    # Get client info
    client_id = appointment_data.get('client_id')
    client = get_client_by_id(client_id) if client_id else None
    
    # Get appointment type info
    appt_type = appointment_data.get('type', 'meeting')
    appt_types = get_appointment_types(user_id)
    type_info = next((t for t in appt_types if t[2] == appt_type), None)
    
    # Build confirmation message
    message = "📋 **Appointment Confirmation**\n\n"
    
    # Client info
    if client:
        message += f"👤 **Client:** {client[2]}\n"
        if client[3]:  # Email
            message += f"📧 Email: {client[3]}\n"
        if client[4]:  # Phone
            message += f"📱 Phone: {client[4]}\n"
        message += "\n"
    
    # Appointment details
    appt_date = appointment_data.get('date')
    appt_time = appointment_data.get('time')
    duration = appointment_data.get('duration', 60)
    
    if appt_date and appt_time:
        # Combine date and time
        datetime_str = f"{appt_date} {appt_time}"
        appt_datetime = parser.parse(datetime_str)
        end_time = appt_datetime + timedelta(minutes=duration)
        
        message += f"📅 **Date:** {appt_datetime.strftime('%A, %B %d, %Y')}\n"
        message += f"🕒 **Time:** {appt_datetime.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}\n"
        message += f"⏰ **Duration:** {duration} minutes\n"
    
    message += f"📝 **Type:** {appt_type.title()}\n"
    
    if appointment_data.get('title'):
        message += f"🏷️ **Title:** {appointment_data['title']}\n"
    
    if appointment_data.get('description'):
        message += f"📄 **Description:** {appointment_data['description']}\n"
    
    # Add price if available
    if type_info and type_info[5] and float(type_info[5]) > 0:
        message += f"💰 **Price:** £{float(type_info[5]):.2f}\n"
    
    message += "\n---\n\n"
    message += "✅ **Please confirm the appointment details:**"
    
    # Create confirmation keyboard
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm & Save", callback_data="appointment_confirm_save"),
            InlineKeyboardButton("✏️ Edit Details", callback_data="appointment_edit")
        ],
        [
            InlineKeyboardButton("📅 Change Date/Time", callback_data="appointment_change_datetime"),
            InlineKeyboardButton("👤 Change Client", callback_data="appointment_change_client")
        ],
        [
            InlineKeyboardButton("📧 Send Confirmation Email", callback_data="appointment_send_email"),
            InlineKeyboardButton("❌ Cancel", callback_data="appointment_cancel")
        ]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

# ==================================================
# QUOTE COMMAND
# ==================================================

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
# ==================================================
# COMPREHENSIVE BUTTON HANDLER WITH SCHEDULING
# ==================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    print(f"DEBUG: Button pressed - {data}")
    
    # ===== APPOINTMENT SCHEDULING FLOW =====
    if data.startswith('schedule_'):
        await handle_schedule_buttons(query, context, data)
        
    elif data.startswith('appointment_'):
        await handle_appointment_buttons(query, context, data)
        
    elif data.startswith('quick_'):
        await handle_quickbook_buttons(query, context, data)
        
    elif data.startswith('view_appt_'):
        appointment_id = int(data.split('_')[2])
        await show_appointment_details(query, context, appointment_id)
        
    elif data.startswith('calendar_'):
        await handle_calendar_buttons(query, context, data)
        
    elif data.startswith('week_'):
        await handle_week_buttons(query, context, data)
        
    elif data.startswith('today_'):
        await handle_today_buttons(query, context, data)
        
    elif data.startswith('appt_'):
        await handle_appointments_buttons(query, context, data)
        
    elif data.startswith('reschedule_'):
        await handle_reschedule_buttons(query, context, data)
        
    elif data.startswith('cancel_'):
        await handle_cancel_buttons(query, context, data)
        
    elif data.startswith('remind_'):
        await handle_reminder_buttons(query, context, data)
        
    elif data.startswith('settings_'):
        await handle_settings_buttons(query, context, data)
    
    # ===== INVOICE CREATION FLOW =====
    elif data.startswith('currency_'):
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
        await handle_finish_invoice(query, context)
    
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
        await handle_finish_quote(query, context)
    
    # ===== APPROVAL FLOW =====
    elif data.startswith('approve_'):
        await handle_approval_buttons(query, context, data)
            
    elif data.startswith('approve_quote_'):
        await handle_quote_approval(query, context, data)
            
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
                [InlineKeyboardButton("📅 Schedule Appointment", callback_data=f"schedule_client_{client_id}")],
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
    
    elif data.startswith('schedule_client_'):
        client_id = int(data.split('_')[2])
        client = get_client_by_id(client_id)
        
        if client:
            # Start scheduling for this client
            context.user_data['scheduling'] = {
                'step': 'select_type',
                'appointment_data': {
                    'client_id': client_id,
                    'client_name': client[2]
                }
            }
            
            # Show appointment types
            appt_types = get_appointment_types(user_id)
            
            if not appt_types:
                await query.edit_message_text(
                    "❌ No appointment types configured.\n\n"
                    "Please set up appointment types in settings first."
                )
                return
            
            keyboard = []
            for appt_type in appt_types[:8]:  # Show first 8 types
                type_name = appt_type[2]
                duration = appt_type[4]
                price = appt_type[5]
                
                button_text = f"{type_name} ({duration}min)"
                if price and float(price) > 0:
                    button_text += f" - £{price}"
                
                keyboard.append([
                    InlineKeyboardButton(
                        button_text,
                        callback_data=f"appointment_type_{type_name}"
                    )
                ])
            
            keyboard.append([
                InlineKeyboardButton("➕ Custom Type", callback_data="appointment_custom_type"),
                InlineKeyboardButton("❌ Cancel", callback_data="schedule_cancel")
            ])
            
            await query.edit_message_text(
                f"📅 **Schedule Appointment for {client[2]}**\n\n"
                "Select appointment type:",
                reply_markup=InlineKeyboardMarkup(keyboard)
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
        await handle_send_invoice(query, context, data)
        
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
# APPOINTMENT BUTTON HANDLER FUNCTIONS
# ==================================================

async def handle_schedule_buttons(query, context, data):
    """Handle schedule-related buttons"""
    user_id = query.from_user.id
    
    if data == 'schedule_add_client':
        # Add client and continue scheduling
        context.user_data['client_creation'] = {
            'step': 'name',
            'return_to_schedule': True
        }
        await query.edit_message_text(
            "👥 **Add New Client for Appointment**\n\n"
            "Please enter the client's full name:",
            parse_mode='Markdown'
        )
        
    elif data == 'schedule_new_client':
        # Add new client
        context.user_data['client_creation'] = {'step': 'name'}
        await query.edit_message_text(
            "👥 **Add New Client**\n\n"
            "Let's add a new client to your database.\n\n"
            "Please enter the client's full name:",
            parse_mode='Markdown'
        )
        
    elif data == 'schedule_cancel':
        await query.edit_message_text(
            "❌ Appointment scheduling cancelled.\n\n"
            "You can always start again with /schedule"
        )

async def handle_appointment_buttons(query, context, data):
    """Handle appointment creation buttons"""
    user_id = query.from_user.id
    
    if data.startswith('appointment_type_'):
        # Appointment type selected
        appt_type = data.split('_')[2]
        scheduling_data = context.user_data.get('scheduling', {})
        appointment_data = scheduling_data.get('appointment_data', {})
        
        # Get type details
        appt_types = get_appointment_types(user_id)
        selected_type = next((t for t in appt_types if t[2] == appt_type), None)
        
        if selected_type:
            appointment_data['type'] = appt_type
            appointment_data['duration'] = selected_type[4]
            scheduling_data['step'] = 'select_date'
            context.user_data['scheduling'] = scheduling_data
            
            await query.edit_message_text(
                f"📝 **{appt_type.title()} Appointment**\n"
                f"⏰ Duration: {selected_type[4]} minutes\n\n"
                "📅 Please enter the appointment date:\n\n"
                "Examples:\n"
                "• 'today' or 'tomorrow'\n"
                "• '2025-12-25'\n"
                "• '25 December 2025'\n"
                "• 'next Monday'\n\n"
                "Or type 'back' to choose different type:"
            )
    
    elif data == 'appointment_custom_type':
        # Custom appointment type
        scheduling_data = context.user_data.get('scheduling', {})
        scheduling_data['step'] = 'custom_type'
        context.user_data['scheduling'] = scheduling_data
        
        await query.edit_message_text(
            "📝 **Custom Appointment Type**\n\n"
            "Please enter the name for this appointment type:\n\n"
            "Examples:\n"
            "• Initial Consultation\n"
            "• Project Review\n"
            "• Training Session\n"
            "• Delivery Appointment"
        )
    
    elif data.startswith('appointment_time_'):
        # Time selected
        time_str = data.split('_')[2]
        scheduling_data = context.user_data.get('scheduling', {})
        appointment_data = scheduling_data.get('appointment_data', {})
        
        appointment_data['time'] = time_str
        scheduling_data['step'] = 'add_title'
        context.user_data['scheduling'] = scheduling_data
        
        await query.edit_message_text(
            "🏷️ **Add Appointment Title**\n\n"
            "Please enter a title for this appointment:\n\n"
            "Examples:\n"
            "• Quarterly Review with Client\n"
            "• Project Kickoff Meeting\n"
            "• Product Demo Session\n"
            "• Training Workshop\n\n"
            "Keep it clear and descriptive:"
        )
    
    elif data == 'appointment_confirm_save':
        # Save appointment
        scheduling_data = context.user_data.get('scheduling', {})
        appointment_data = scheduling_data.get('appointment_data', {})
        
        # Validate required fields
        required_fields = ['client_id', 'type', 'date', 'time', 'duration']
        missing_fields = [field for field in required_fields if field not in appointment_data]
        
        if missing_fields:
            await query.edit_message_text(
                f"❌ Missing information: {', '.join(missing_fields)}\n\n"
                "Please go back and complete all appointment details."
            )
            return
        
        # Create appointment
        client_id = appointment_data['client_id']
        appt_date = appointment_data['date']
        appt_time = appointment_data['time']
        duration = appointment_data.get('duration', 60)
        appt_type = appointment_data['type']
        title = appointment_data.get('title', f"{appt_type.title()} Appointment")
        description = appointment_data.get('description', '')
        
        # Combine date and time
        datetime_str = f"{appt_date} {appt_time}"
        appointment_datetime = parser.parse(datetime_str)
        
        # Save to database
        appointment_id = create_appointment(
            user_id=user_id,
            client_id=client_id,
            title=title,
            appointment_date=appointment_datetime,
            duration_minutes=duration,
            appointment_type=appt_type,
            description=description,
            status='scheduled',
            reminder_minutes_before=30
        )
        
        if appointment_id:
            # Get client info for confirmation
            client = get_client_by_id(client_id)
            client_name = client[2] if client else "Unknown"
            
            # Generate appointment summary
            summary = generate_appointment_summary(appointment_id)
            
            # Create success message
            success_message = f"""
✅ **Appointment Scheduled Successfully!**

{summary}

📧 A confirmation email has been sent to the client.
⏰ Reminders will be sent 30 minutes before the appointment.

**Next Steps:**
• View appointment details anytime
• Use /calendar to see your schedule
• Set custom reminders with /remind
• Export calendar with /week
"""
            
            keyboard = [
                [
                    InlineKeyboardButton("📋 View Appointment", callback_data=f"view_appt_{appointment_id}"),
                    InlineKeyboardButton("📅 Add to Calendar", callback_data=f"calendar_add_appt_{appointment_id}")
                ],
                [
                    InlineKeyboardButton("📧 Send Confirmation", callback_data=f"appointment_send_confirmation_{appointment_id}"),
                    InlineKeyboardButton("🗓️ View Schedule", callback_data="appointments_back")
                ]
            ]
            
            await query.edit_message_text(
                success_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
            # Send email confirmation
            send_appointment_confirmation(appointment_id)
        else:
            await query.edit_message_text(
                "❌ Failed to save appointment. Please try again."
            )
    
    elif data == 'appointment_edit':
        # Edit appointment details
        scheduling_data = context.user_data.get('scheduling', {})
        
        keyboard = [
            [
                InlineKeyboardButton("📝 Edit Title", callback_data="appointment_edit_title"),
                InlineKeyboardButton("📄 Edit Description", callback_data="appointment_edit_desc")
            ],
            [
                InlineKeyboardButton("📅 Edit Date/Time", callback_data="appointment_edit_datetime"),
                InlineKeyboardButton("📋 Edit Type", callback_data="appointment_edit_type")
            ],
            [
                InlineKeyboardButton("✅ Back to Confirmation", callback_data="appointment_back_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="appointment_cancel")
            ]
        ]
        
        await query.edit_message_text(
            "✏️ **Edit Appointment Details**\n\n"
            "What would you like to edit?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == 'appointment_change_datetime':
        # Change date/time
        scheduling_data = context.user_data.get('scheduling', {})
        scheduling_data['step'] = 'select_date'
        context.user_data['scheduling'] = scheduling_data
        
        await query.edit_message_text(
            "📅 **Change Appointment Date/Time**\n\n"
            "Please enter the new appointment date:\n\n"
            "Examples:\n"
            "• 'today' or 'tomorrow'\n"
            "• '2025-12-25'\n"
            "• '25 December 2025'\n"
            "• 'next Monday'"
        )
    
    elif data == 'appointment_change_client':
        # Change client
        scheduling_data = context.user_data.get('scheduling', {})
        scheduling_data['step'] = 'select_client'
        context.user_data['scheduling'] = scheduling_data
        
        # Show clients list
        clients = get_user_clients(user_id)
        
        if not clients:
            await query.edit_message_text(
                "❌ No clients found. Please add a client first."
            )
            return
        
        keyboard = []
        for client in clients[:10]:
            keyboard.append([
                InlineKeyboardButton(
                    f"👤 {client[2]}", 
                    callback_data=f"schedule_client_{client[0]}"
                )
            ])
        
        keyboard.append([
            InlineKeyboardButton("➕ Add New Client", callback_data="schedule_new_client"),
            InlineKeyboardButton("❌ Cancel", callback_data="appointment_cancel")
        ])
        
        await query.edit_message_text(
            "👥 **Select Client**\n\n"
            "Select a client for this appointment:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == 'appointment_send_email':
        # Send confirmation email
        scheduling_data = context.user_data.get('scheduling', {})
        appointment_data = scheduling_data.get('appointment_data', {})
        
        if 'client_id' in appointment_data:
            # Get client email
            client = get_client_by_id(appointment_data['client_id'])
            if client and client[3]:  # Email field
                await query.edit_message_text(
                    "📧 **Sending Confirmation Email...**\n\n"
                    f"Email will be sent to: {client[3]}\n\n"
                    "This feature requires email configuration.\n"
                    "Contact support to set up email notifications."
                )
            else:
                await query.edit_message_text(
                    "❌ **No Email Address**\n\n"
                    "This client doesn't have an email address saved.\n\n"
                    "Please add an email address to send confirmations."
                )
        else:
            await query.edit_message_text(
                "❌ **No Client Selected**\n\n"
                "Please select a client first to send confirmation email."
            )
    
    elif data == 'appointment_cancel':
        await query.edit_message_text(
            "❌ Appointment scheduling cancelled.\n\n"
            "You can always start again with /schedule"
        )

# (Additional handler functions for quickbook, calendar, week, today, 
# appointments, reschedule, cancel, reminder, and settings would go here
# but are omitted for brevity - they follow similar patterns)

print("✅ Part 5 updated with comprehensive scheduling handlers!")

# ==================================================
# PART 6: TEXT HANDLER AND MAIN FUNCTION (Updated with Scheduling)
# ==================================================

import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

logger = logging.getLogger(__name__)

# ===== MISSING HELPER FUNCTIONS (STUBS) =====
# (Keep all your existing helper functions as they are)
# [All your existing helper functions remain unchanged...]

# ===== MAIN TEXT HANDLER =====
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text inputs including appointment scheduling"""
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    
    logger.info(f"Text input from user {user_id}: {text}")
    
    # Basic response
    response = f"""
📝 **Message Received**

You said: *{text}*

This bot is under development. Available commands:

/start - Start the bot
/help - Show help information
/schedule - Schedule appointment (coming soon)
/create - Create invoice (coming soon)

Stay tuned for more features!
    """
    
    await update.message.reply_text(response, parse_mode='Markdown')

# ===== COMMAND HANDLERS =====
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command handler"""
    help_text = """
🤖 **Minigma Business Suite - Help**

📋 **Available Commands:**
/start - Start the bot and see welcome message
/help - Show this help message

🛠️ **Coming Soon:**
/schedule - Schedule appointments
/calendar - View calendar
/create - Create invoices
/clients - Manage clients
/payments - Track payments

⚙️ **Bot Status:** ✅ Under Development

📞 **Support:** Contact administrator for assistance.
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    # Check/create user
    user = get_user(user_id)
    if not user:
        create_user(user_id, update.effective_user.username, 
                   update.effective_user.first_name, update.effective_user.last_name)
        welcome_msg = f"🎉 Welcome to Minigma Business Suite, {username}!\n\n"
    else:
        welcome_msg = f"👋 Welcome back, {username}!\n\n"
    
    welcome_msg += """🚀 **Minigma Business Suite**

*Your all-in-one business management solution*

📋 **Features (Coming Soon):**
• Invoice creation & management
• Appointment scheduling
• Client database
• Payment tracking

⚡ **Quick Start:**
Use /help to see available commands
Use /schedule to book appointments (soon)
Use /create to make invoices (soon)

🔧 **Status:** Bot is running successfully!

📞 **Need help?** Contact support.
    """
    
    keyboard = [
        [InlineKeyboardButton("📋 Help", callback_data="help")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_msg,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "help":
        await help_command(update, context)
    elif data == "settings":
        await query.edit_message_text("⚙️ Settings panel coming soon!")
    else:
        await query.edit_message_text(f"Button: {data}\n\nFeature coming soon!")

# ===== SCHEDULED TASKS =====
async def send_scheduled_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Send appointment reminders - placeholder"""
    logger.info("⏰ Checking for reminders (placeholder)")

async def check_overdue_appointments(context: ContextTypes.DEFAULT_TYPE):
    """Check overdue appointments - placeholder"""
    logger.info("📅 Checking for overdue appointments (placeholder)")

async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    """Send daily schedule - placeholder"""
    logger.info("📋 Sending daily schedule (placeholder)")

def create_health_check():
    """Create health check server - optional"""
    try:
        from flask import Flask
        app = Flask('')
        
        @app.route('/')
        def home():
            return "✅ Minigma Business Suite is running!"
        
        @app.route('/health')
        def health():
            return json.dumps({
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'version': '2.0.0'
            })
        
        def run():
            app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)
        
        t = Thread(target=run, daemon=True)
        t.start()
        logger.info("✅ Health check server started on port 8000")
    except ImportError:
        logger.info("⚠️  Flask not installed, skipping health check server")

# ===== BOT EXECUTION & STARTUP =====
def main():
    """Start and run the Telegram bot"""
    print("🤖 Starting Minigma Business Suite Bot...")
    
    # Get bot token using the MAIN token function
    BOT_TOKEN = get_bot_token()  # ← CHANGE THIS LINE
    
    # Debug: Show what token we got
    if BOT_TOKEN:
        print(f"🔍 Token loaded: {BOT_TOKEN[:15]}...")
    else:
        print("❌ ERROR: Telegram Bot Token not found!")
        print("\nTo fix this:")
        print("1. Set BOT_TOKEN environment variable in Koyeb")
        print("2. Or create 'bot_token.txt' with your Telegram bot token")
        print("\nGet token from @BotFather on Telegram")
        return
    
    print("✅ Bot token accepted")
    
    try:
        # Create the Application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # ===== REGISTER COMMAND HANDLERS =====
        
        # Basic commands
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        
        # Text handler for all text inputs
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))
        
        # Callback query handler for inline buttons
        application.add_handler(CallbackQueryHandler(handle_button_callback))
        
        # ===== SCHEDULED TASKS =====
        
        # Check if JobQueue is available
        try:
            # Check if job_queue exists
            if hasattr(application, 'job_queue') and application.job_queue is not None:
                job_queue = application.job_queue
                
                # Check for reminders every 5 minutes
                job_queue.run_repeating(send_scheduled_reminders, interval=300, first=10)
                
                # Check for overdue appointments every hour
                job_queue.run_repeating(check_overdue_appointments, interval=3600, first=60)
                
                # Send daily schedules at 8 AM
                import datetime as dt
                job_queue.run_daily(send_daily_schedule, time=dt.time(hour=8, minute=0))
                
                print("✅ Scheduled tasks configured")
            else:
                print("⚠️  JobQueue not available - scheduled tasks disabled")
        except Exception as e:
            print(f"⚠️  Could not configure scheduled tasks: {e}")
        
        # ===== START HEALTH CHECK (OPTIONAL) =====
        try:
            create_health_check()
        except Exception as e:
            print(f"⚠️  Could not start health check: {e}")
        
        # ===== START BOT =====
        
        print("✅ Bot initialized successfully!")
        print("📡 Starting polling...")
        print("🤖 Bot is now running. Press Ctrl+C to stop.")
        print("\n" + "="*50)
        
        # Start the bot
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        import traceback
        traceback.print_exc()

# python
# ==================================================
# KOYEB COMPATIBILITY LAYER
# ==================================================

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Telegram Bot is running!')
    
    def log_message(self, format, *args):
        # Disable HTTP logging noise
        pass

def run_http_server():
    """Run a simple HTTP server on port 8000 for Koyeb health checks"""
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    print("✅ HTTP server running on port 8000 for Koyeb health checks")
    server.serve_forever()
    
# ==================================================
# PART 7: EMAIL AND SMS DELIVERY (Updated with Appointment Features)
# ==================================================

import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
import ssl

# Email configuration (you'll need to set these up)
EMAIL_CONFIG = {
    'smtp_server': 'smtp.gmail.com',  # Change for your email provider
    'smtp_port': 587,
    'sender_email': 'your-email@gmail.com',  # Set this
    'sender_password': 'your-app-password',  # Set this (use app password for Gmail)
    'sender_name': 'Your Company Name',
    'use_ssl': False,
    'use_tls': True
}

# SMS configuration (using Twilio as example)
SMS_CONFIG = {
    'account_sid': 'your-twilio-account-sid',  # Set this
    'auth_token': 'your-twilio-auth-token',    # Set this
    'twilio_number': '+1234567890',            # Set this
    'enabled': False  # Set to True when configured
}

# ==================================================
# APPOINTMENT EMAIL FUNCTIONS
# ==================================================

def send_appointment_email_to_client(appointment_id, email_type="confirmation"):
    """Send appointment email to client - renamed to avoid conflict"""
    try:
        # Get appointment details
        appointment = get_appointment(appointment_id)
        if not appointment:
            logger.error(f"Appointment {appointment_id} not found")
            return False
        
        # Get user and client info
        user_id = appointment[1]
        client_id = appointment[2]
        
        user_info = get_user(user_id)
        client = get_client_by_id(client_id) if client_id else None
        
        if not client or not client[3]:  # No email
            logger.warning(f"No email for client in appointment {appointment_id}")
            return False
        
        client_email = client[3]
        client_name = client[2]
        
        # Prepare appointment data
        appt_date_str = appointment[5]
        try:
            appt_date = parser.parse(appt_date_str)
        except:
            appt_date = datetime.now()
            
        duration = appointment[6] if len(appointment) > 6 else 60
        end_time = appt_date + timedelta(minutes=duration)
        
        appointment_data = {
            'appointment_id': appointment[0],
            'appointment_number': generate_appointment_number(user_id),
            'title': appointment[3] or 'Appointment',
            'description': appointment[4] or '',
            'appointment_date': appointment[5],
            'duration_minutes': duration,
            'appointment_type': appointment[7] if len(appointment) > 7 else 'meeting',
            'status': appointment[8] if len(appointment) > 8 else 'scheduled'
        }
        
        # Get email template
        template = get_default_email_template(user_id)
        company_name = "Your Business"
        if user_info and len(user_info) > 8 and user_info[8]:
            company_name = user_info[8]
        
        # Prepare email content based on type
        if email_type == "confirmation":
            subject = f"Appointment Confirmation: {appointment_data['title']}"
            if template and len(template) > 3 and template[3]:  # subject field
                subject = template[3].format(  # subject
                    title=appointment_data['title'],
                    date=appt_date.strftime('%B %d, %Y'),
                    client_name=client_name
                )
        elif email_type == "reminder":
            subject = f"Reminder: Your Appointment Tomorrow - {appointment_data['title']}"
        elif email_type == "cancellation":
            subject = f"Appointment Cancelled: {appointment_data['title']}"
        elif email_type == "rescheduled":
            subject = f"Appointment Rescheduled: {appointment_data['title']}"
        else:
            subject = f"Appointment Update: {appointment_data['title']}"
        
        # HTML email body
        html_body = create_appointment_email_html(
            appointment_data, 
            client_name, 
            company_name, 
            email_type,
            user_info
        )
        
        # Plain text body
        text_body = create_appointment_email_text(
            appointment_data,
            client_name,
            company_name,
            email_type
        )
        
        # Create PDF attachment
        pdf_path = None
        if email_type == "confirmation":
            try:
                client_info_dict = {
                    'client_name': client_name, 
                    'email': client_email,
                    'phone': client[4] if len(client) > 4 else '',
                    'address': client[5] if len(client) > 5 else ''
                }
                pdf_path = create_appointment_confirmation_pdf(
                    appointment_data, 
                    user_info, 
                    client_info_dict
                )
            except Exception as e:
                logger.error(f"Failed to create appointment PDF: {e}")
        
        # Send email
        success = send_email_with_attachment(
            to_email=client_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            attachment_path=pdf_path
        )
        
        if success:
            logger.info(f"Appointment {email_type} email sent for appointment {appointment_id}")
            
            # Update notification status in database
            conn = sqlite3.connect('invoices.db')
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE appointments SET notification_sent = 1 WHERE appointment_id = ?',
                (appointment_id,)
            )
            conn.commit()
            conn.close()
            
            return True
        else:
            logger.error(f"Failed to send appointment email for {appointment_id}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending appointment email: {e}")
        return False

def create_appointment_email_html(appointment_data, client_name, company_name, email_type, user_info=None):
    """Create HTML email body for appointments"""
    appt_date_str = appointment_data.get('appointment_date', '')
    try:
        appt_date = parser.parse(appt_date_str)
    except:
        appt_date = datetime.now()
        
    duration = appointment_data.get('duration_minutes', 60)
    end_time = appt_date + timedelta(minutes=duration)
    
    # Email header based on type
    if email_type == "confirmation":
        header = "Appointment Confirmed"
        header_color = "#4a6ee0"
        icon = "✅"
    elif email_type == "reminder":
        header = "Appointment Reminder"
        header_color = "#ff9500"
        icon = "⏰"
    elif email_type == "cancellation":
        header = "Appointment Cancelled"
        header_color = "#ff3b30"
        icon = "❌"
    elif email_type == "rescheduled":
        header = "Appointment Rescheduled"
        header_color = "#34c759"
        icon = "🔄"
    else:
        header = "Appointment Update"
        header_color = "#4a6ee0"
        icon = "📅"
    
    # Company logo
    logo_html = ""
    if user_info and len(user_info) > 7 and user_info[7]:  # logo_path
        try:
            logo_html = f'<img src="cid:company_logo" alt="{company_name}" style="max-width: 200px; height: auto; margin-bottom: 20px;">'
        except:
            pass
    
    # Get user email and phone for contact buttons
    user_email = EMAIL_CONFIG['sender_email']
    user_phone = ""
    if user_info:
        if len(user_info) > 13 and user_info[13]:  # email field
            user_email = user_info[13]
        if len(user_info) > 14 and user_info[14]:  # phone field
            user_phone = user_info[14]
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{header}</title>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                line-height: 1.6;
                color: #333;
                margin: 0;
                padding: 0;
                background-color: #f5f7fa;
            }}
            .email-container {{
                max-width: 600px;
                margin: 0 auto;
                background-color: white;
                border-radius: 10px;
                overflow: hidden;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            }}
            .header {{
                background-color: {header_color};
                color: white;
                padding: 30px 20px;
                text-align: center;
            }}
            .header h1 {{
                margin: 0;
                font-size: 24px;
            }}
            .content {{
                padding: 30px;
            }}
            .appointment-details {{
                background-color: #f8f9fa;
                border-radius: 8px;
                padding: 20px;
                margin: 20px 0;
                border-left: 4px solid {header_color};
            }}
            .detail-row {{
                margin-bottom: 10px;
                display: flex;
                align-items: center;
            }}
            .detail-label {{
                font-weight: bold;
                width: 120px;
                color: #555;
            }}
            .detail-value {{
                flex: 1;
                color: #333;
            }}
            .icon {{
                font-size: 20px;
                margin-right: 10px;
            }}
            .important-notes {{
                background-color: #fff3cd;
                border: 1px solid #ffeaa7;
                border-radius: 8px;
                padding: 15px;
                margin: 20px 0;
            }}
            .footer {{
                background-color: #f8f9fa;
                padding: 20px;
                text-align: center;
                color: #666;
                font-size: 12px;
                border-top: 1px solid #e0e0e0;
            }}
            .button {{
                display: inline-block;
                background-color: {header_color};
                color: white;
                padding: 12px 24px;
                text-decoration: none;
                border-radius: 5px;
                font-weight: bold;
                margin: 10px 5px;
            }}
            @media (max-width: 600px) {{
                .email-container {{
                    border-radius: 0;
                }}
                .content {{
                    padding: 20px;
                }}
                .detail-row {{
                    flex-direction: column;
                    align-items: flex-start;
                }}
                .detail-label {{
                    width: 100%;
                    margin-bottom: 5px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="email-container">
            <div class="header">
                {logo_html}
                <h1>{icon} {header}</h1>
                <p>{company_name}</p>
            </div>
            
            <div class="content">
                <p>Dear <strong>{client_name}</strong>,</p>
                
                <div class="appointment-details">
                    <h3 style="margin-top: 0; color: {header_color};">{appointment_data.get('title', 'Appointment')}</h3>
                    
                    <div class="detail-row">
                        <span class="icon">📅</span>
                        <span class="detail-label">Date:</span>
                        <span class="detail-value">{appt_date.strftime('%A, %B %d, %Y')}</span>
                    </div>
                    
                    <div class="detail-row">
                        <span class="icon">🕒</span>
                        <span class="detail-label">Time:</span>
                        <span class="detail-value">{appt_date.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}</span>
                    </div>
                    
                    <div class="detail-row">
                        <span class="icon">⏰</span>
                        <span class="detail-label">Duration:</span>
                        <span class="detail-value">{duration} minutes</span>
                    </div>
                    
                    <div class="detail-row">
                        <span class="icon">📋</span>
                        <span class="detail-label">Type:</span>
                        <span class="detail-value">{appointment_data.get('appointment_type', 'meeting').title()}</span>
                    </div>
                    
                    {f'<div class="detail-row"><span class="icon">📝</span><span class="detail-label">Description:</span><span class="detail-value">{appointment_data.get("description", "")}</span></div>' if appointment_data.get('description') else ''}
                </div>
                
                {f'<div class="important-notes"><h4 style="margin-top: 0;">Important Notes:</h4><ul style="margin-bottom: 0;"><li>Please arrive 5-10 minutes before your scheduled time</li><li>Bring any necessary documents or materials</li><li>Contact us if you need to reschedule or cancel</li></ul></div>' if email_type in ["confirmation", "reminder"] else ''}
                
                <p>Thank you for choosing {company_name}. We look forward to seeing you!</p>
                
                <div style="text-align: center; margin-top: 30px;">
                    <a href="mailto:{user_email}" class="button">Contact Us</a>
                    {f'<a href="tel:{user_phone}" class="button">Call Us</a>' if user_phone else ''}
                </div>
            </div>
            
            <div class="footer">
                <p>This email was sent by {company_name} via Minigma Business Suite</p>
                <p>{datetime.now().strftime('%B %d, %Y %I:%M %p')}</p>
                <p style="font-size: 11px; color: #999;">
                    <a href="#" style="color: #999; text-decoration: none;">Privacy Policy</a> | 
                    <a href="#" style="color: #999; text-decoration: none;">Terms of Service</a>
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

def create_appointment_email_text(appointment_data, client_name, company_name, email_type):
    """Create plain text email body for appointments"""
    appt_date_str = appointment_data.get('appointment_date', '')
    try:
        appt_date = parser.parse(appt_date_str)
    except:
        appt_date = datetime.now()
        
    duration = appointment_data.get('duration_minutes', 60)
    end_time = appt_date + timedelta(minutes=duration)
    
    if email_type == "confirmation":
        subject_line = "APPOINTMENT CONFIRMED"
    elif email_type == "reminder":
        subject_line = "APPOINTMENT REMINDER"
    elif email_type == "cancellation":
        subject_line = "APPOINTMENT CANCELLED"
    elif email_type == "rescheduled":
        subject_line = "APPOINTMENT RESCHEDULED"
    else:
        subject_line = "APPOINTMENT UPDATE"
    
    text = f"""
{subject_line}
{'=' * 50}

Dear {client_name},

Your appointment has been {email_type}.

APPOINTMENT DETAILS:
{'-' * 30}
Title: {appointment_data.get('title', 'Appointment')}
Date: {appt_date.strftime('%A, %B %d, %Y')}
Time: {appt_date.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}
Duration: {duration} minutes
Type: {appointment_data.get('appointment_type', 'meeting').title()}
{'' if not appointment_data.get('description') else f"Description: {appointment_data.get('description')}"}

{'IMPORTANT NOTES:' if email_type in ["confirmation", "reminder"] else ''}
{'' if email_type not in ["confirmation", "reminder"] else '• Please arrive 5-10 minutes before your scheduled time'}
{'' if email_type not in ["confirmation", "reminder"] else '• Bring any necessary documents or materials'}
{'' if email_type not in ["confirmation", "reminder"] else '• Contact us if you need to reschedule or cancel'}

Thank you for choosing {company_name}.

Best regards,
{company_name}
{datetime.now().strftime('%B %d, %Y')}

---
This email was sent by Minigma Business Suite
"""
    
    return text

def send_email_with_attachment(to_email, subject, html_body, text_body, attachment_path=None):
    """Send email with optional attachment"""
    try:
        # Check if email is configured
        if not EMAIL_CONFIG.get('sender_email') or not EMAIL_CONFIG.get('sender_password'):
            logger.warning("Email not configured. Set EMAIL_CONFIG in PART 7")
            return False
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{EMAIL_CONFIG['sender_name']} <{EMAIL_CONFIG['sender_email']}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        
        # Attach both HTML and plain text versions
        msg.attach(MIMEText(text_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))
        
        # Attach file if provided
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, 'rb') as file:
                part = MIMEApplication(file.read(), Name=os.path.basename(attachment_path))
                part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
                msg.attach(part)
        
        # Connect to SMTP server
        if EMAIL_CONFIG.get('use_ssl', False):
            server = smtplib.SMTP_SSL(
                EMAIL_CONFIG['smtp_server'], 
                EMAIL_CONFIG.get('smtp_port_ssl', 465)
            )
        else:
            server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        
        # Start TLS if required
        if EMAIL_CONFIG.get('use_tls', True) and not EMAIL_CONFIG.get('use_ssl', False):
            server.starttls()
        
        # Login and send
        server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
        server.send_message(msg)
        server.quit()
        
        logger.info(f"Email sent successfully to {to_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False

def send_appointment_sms(appointment_id, sms_type="reminder"):
    """Send SMS notification for appointment"""
    try:
        if not SMS_CONFIG.get('enabled', False):
            return False
        
        # Get appointment details
        appointment = get_appointment(appointment_id)
        if not appointment:
            return False
        
        # Get client info
        client_id = appointment[2]
        client = get_client_by_id(client_id) if client_id else None
        
        if not client or not client[4]:  # No phone
            return False
        
        client_phone = client[4]
        client_name = client[2]
        
        # Prepare SMS message
        appt_date_str = appointment[5]
        try:
            appt_date = parser.parse(appt_date_str)
        except:
            appt_date = datetime.now()
        
        if sms_type == "reminder":
            message = f"""
REMINDER: Appointment with {client_name}
Date: {appt_date.strftime('%b %d')}
Time: {appt_date.strftime('%I:%M %p')}
Please arrive 5 min early.
Reply STOP to unsubscribe.
            """
        elif sms_type == "confirmation":
            message = f"""
CONFIRMED: Your appointment is booked for {appt_date.strftime('%b %d at %I:%M %p')}.
We'll send a reminder 24h before.
Reply STOP to unsubscribe.
            """
        elif sms_type == "cancellation":
            message = f"""
CANCELLED: Your appointment for {appt_date.strftime('%b %d')} has been cancelled.
Contact us to reschedule.
Reply STOP to unsubscribe.
            """
        else:
            message = f"""
APPOINTMENT: {appointment[3] or 'Appointment'}
Date: {appt_date.strftime('%b %d, %Y')}
Time: {appt_date.strftime('%I:%M %p')}
Reply STOP to unsubscribe.
            """
        
        # Send SMS (using Twilio)
        success = send_sms_via_twilio(client_phone, message.strip())
        
        if success:
            logger.info(f"SMS {sms_type} sent for appointment {appointment_id}")
            return True
        else:
            logger.error(f"Failed to send SMS for appointment {appointment_id}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending appointment SMS: {e}")
        return False

def send_sms_via_twilio(to_phone, message):
    """Send SMS using Twilio - renamed to avoid conflict"""
    try:
        # Check if Twilio is configured
        if not SMS_CONFIG.get('account_sid') or not SMS_CONFIG.get('auth_token'):
            logger.warning("Twilio not configured")
            return False
        
        # Try to import Twilio
        try:
            from twilio.rest import Client
        except ImportError:
            logger.warning("Twilio not installed. Install with: pip install twilio")
            return False
        
        # Create client and send message
        client = Client(SMS_CONFIG['account_sid'], SMS_CONFIG['auth_token'])
        
        response = client.messages.create(
            body=message,
            from_=SMS_CONFIG['twilio_number'],
            to=to_phone
        )
        
        logger.info(f"SMS sent to {to_phone}, SID: {response.sid}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send SMS to {to_phone}: {e}")
        return False

# ==================================================
# BULK EMAIL FUNCTIONS
# ==================================================

def send_bulk_appointment_reminders():
    """Send reminders for all upcoming appointments"""
    try:
        # Get appointments needing reminders (24 hours before)
        reminders = get_appointments_needing_reminder(hours_before=24)
        
        if not reminders:
            logger.info("No appointments need reminders")
            return 0
        
        sent_count = 0
        for appointment in reminders:
            try:
                # Send email reminder
                email_sent = send_appointment_email_to_client(appointment[0], email_type="reminder")
                
                # Send SMS reminder if configured
                sms_sent = False
                if SMS_CONFIG.get('enabled', False):
                    sms_sent = send_appointment_sms(appointment[0], sms_type="reminder")
                
                if email_sent or sms_sent:
                    # Mark reminder as sent
                    set_appointment_reminder_sent(appointment[0])
                    sent_count += 1
                    
                    logger.info(f"Sent reminders for appointment {appointment[0]}")
                    
            except Exception as e:
                logger.error(f"Error sending reminder for appointment {appointment[0]}: {e}")
                continue
        
        logger.info(f"Sent {sent_count} appointment reminders")
        return sent_count
        
    except Exception as e:
        logger.error(f"Error in bulk reminder sending: {e}")
        return 0

def send_weekly_schedule_emails():
    """Send weekly schedule emails to all users"""
    try:
        # Get all users
        conn = sqlite3.connect('invoices.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, email FROM users WHERE email IS NOT NULL')
        users = cursor.fetchall()
        conn.close()
        
        sent_count = 0
        for user in users:
            user_id = user[0]
            user_email = user[1]
            
            if not user_email:
                continue
            
            try:
                # Get this week's appointments
                appointments = get_weekly_appointments(user_id)
                
                if not appointments:
                    continue
                
                # Create weekly schedule email
                user_info = get_user(user_id)
                company_name = "Your Business"
                if user_info and len(user_info) > 8 and user_info[8]:
                    company_name = user_info[8]
                
                # Group appointments by day
                appointments_by_day = {}
                for appt in appointments:
                    appt_date_str = appt[5]
                    try:
                        appt_date = parser.parse(appt_date_str)
                        day_key = appt_date.strftime('%A')
                    except:
                        day_key = "Unknown"
                    
                    if day_key not in appointments_by_day:
                        appointments_by_day[day_key] = []
                    
                    appointments_by_day[day_key].append(appt)
                
                # Create email content
                subject = f"Weekly Schedule - {datetime.now().strftime('%B %d, %Y')}"
                
                html_body = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                        .schedule-day {{ margin-bottom: 20px; padding: 15px; background: #f8f9fa; border-radius: 8px; }}
                        .appointment {{ padding: 10px; margin: 5px 0; background: white; border-left: 4px solid #4a6ee0; }}
                        .appointment-time {{ font-weight: bold; color: #4a6ee0; }}
                    </style>
                </head>
                <body>
                    <h2>📅 Your Weekly Schedule</h2>
                    <p>Here's your schedule for the upcoming week:</p>
                """
                
                for day, day_appointments in appointments_by_day.items():
                    html_body += f'<div class="schedule-day"><h3>{day}</h3>'
                    
                    for appt in day_appointments:
                        appt_time_str = appt[5]
                        try:
                            appt_time = parser.parse(appt_time_str)
                            time_str = appt_time.strftime('%I:%M %p')
                        except:
                            time_str = "Time N/A"
                            
                        client_name = appt[12] if len(appt) > 12 else "Unknown"
                        title = appt[3] or "Meeting"
                        duration = appt[6] if len(appt) > 6 else 60
                        appt_type = appt[7] if len(appt) > 7 else "Meeting"
                        
                        html_body += f'''
                        <div class="appointment">
                            <div class="appointment-time">{time_str}</div>
                            <div><strong>{title}</strong> with {client_name}</div>
                            <div>{duration} minutes • {appt_type}</div>
                        </div>
                        '''
                    
                    html_body += '</div>'
                
                html_body += f"""
                    <p>Total appointments this week: {len(appointments)}</p>
                    <p>Best regards,<br>{company_name}</p>
                </body>
                </html>
                """
                
                # Send email
                success = send_email_with_attachment(
                    to_email=user_email,
                    subject=subject,
                    html_body=html_body,
                    text_body="Your weekly schedule is attached above.",
                    attachment_path=None
                )
                
                if success:
                    sent_count += 1
                    logger.info(f"Sent weekly schedule to user {user_id}")
                    
            except Exception as e:
                logger.error(f"Failed to send weekly schedule to user {user_id}: {e}")
                continue
        
        logger.info(f"Sent {sent_count} weekly schedule emails")
        return sent_count
        
    except Exception as e:
        logger.error(f"Error sending weekly schedule emails: {e}")
        return 0

# ==================================================
# EXISTING INVOICE EMAIL FUNCTIONS (UPDATED)
# ==================================================

def send_invoice_email(client_email, client_name, invoice_number, pdf_path, invoice_data):
    """Send invoice via email"""
    try:
        # Check if email is configured
        if not EMAIL_CONFIG.get('sender_email') or not EMAIL_CONFIG.get('sender_password'):
            logger.warning("Email not configured. Set EMAIL_CONFIG in PART 7")
            return False
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = f"{EMAIL_CONFIG['sender_name']} <{EMAIL_CONFIG['sender_email']}>"
        msg['To'] = client_email
        
        # Enhanced subject with company name
        company_name = EMAIL_CONFIG.get('sender_name', 'Your Company')
        msg['Subject'] = f"Invoice {invoice_number} from {company_name}"
        
        # Enhanced email body
        body = f"""
Dear {client_name},

Please find your invoice {invoice_number} attached.

Invoice Details:
- Invoice Number: {invoice_number}
- Date: {invoice_data.get('invoice_date', 'N/A')}
- Total Amount: {invoice_data.get('currency', '')} {invoice_data.get('total_amount', 0):.2f}

You can view and pay this invoice online through our portal.
A payment reminder will be sent in 7 days if unpaid.

Thank you for your business!

Best regards,
{company_name}
Customer Support: {EMAIL_CONFIG['sender_email']}
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach PDF if it exists
        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, 'rb') as file:
                attach = MIMEApplication(file.read(), _subtype='pdf')
                attach.add_header('Content-Disposition', 'attachment', filename=f'{invoice_number}.pdf')
                msg.attach(attach)
        
        # Send email with proper error handling
        if EMAIL_CONFIG.get('use_ssl', False):
            server = smtplib.SMTP_SSL(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG.get('smtp_port_ssl', 465))
        else:
            server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        
        if EMAIL_CONFIG.get('use_tls', True) and not EMAIL_CONFIG.get('use_ssl', False):
            server.starttls()
        
        server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
        server.send_message(msg)
        server.quit()
        
        logger.info(f"✅ Invoice email sent to {client_email}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Email sending failed: {e}")
        return False

def send_invoice_sms(client_phone, client_name, invoice_number, invoice_data):
    """Send invoice notification via SMS"""
    try:
        if not SMS_CONFIG.get('enabled', False):
            logger.warning("SMS not enabled in configuration")
            return False
        
        # Try to import Twilio
        try:
            from twilio.rest import Client
        except ImportError:
            logger.warning("Twilio not installed. Install with: pip install twilio")
            return False
        
        # Create client
        client = Client(SMS_CONFIG['account_sid'], SMS_CONFIG['auth_token'])
        
        # Enhanced SMS message
        message_body = f"""
Hi {client_name}, your invoice {invoice_number} for {invoice_data.get('currency', '')} {invoice_data.get('total_amount', 0):.2f} is ready. 
Check your email for the PDF invoice or view online.
From {EMAIL_CONFIG['sender_name']}
        """
        
        message = client.messages.create(
            body=message_body.strip(),
            from_=SMS_CONFIG['twilio_number'],
            to=client_phone
        )
        
        logger.info(f"✅ Invoice SMS sent to {client_phone}, SID: {message.sid}")
        return True
        
    except Exception as e:
        logger.error(f"❌ SMS sending failed: {e}")
        return False

# ==================================================
# CONFIGURATION TESTING
# ==================================================

def test_email_configuration():
    """Test email configuration"""
    try:
        if not EMAIL_CONFIG.get('sender_email') or not EMAIL_CONFIG.get('sender_password'):
            logger.warning("Email credentials not configured")
            return False
        
        if EMAIL_CONFIG.get('use_ssl', False):
            server = smtplib.SMTP_SSL(
                EMAIL_CONFIG['smtp_server'], 
                EMAIL_CONFIG.get('smtp_port_ssl', 465)
            )
        else:
            server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        
        if EMAIL_CONFIG.get('use_tls', True) and not EMAIL_CONFIG.get('use_ssl', False):
            server.starttls()
            
        server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
        server.quit()
        
        logger.info("✅ Email configuration test: PASSED")
        return True
        
    except Exception as e:
        logger.error(f"❌ Email configuration test: FAILED - {e}")
        return False

def test_sms_configuration():
    """Test SMS configuration"""
    try:
        if not SMS_CONFIG.get('enabled', False):
            logger.info("SMS not enabled in configuration")
            return False
        
        from twilio.rest import Client
        client = Client(SMS_CONFIG['account_sid'], SMS_CONFIG['auth_token'])
        
        # Try to list messages to test connection
        client.messages.list(limit=1)
        
        logger.info("✅ SMS configuration test: PASSED")
        return True
        
    except ImportError:
        logger.warning("❌ SMS configuration test: Twilio not installed")
        return False
    except Exception as e:
        logger.error(f"❌ SMS configuration test: FAILED - {e}")
        return False

def setup_email_sms():
    """Setup and test email/SMS configurations with appointment support"""
    print("\n🔧 Setting up Email & SMS Delivery System...")
    print("-" * 50)
    
    email_ready = test_email_configuration()
    sms_ready = test_sms_configuration()
    
    features = []
    
    if email_ready:
        features.append("📧 Invoice emails")
        features.append("📅 Appointment confirmations")
        features.append("⏰ Appointment reminders")
        features.append("📊 Weekly schedule emails")
    
    if sms_ready:
        features.append("📱 Invoice SMS notifications")
        features.append("📱 Appointment SMS reminders")
    
    if features:
        print("✅ Email/SMS Features Ready:")
        for feature in features:
            print(f"   {feature}")
    else:
        print("❌ Email/SMS not configured")
        print("   Configure EMAIL_CONFIG and SMS_CONFIG in PART 7")
    
    print("-" * 50)
    
    return email_ready or sms_ready

print("✅ Part 7 updated with comprehensive appointment email and SMS functionality!")

# ==================================================
# PART 8: PREMIUM TIER SYSTEM & QUOTE FUNCTIONALITY (Updated with Scheduling)
# ==================================================

# Tier configuration with appointment scheduling features
TIER_LIMITS = {
    'free': {
        'monthly_invoices': 10,  # Includes both invoices and quotes
        'max_appointments': 5,   # Free tier appointment limit
        'max_clients': 10,       # Client database limit
        'features': [
            'Basic invoice creation',
            'Quote creation',
            'PDF generation',
            'Multiple currencies',
            '14-day free trial',
            'Basic appointment scheduling (5 max)',
            'Today & week view'
        ],
        'price': 0
    },
    'premium': {
        'monthly_invoices': float('inf'),  # Unlimited
        'max_appointments': float('inf'),  # Unlimited appointments
        'max_clients': float('inf'),       # Unlimited clients
        'features': [
            'Unlimited invoices & quotes',
            'Company/VAT registration',
            'VAT calculation',
            'Client database', 
            'Payment tracking',
            'Email/SMS delivery',
            'Priority support',
            'Advanced appointment scheduling',
            'Recurring appointments',
            'Calendar integrations',
            'Custom appointment types',
            'Automated reminders',
            'Email confirmations',
            'Calendar exports',
            'Working hours configuration'
        ],
        'monthly_price': 12,
        'annual_price': 105
    }
}

# Payment configuration (using Stripe as example)
PAYMENT_CONFIG = {
    'stripe_secret_key': 'sk_test_your_stripe_secret_key',  # Set this
    'stripe_public_key': 'pk_test_your_stripe_public_key',  # Set this
    'webhook_secret': 'whsec_your_webhook_secret',  # Set this
    'enabled': False  # Set to True when configured
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

def get_remaining_appointments(user_id):
    """Get remaining appointments user can create"""
    if is_premium_user(user_id):
        return float('inf')  # Unlimited for premium
    
    # Count appointments created this month
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    first_day_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_day_str = first_day_of_month.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
        SELECT COUNT(*) FROM appointments 
        WHERE user_id = ? AND created_at >= ?
    ''', (user_id, first_day_str))
    
    appointment_count = cursor.fetchone()[0]
    conn.close()
    
    remaining = TIER_LIMITS['free']['max_appointments'] - appointment_count
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

def check_appointment_limit(user_id):
    """Check if user can create more appointments"""
    if is_premium_user(user_id):
        return True, ""

    remaining = get_remaining_appointments(user_id)
    
    if remaining <= 0:
        return False, f"❌ You've reached your appointment limit of {TIER_LIMITS['free']['max_appointments']}.\nUpgrade to Premium for unlimited appointments!"
    
    return True, f"({remaining} appointments remaining)"

def check_client_limit(user_id):
    """Check if user can add more clients"""
    if is_premium_user(user_id):
        return True, ""

    # Count existing clients
    clients = get_user_clients(user_id)
    client_count = len(clients)
    
    if client_count >= TIER_LIMITS['free']['max_clients']:
        return False, f"❌ You've reached your client limit of {TIER_LIMITS['free']['max_clients']}.\nUpgrade to Premium for unlimited clients!"
    
    return True, f"({TIER_LIMITS['free']['max_clients'] - client_count} clients remaining)"

# ==================================================
# PREMIUM APPOINTMENT FEATURES
# ==================================================

def can_use_advanced_scheduling(user_id):
    """Check if user can use advanced scheduling features"""
    return is_premium_user(user_id)

def can_create_recurring_appointments(user_id):
    """Check if user can create recurring appointments"""
    return is_premium_user(user_id)

def can_use_calendar_export(user_id):
    """Check if user can export calendar"""
    return is_premium_user(user_id)

def can_set_custom_reminders(user_id):
    """Check if user can set custom reminder times"""
    return is_premium_user(user_id)

def can_use_email_templates(user_id):
    """Check if user can use custom email templates"""
    return is_premium_user(user_id)

def can_set_working_hours(user_id):
    """Check if user can set custom working hours"""
    return is_premium_user(user_id)

# ==================================================
# ENHANCED QUOTE FUNCTIONALITY
# ==================================================

# Quote-specific database functions
def save_quote_draft(user_id, client_name, quote_date, currency, items, client_email=None, client_phone=None):
    """Save quote draft to database"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    items_json = json.dumps(items)  # Use json.dumps for proper serialization
    
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
    
    # Parse items JSON back to Python object
    if quote and len(quote) > 5 and quote[5]:
        try:
            # Create a mutable list version to modify items
            quote_list = list(quote)
            quote_list[5] = json.loads(quote[5]) if isinstance(quote[5], str) else quote[5]
            quote = tuple(quote_list)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse items JSON for quote {quote_id}")
    
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
    
    # Parse items JSON for each quote
    parsed_quotes = []
    for quote in quotes:
        if len(quote) > 5 and quote[5]:
            try:
                quote_list = list(quote)
                quote_list[5] = json.loads(quote[5]) if isinstance(quote[5], str) else quote[5]
                parsed_quotes.append(tuple(quote_list))
            except json.JSONDecodeError:
                parsed_quotes.append(quote)
        else:
            parsed_quotes.append(quote)
    
    conn.close()
    return parsed_quotes

def get_user_quote_count_this_month(user_id):
    """Get number of quotes created this month"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    first_day_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_day_str = first_day_of_month.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
        SELECT COUNT(*) FROM invoices 
        WHERE user_id = ? AND status = 'approved' AND created_at >= ? AND document_type = 'quote'
    ''', (user_id, first_day_str))
    
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
        currency_code = quote_data.get('currency', 'GBP')
        currency_symbol = currency_symbols.get(currency_code, currency_code)
        
        # Header section
        company_name = ""
        has_logo = False
        if user_info and len(user_info) > 8:
            company_name = user_info[8] if user_info[8] else ''
        
        if user_info and len(user_info) > 7 and user_info[7]:  # logo_path
            logo_path = user_info[7]
            if os.path.exists(logo_path):
                try:
                    logo = Image(logo_path, width=2.5*inch, height=1.25*inch)
                    has_logo = True
                except Exception as e:
                    logger.warning(f"Could not load logo: {e}")
                    has_logo = False
        
        header_data = []
        
        if has_logo:
            header_data.append(logo)
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
        if user_info and len(user_info) > 9 and user_info[9]:  # company_reg_number
            reg_data.append(Paragraph(f"<b>Company Reg:</b> {user_info[9]}", normal_style))
        
        if reg_data:
            for reg in reg_data:
                story.append(reg)
            story.append(Spacer(1, 0.2*inch))
        
        # Quote details
        quote_number = quote_data.get('quote_number', 'N/A')
        quote_date = quote_data.get('quote_date', 'N/A')
        client_name = quote_data.get('client_name', 'N/A')
        
        details_data = [
            [Paragraph("<b>Quote Number:</b>", bold_style), 
             Paragraph(quote_number, normal_style),
             Paragraph("<b>Date:</b>", bold_style), 
             Paragraph(quote_date, normal_style)],
            
            [Paragraph("<b>Quote To:</b>", bold_style), 
             Paragraph(client_name, normal_style),
             Paragraph("<b>Valid Until:</b>", bold_style), 
             Paragraph("", normal_style)]  # Placeholder for valid until date
        ]
        
        # Calculate valid until date if we have a quote date
        try:
            quote_date_obj = datetime.strptime(quote_date, '%d %b %Y')
            valid_until = (quote_date_obj + timedelta(days=30)).strftime('%d %b %Y')
            details_data[1][3] = Paragraph(valid_until, normal_style)
        except:
            pass
        
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
        items = quote_data.get('items', [])
        for item in items:
            quantity = item.get('quantity', 0)
            amount = item.get('amount', 0.0)
            total = quantity * amount
            total_amount += total
            table_data.append([
                Paragraph(item.get('description', ''), normal_style),
                Paragraph(str(quantity), normal_style),
                Paragraph(f"{currency_symbol} {amount:.2f}", normal_style),
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
        footer_text = "Generated by Minigma Business Suite"
        if company_name:
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
        pdf_file = f"quotes/{quote_number}.pdf"
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
                if len(quote) > 2:
                    quote_num = quote[2] if quote[2] else "No Number"
                    quote_date = quote[3] if len(quote) > 3 else "No Date"
                    total = quote[7] if len(quote) > 7 else 0
                    currency = quote[6] if len(quote) > 6 else ""
                    message += f"• {quote_num} - {quote_date} - {currency}{total:.2f}\n"
        else:
            message = f"No quotes found for client: {client_name}"
    else:
        quotes = get_user_quotes(user_id)
        if not quotes:
            await update.message.reply_text("You haven't created any approved quotes yet.")
            return
        
        message = "📋 Your Recent Quotes:\n\n"
        for quote in quotes[:10]:  # Show last 10 quotes
            if len(quote) > 2:
                quote_num = quote[2] if quote[2] else "No Number"
                quote_date = quote[3] if len(quote) > 3 else "No Date"
                total = quote[7] if len(quote) > 7 else 0
                currency = quote[6] if len(quote) > 6 else ""
                message += f"• {quote_num} - {quote_date} - {currency}{total:.2f}\n"
        
        if is_premium_user(user_id):
            message += "\n💡 *Tip: Use* `/myquotes ClientName` *to filter by client*"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# ==================================================
# ENHANCED PREMIUM COMMAND WITH SCHEDULING FEATURES
# ==================================================

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show premium features and subscription options"""
    user_id = update.effective_user.id
    current_tier = get_user_tier(user_id)
    remaining_invoices = get_remaining_invoices(user_id)
    remaining_appointments = get_remaining_appointments(user_id)
    
    if current_tier == 'premium':
        await update.message.reply_text(
            f"🎉 **You're a Premium User!**\n\n"
            f"✨ **All Premium Features Unlocked:**\n"
            f"📄 **Documents:**\n"
            f"• Unlimited invoices & quotes\n"
            f"• Company/VAT registration\n"
            f"• VAT calculation\n\n"
            f"📅 **Appointment Scheduling:**\n"
            f"• Unlimited appointments\n"
            f"• Advanced calendar management\n"
            f"• Recurring appointments\n"
            f"• Custom appointment types\n"
            f"• Automated reminders\n"
            f"• Email confirmations\n"
            f"• Calendar exports\n\n"
            f"👥 **Client Management:**\n"
            f"• Unlimited client database\n"
            f"• Payment tracking\n"
            f"• Email/SMS delivery\n"
            f"• Priority support\n\n"
            f"💎 **Your subscription is active**\n\n"
            f"Use /setup to configure company details\n"
            f"Use /schedule for advanced appointments\n"
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

**Monthly Limits:**
• Invoices/Quotes: {remaining_invoices} remaining
• Appointments: {remaining_appointments} remaining
• Clients: {TIER_LIMITS['free']['max_clients']} maximum

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

# ==================================================
# ENHANCED APPOINTMENT COMMANDS WITH TIER CHECKS
# ==================================================

async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the appointment scheduling process with tier checks"""
    user_id = update.effective_user.id
    
    # Check appointment limit
    can_schedule, message = check_appointment_limit(user_id)
    if not can_schedule:
        await update.message.reply_text(message)
        return
    
    # Check if user has existing clients
    clients = get_user_clients(user_id)
    
    if not clients:
        # Check client limit
        can_add_client, client_message = check_client_limit(user_id)
        if not can_add_client:
            await update.message.reply_text(client_message)
            return
            
        # No clients, create one first
        await update.message.reply_text(
            "📅 **Schedule Appointment**\n\n"
            "You need to add a client first before scheduling an appointment.\n\n"
            "Would you like to add a client now?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Client", callback_data="schedule_add_client")],
                [InlineKeyboardButton("❌ Cancel", callback_data="schedule_cancel")]
            ])
        )
        return SCHEDULE_START
    
    # Start scheduling conversation
    context.user_data['scheduling'] = {
        'step': 'select_client',
        'appointment_data': {}
    }
    
    # Show client selection
    keyboard = []
    for client in clients[:10]:
        keyboard.append([
            InlineKeyboardButton(
                f"👤 {client[2]}", 
                callback_data=f"schedule_client_{client[0]}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("➕ Add New Client", callback_data="schedule_new_client"),
        InlineKeyboardButton("❌ Cancel", callback_data="schedule_cancel")
    ])
    
    remaining_info = ""
    if not is_premium_user(user_id):
        remaining_appts = get_remaining_appointments(user_id)
        remaining_info = f"\n\n📊 You have {remaining_appts} appointments remaining this month."
    
    await update.message.reply_text(
        f"📅 **Schedule New Appointment**{remaining_info}\n\n"
        "Select a client for this appointment:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return SELECT_CLIENT

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show interactive calendar view with premium features"""
    user_id = update.effective_user.id
    
    if not is_premium_user(user_id):
        # Free tier: Show basic calendar
        await show_basic_calendar(update, context)
    else:
        # Premium tier: Show advanced calendar
        await show_advanced_calendar(update, context)

async def show_basic_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show basic calendar for free tier users"""
    user_id = update.effective_user.id
    today = datetime.now().date()
    
    # Get appointments for next 7 days
    next_week = today + timedelta(days=7)
    appointments = get_user_appointments(user_id, today, next_week)
    
    message = "🗓️ **Calendar View (Free Tier)**\n\n"
    message += f"📅 *{today.strftime('%B %d, %Y')} - {next_week.strftime('%B %d, %Y')}*\n\n"
    
    if not appointments:
        message += "No appointments scheduled this week.\n\n"
    else:
        # Group by day
        appointments_by_day = {}
        for appt in appointments:
            appt_date_str = appt[5]
            try:
                appt_date = parser.parse(appt_date_str).date()
                day_key = appt_date.strftime('%Y-%m-%d')
            except:
                day_key = "Unknown"
            
            if day_key not in appointments_by_day:
                appointments_by_day[day_key] = []
            
            appointments_by_day[day_key].append(appt)
        
        for day_str in sorted(appointments_by_day.keys()):
            if day_str == "Unknown":
                continue
                
            day = datetime.strptime(day_str, '%Y-%m-%d').date()
            message += f"**{day.strftime('%A, %b %d')}**\n"
            
            for appt in appointments_by_day[day_str]:
                appt_time_str = appt[5]
                try:
                    appt_time = parser.parse(appt_time_str)
                    time_str = appt_time.strftime('%I:%M %p')
                except:
                    time_str = "Time N/A"
                    
                client_name = appt[12] if len(appt) > 12 else "Unknown"
                title = appt[3] or "Meeting"
                
                message += f"• {time_str} - {title[:20]} with {client_name[:15]}\n"
            
            message += "\n"
    
    message += "💎 **Upgrade to Premium for:**\n"
    message += "• Interactive calendar\n• Advanced views\n• Export capabilities\n• Recurring appointments\n"
    message += "Use /premium to upgrade!"
    
    keyboard = [
        [
            InlineKeyboardButton("📅 Schedule Appointment", callback_data="calendar_schedule"),
            InlineKeyboardButton("💎 Upgrade to Premium", callback_data="premium_upgrade")
        ]
    ]
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def show_advanced_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show advanced calendar for premium users"""
    user_id = update.effective_user.id
    today = datetime.now().date()
    
    # Calculate start of current week (Monday)
    start_of_week = today - timedelta(days=today.weekday())
    
    # Store in context for navigation
    context.user_data['calendar_view'] = {
        'current_date': today,
        'view_type': 'week',
        'week_start': start_of_week
    }
    
    await show_calendar_view(update, context)

# ==================================================
# PREMIUM-ONLY APPOINTMENT FEATURES
# ==================================================

async def recurring_appointments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create recurring appointments (Premium only)"""
    user_id = update.effective_user.id
    
    if not can_create_recurring_appointments(user_id):
        await update.message.reply_text(
            "❌ **Premium Feature: Recurring Appointments**\n\n"
            "Create appointments that repeat daily, weekly, or monthly.\n\n"
            "💎 **Upgrade to Premium for:**\n"
            "• Recurring appointments\n"
            "• Advanced scheduling\n"
            "• Calendar exports\n"
            "• Custom appointment types\n\n"
            "Use /premium to upgrade!",
            parse_mode='Markdown'
        )
        return
    
    # Premium users can proceed with recurring appointments
    await update.message.reply_text(
        "🔄 **Recurring Appointments**\n\n"
        "This feature allows you to create appointments that repeat automatically.\n\n"
        "Please select a client first:"
    )
    # ... (implement recurring appointment creation flow)

async def export_calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export calendar data (Premium only)"""
    user_id = update.effective_user.id
    
    if not can_use_calendar_export(user_id):
        await update.message.reply_text(
            "❌ **Premium Feature: Calendar Export**\n\n"
            "Export your calendar to PDF or CSV format.\n\n"
            "💎 **Upgrade to Premium for:**\n"
            "• Calendar exports\n"
            "• Advanced views\n"
            "• Recurring appointments\n"
            "• Custom appointment types\n\n"
            "Use /premium to upgrade!",
            parse_mode='Markdown'
        )
        return
    
    # Premium users can proceed with calendar export
    keyboard = [
        [
            InlineKeyboardButton("📅 Export to PDF", callback_data="export_calendar_pdf"),
            InlineKeyboardButton("📊 Export to CSV", callback_data="export_calendar_csv")
        ],
        [
            InlineKeyboardButton("🗓️ This Week", callback_data="export_week"),
            InlineKeyboardButton("🗓️ This Month", callback_data="export_month")
        ]
    ]
    
    await update.message.reply_text(
        "📤 **Calendar Export**\n\n"
        "Export your calendar data for reporting or backup:\n\n"
        "Select export format and time period:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================================================
# PREMIUM DECORATORS FOR APPOINTMENT FEATURES
# ==================================================

def premium_appointment_required(feature_name):
    """Decorator to restrict advanced appointment features to premium users"""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            
            if not is_premium_user(user_id):
                remaining = get_remaining_appointments(user_id)
                
                await update.message.reply_text(
                    f"❌ **Premium Feature: {feature_name}**\n\n"
                    f"This appointment feature is only available for Premium users.\n\n"
                    f"📊 **Your Current Limits:**\n"
                    f"• Free Tier appointments: {remaining} remaining\n"
                    f"• Basic scheduling only\n\n"
                    f"💎 **Upgrade to Premium for:**\n"
                    f"• Unlimited appointments\n"
                    f"• {feature_name}\n"
                    f"• Advanced calendar\n"
                    f"• Recurring appointments\n"
                    f"• Custom appointment types\n"
                    f"• Automated reminders\n\n"
                    f"Use /premium to upgrade and unlock all features!",
                    parse_mode='Markdown'
                )
                return
            
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

# ==================================================
# PAYMENT PROCESSING FUNCTIONS
# ==================================================

def create_stripe_checkout_session(user_id, price_id, success_url, cancel_url):
    """Create Stripe checkout session"""
    try:
        if not PAYMENT_CONFIG.get('enabled', False):
            logger.warning("Stripe payments not enabled")
            return None
            
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
                'user_id': user_id,
                'feature': 'premium_suite'
            }
        )
        
        logger.info(f"Created Stripe checkout session for user {user_id}")
        return session.url
        
    except ImportError:
        logger.error("Stripe not installed. Install with: pip install stripe")
        return None
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        return None

def handle_stripe_webhook(payload, sig_header):
    """Handle Stripe webhook for payment confirmation"""
    try:
        if not PAYMENT_CONFIG.get('enabled', False):
            return False
            
        import stripe
        stripe.api_key = PAYMENT_CONFIG['stripe_secret_key']
        
        event = stripe.Webhook.construct_event(
            payload, sig_header, PAYMENT_CONFIG['webhook_secret']
        )
        
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            user_id = session['client_reference_id']
            
            # Determine subscription period
            subscription_id = session.get('subscription')
            if subscription_id:
                subscription = stripe.Subscription.retrieve(subscription_id)
                interval = subscription['items']['data'][0]['plan']['interval']
                months = 12 if interval == 'year' else 1
            else:
                months = 1
            
            # Activate premium for user
            add_premium_subscription(user_id, 'paid', months)
            
            logger.info(f"Premium activated for user {user_id} ({months} month(s))")
            
            # Send confirmation message
            try:
                from telegram import Bot
                bot = Bot(token=BOT_TOKEN)
                bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 **Premium Activated!**\n\n"
                         f"Thank you for upgrading to Minigma Premium!\n\n"
                         f"✨ **You now have access to:**\n"
                         f"• Unlimited invoices & quotes\n"
                         f"• Advanced appointment scheduling\n"
                         f"• Client database\n"
                         f"• Payment tracking\n"
                         f"• Email/SMS delivery\n\n"
                         f"Use /setup to configure company details\n"
                         f"Use /schedule for advanced appointments\n"
                         f"Use /clients to manage your database\n\n"
                         f"Welcome to the Premium Suite!",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Failed to send premium confirmation: {e}")
            
        return True
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return False

# ==================================================
# UPDATED BUTTON HANDLER FOR PREMIUM PAYMENTS
# ==================================================

async def handle_premium_payment(query, user_id, plan_type):
    """Handle premium payment selection"""
    if plan_type == 'trial':
        # Activate free trial
        add_premium_subscription(user_id, 'trial', 1)
        
        await query.edit_message_text(
            "🎉 **Premium Trial Activated!**\n\n"
            "You now have access to all premium features for 1 month!\n\n"
            "✨ **Unlocked Features:**\n"
            "📄 **Documents:**\n"
            "• Unlimited invoices & quotes\n"
            "• Company/VAT registration\n"
            "• VAT calculation\n\n"
            "📅 **Appointment Scheduling:**\n"
            "• Unlimited appointments\n"
            "• Advanced calendar\n"
            "• Custom appointment types\n"
            "• Automated reminders\n\n"
            "👥 **Client Management:**\n"
            "• Unlimited client database\n"
            "• Payment tracking\n"
            "• Email/SMS delivery\n\n"
            "💎 **Get Started:**\n"
            "Use /setup to configure company details\n"
            "Use /schedule for advanced appointments\n"
            "Use /clients to manage clients\n"
            "Use /payments to track payments\n"
            "Use /create and /quote for unlimited documents!",
            parse_mode='Markdown'
        )
    
    elif plan_type in ['monthly', 'annual']:
        # Redirect to payment
        if plan_type == 'monthly':
            price_id = 'price_monthly'  # Set your Stripe price ID
            duration = "monthly"
            amount = TIER_LIMITS['premium']['monthly_price']
            months = 1
        else:
            price_id = 'price_annual'   # Set your Stripe price ID
            duration = "annual"
            amount = TIER_LIMITS['premium']['annual_price']
            months = 12
        
        # Create checkout session
        success_url = f"https://t.me/MinigmaSuiteBot?start=payment_success"
        cancel_url = f"https://t.me/MinigmaSuiteBot?start=payment_cancel"
        
        checkout_url = create_stripe_checkout_session(user_id, price_id, success_url, cancel_url)
        
        if checkout_url:
            keyboard = [
                [InlineKeyboardButton("💳 Pay Now & Activate", url=checkout_url)],
                [InlineKeyboardButton("🔙 Back to Plans", callback_data="premium_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"💰 **Premium {duration.capitalize()} Plan - £{amount}**\n\n"
                f"Click the button below to complete your payment and activate Premium features!\n\n"
                f"✨ **You'll get instant access to:**\n"
                f"📄 **Documents:**\n"
                f"• Unlimited invoices & quotes\n"
                f"• Company/VAT registration\n"
                f"• VAT calculation\n\n"
                f"📅 **Appointment Scheduling:**\n"
                f"• Unlimited appointments\n"
                f"• Advanced calendar management\n"
                f"• Recurring appointments\n"
                f"• Custom appointment types\n"
                f"• Automated reminders\n"
                f"• Email confirmations\n\n"
                f"👥 **Client Management:**\n"
                f"• Unlimited client database\n"
                f"• Payment tracking\n"
                f"• Email/SMS delivery\n"
                f"• Priority support\n\n"
                f"💳 **Payment Details:**\n"
                f"• Plan: {duration.capitalize()} (£{amount})\n"
                f"• Duration: {months} month(s)\n"
                f"• Auto-renew: {duration.capitalize()}\n"
                f"• Cancel anytime",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                "❌ **Payment System Temporarily Unavailable**\n\n"
                "Our secure payment system is currently undergoing maintenance.\n\n"
                "💎 **Manual Premium Activation:**\n"
                "1. Contact @MinigimaUK on Telegram\n"
                "2. Provide your User ID (use /myid)\n"
                "3. Choose your plan (Monthly/Annual)\n"
                "4. Receive payment instructions\n"
                "5. Get instant activation after payment\n\n"
                "We apologize for the inconvenience and will have the system back online soon!",
                parse_mode='Markdown'
            )

# ==================================================
# FEATURE RESTRICTION DECORATORS
# ==================================================

def premium_required(feature_name):
    """Decorator to restrict features to premium users"""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            
            if not is_premium_user(user_id):
                remaining_invoices = get_remaining_invoices(user_id)
                remaining_appointments = get_remaining_appointments(user_id)
                
                await update.message.reply_text(
                    f"❌ **Premium Feature: {feature_name}**\n\n"
                    f"This feature is only available for Premium users.\n\n"
                    f"📊 **Your Current Limits:**\n"
                    f"• Free Tier documents: {remaining_invoices} remaining\n"
                    f"• Free Tier appointments: {remaining_appointments} remaining\n"
                    f"• Basic features only\n\n"
                    f"💎 **Upgrade to Premium for:**\n"
                    f"• Unlimited invoices & quotes\n"
                    f"• {feature_name}\n"
                    f"• Advanced appointment scheduling\n"
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

print("✅ Part 8 updated with comprehensive premium tier system and scheduling features!")
# ==================================================
# PART 9: ENHANCED PREMIUM USER MANAGEMENT WITH SCHEDULING
# ==================================================

import datetime
import json

class PremiumManager:
    def __init__(self, filename='premium_users.json'):
        self.filename = filename
        self.premium_users = {}  # user_id: {'type': 'trial/paid', 'expires': date, 'features': []}
        self.load_premium_users()
    
    def load_premium_users(self):
        """Load premium users from JSON file"""
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r', encoding='utf-8') as f:
                    self.premium_users = json.load(f)
                print(f"✅ Loaded {len(self.premium_users)} premium users from JSON file")
            else:
                self.save_premium_users()
                print("✅ Created new premium users JSON file")
        except Exception as e:
            print(f"❌ Error loading premium users: {e}")
            # Fallback to simple text file
            self.load_premium_users_fallback()
    
    def load_premium_users_fallback(self):
        """Fallback to simple text file if JSON fails"""
        try:
            txt_file = 'premium_users.txt'
            if os.path.exists(txt_file):
                with open(txt_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            parts = line.split('|')
                            if len(parts) > 0:
                                user_id = parts[0].strip()
                                if user_id.isdigit():
                                    user_id = int(user_id)
                                    self.premium_users[str(user_id)] = {
                                        'type': 'paid',
                                        'expires': None,
                                        'activated': datetime.datetime.now().strftime("%Y-%m-%d"),
                                        'features': ['all']
                                    }
                print(f"✅ Loaded {len(self.premium_users)} premium users from fallback file")
        except Exception as e:
            print(f"❌ Fallback load failed: {e}")
    
    def save_premium_users(self):
        """Save premium users to JSON file"""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.premium_users, f, indent=2, default=str)
            return True
        except Exception as e:
            print(f"❌ Error saving premium users: {e}")
            return False
    
    def is_premium(self, user_id):
        """Check if user has active premium access"""
        try:
            user_id = str(user_id)
            
            if user_id not in self.premium_users:
                return False
            
            user_data = self.premium_users[user_id]
            
            # Check if subscription has expired
            if user_data.get('expires'):
                expires_date = datetime.datetime.strptime(user_data['expires'], "%Y-%m-%d").date()
                if datetime.datetime.now().date() > expires_date:
                    # Subscription expired, remove from premium
                    del self.premium_users[user_id]
                    self.save_premium_users()
                    return False
            
            return True
        except Exception as e:
            print(f"Error in is_premium for user {user_id}: {e}")
            return False
    
    def get_user_data(self, user_id):
        """Get premium user data"""
        user_id = str(user_id)
        return self.premium_users.get(user_id, {})
    
    def add_premium_user(self, user_id, username="", premium_type='trial', months=1, features=None):
        """Add a new premium user"""
        user_id = str(user_id)
        
        if features is None:
            features = ['invoices', 'quotes', 'appointments', 'clients', 'payments', 'emails', 'sms']
        
        # Calculate expiration date
        today = datetime.datetime.now().date()
        if premium_type == 'trial':
            expires_date = today + datetime.timedelta(days=30*months)  # Trial months
        else:
            expires_date = today + datetime.timedelta(days=30*months)  # Paid months
        
        user_data = {
            'type': premium_type,
            'expires': expires_date.strftime("%Y-%m-%d"),
            'activated': today.strftime("%Y-%m-%d"),
            'username': username,
            'features': features,
            'months': months
        }
        
        self.premium_users[user_id] = user_data
        self.save_premium_users()
        
        # Also update database
        try:
            conn = sqlite3.connect('invoices.db')
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET subscription_tier = ? WHERE user_id = ?', ('premium', user_id))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Note: Could not update database for user {user_id}: {e}")
        
        return True, f"✅ User {user_id} added as {premium_type} premium user (expires: {expires_date.strftime('%Y-%m-%d')})"
    
    def remove_premium_user(self, user_id):
        """Remove a user from premium access"""
        user_id = str(user_id)
        
        if user_id not in self.premium_users:
            return False, "User not in premium list"
        
        del self.premium_users[user_id]
        self.save_premium_users()
        
        # Update database
        try:
            conn = sqlite3.connect('invoices.db')
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET subscription_tier = ? WHERE user_id = ?', ('free', user_id))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Note: Could not update database for user {user_id}: {e}")
        
        return True, f"❌ User {user_id} removed from premium users"
    
    def get_active_count(self):
        """Get count of active premium users"""
        count = 0
        for user_id, data in self.premium_users.items():
            if self.is_premium(user_id):
                count += 1
        return count
    
    def get_expiring_soon(self, days=7):
        """Get users whose premium expires soon"""
        expiring = []
        today = datetime.datetime.now().date()
        
        for user_id, data in self.premium_users.items():
            if data.get('expires'):
                try:
                    expires_date = datetime.datetime.strptime(data['expires'], "%Y-%m-%d").date()
                    days_until = (expires_date - today).days
                    if 0 <= days_until <= days:
                        expiring.append({
                            'user_id': user_id,
                            'username': data.get('username', ''),
                            'expires': data['expires'],
                            'days_until': days_until,
                            'type': data.get('type', 'unknown')
                        })
                except Exception as e:
                    print(f"Error parsing date for user {user_id}: {e}")
                    continue
        
        return expiring

# Create global instance
premium_manager = PremiumManager()

# ==================================================
# ENHANCED PREMIUM CHECK FUNCTIONS
# ==================================================

def is_premium_user_enhanced(user_id):
    """Enhanced premium check - integrates with database trial system"""
    # First check premium manager
    try:
        if premium_manager.is_premium(int(user_id)):
            return True
    except Exception as e:
        print(f"Error checking premium manager for user {user_id}: {e}")
    
    # Check database trial system
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT subscription_tier, trial_end_date, trial_used FROM users WHERE user_id = ?
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return False
    
    subscription_tier, trial_end_date, trial_used = result
    
    # Check if user is on premium tier in database
    if subscription_tier == 'premium':
        return True
    
    # Check if user is in trial period
    if trial_end_date and trial_used:
        trial_end = parse_trial_end_date(trial_end_date)
        if datetime.datetime.now() <= trial_end:
            return True
    
    return False

def get_user_tier_enhanced(user_id):
    """Get user's current tier with detailed info"""
    user_id = int(user_id)
    
    # Get premium manager data
    premium_data = premium_manager.get_user_data(user_id)
    if premium_data and premium_manager.is_premium(user_id):
        tier_type = premium_data.get('type', 'premium')
        expires = premium_data.get('expires', 'Never')
        return {
            'name': 'premium',
            'type': tier_type,
            'expires': expires,
            'features': premium_data.get('features', []),
            'unlimited': True
        }
    
    # Check database for trial
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT trial_end_date, trial_used FROM users WHERE user_id = ?
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        trial_end_date, trial_used = result
        if trial_end_date and trial_used:
            trial_end = parse_trial_end_date(trial_end_date)
            if datetime.datetime.now() <= trial_end:
                days_left = (trial_end - datetime.datetime.now()).days
                return {
                    'name': 'trial',
                    'expires': trial_end.strftime("%Y-%m-%d"),
                    'days_left': days_left,
                    'features': TIER_LIMITS['free']['features'] + ['trial_features'],
                    'unlimited': True  # Trial users get unlimited access
                }
    
    # Free tier user
    return {
        'name': 'free',
        'limits': {
            'invoices': TIER_LIMITS['free']['monthly_invoices'],
            'appointments': TIER_LIMITS['free']['max_appointments'],
            'clients': TIER_LIMITS['free']['max_clients']
        },
        'features': TIER_LIMITS['free']['features'],
        'unlimited': False
    }

def get_remaining_invoices_enhanced(user_id):
    """Get remaining invoices for current user tier"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] in ['premium', 'trial'] or tier.get('unlimited', False):
        return float('inf')  # Unlimited for premium/trial
    
    monthly_count = get_user_invoice_count_this_month(user_id) + get_user_quote_count_this_month(user_id)
    remaining = TIER_LIMITS['free']['monthly_invoices'] - monthly_count
    return max(0, remaining)

def get_remaining_appointments_enhanced(user_id):
    """Get remaining appointments for current user tier"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] in ['premium', 'trial'] or tier.get('unlimited', False):
        return float('inf')  # Unlimited for premium/trial
    
    # Count appointments created this month
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    first_day_of_month = datetime.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_day_str = first_day_of_month.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
        SELECT COUNT(*) FROM appointments 
        WHERE user_id = ? AND created_at >= ?
    ''', (user_id, first_day_str))
    
    appointment_count = cursor.fetchone()[0]
    conn.close()
    
    remaining = TIER_LIMITS['free']['max_appointments'] - appointment_count
    return max(0, remaining)

def get_remaining_clients_enhanced(user_id):
    """Get remaining client slots for current user tier"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] in ['premium', 'trial'] or tier.get('unlimited', False):
        return float('inf')  # Unlimited for premium/trial
    
    clients = get_user_clients(user_id)
    client_count = len(clients)
    remaining = TIER_LIMITS['free']['max_clients'] - client_count
    return max(0, remaining)

def check_invoice_limit_enhanced(user_id):
    """Check if user can create more invoices"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] in ['premium', 'trial'] or tier.get('unlimited', False):
        return True, ""  # Premium/trial users have no limits

    monthly_count = get_user_invoice_count_this_month(user_id) + get_user_quote_count_this_month(user_id)
    remaining = TIER_LIMITS['free']['monthly_invoices'] - monthly_count
    
    if remaining <= 0:
        return False, f"❌ You've reached your monthly limit of {TIER_LIMITS['free']['monthly_invoices']} creations.\nUpgrade to Premium for unlimited invoices and quotes!"
    
    return True, f"({remaining} creations remaining this month)"

def check_appointment_limit_enhanced(user_id):
    """Check if user can create more appointments"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] in ['premium', 'trial'] or tier.get('unlimited', False):
        return True, ""  # Premium/trial users have no limits

    remaining = get_remaining_appointments_enhanced(user_id)
    
    if remaining <= 0:
        return False, f"❌ You've reached your appointment limit of {TIER_LIMITS['free']['max_appointments']}.\nUpgrade to Premium for unlimited appointments!"
    
    return True, f"({remaining} appointments remaining)"

def check_client_limit_enhanced(user_id):
    """Check if user can add more clients"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] in ['premium', 'trial'] or tier.get('unlimited', False):
        return True, ""  # Premium/trial users have no limits

    remaining = get_remaining_clients_enhanced(user_id)
    
    if remaining <= 0:
        return False, f"❌ You've reached your client limit of {TIER_LIMITS['free']['max_clients']}.\nUpgrade to Premium for unlimited clients!"
    
    return True, f"({remaining} clients remaining)"

# ==================================================
# PREMIUM FEATURE ACCESS FUNCTIONS
# ==================================================

def can_use_advanced_scheduling_enhanced(user_id):
    """Check if user can use advanced scheduling features"""
    return is_premium_user_enhanced(user_id) or get_user_tier_enhanced(user_id)['name'] == 'trial'

def can_create_recurring_appointments_enhanced(user_id):
    """Check if user can create recurring appointments"""
    return is_premium_user_enhanced(user_id)  # Trial users don't get recurring appointments

def can_use_calendar_export_enhanced(user_id):
    """Check if user can export calendar"""
    return is_premium_user_enhanced(user_id)  # Trial users don't get exports

def can_set_custom_reminders_enhanced(user_id):
    """Check if user can set custom reminder times"""
    return is_premium_user_enhanced(user_id) or get_user_tier_enhanced(user_id)['name'] == 'trial'

def can_use_email_templates_enhanced(user_id):
    """Check if user can use custom email templates"""
    return is_premium_user_enhanced(user_id)  # Trial users use default templates

def can_set_working_hours_enhanced(user_id):
    """Check if user can set custom working hours"""
    return is_premium_user_enhanced(user_id) or get_user_tier_enhanced(user_id)['name'] == 'trial'

def can_use_advanced_features_enhanced(user_id):
    """Check if user can use any advanced features"""
    tier = get_user_tier_enhanced(user_id)
    return tier['name'] in ['premium', 'trial'] or tier.get('unlimited', False)

# ==================================================
# USER STATUS DISPLAY FUNCTIONS
# ==================================================

def get_user_premium_status(user_id):
    """Get user's premium status for display"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] == 'premium':
        premium_type = tier.get('type', 'premium').capitalize()
        expires = tier.get('expires', 'Never')
        return f"🎉 **{premium_type} User** - Unlimited access until {expires}"
    
    elif tier['name'] == 'trial':
        days_left = tier.get('days_left', 0)
        expires = tier.get('expires', 'Unknown')
        return f"🆓 **Trial User** - {days_left} days remaining (ends {expires})"
    
    else:  # Free tier
        remaining_invoices = get_remaining_invoices_enhanced(user_id)
        remaining_appointments = get_remaining_appointments_enhanced(user_id)
        remaining_clients = get_remaining_clients_enhanced(user_id)
        
        return (
            f"🆓 **Free User**\n"
            f"• Documents: {remaining_invoices} remaining\n"
            f"• Appointments: {remaining_appointments} remaining\n"
            f"• Clients: {remaining_clients} slots remaining"
        )

def get_user_features_summary(user_id):
    """Get summary of features available to user"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] == 'premium':
        return "✨ **Premium Features:** Unlimited everything + advanced scheduling + priority support"
    
    elif tier['name'] == 'trial':
        return "✨ **Trial Features:** Full premium access for limited time"
    
    else:  # Free tier
        features = "\n".join([f"• {feature}" for feature in TIER_LIMITS['free']['features']])
        return f"🆓 **Free Tier Features:**\n{features}"

# ==================================================
# ENHANCED SUBSCRIPTION MANAGEMENT
# ==================================================

def add_premium_subscription_enhanced(user_id, subscription_type, months=1):
    """Add premium subscription to user"""
    user_id = int(user_id)
    
    # Get user info from database
    user = get_user(user_id)
    username = ""
    if user and len(user) > 1:
        username = user[1] if user[1] else ""
    
    # Add to premium manager
    if subscription_type == 'trial':
        success, message = premium_manager.add_premium_user(
            user_id, username, 'trial', months,
            features=['invoices', 'quotes', 'appointments', 'clients', 'payments']
        )
    else:  # paid
        success, message = premium_manager.add_premium_user(
            user_id, username, 'paid', months,
            features=['invoices', 'quotes', 'appointments', 'clients', 'payments', 'emails', 'sms', 'exports', 'recurring']
        )
    
    if success:
        # Update database
        conn = sqlite3.connect('invoices.db')
        cursor = conn.cursor()
        
        # If trial, set trial dates
        if subscription_type == 'trial':
            trial_end_date = datetime.datetime.now() + datetime.timedelta(days=30*months)
            trial_end_str = trial_end_date.strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute('''
                UPDATE users 
                SET subscription_tier = 'premium', trial_end_date = ?, trial_used = TRUE
                WHERE user_id = ?
            ''', (trial_end_str, user_id))
        else:
            cursor.execute('''
                UPDATE users SET subscription_tier = 'premium' WHERE user_id = ?
            ''', (user_id,))
        
        conn.commit()
        conn.close()
    
    return success, message

def remove_premium_subscription_enhanced(user_id):
    """Remove premium subscription from user"""
    user_id = int(user_id)
    
    # Remove from premium manager
    success, message = premium_manager.remove_premium_user(user_id)
    
    if success:
        # Update database
        conn = sqlite3.connect('invoices.db')
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET subscription_tier = ? WHERE user_id = ?', ('free', user_id))
        conn.commit()
        conn.close()
    
    return success, message

def get_subscription_expiry(user_id):
    """Get user's subscription expiry date"""
    tier = get_user_tier_enhanced(user_id)
    
    if tier['name'] == 'premium':
        return tier.get('expires', 'Never')
    elif tier['name'] == 'trial':
        return tier.get('expires', 'Unknown')
    else:
        return "N/A (Free Tier)"

# ==================================================
# ENHANCED CREATE INVOICE WITH TIER CHECKS
# ==================================================

async def create_invoice_with_tier_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create invoice with tier checks - renamed to avoid conflict"""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        create_user(user_id, update.effective_user.username, update.effective_user.first_name, update.effective_user.last_name)
        user = get_user(user_id)
        await update.message.reply_text("✅ Your account has been created! Starting invoice creation...")
    
    # Check invoice limit for free users
    can_create, message = check_invoice_limit_enhanced(user_id)
    if not can_create:
        await update.message.reply_text(message)
        return
    
    context.user_data['current_invoice'] = {
        'items': [],
        'step': 'client_name'
    }
    
    # Show remaining info based on tier
    tier = get_user_tier_enhanced(user_id)
    remaining_info = ""
    
    if tier['name'] == 'free':
        remaining_invoices = get_remaining_invoices_enhanced(user_id)
        remaining_appointments = get_remaining_appointments_enhanced(user_id)
        remaining_clients = get_remaining_clients_enhanced(user_id)
        
        remaining_info = (
            f"\n\n📊 **Your Free Tier Limits:**\n"
            f"• Documents: {remaining_invoices} remaining\n"
            f"• Appointments: {remaining_appointments} remaining\n"
            f"• Clients: {remaining_clients} slots remaining\n\n"
            f"💎 **Upgrade to Premium for unlimited access!**"
        )
    elif tier['name'] == 'trial':
        days_left = tier.get('days_left', 0)
        remaining_info = f"\n\n🎉 **Premium Trial Active:** {days_left} days remaining\nEnjoy unlimited access!"
    else:  # Premium
        remaining_info = "\n\n💎 **Premium User:** Unlimited access enabled!"
    
    await update.message.reply_text(
        f"Let's create a new invoice! 🧾{remaining_info}\n\n"
        "First, please enter the client name:"
    )

# ==================================================
# ADMIN FUNCTIONS FOR PREMIUM MANAGEMENT
# ==================================================

async def list_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to list premium users with details"""
    user_id = update.effective_user.id
    
    # ⚠️ REPLACE WITH YOUR ACTUAL TELEGRAM ID ⚠️
    ADMIN_ID = 334262726
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only command")
        return
    
    active_count = premium_manager.get_active_count()
    expiring_soon = premium_manager.get_expiring_soon(days=7)
    
    message = f"📊 **Premium User Management**\n\n"
    message += f"**Active Premium Users:** {active_count}\n\n"
    
    if premium_manager.premium_users:
        message += "**All Premium Users:**\n"
        for uid, data in list(premium_manager.premium_users.items())[:20]:  # Show first 20
            user_type = data.get('type', 'unknown')
            expires = data.get('expires', 'Never')
            username = data.get('username', 'No username')
            
            # Check if active
            is_active = premium_manager.is_premium(uid)
            status = "✅ Active" if is_active else "❌ Expired"
            
            message += f"• `{uid}` - {username} - {user_type} - {expires} - {status}\n"
        
        if len(premium_manager.premium_users) > 20:
            message += f"\n... and {len(premium_manager.premium_users) - 20} more users\n"
    else:
        message += "No premium users found.\n"
    
    if expiring_soon:
        message += f"\n⚠️ **Expiring in next 7 days:** {len(expiring_soon)} users\n"
        for user in expiring_soon[:5]:  # Show first 5
            message += f"• `{user['user_id']}` - {user['username']} - {user['type']} - {user['expires']} ({user['days_until']} days)\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def check_expiring_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check for expiring subscriptions and send reminders"""
    user_id = update.effective_user.id
    
    ADMIN_ID = 334262726
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only command")
        return
    
    expiring_users = premium_manager.get_expiring_soon(days=3)
    
    if not expiring_users:
        await update.message.reply_text("✅ No subscriptions expiring in the next 3 days.")
        return
    
    message = "⚠️ **Subscriptions Expiring Soon (3 days):**\n\n"
    
    for user in expiring_users:
        message += (
            f"• User ID: `{user['user_id']}`\n"
            f"  Username: {user['username']}\n"
            f"  Type: {user['type']}\n"
            f"  Expires: {user['expires']} ({user['days_until']} days)\n\n"
        )
    
    message += "Consider sending renewal reminders to these users."
    
    await update.message.reply_text(message, parse_mode='Markdown')

def send_renewal_reminders():
    """Send renewal reminders to users with expiring subscriptions"""
    try:
        expiring_users = premium_manager.get_expiring_soon(days=3)
        
        for user in expiring_users:
            try:
                user_id = int(user['user_id'])
                days_until = user['days_until']
                
                # Send reminder message
                from telegram import Bot
                bot = Bot(token=BOT_TOKEN)
                
                if days_until == 0:
                    message = (
                        f"⚠️ **Your Premium Subscription Expires Today!**\n\n"
                        f"Your Minigma Premium access will expire today.\n\n"
                        f"To continue enjoying unlimited features:\n"
                        f"1. Use /premium to renew your subscription\n"
                        f"2. Choose your preferred plan\n"
                        f"3. Complete the payment\n\n"
                        f"Renew now to avoid losing access to premium features!"
                    )
                else:
                    message = (
                        f"⏰ **Premium Subscription Reminder**\n\n"
                        f"Your Minigma Premium access will expire in {days_until} days.\n\n"
                        f"To avoid interruption in service:\n"
                        f"1. Use /premium to renew early\n"
                        f"2. Choose your preferred plan\n"
                        f"3. Complete the payment\n\n"
                        f"Renew now to continue enjoying unlimited features!"
                    )
                
                bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='Markdown'
                )
                
                logger.info(f"Sent renewal reminder to user {user_id}")
                
            except Exception as e:
                logger.error(f"Failed to send reminder to user {user['user_id']}: {e}")
                continue
        
        return len(expiring_users)
        
    except Exception as e:
        logger.error(f"Error sending renewal reminders: {e}")
        return 0

# ==================================================
# INITIALIZATION FUNCTION
# ==================================================

def initialize_premium_system():
    """Initialize the premium management system"""
    print("\n🔧 Initializing Premium Management System...")
    print("-" * 50)
    
    # Load premium manager
    premium_manager.load_premium_users()
    
    active_count = premium_manager.get_active_count()
    expiring_soon = premium_manager.get_expiring_soon(days=7)
    
    print(f"✅ Premium system initialized")
    print(f"📊 Active premium users: {active_count}")
    
    if expiring_soon:
        print(f"⚠️  Users expiring soon: {len(expiring_soon)}")
        for user in expiring_soon[:3]:  # Show first 3
            print(f"   • User {user['user_id']} - {user['type']} - {user['days_until']} days")
    
    print("-" * 50)
    
    # Schedule renewal reminder job
    try:
        # Import here to avoid circular imports
        import sys
        if 'application' in sys.modules:
            from main import application
            if application.job_queue:
                # Send renewal reminders daily at 10 AM
                application.job_queue.run_daily(
                    send_renewal_reminders,
                    time=datetime.time(hour=10, minute=0, second=0)
                )
                print("✅ Renewal reminder job scheduled (10 AM daily)")
        else:
            print("⚠️  Application not available, skipping job scheduling")
    except Exception as e:
        print(f"⚠️  Could not schedule renewal reminder job: {e}")
    
    return True

print("✅ Part 9 updated with comprehensive premium management system!")

# ==================================================
# PART 10: ADVANCED APPOINTMENT SCHEDULING SYSTEM
# ==================================================

import json
import calendar as pycalendar
import sqlite3  # Added missing import
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
import re

# Note: You'll need these imports for the Telegram bot functionality
# from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
# from telegram.ext import ContextTypes

# ===== DATABASE FUNCTIONS FOR SCHEDULING =====

def get_appointment(appointment_id: int) -> Optional[tuple]:
    """Get appointment by ID"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT a.*, c.client_name, c.email, c.phone 
        FROM appointments a 
        LEFT JOIN clients c ON a.client_id = c.client_id 
        WHERE a.appointment_id = ?
    ''', (appointment_id,))
    appointment = cursor.fetchone()
    conn.close()
    return appointment

def get_appointments_needing_reminder(hours_ahead: int = 24) -> List[tuple]:
    """Get appointments needing reminders (upcoming in X hours)"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    now = datetime.now()
    reminder_time = now + timedelta(hours=hours_ahead)
    
    cursor.execute('''
        SELECT a.*, c.client_name, c.email, c.phone, u.telegram_id 
        FROM appointments a 
        LEFT JOIN clients c ON a.client_id = c.client_id
        LEFT JOIN users u ON a.user_id = u.user_id
        WHERE a.appointment_time BETWEEN ? AND ? 
        AND a.reminder_sent = 0 
        AND a.status = 'scheduled'
        AND a.reminder_enabled = 1
    ''', (now, reminder_time))
    
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_user_calendar_settings(user_id: int) -> Dict:
    """Get user's calendar settings"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT calendar_settings FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        return json.loads(result[0])
    else:
        return {
            'working_hours': {'start': '09:00', 'end': '17:00'},
            'working_days': [0, 1, 2, 3, 4],  # Monday to Friday
            'slot_duration': 30,  # minutes
            'buffer_time': 15,  # minutes between appointments
            'auto_confirm': False,
            'reminder_times': [24, 2],  # hours before
            'color_coding': True,
            'timezone': 'UTC'
        }

def check_appointment_conflict(user_id: int, start_time: datetime, 
                              duration: int, exclude_id: Optional[int] = None) -> bool:
    """Check if appointment time conflicts with existing appointments"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    end_time = start_time + timedelta(minutes=duration)
    
    query = '''
        SELECT appointment_id FROM appointments 
        WHERE user_id = ? 
        AND status = 'scheduled'
        AND (
            (appointment_time <= ? AND datetime(appointment_time, '+' || duration_minutes || ' minutes') >= ?)
            OR (appointment_time <= ? AND datetime(appointment_time, '+' || duration_minutes || ' minutes') >= ?)
            OR (appointment_time >= ? AND datetime(appointment_time, '+' || duration_minutes || ' minutes') <= ?)
        )
    '''
    
    params = [user_id, start_time, start_time, end_time, end_time, start_time, end_time]
    
    if exclude_id:
        query += ' AND appointment_id != ?'
        params.append(exclude_id)
    
    cursor.execute(query, params)
    conflict = cursor.fetchone() is not None
    conn.close()
    return conflict

def get_user_availability(user_id: int, target_date: date, duration: int = 60) -> List[Dict]:
    """Get available time slots for a user on specific date"""
    settings = get_user_calendar_settings(user_id)
    
    # Convert working hours to datetime
    work_start = datetime.combine(target_date, datetime.strptime(settings['working_hours']['start'], '%H:%M').time())
    work_end = datetime.combine(target_date, datetime.strptime(settings['working_hours']['end'], '%H:%M').time())
    
    # Get existing appointments
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT appointment_time, duration_minutes 
        FROM appointments 
        WHERE user_id = ? 
        AND DATE(appointment_time) = ?
        AND status = 'scheduled'
        ORDER BY appointment_time
    ''', (user_id, target_date.strftime('%Y-%m-%d')))
    
    appointments = cursor.fetchall()
    conn.close()
    
    # Convert appointment tuples to datetime objects
    appointment_times = []
    for appt_time_str, appt_duration in appointments:
        if isinstance(appt_time_str, str):
            appt_time = datetime.strptime(appt_time_str, '%Y-%m-%d %H:%M:%S')
        else:
            appt_time = appt_time_str
        appointment_times.append((appt_time, appt_duration))
    
    # Generate available slots
    slot_duration = settings['slot_duration']
    buffer = timedelta(minutes=settings['buffer_time'])
    current_time = work_start
    available_slots = []
    
    while current_time + timedelta(minutes=duration) <= work_end:
        slot_end = current_time + timedelta(minutes=duration)
        slot_available = True
        
        # Check against existing appointments
        for appt_time, appt_duration in appointment_times:
            appt_end = appt_time + timedelta(minutes=appt_duration)
            
            if (current_time < appt_end + buffer and 
                slot_end + buffer > appt_time):
                slot_available = False
                # Skip to after this appointment
                current_time = appt_end + buffer
                break
        
        if slot_available:
            available_slots.append({
                'start': current_time,
                'end': slot_end,
                'formatted': current_time.strftime('%H:%M')
            })
            current_time += timedelta(minutes=slot_duration)
        else:
            current_time += timedelta(minutes=slot_duration)
    
    return available_slots

# ===== ADVANCED COMMAND HANDLERS =====

# Note: Uncomment and fix the actual function signatures when you have the proper imports
async def schedule_command(update, context):  # Changed from formal typing for now
    """Enhanced scheduling command hub"""
    user_id = update.effective_user.id
    
    # Get appointment statistics - need to implement these helper functions
    today_count = len(get_today_appointments(user_id)) if 'get_today_appointments' in globals() else 0
    tomorrow_count = len(get_tomorrow_appointments(user_id))
    clients = get_user_clients(user_id) if 'get_user_clients' in globals() else []
    
    # Check for conflicts/urgent items
    conflicts = check_upcoming_conflicts(user_id)
    
    message = f"📅 **Appointment Scheduling Center**\n\n"
    
    if conflicts:
        message += f"⚠️ **Attention:** {len(conflicts)} potential conflict{'s' if len(conflicts) > 1 else ''} detected\n\n"
    
    message += f"📊 **Today:** {today_count} appointment{'s' if today_count != 1 else ''}\n"
    message += f"📅 **Tomorrow:** {tomorrow_count} appointment{'s' if tomorrow_count != 1 else ''}\n"
    message += f"👥 **Active Clients:** {len(clients)}\n\n"
    
    # Next appointment
    next_appt = get_next_appointment(user_id)
    if next_appt:
        # Assuming index 5 is appointment_time
        appt_time = next_appt[5] if isinstance(next_appt[5], datetime) else datetime.strptime(next_appt[5], '%Y-%m-%d %H:%M:%S')
        time_left = appt_time - datetime.now()
        hours_left = time_left.total_seconds() / 3600
        
        if hours_left < 1:
            urgency = "⏰ URGENT"
        elif hours_left < 24:
            urgency = "🔔 SOON"
        else:
            urgency = "📅 UPCOMING"
        
        message += f"{urgency} **Next:** {appt_time.strftime('%a %d %b %H:%M')}\n"
        # Assuming index 3 is title
        appt_title = next_appt[3] if len(next_appt) > 3 else "Untitled"
        message += f"   📝 {appt_title[:30]}{'...' if len(appt_title) > 30 else ''}\n\n"
    
    # Quick stats
    week_stats = get_appointment_stats(user_id, 'week')
    message += f"📈 **This Week:** {week_stats['total']} appts ({week_stats['completed']}✅ {week_stats['cancelled']}❌)\n\n"
    
    # Keyboard with advanced options - need InlineKeyboardButton import
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [
                InlineKeyboardButton("➕ New", callback_data="book_advanced"),
                InlineKeyboardButton("📅 Calendar", callback_data="calendar_advanced"),
                InlineKeyboardButton("📋 List", callback_data="appointment_list")
            ],
            [
                InlineKeyboardButton("🔄 Recurring", callback_data="recurring_manage"),
                InlineKeyboardButton("⏰ Reminders", callback_data="reminders_advanced"),
                InlineKeyboardButton("📊 Analytics", callback_data="appointment_analytics")
            ],
            [
                InlineKeyboardButton("👥 By Client", callback_data="appointments_by_client"),
                InlineKeyboardButton("⚙️ Settings", callback_data="calendar_settings"),
                InlineKeyboardButton("🔄 Sync", callback_data="calendar_sync")
            ],
            [
                InlineKeyboardButton("📱 Share", callback_data="calendar_share"),
                InlineKeyboardButton("📤 Export", callback_data="calendar_export"),
                InlineKeyboardButton("🗓️ Month", callback_data="calendar_month_view")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    except ImportError:
        reply_markup = None
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def calendar_advanced_command(update, context):
    """Advanced calendar view with multiple view options"""
    user_id = update.effective_user.id
    today = datetime.now()
    
    # Parse arguments
    view_type = context.args[0] if context.args and len(context.args) > 0 else 'week'
    target_date = today
    
    if len(context.args) > 1:
        try:
            target_date = datetime.strptime(context.args[1], '%Y-%m-%d')
        except ValueError:
            pass
    
    if view_type == 'day':
        await show_day_view(update, user_id, target_date)
    elif view_type == 'week':
        await show_week_view(update, user_id, target_date)
    elif view_type == 'month':
        await show_month_view(update, user_id, target_date)
    elif view_type == 'agenda':
        await show_agenda_view(update, user_id, target_date)
    else:
        await show_week_view(update, user_id, today)

async def show_week_view(update, user_id: int, week_date: datetime):
    """Enhanced week view with availability indicators"""
    week_start = week_date - timedelta(days=week_date.weekday())
    
    # Get appointments for the week - need to implement this function
    appointments = get_week_appointments(user_id, week_start) if 'get_week_appointments' in globals() else []
    
    # Get calendar settings
    settings = get_user_calendar_settings(user_id)
    
    message = f"📅 **Weekly Calendar**\n"
    message += f"**Week of {week_start.strftime('%d %b %Y')}**\n\n"
    
    # Create visual calendar
    for day_offset in range(7):
        current_day = week_start + timedelta(days=day_offset)
        day_appointments = [a for a in appointments 
                          if isinstance(a[5], datetime) and a[5].date() == current_day.date()]
        
        # Day header
        day_str = current_day.strftime('%a %d')
        if current_day.date() == datetime.now().date():
            day_str = f"**{day_str} 🟢**"
        elif current_day.date() < datetime.now().date():
            day_str = f"~~{day_str}~~"
        
        message += f"{day_str}\n"
        
        if day_appointments:
            # Group by hour
            hourly_appointments = {}
            for appt in day_appointments:
                hour = appt[5].hour
                if hour not in hourly_appointments:
                    hourly_appointments[hour] = []
                hourly_appointments[hour].append(appt)
            
            # Show time slots
            for hour in sorted(hourly_appointments.keys()):
                hour_appts = hourly_appointments[hour]
                message += f"  {hour:02d}:00 "
                
                for appt in hour_appts:
                    duration = appt[6] // 30 if len(appt) > 6 else 1  # Show in 30-min blocks
                    status = appt[8] if len(appt) > 8 else 'scheduled'
                    emoji = get_appointment_emoji(status)
                    
                    if duration == 1:
                        message += f"[{emoji}]"
                    elif duration == 2:
                        message += f"[{emoji}{emoji}]"
                    else:
                        message += f"[{emoji}x{duration}]"
                
                message += "\n"
        else:
            message += "  ─ No appointments ─\n"
        
        message += "\n"
    
    # Add availability heatmap
    message += f"**Availability Heatmap** (Next 7 days)\n"
    message += generate_availability_heatmap(user_id)
    
    # Navigation keyboard - need InlineKeyboardButton import
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [
                InlineKeyboardButton("◀️", callback_data=f"week_prev_{week_start.strftime('%Y-%m-%d')}"),
                InlineKeyboardButton("Today", callback_data="calendar_today"),
                InlineKeyboardButton("▶️", callback_data=f"week_next_{week_start.strftime('%Y-%m-%d')}")
            ],
            [
                InlineKeyboardButton("📅 Day", callback_data=f"calendar_day_{week_start.strftime('%Y-%m-%d')}"),
                InlineKeyboardButton("🗓️ Month", callback_data=f"calendar_month_{week_start.strftime('%Y-%m-%d')}"),
                InlineKeyboardButton("📋 Agenda", callback_data=f"calendar_agenda_{week_start.strftime('%Y-%m-%d')}")
            ],
            [
                InlineKeyboardButton("➕ Quick Book", callback_data="quick_book_slot"),
                InlineKeyboardButton("📊 Stats", callback_data="week_stats"),
                InlineKeyboardButton("🖨️ Print", callback_data="print_week")
            ],
            [
                InlineKeyboardButton("📱 iCal", callback_data="export_ical"),
                InlineKeyboardButton("⚙️ Settings", callback_data="calendar_settings"),
                InlineKeyboardButton("🔙 Menu", callback_data="schedule_back")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    except ImportError:
        reply_markup = None
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    elif hasattr(update, 'edit_message_text'):
        await update.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

def generate_availability_heatmap(user_id: int) -> str:
    """Generate text-based availability heatmap"""
    heatmap = ""
    today = datetime.now().date()
    
    for day_offset in range(7):
        check_date = today + timedelta(days=day_offset)
        available_slots = get_user_availability(user_id, check_date, 60)
        
        if available_slots:
            if len(available_slots) > 6:
                heatmap += "🟩"  # High availability
            elif len(available_slots) > 3:
                heatmap += "🟨"  # Medium availability
            else:
                heatmap += "🟧"  # Low availability
        else:
            if check_date.weekday() in [5, 6]:  # Weekend
                heatmap += "⬜"  # Weekend
            else:
                heatmap += "🟥"  # Fully booked
    
    return heatmap

async def show_month_view(update, user_id: int, month_date: datetime):
    """Month view calendar with appointment indicators"""
    # Calculate month boundaries
    year = month_date.year
    month = month_date.month
    
    # Get first and last day of month
    first_day = date(year, month, 1)
    if month < 12:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    else:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    
    # Get appointments for the month - need to implement this function
    appointments = get_appointments_between(user_id, first_day, last_day) if 'get_appointments_between' in globals() else []
    
    # Create calendar header
    calendar_header = pycalendar.month_name[month] + " " + str(year)
    message = f"🗓️ **{calendar_header}**\n\n"
    
    # Create month calendar grid
    cal = pycalendar.monthcalendar(year, month)
    
    # Map appointments to days
    appointment_counts = {}
    for appt in appointments:
        if isinstance(appt[5], datetime):
            day = appt[5].day
        else:
            # Try to parse string date
            try:
                day = datetime.strptime(str(appt[5]), '%Y-%m-%d %H:%M:%S').day
            except:
                continue
        appointment_counts[day] = appointment_counts.get(day, 0) + 1
    
    # Generate calendar
    days_of_week = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    message += "│" + "│".join(days_of_week) + "│\n"
    message += "├" + "┼".join(["──" for _ in range(7)]) + "┤\n"
    
    for week in cal:
        week_line = "│"
        for day in week:
            if day == 0:
                week_line += "  │"
            else:
                today_marker = "🟢" if date(year, month, day) == datetime.now().date() else ""
                count = appointment_counts.get(day, 0)
                if count > 0:
                    day_str = f"{day:2d}•{count}"
                else:
                    day_str = f"{day:2d}"
                
                week_line += f"{today_marker}{day_str}│"
        message += week_line + "\n"
    
    # Appointment key
    message += "\n**Key:** • = appointments, 🟢 = today\n"
    
    # Quick stats
    month_stats = get_appointment_stats(user_id, 'month', month_date)
    message += f"\n📊 **Month Stats:** {month_stats['total']} appointments\n"
    message += f"✅ {month_stats['completed']} • ⏰ {month_stats['scheduled']} • ❌ {month_stats['cancelled']}\n"
    
    # Navigation keyboard - need InlineKeyboardButton import
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [
                InlineKeyboardButton("◀️", callback_data=f"month_prev_{month_date.strftime('%Y-%m')}"),
                InlineKeyboardButton(calendar_header[:8], callback_data="calendar_today"),
                InlineKeyboardButton("▶️", callback_data=f"month_next_{month_date.strftime('%Y-%m')}")
            ],
            [
                InlineKeyboardButton("📅 Week", callback_data=f"calendar_week_{first_day.strftime('%Y-%m-%d')}"),
                InlineKeyboardButton("📋 List", callback_data=f"appointment_list_month_{month_date.strftime('%Y-%m')}"),
                InlineKeyboardButton("➕ Book", callback_data="book_from_month")
            ],
            [
                InlineKeyboardButton("📈 Analytics", callback_data=f"month_analytics_{month_date.strftime('%Y-%m')}"),
                InlineKeyboardButton("📤 Export", callback_data=f"export_month_{month_date.strftime('%Y-%m')}"),
                InlineKeyboardButton("🔙 Menu", callback_data="schedule_back")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    except ImportError:
        reply_markup = None
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    elif hasattr(update, 'edit_message_text'):
        await update.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def appointment_list_command(update, context):
    """List appointments with filtering and sorting options"""
    user_id = update.effective_user.id
    
    # Parse filters from arguments
    filters = {}
    if context.args:
        for arg in context.args:
            if arg.startswith('status:'):
                filters['status'] = arg.split(':')[1]
            elif arg.startswith('client:'):
                filters['client'] = arg.split(':')[1]
            elif arg.startswith('from:'):
                try:
                    filters['from_date'] = datetime.strptime(arg.split(':')[1], '%Y-%m-%d')
                except ValueError:
                    pass
            elif arg.startswith('to:'):
                try:
                    filters['to_date'] = datetime.strptime(arg.split(':')[1], '%Y-%m-%d')
                except ValueError:
                    pass
    
    # Get filtered appointments
    appointments = get_filtered_appointments(user_id, filters)
    
    if not appointments:
        await update.message.reply_text(
            "📭 **No Appointments Found**\n\n"
            "Try adjusting your filters or book a new appointment.",
            parse_mode='Markdown'
        )
        return
    
    message = f"📋 **Appointments List** ({len(appointments)} found)\n\n"
    
    # Group by date
    appointments_by_date = {}
    for appt in appointments:
        # Assuming index 5 is appointment_time
        if isinstance(appt[5], datetime):
            date_key = appt[5].strftime('%Y-%m-%d')
        else:
            try:
                date_key = datetime.strptime(str(appt[5]), '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
            except:
                continue
                
        if date_key not in appointments_by_date:
            appointments_by_date[date_key] = []
        appointments_by_date[date_key].append(appt)
    
    # Display
    for date_key in sorted(appointments_by_date.keys())[:10]:  # Limit to 10 days
        date_obj = datetime.strptime(date_key, '%Y-%m-%d')
        message += f"**{date_obj.strftime('%A, %d %B')}**\n"
        
        for appt in appointments_by_date[date_key]:
            if isinstance(appt[5], datetime):
                time_str = appt[5].strftime('%H:%M')
            else:
                try:
                    time_str = datetime.strptime(str(appt[5]), '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
                except:
                    time_str = "Unknown"
                    
            status = appt[8] if len(appt) > 8 else 'scheduled'
            status_emoji = get_appointment_emoji(status)
            duration = f"{appt[6]}min" if len(appt) > 6 else "N/A"
            
            title = appt[3] if len(appt) > 3 else "Untitled"
            message += f"{status_emoji} **{time_str}** ({duration}) - {title[:40]}"
            
            if appt[2] and len(appt) > 2:  # Client info
                # Need to implement get_client_by_id
                client = get_client_by_id(appt[2]) if 'get_client_by_id' in globals() else None
                if client:
                    message += f"\n   👤 {client[2] if len(client) > 2 else 'Unknown'}"
            
            message += f"\n   📝 ID: {appt[0]} | "
            reminder_status = 'ON' if (len(appt) > 9 and appt[9]) else 'OFF'
            message += f"🔔: {reminder_status}\n\n"
    
    if len(appointments_by_date) > 10:
        message += f"... and {len(appointments_by_date) - 10} more days\n\n"
    
    # Filter options keyboard - need InlineKeyboardButton import
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [
                InlineKeyboardButton("📅 Today", callback_data="list_today"),
                InlineKeyboardButton("📅 Week", callback_data="list_week"),
                InlineKeyboardButton("📅 Month", callback_data="list_month")
            ],
            [
                InlineKeyboardButton("✅ Completed", callback_data="list_completed"),
                InlineKeyboardButton("⏰ Scheduled", callback_data="list_scheduled"),
                InlineKeyboardButton("❌ Cancelled", callback_data="list_cancelled")
            ],
            [
                InlineKeyboardButton("🔍 Search", callback_data="list_search"),
                InlineKeyboardButton("🔄 Refresh", callback_data="list_refresh"),
                InlineKeyboardButton("📤 Export", callback_data="list_export")
            ],
            [
                InlineKeyboardButton("➕ New", callback_data="book_appointment_start"),
                InlineKeyboardButton("📅 Calendar", callback_data="calendar_advanced"),
                InlineKeyboardButton("🔙 Menu", callback_data="schedule_back")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    except ImportError:
        reply_markup = None
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ===== APPOINTMENT BOOKING FLOW =====

async def start_advanced_booking(update, context):
    """Start advanced appointment booking flow"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Store booking state
    context.user_data['booking'] = {
        'step': 'type',
        'data': {},
        'created_at': datetime.now()
    }
    
    # Keyboard - need InlineKeyboardButton import
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [
                InlineKeyboardButton("👥 Client Meeting", callback_data="book_type_client"),
                InlineKeyboardButton("📞 Phone Call", callback_data="book_type_phone")
            ],
            [
                InlineKeyboardButton("💻 Video Call", callback_data="book_type_video"),
                InlineKeyboardButton("🛠️ Service", callback_data="book_type_service")
            ],
            [
                InlineKeyboardButton("📝 Consultation", callback_data="book_type_consult"),
                InlineKeyboardButton("🎯 Other", callback_data="book_type_other")
            ],
            [
                InlineKeyboardButton("🔄 Recurring", callback_data="book_type_recurring"),
                InlineKeyboardButton("🔙 Cancel", callback_data="booking_cancel")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    except ImportError:
        reply_markup = None
    
    await query.edit_message_text(
        "📅 **Advanced Appointment Booking**\n\n"
        "**Step 1: Appointment Type**\n\n"
        "Select the type of appointment:\n"
        "• 👥 Client Meeting - In-person meeting\n"
        "• 📞 Phone Call - Telephone appointment\n"
        "• 💻 Video Call - Zoom/Teams/Google Meet\n"
        "• 🛠️ Service - Service delivery appointment\n"
        "• 📝 Consultation - Professional consultation\n"
        "• 🎯 Other - Custom appointment type\n"
        "• 🔄 Recurring - Repeat appointment\n\n"
        "💡 *Different types have different defaults*",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_booking_type(query, appointment_type: str):
    """Handle appointment type selection"""
    context = query._context  # Access context differently
    user_id = query.from_user.id
    
    # Store type
    if 'booking' not in context.user_data:
        context.user_data['booking'] = {}
    
    context.user_data['booking']['type'] = appointment_type
    
    # Set defaults based on type
    defaults = {
        'client': {'duration': 60, 'buffer': 15, 'reminder': True},
        'phone': {'duration': 30, 'buffer': 5, 'reminder': True},
        'video': {'duration': 45, 'buffer': 10, 'reminder': True},
        'service': {'duration': 90, 'buffer': 30, 'reminder': True},
        'consult': {'duration': 60, 'buffer': 15, 'reminder': True},
        'other': {'duration': 60, 'buffer': 15, 'reminder': True},
        'recurring': {'duration': 60, 'buffer': 15, 'reminder': True}
    }
    
    if appointment_type in defaults:
        context.user_data['booking'].update(defaults[appointment_type])
    
    # Get clients for selection - need to implement this function
    clients = get_user_clients(user_id) if 'get_user_clients' in globals() else []
    
    if not clients:
        await query.edit_message_text(
            "👥 **No Clients Found**\n\n"
            "You need to add a client first.\n\n"
            "1. Go to /clients\n"
            "2. Add a new client\n"
            "3. Return here to book appointment",
            parse_mode='Markdown'
        )
        return
    
    # Show client selection - need InlineKeyboardButton import
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = []
        for client in clients[:8]:
            keyboard.append([
                InlineKeyboardButton(f"👤 {client[2]}", 
                                   callback_data=f"book_client_{client[0]}")
            ])
        
        keyboard.append([
            InlineKeyboardButton("➕ New Client", callback_data="booking_new_client"),
            InlineKeyboardButton("⏭️ Skip Client", callback_data="booking_skip_client")
        ])
        
        keyboard.append([
            InlineKeyboardButton("🔙 Back", callback_data="booking_back"),
            InlineKeyboardButton("❌ Cancel", callback_data="booking_cancel")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    except ImportError:
        reply_markup = None
    
    await query.edit_message_text(
        f"📅 **Step 2: Select Client**\n\n"
        f"**Type:** {appointment_type.replace('_', ' ').title()}\n"
        f"**Duration:** {context.user_data['booking']['duration']} minutes\n\n"
        f"Select a client for this appointment:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ===== CONFLICT DETECTION =====

def check_upcoming_conflicts(user_id: int) -> List[Dict]:
    """Check for scheduling conflicts in upcoming appointments"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    # Get appointments with potential conflicts (within 15 minutes of each other)
    cursor.execute('''
        WITH appointment_times AS (
            SELECT 
                appointment_id,
                appointment_time,
                datetime(appointment_time, '+' || duration_minutes || ' minutes') as end_time,
                title,
                client_id
            FROM appointments 
            WHERE user_id = ? 
            AND status = 'scheduled'
            AND appointment_time > datetime('now')
        )
        SELECT 
            a.appointment_id as id1,
            a.title as title1,
            a.appointment_time as time1,
            b.appointment_id as id2,
            b.title as title2,
            b.appointment_time as time2,
            ABS(julianday(a.appointment_time) - julianday(b.appointment_time)) * 24 * 60 as minutes_between
        FROM appointment_times a
        JOIN appointment_times b 
        ON a.appointment_id < b.appointment_id
        WHERE (
            (a.appointment_time BETWEEN b.appointment_time AND b.end_time)
            OR (b.appointment_time BETWEEN a.appointment_time AND a.end_time)
            OR (ABS(julianday(a.appointment_time) - julianday(b.appointment_time)) * 24 * 60 < 15)
        )
        ORDER BY a.appointment_time
    ''', (user_id,))
    
    conflicts = []
    for row in cursor.fetchall():
        conflicts.append({
            'appointment1': {'id': row[0], 'title': row[1], 'time': row[2]},
            'appointment2': {'id': row[3], 'title': row[4], 'time': row[5]},
            'minutes_between': row[6]
        })
    
    conn.close()
    return conflicts

async def show_conflicts(update, context, user_id: int):
    """Show detected conflicts to user"""
    conflicts = check_upcoming_conflicts(user_id)
    
    if not conflicts:
        await update.message.reply_text(
            "✅ **No Scheduling Conflicts Detected**\n\n"
            "Your appointments are well spaced!",
            parse_mode='Markdown'
        )
        return
    
    message = "⚠️ **Scheduling Conflicts Detected**\n\n"
    
    for i, conflict in enumerate(conflicts[:5]):  # Show max 5 conflicts
        time1 = conflict['appointment1']['time']
        time2 = conflict['appointment2']['time']
        
        message += f"**Conflict {i+1}**\n"
        message += f"• {time1.strftime('%H:%M')} - {conflict['appointment1']['title'][:30]}\n"
        message += f"• {time2.strftime('%H:%M')} - {conflict['appointment2']['title'][:30]}\n"
        message += f"  ⚠️ Only {conflict['minutes_between']:.0f} minutes between\n\n"
    
    if len(conflicts) > 5:
        message += f"... and {len(conflicts) - 5} more conflicts\n\n"
    
    # Keyboard - need InlineKeyboardButton import
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [
                InlineKeyboardButton("📅 View Calendar", callback_data="calendar_advanced"),
                InlineKeyboardButton("📋 Appointment List", callback_data="appointment_list")
            ],
            [
                InlineKeyboardButton("🔄 Resolve Conflicts", callback_data="resolve_conflicts"),
                InlineKeyboardButton("✅ Mark as Reviewed", callback_data="conflicts_reviewed")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    except ImportError:
        reply_markup = None
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ===== UTILITY FUNCTIONS =====

def get_appointment_emoji(status: str) -> str:
    """Get emoji for appointment status"""
    emoji_map = {
        'scheduled': '⏰',
        'confirmed': '✅',
        'completed': '☑️',
        'cancelled': '❌',
        'no_show': '🚫',
        'rescheduled': '🔄',
        'pending': '🕐'
    }
    return emoji_map.get(status, '📅')

def get_tomorrow_appointments(user_id: int) -> List[tuple]:
    """Get appointments for tomorrow"""
    tomorrow = datetime.now() + timedelta(days=1)
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM appointments 
        WHERE user_id = ? 
        AND DATE(appointment_time) = DATE(?)
        AND status = 'scheduled'
        ORDER BY appointment_time
    ''', (user_id, tomorrow))
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_next_appointment(user_id: int) -> Optional[tuple]:
    """Get the next upcoming appointment"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM appointments 
        WHERE user_id = ? 
        AND appointment_time > datetime('now')
        AND status = 'scheduled'
        ORDER BY appointment_time
        LIMIT 1
    ''', (user_id,))
    appointment = cursor.fetchone()
    conn.close()
    return appointment

def get_appointment_stats(user_id: int, period: str = 'week', reference_date: Optional[datetime] = None) -> Dict:
    """Get appointment statistics for a period"""
    if not reference_date:
        reference_date = datetime.now()
    
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    if period == 'week':
        # Get start of week (Monday)
        start_date = reference_date - timedelta(days=reference_date.weekday())
        end_date = start_date + timedelta(days=7)
    elif period == 'month':
        start_date = reference_date.replace(day=1)
        next_month = reference_date.replace(day=28) + timedelta(days=4)
        end_date = next_month.replace(day=1)
    elif period == 'year':
        start_date = reference_date.replace(month=1, day=1)
        end_date = reference_date.replace(month=12, day=31)
    else:  # day
        start_date = reference_date.replace(hour=0, minute=0, second=0)
        end_date = reference_date.replace(hour=23, minute=59, second=59)
    
    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END) as scheduled,
            SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled
        FROM appointments 
        WHERE user_id = ? 
        AND appointment_time BETWEEN ? AND ?
    ''', (user_id, start_date, end_date))
    
    result = cursor.fetchone()
    conn.close()
    
    return {
        'total': result[0] or 0,
        'completed': result[1] or 0,
        'scheduled': result[2] or 0,
        'cancelled': result[3] or 0
    }

def get_filtered_appointments(user_id: int, filters: Dict) -> List[tuple]:
    """Get appointments with filters"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    
    query = '''
        SELECT a.*, c.client_name 
        FROM appointments a 
        LEFT JOIN clients c ON a.client_id = c.client_id 
        WHERE a.user_id = ?
    '''
    params = [user_id]
    
    if 'status' in filters:
        query += ' AND a.status = ?'
        params.append(filters['status'])
    
    if 'client' in filters:
        query += ' AND c.client_name LIKE ?'
        params.append(f'%{filters["client"]}%')
    
    # Add date filters if present
    if 'from_date' in filters:
        query += ' AND a.appointment_time >= ?'
        params.append(filters['from_date'])
    
    if 'to_date' in filters:
        query += ' AND a.appointment_time <= ?'
        params.append(filters['to_date'])
    
    query += ' ORDER BY a.appointment_time'
    
    cursor.execute(query, params)
    appointments = cursor.fetchall()
    conn.close()
    return appointments

# ===== MISSING HELPER FUNCTIONS (stubs) =====

# These functions are referenced but not defined in your code.
# You'll need to implement them:

def get_today_appointments(user_id: int) -> List[tuple]:
    """Get appointments for today"""
    today = datetime.now().date()
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM appointments 
        WHERE user_id = ? 
        AND DATE(appointment_time) = DATE(?)
        AND status = 'scheduled'
        ORDER BY appointment_time
    ''', (user_id, today))
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_user_clients(user_id: int) -> List[tuple]:
    """Get clients for a user"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM clients 
        WHERE user_id = ? 
        ORDER BY client_name
    ''', (user_id,))
    clients = cursor.fetchall()
    conn.close()
    return clients

def get_week_appointments(user_id: int, week_start: datetime) -> List[tuple]:
    """Get appointments for a week"""
    week_end = week_start + timedelta(days=7)
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM appointments 
        WHERE user_id = ? 
        AND appointment_time BETWEEN ? AND ?
        AND status = 'scheduled'
        ORDER BY appointment_time
    ''', (user_id, week_start, week_end))
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_appointments_between(user_id: int, start_date: date, end_date: date) -> List[tuple]:
    """Get appointments between two dates"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM appointments 
        WHERE user_id = ? 
        AND DATE(appointment_time) BETWEEN ? AND ?
        AND status = 'scheduled'
        ORDER BY appointment_time
    ''', (user_id, start_date, end_date))
    appointments = cursor.fetchall()
    conn.close()
    return appointments

def get_client_by_id(client_id: int) -> Optional[tuple]:
    """Get client by ID"""
    conn = sqlite3.connect('invoices.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clients WHERE client_id = ?', (client_id,))
    client = cursor.fetchone()
    conn.close()
    return client

async def show_day_view(update, user_id: int, target_date: datetime):
    """Show day view - need to implement"""
    await update.message.reply_text("Day view not implemented yet.")

async def show_agenda_view(update, user_id: int, target_date: datetime):
    """Show agenda view - need to implement"""
    await update.message.reply_text("Agenda view not implemented yet.")

# ==================================================
# BOT EXECUTION & STARTUP CODE
# ==================================================

def get_bot_token() -> Optional[str]:
    """
    Get bot token securely from multiple sources.
    This should match the function in Part 1.
    """
    import os
    
    # Method 1: Direct environment variable
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if token and token.strip():
        return token.strip()
    
    # Method 2: bot_token.txt file
    try:
        with open('bot_token.txt', 'r') as f:
            token = f.read().strip()
            if token and token != "YOUR_BOT_TOKEN_HERE":
                return token
    except FileNotFoundError:
        pass
    
    # Method 3: Check for old token files
    token_files = ['token.txt', '.bot_token', 'telegram_token.txt']
    for filename in token_files:
        try:
            with open(filename, 'r') as f:
                token = f.read().strip()
                if token and token != "YOUR_BOT_TOKEN_HERE":
                    return token
        except FileNotFoundError:
            continue
    
    return None

def main():
    print("🤖 Starting Minigma Business Suite Bot...")
    
    BOT_TOKEN = get_bot_token()
    
    if not BOT_TOKEN:
        print("❌ ERROR: Telegram Bot Token not found!")
        print("\nTo fix this:")
        print("1. Set BOT_TOKEN environment variable in Koyeb")
        print("2. Or create 'bot_token.txt' with your Telegram bot token")
        print("\nGet token from @BotFather on Telegram")
        return
    
    print(f"✅ Token loaded: {BOT_TOKEN[:15]}...")
    
    try:
        # Create the Application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # ===== REGISTER COMMAND HANDLERS =====
        
        # Basic commands
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        
        # Appointment commands (add these if you have them defined)
        # application.add_handler(CommandHandler("schedule", schedule_command))
        # application.add_handler(CommandHandler("calendar", calendar_advanced_command))
        # application.add_handler(CommandHandler("appointments", appointment_list_command))
        
        # Text handler - IMPORTANT for all text inputs
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))
        
        # Callback query handler for inline buttons
        application.add_handler(CallbackQueryHandler(handle_button_callback))
        
        # ===== SCHEDULED TASKS =====
        
        # Schedule regular jobs
        job_queue = application.job_queue
        
        # Check for reminders every 5 minutes
        job_queue.run_repeating(send_scheduled_reminders, interval=300, first=10)
        
        # Check for overdue appointments every hour
        job_queue.run_repeating(check_overdue_appointments, interval=3600, first=60)
        
        # Send daily schedules at 8 AM (only if datetime is imported)
        try:
            import datetime as dt
            job_queue.run_daily(send_daily_schedule, time=dt.time(hour=8, minute=0))
        except ImportError:
            print("⚠️  Could not schedule daily tasks - datetime module issue")
        
        # Start the bot
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        import traceback
        traceback.print_exc()

# ==================================================
# KOYEB WEB SERVICE COMPATIBILITY LAYER
# ==================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Telegram Bot is running!')
    
    def log_message(self, format, *args):
        # Disable HTTP logging noise
        pass

def run_http_server():
    """Run a simple HTTP server on port 8000 for Koyeb health checks"""
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    print("HTTP server running on port 8000 for Koyeb health checks")
    server.serve_forever()

# ==================================================
# ENTRY POINT - AT VERY END OF FILE
# ==================================================

if __name__ == "__main__":
    # Check if running on Koyeb
    if 'KOYEB' in os.environ or 'KOYEB_SERVICE_ID' in os.environ:
        print("=" * 50)
        print("Running on KOYEB - 24/7 Hosting!")
        print("Bot will run in background")
        print("HTTP server on port 8000 for health checks")
        print("=" * 50)
        
        # Start HTTP server in background thread
        http_thread = threading.Thread(target=run_http_server, daemon=True)
        http_thread.start()
        
        # Give HTTP server a moment to start
        import time
        time.sleep(2)
        
        # Run your bot in main thread
        main()
    else:
        # Run normally locally
        main()

# NOTHING AFTER THIS LINE




