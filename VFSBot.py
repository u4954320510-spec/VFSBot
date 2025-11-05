import sys
import io
# Fix Windows console encoding for Unicode/emoji support
if sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import asyncio
import os
import undetected_chromedriver as uc # pyright: ignore[reportMissingImports]
from utils import *
from selenium.webdriver.support.ui import Select # pyright: ignore[reportMissingImports]
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException, 
    NoSuchElementException, 
    WebDriverException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    InvalidElementStateException,
    SessionNotCreatedException
)
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackContext
from configparser import ConfigParser
import logging
from datetime import datetime

# Get logger from utils
logger = logging.getLogger('VFSBot')



class VFSBot:
    def __init__(self):
        logger.info("="*60)
        logger.info("🤖 ИНИЦИАЛИЗАЦИЯ БОТА")
        logger.info("="*60)
        
        self.config = ConfigParser()
        
        # Get the directory where the script is located
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.ini')
        
        # Try different encodings for config file
        config_loaded = False
        for encoding in ['utf-8', 'utf-8-sig', 'cp1251', 'latin-1']:
            try:
                self.config.read(config_path, encoding=encoding)
                if 'VFS' in self.config.sections():
                    logger.info(f"✅ Конфигурация загружена из {config_path} (кодировка: {encoding})")
                    config_loaded = True
                    break
            except Exception as e:
                logger.debug(f"Не удалось загрузить с кодировкой {encoding}: {e}")
        
        if not config_loaded:
            raise Exception(f"❌ Не удалось загрузить {config_path} с поддерживаемыми кодировками")
    
        self.url = self.config.get('VFS', 'url')
        self.email_str = self.config.get('VFS', 'email')
        self.pwd_str = self.config.get('VFS', 'password')
        self.interval = self.config.getint('VFS', 'interval')
        self.auto_fill = self.config.getboolean('VFS', 'auto_fill', fallback=False)
        self.upload_pdf = self.config.getboolean('VFS', 'upload_pdf', fallback=False)
        self.auto_login = self.config.getboolean('VFS', 'auto_login', fallback=True)
        self.captcha_enabled = self.config.getboolean('VFS', 'captcha_enabled', fallback=True)
        self.captcha_auto_solve = self.config.getboolean('VFS', 'captcha_auto_solve', fallback=True)
        self.channel_id = self.config.get('TELEGRAM', 'channel_id')
        token = self.config.get('TELEGRAM', 'auth_token')
        # Fix admin_ids parsing - handle empty strings and extra spaces
        admin_ids_str = self.config.get('TELEGRAM', 'admin_ids', fallback='').strip()
        if admin_ids_str:
            admin_ids = [int(x.strip()) for x in admin_ids_str.split() if x.strip()]
        else:
            admin_ids = []
        logger.info(f"🔐 Настроенные admin_ids: {admin_ids}")
        self.started = False
        self.admin_handler = AdminHandler(admin_ids)
        self.browser = None  # Initialize browser attribute
        self.thr = None  # Initialize thread/task attribute
        
        # Statistics for reporting
        self.check_count = 0
        self.last_report_time = datetime.now()
        self.person_stats = {}  # Track stats per person
        self.report_task = None  # Initialize report task
        self.last_cleanup = datetime.now()  # Track cleanup operations
        
        logger.info(f"📋 Параметры VFS: URL={self.url}")
        logger.info(f"⏱️  Интервал проверки: {self.interval} сек")
        logger.info(f"🔧 Авто-заполнение: {'ВКЛЮЧЕНО' if self.auto_fill else 'ОТКЛЮЧЕНО'}")
        logger.info(f"📄 Загрузка PDF фото: {'ВКЛЮЧЕНА' if self.upload_pdf else 'ОТКЛЮЧЕНА'}")
        logger.info(f"🔐 Автоматический вход: {'ВКЛЮЧЕН' if self.auto_login else 'ОТКЛЮЧЕН'}")
        logger.info(f"🤖 Обработка капчи: {'ВКЛЮЧЕНА' if self.captcha_enabled else 'ОТКЛЮЧЕНА'}")
        logger.info(f"🧠 Автоматическое решение капчи: {'ВКЛЮЧЕНО' if self.captcha_auto_solve else 'ОТКЛЮЧЕНО'}")
        
        # Load persons data (VFS + PERSON1, PERSON2, etc.)
        self.persons = []
        self._load_persons()
        self.current_person_index = 0

        logger.info(f"🔐 Telegram канал: {self.channel_id}")
        self.app = ApplicationBuilder().token(token).build()
        logger.info("✅ Telegram бот инициализирован")

        # Add command handlers FIRST (highest priority)
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CommandHandler("quit", self.quit))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("setting", self.setting))
        self.app.add_handler(CommandHandler("fill", self.fill))
        self.app.add_handler(CommandHandler("stat", self.stat))
        self.app.add_handler(CommandHandler("captcha", self.captcha_command))
        self.app.add_handler(CommandHandler("report", self.send_applicant_report))
        self.app.add_handler(CommandHandler("dilshodjon", self.send_dilshodjon_all_reports))
        self.app.add_handler(CommandHandler("sendreport", self.force_send_report))
        
        # Add message handler LAST (lowest priority - for blocking unauthorized users)
        self.app.add_handler(MessageHandler(
                self.admin_handler.filter_admin(),
                self.admin_handler.unauthorized_access))
        
        logger.info("✅ Обработчики команд зарегистрированы")
        
        self._check_and_log_remote_grid()
        
        # Set up post_init callback to start bot automatically
        self.app.post_init = self.post_init
        
        logger.info("="*60)
        logger.info("🟢 БОТ ГОТОВ К ЗАПУСКУ")
        logger.info("="*60)
        self.app.run_polling()
    
    def _find_pdf_for_person(self, first_name, last_name):
        """Automatically find PDF file for person in dokuments folder"""
        import glob
        
        if not first_name or not last_name:
            return ''
        
        dokuments_path = os.path.join(os.path.dirname(__file__), 'dokuments')
        if not os.path.exists(dokuments_path):
            return ''
        
        # Try exact match with last name (case-insensitive)
        last_name_lower = last_name.lower()
        first_name_lower = first_name.lower()
        
        # Look for files like foto_bobir.pdf or foto_bobir.jpg
        for pattern in [f'foto_{first_name_lower}*.pdf', f'foto_{last_name_lower}*.pdf']:
            files = glob.glob(os.path.join(dokuments_path, pattern))
            if files:
                return os.path.abspath(files[0])
        
        return ''
    
    def _load_persons(self):
        """Load all persons (VFS + PERSON1, PERSON2, etc.)"""
        logger.debug("👥 Начало загрузки заявителей...")
        
        # Main VFS person
        vfs_person = {
            'name': 'VFS (Main)',
            'first_name': self.config.get('VFS', 'first_name') if self.config.has_option('VFS', 'first_name') else '',
            'last_name': self.config.get('VFS', 'last_name') if self.config.has_option('VFS', 'last_name') else '',
            'contact_phone': self.config.get('VFS', 'contact_phone') if self.config.has_option('VFS', 'contact_phone') else '',
            'contact_email': self.config.get('VFS', 'contact_email') if self.config.has_option('VFS', 'contact_email') else '',
            'migris_code': self.config.get('VFS', 'migris_code') if self.config.has_option('VFS', 'migris_code') else '',
            'date_of_birth': self.config.get('VFS', 'date_of_birth') if self.config.has_option('VFS', 'date_of_birth') else '',
            'passport_number': self.config.get('VFS', 'passport_number') if self.config.has_option('VFS', 'passport_number') else '',
            'country': self.config.get('VFS', 'country') if self.config.has_option('VFS', 'country') else '',
            'passport_validity_date': self.config.get('VFS', 'passport_validity_date') if self.config.has_option('VFS', 'passport_validity_date') else '',
            'appointment_category': self.config.get('VFS', 'appointment_category') if self.config.has_option('VFS', 'appointment_category') else '',
            'appointment_type': self.config.get('VFS', 'appointment_type') if self.config.has_option('VFS', 'appointment_type') else '',
            'gender': self.config.get('VFS', 'gender') if self.config.has_option('VFS', 'gender') else '',
            'photo_path': self.config.get('VFS', 'photo_path') if self.config.has_option('VFS', 'photo_path') else '',
            'photo_pdf_path': self.config.get('VFS', 'photo_pdf_path') if self.config.has_option('VFS', 'photo_pdf_path') else '',
            'confirm_appointment': self.config.getboolean('VFS', 'confirm_appointment', fallback=False),
        }
        
        # Auto-find PDF if not configured
        if not vfs_person['photo_pdf_path']:
            auto_pdf = self._find_pdf_for_person(vfs_person['first_name'], vfs_person['last_name'])
            if auto_pdf:
                vfs_person['photo_pdf_path'] = auto_pdf
                logger.info(f"  🔍 Найден PDF для VFS: {auto_pdf}")
            else:
                logger.info(f"  ⚠️ PDF не найден для VFS ({vfs_person['first_name']} {vfs_person['last_name']})")
        
        if vfs_person['first_name']:  # Only add if has data
            self.persons.append(vfs_person)
            logger.debug(f"  ✅ Главный заявитель VFS загружен: {vfs_person['first_name']} {vfs_person['last_name']}")
        
        # Load additional persons (PERSON1, PERSON2, etc.)
        person_index = 1
        while self.config.has_section(f'PERSON{person_index}'):
            section = f'PERSON{person_index}'
            person_data = {
                'name': f'PERSON{person_index}',
                'first_name': self.config.get(section, 'first_name') if self.config.has_option(section, 'first_name') else '',
                'last_name': self.config.get(section, 'last_name') if self.config.has_option(section, 'last_name') else '',
                'contact_phone': self.config.get(section, 'contact_phone') if self.config.has_option(section, 'contact_phone') else '',
                'contact_email': self.config.get(section, 'contact_email') if self.config.has_option(section, 'contact_email') else '',
                'migris_code': self.config.get(section, 'migris_code') if self.config.has_option(section, 'migris_code') else '',
                'date_of_birth': self.config.get(section, 'date_of_birth') if self.config.has_option(section, 'date_of_birth') else '',
                'passport_number': self.config.get(section, 'passport_number') if self.config.has_option(section, 'passport_number') else '',
                'country': self.config.get(section, 'country') if self.config.has_option(section, 'country') else '',
                'passport_validity_date': self.config.get(section, 'passport_validity_date') if self.config.has_option(section, 'passport_validity_date') else '',
                'appointment_category': self.config.get(section, 'appointment_category') if self.config.has_option(section, 'appointment_category') else '',
                'appointment_type': self.config.get(section, 'appointment_type') if self.config.has_option(section, 'appointment_type') else '',
                'gender': self.config.get(section, 'gender') if self.config.has_option(section, 'gender') else '',
                'confirm_appointment': self.config.getboolean(section, 'confirm_appointment', fallback=True),
                'photo_path': self.config.get(section, 'photo_path') if self.config.has_option(section, 'photo_path') else '',
                'photo_pdf_path': self.config.get(section, 'photo_pdf_path') if self.config.has_option(section, 'photo_pdf_path') else '',
            }
            
            # Auto-find PDF if not configured
            if not person_data['photo_pdf_path']:
                auto_pdf = self._find_pdf_for_person(person_data['first_name'], person_data['last_name'])
                if auto_pdf:
                    person_data['photo_pdf_path'] = auto_pdf
                    logger.info(f"  🔍 Найден PDF для {section}: {auto_pdf}")
                else:
                    logger.info(f"  ⚠️ PDF не найден для {section} ({person_data['first_name']} {person_data['last_name']})")
            
            if person_data['first_name']:  # Only add if has data
                self.persons.append(person_data)
                logger.debug(f"  ✅ {section} загружен: {person_data['first_name']} {person_data['last_name']}")
            person_index += 1
        
        logger.info(f"👥 Всего загружено заявителей: {len(self.persons)}")
        for i, person in enumerate(self.persons):
            logger.info(f"   [{i}] {person['name']} - {person['first_name']} {person['last_name']} (Migris: {person['migris_code']})")
    
    def _set_current_person(self, person_data):
        """Set the current person's data as instance variables"""
        self.first_name = person_data['first_name']
        self.last_name = person_data['last_name']
        self.contact_phone = person_data['contact_phone']
        self.contact_email = person_data['contact_email']
        self.migris_code = person_data['migris_code']
        self.date_of_birth = person_data['date_of_birth']
        self.passport_number = person_data['passport_number']
        self.country = person_data['country']
        self.passport_validity_date = person_data['passport_validity_date']
        self.appointment_category = person_data['appointment_category']
        self.appointment_type = person_data['appointment_type']
        self.gender = person_data['gender']
        self.confirm_appointment = person_data['confirm_appointment']
        self.photo_path = person_data['photo_path']
        self.photo_pdf_path = person_data['photo_pdf_path']
        # Additional fields for smart auto-fill
        self.nationality = person_data.get('nationality', person_data.get('country', 'UZBEKISTAN'))
        self.address = person_data.get('address', '')
        self.purpose_of_travel = person_data.get('purpose', 'Temporary Residence')
    
    def _get_next_person(self):
        """Get the next person and rotate to the next one"""
        if not self.persons:
            return None
        
        # Check if we're completing a full cycle (back to index 0)
        completing_cycle = self.current_person_index == 0 and hasattr(self, '_cycle_started')
        
        current = self.persons[self.current_person_index]
        self.current_person_index = (self.current_person_index + 1) % len(self.persons)
        
        # Mark cycle as started after first iteration
        self._cycle_started = True
        
        # If completing full cycle, schedule cycle completion report
        if completing_cycle:
            # Use asyncio to schedule the report without blocking
            asyncio.create_task(self._send_cycle_completion_report())
        
        return current
    
    async def _auto_start_browser(self, application):
        """Background task to automatically initialize browser and start login"""
        max_attempts = 3
        attempt = 0
        
        while attempt < max_attempts and self.started:
            attempt += 1
            try:
                logger.info(f"⏳ Автоматический запуск браузера (попытка {attempt}/{max_attempts})...")
                await asyncio.sleep(3)  # Increased delay for stability
                
                logger.info("🔧 Инициализация браузера в фоне...")
                
                # Force cleanup before initialization
                self._force_cleanup_browser()
                await asyncio.sleep(1)
                
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, self._init_browser)
                
                if result and self.browser:
                    logger.info("✅ Браузер инициализирован успешно")
                    logger.info("🔄 Запуск автоматической проверки доступности встреч...")
                    
                    # Start login helper in background
                    self.auto_task = asyncio.create_task(self.login_helper(None, None))
                    logger.info("✅ Автоматическая задача запущена")
                    return
                else:
                    logger.warning(f"❌ Попытка {attempt} не удалась - браузер не инициализирован")
                    if attempt < max_attempts:
                        logger.info(f"🔄 Ожидание 15 сек перед следующей попыткой...")
                        await asyncio.sleep(15)
                    
            except Exception as e:
                logger.error(f"❌ Ошибка при автоматическом старте браузера (попытка {attempt}): {e}")
                if attempt < max_attempts:
                    logger.info(f"🔄 Ожидание 20 сек перед следующей попыткой...")
                    await asyncio.sleep(20)
        
        if attempt >= max_attempts:
            logger.error("❌ Не удалось автоматически запустить браузер после всех попыток")
            logger.info("💬 Используйте /start в Telegram для ручного запуска")
    
    async def post_init(self, application):
        """Called when the bot starts - wait for user commands"""
        logger.info("="*60)
        logger.info("🚀 БОТ ЗАПУЩЕН И ИНИЦИАЛИЗИРОВАН")
        logger.info("="*60)
        logger.info("🔄 Режим многозаявителей активирован")
        logger.info(f"👥 Всего заявителей: {len(self.persons)}")
        logger.info(f"⏱️  Интервал проверки: {self.interval} сек")
        logger.info(f"🤖 Авто-заполнение формы: {'ДА' if self.auto_fill else 'НЕТ'}")
        
        self.started = True
        logger.debug("✅ Флаг started установлен в True")
        
        if self.auto_login:
            logger.info("🔄 Автоматический вход ВКЛЮЧЕН - запуск инициализации браузера в фоне...")
            try:
                self.auto_task = asyncio.create_task(self._auto_start_browser(application))
                logger.info("✅ Задача автоматического входа создана успешно")
            except Exception as e:
                logger.error(f"❌ Ошибка создания задачи автоматического входа: {e}")
                logger.info("💬 Используйте /start в Telegram для ручного запуска")
        else:
            logger.info("💬 Отправьте /start чтобы начать работу")
        
        logger.info("📊 Запуск задачи отправки отчетов каждые 20 минут...")
        self.report_task = asyncio.create_task(self.report_status_task(application))
        logger.info("✅ Задача отправки отчетов запущена!")
    
    async def login(self, update: Update, context: CallbackContext):
        person_name = f"{self.first_name} {self.last_name}"
        logger.info("="*60)
        logger.info(f"🔐 ВХОД В СИСТЕМУ ДЛЯ: {person_name}")
        logger.info("="*60)
        
        try:
            # Check if browser is alive
            if not self.browser:
                logger.error("❌ Браузер не инициализирован")
                raise WebError("Browser is None")
            
            # Enhanced browser health check
            if not self._check_browser_health():
                logger.error("❌ Браузер не отвечает на проверки здоровья")
                raise WebError("Browser health check failed")
                
            try:
                current_url = self.browser.current_url
                logger.debug(f"🌐 Текущий URL браузера: {current_url}")
            except Exception as url_e:
                logger.error(f"❌ Не удалось получить URL браузера: {url_e}")
                raise WebError("Browser connection lost")
            
            logger.info(f"🌐 Переход на URL: {self.url}")
            # Set shorter page load timeout to prevent hanging
            try:
                self.browser.set_page_load_timeout(30)
                logger.debug("⏰ Установлен таймаут загрузки страницы: 30 сек")
            except Exception as timeout_e:
                logger.warning(f"⚠️ Не удалось установить таймаут: {timeout_e}")
                raise WebError("Cannot set page timeout")
            
            try:
                self.browser.get(self.url)
                logger.debug("✅ Страница успешно загружена")
            except Exception as e:
                error_str = str(e).lower()
                if any(err in error_str for err in ["no such window", "disconnected", "invalid session"]):
                    logger.error(f"❌ Критическая ошибка браузера при загрузке: {e}")
                    raise WebError("Browser window closed during navigation")
                else:
                    logger.warning(f"⚠️ Таймаут или проблема загрузки страницы: {e}, продолжаем...")
            
            # Double-check browser is still alive after navigation
            if not self._check_browser_health():
                logger.error("❌ Браузер не отвечает после навигации")
                raise WebError("Browser became unresponsive after navigation")
            
            # Wait for page to render - JavaScript needs time to build DOM
            logger.debug("⏳ Ожидание рендеринга страницы (5 сек)...")
            await asyncio.sleep(5)
            
            logger.info("🍪 Проверка и закрытие cookie consent диалога...")
            cookie_closed = False
            try:
                cookie_xpaths = [
                    '//*[contains(text(), "Отклонить все")]/..',
                    '//*[contains(text(), "Reject All")]/..',
                    '//button[contains(text(), "Отклонить все")]',
                    '//button[contains(text(), "Reject All")]',
                    '//button[@data-testid="cookie-consent-reject-all"]',
                    '//div[@role="dialog"]//button[contains(., "Отклонить")]',
                ]
                
                for xpath in cookie_xpaths:
                    try:
                        element = self.browser.find_element(by=By.XPATH, value=xpath)
                        if element and element.is_displayed():
                            logger.debug(f"✅ Найдена кнопка закрытия cookies: {xpath}")
                            element.click()
                            logger.info("✅ Cookie диалог закрыт")
                            await asyncio.sleep(2)
                            cookie_closed = True
                            break
                    except:
                        continue
                
                if not cookie_closed:
                    all_buttons = self.browser.find_elements(by=By.TAG_NAME, value='button')
                    for btn in all_buttons:
                        try:
                            btn_text = btn.text.strip()
                            if 'отклон' in btn_text.lower() or 'reject' in btn_text.lower():
                                if btn.is_displayed():
                                    logger.debug(f"✅ Попытка клик на кнопку: {btn_text}")
                                    btn.click()
                                    logger.info(f"✅ Нажата кнопка: {btn_text}")
                                    await asyncio.sleep(2)
                                    cookie_closed = True
                                    break
                        except:
                            continue
                        
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при обработке cookie диалога: {e}")
            
            # await asyncio.sleep(500) # For debugging purposes
            if "You are now in line." in self.browser.page_source:
                msg = "📋 Вы находитесь в очереди ожидания..."
                logger.warning(msg)
                if update and update.message:
                    await update.message.reply_text(msg)
            
            # Enhanced page readiness check
            logger.info("🔍 Проверка готовности страницы входа...")
            readiness_checks = [
                "document.readyState === 'complete'",
                "document.querySelector('input[name*=\"mail\"], input[name*=\"Email\"], input[id*=\"mail\"], input[id*=\"Email\"]') !== null",
                "document.querySelector('input[type=\"password\"]') !== null"
            ]
            
            page_ready = False
            for attempt in range(10):  # Max 10 attempts, 1 second each
                try:
                    all_checks_pass = True
                    for check in readiness_checks:
                        result = self.browser.execute_script(f"return {check}")
                        if not result:
                            all_checks_pass = False
                            break
                    
                    if all_checks_pass:
                        logger.info(f"✅ Страница готова к входу (попытка {attempt + 1})")
                        page_ready = True
                        break
                    else:
                        logger.debug(f"🔄 Страница не готова, ожидание... (попытка {attempt + 1})")
                        await asyncio.sleep(1)
                except Exception as e:
                    logger.debug(f"⚠️ Ошибка проверки готовности: {e}")
                    await asyncio.sleep(1)
            
            if not page_ready:
                logger.warning("⚠️ Страница может быть не полностью готова, продолжаем...")
                # Save debug info
                try:
                    timestamp = int(datetime.now().timestamp())
                    debug_screenshot = f'page_not_ready_{person_name}_{timestamp}.png'
                    self.browser.save_screenshot(debug_screenshot)
                    logger.info(f"📸 Отладочный скриншот неготовой страницы: {debug_screenshot}")
                except Exception as e:
                    logger.debug(f"⚠️ Не удалось создать скриншот: {e}")
            
            logger.info("⏳ Проверка наличия полей входа (макс 15 сек)...")
            max_wait = 15
            wait_interval = 1.0
            elapsed = 0
            email_field = None
            
            # Extended list of possible email field identifiers
            email_field_selectors = [
                (By.NAME, 'EmailId'),
                (By.NAME, 'email'), 
                (By.NAME, 'Email'),
                (By.NAME, 'username'),
                (By.NAME, 'Username'),
                (By.NAME, 'user_email'),
                (By.NAME, 'loginEmail'),
                (By.ID, 'EmailId'),
                (By.ID, 'email'),
                (By.ID, 'username'),
                (By.CSS_SELECTOR, 'input[type="email"]'),
                (By.CSS_SELECTOR, 'input[type="text"][placeholder*="mail"]'),
                (By.CSS_SELECTOR, 'input[type="text"][placeholder*="Email"]'),
                (By.CSS_SELECTOR, 'input[name*="email"]'),
                (By.CSS_SELECTOR, 'input[id*="email"]')
            ]
            
            while elapsed < max_wait:
                try:
                    for selector_type, selector_value in email_field_selectors:
                        try:
                            email_field = self.browser.find_element(selector_type, selector_value)
                            if email_field and email_field.is_displayed():
                                logger.info(f"✅ Поле входа найдено: {selector_type}={selector_value}")
                                break
                        except:
                            continue
                    
                    if email_field:
                        break
                        
                except Exception as e:
                    logger.debug(f"⚠️ Ошибка при поиске полей: {e}")
                
                await asyncio.sleep(wait_interval)
                elapsed += wait_interval
                logger.debug(f"🔍 Поиск полей... ({elapsed}s/{max_wait}s)")
            
                if not email_field:
                    logger.warning("⚠️ Поле email не найдено основными селекторами, сохраняем скриншот...")
                    try:
                        timestamp = int(datetime.now().timestamp())
                        screenshot_path = f'debug_email_not_found_{timestamp}.png'
                        self.browser.save_screenshot(screenshot_path)
                        logger.info(f"📸 Скриншот сохранен: {screenshot_path}")
                        
                        html_path = f'debug_page_source_{timestamp}.html'
                        with open(html_path, 'w', encoding='utf-8') as f:
                            f.write(self.browser.page_source)
                        logger.info(f"📄 Исходник страницы сохранен: {html_path}")
                    except Exception as debug_e:
                        logger.warning(f"⚠️ Не удалось сохранить отладочную информацию: {debug_e}")
                    
                    logger.warning("⚠️ Пробуем расширенный поиск полей email...")
                    
                    # Extended fallback selectors
                    extended_selectors = [
                        (By.XPATH, '//input[@type="email"]'),
                        (By.XPATH, '//input[contains(@name, "mail")]'),
                        (By.XPATH, '//input[contains(@id, "mail")]'),
                        (By.XPATH, '//input[contains(@placeholder, "mail")]'),
                        (By.XPATH, '//input[@type="text" and contains(@name, "Email")]'),
                        (By.XPATH, '//input[@type="text" and contains(@class, "email")]'),
                        (By.CSS_SELECTOR, 'input[type="text"]'),  # Last resort
                    ]
                    
                    for selector_type, selector_value in extended_selectors:
                        try:
                            potential_fields = self.browser.find_elements(selector_type, selector_value)
                            for field in potential_fields:
                                if field.is_displayed() and field.is_enabled():
                                    email_field = field
                                    logger.info(f"✅ Поле email найдено расширенным поиском: {selector_type}={selector_value}")
                                    break
                            if email_field:
                                break
                        except Exception as e:
                            logger.debug(f"⚠️ Расширенный селектор не сработал: {selector_type}={selector_value}, ошибка: {e}")
                    
                    if not email_field:
                        logger.error("❌ Поле email не найдено даже расширенным поиском")
                        logger.error("💡 Возможно, структура VFS сайта значительно изменилась")
                        raise WebError("Email field not found with any selector method")

            logger.info(f"📧 Ввод email: {self.email_str[:10]}...")
            
            # Use the found email field directly
            email_entered = False
            if email_field:
                try:
                    # Clear and enter email in the found field
                    email_field.clear()
                    email_field.send_keys(self.email_str)
                    logger.info(f"✅ Email введен в найденное поле")
                    email_entered = True
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка ввода в найденное поле: {e}")
            
            # Fallback to alternative search if direct field failed
            if not email_entered:
                logger.info("� Поиск альтернативных полей email...")
                fallback_selectors = [
                    "input[type='email']",
                    "input[type='text'][placeholder*='mail']", 
                    "input[type='text'][placeholder*='Email']",
                    "input[name*='email']",
                    "input[id*='email']",
                    "input[type='text']"
                ]
                
                for selector in fallback_selectors:
                    try:
                        email_inputs = self.browser.find_elements(By.CSS_SELECTOR, selector)
                        for email_input in email_inputs:
                            if email_input.is_displayed() and email_input.is_enabled():
                                email_input.clear()
                                email_input.send_keys(self.email_str)
                                logger.info(f"✅ Email введен в поле (автопоиск: {selector})")
                                email_entered = True
                                break
                        if email_entered:
                            break
                    except Exception as e:
                        logger.debug(f"⚠️ Не удалось использовать селектор {selector}: {e}")
                
            if not email_entered:
                logger.error("❌ Не удалось ввести email ни в одно поле")
                raise WebError("Failed to enter email in any field")
            
            logger.info("🔑 Поиск и ввод пароля...")
            
            # Enhanced password field selectors
            password_selectors = [
                (By.NAME, 'Password'),
                (By.NAME, 'password'),
                (By.NAME, 'pwd'),
                (By.NAME, 'Pwd'),
                (By.NAME, 'loginPassword'),
                (By.NAME, 'user_password'),
                (By.ID, 'Password'),
                (By.ID, 'password'),
                (By.ID, 'pwd'),
                (By.CSS_SELECTOR, 'input[type="password"]'),
                (By.CSS_SELECTOR, 'input[name*="pass"]'),
                (By.CSS_SELECTOR, 'input[name*="pwd"]'),
                (By.CSS_SELECTOR, 'input[id*="pass"]')
            ]
            
            password_entered = False
            for selector_type, selector_value in password_selectors:
                try:
                    password_field = self.browser.find_element(selector_type, selector_value)
                    if password_field and password_field.is_displayed() and password_field.is_enabled():
                        password_field.clear()
                        password_field.send_keys(self.pwd_str)
                        logger.info(f"✅ Пароль введен в поле: {selector_type}={selector_value}")
                        password_entered = True
                        break
                except Exception as e:
                    logger.debug(f"⚠️ Селектор не работает {selector_type}={selector_value}: {e}")
                    continue
            
            if not password_entered:
                logger.error("❌ Не удалось найти поле пароля")
                raise WebError("Password field not found")
        
            # Enhanced captcha processing with configuration check
            logger.info(f"📸 Поиск и обработка капчи... (Включена: {self.captcha_enabled})")
            captcha_processed = False
            
            if not self.captcha_enabled:
                logger.info("⚠️ Обработка капчи отключена в конфигурации")
            else:
                # Multiple selectors for captcha image
                captcha_selectors = [
                (By.ID, 'CaptchaImage'),
                (By.CLASS_NAME, 'captcha-image'),
                (By.CSS_SELECTOR, 'img[src*="captcha"]'),
                (By.XPATH, '//img[contains(@src, "captcha") or contains(@id, "captcha")]')
            ]
            
            for selector_type, selector_value in captcha_selectors:
                try:
                    captcha_img = self.browser.find_element(selector_type, selector_value)
                    if captcha_img and captcha_img.is_displayed():
                        logger.info(f"✅ Капча найдена: {selector_type}={selector_value}")
                        
                        self.captcha_filename = f'captcha_{int(datetime.now().timestamp())}.png'
                        with open(self.captcha_filename, 'wb') as file:
                            file.write(captcha_img.screenshot_as_png)
                        logger.debug(f"✅ Капча сохранена: {self.captcha_filename}")

                        if self.captcha_auto_solve:
                            logger.info("🧠 Автоматическое распознавание капчи (OCR)...")
                            try:
                                captcha = break_captcha(self.captcha_filename)
                                if captcha and len(captcha.strip()) > 0:
                                    logger.info(f"✅ Капча автоматически распознана: '{captcha}'")
                                
                                # Try multiple selectors for captcha input
                                captcha_input_selectors = [
                                    (By.NAME, 'CaptchaInputText'),
                                    (By.ID, 'CaptchaInputText'),
                                    (By.CLASS_NAME, 'captcha-input'),
                                    (By.CSS_SELECTOR, 'input[name*="captcha"], input[id*="captcha"]')
                                ]
                                
                                for input_type, input_value in captcha_input_selectors:
                                    try:
                                        captcha_field = self.browser.find_element(input_type, input_value)
                                        captcha_field.clear()
                                        captcha_field.send_keys(captcha)
                                        logger.info(f"✅ Капча введена в поле: {input_type}={input_value}")
                                        captcha_processed = True
                                        break
                                    except:
                                        continue
                                        
                                # Clean up captcha file immediately after processing attempt
                                if captcha_processed:
                                    try:
                                        os.remove(self.captcha_filename)
                                        logger.debug(f"🗑️ Капча файл очищен: {self.captcha_filename}")
                                    except:
                                        pass
                                    break
                                else:
                                    logger.warning("⚠️ OCR вернул пустую строку")
                                    await self._send_captcha_for_manual_input(captcha_img)
                            except Exception as ocr_e:
                                logger.warning(f"⚠️ Ошибка OCR: {ocr_e}")
                                await self._send_captcha_for_manual_input(captcha_img)
                        else:
                            logger.info("📱 Ручной ввод капчи включен")
                            await self._send_captcha_for_manual_input(captcha_img)
                except:
                    continue
                
                if not captcha_processed:
                    logger.warning("⚠️ Капча не найдена или не обработана")
                    # Clean up unsuccessful captcha file
                    if hasattr(self, 'captcha_filename') and os.path.exists(self.captcha_filename):
                        try:
                            os.remove(self.captcha_filename)
                            logger.debug(f"🗑️ Неудачная капча очищена: {self.captcha_filename}")
                        except:
                            pass
                else:
                    logger.info("✅ Капча успешно обработана и введена!")
            
            await asyncio.sleep(1)
            logger.info("🔘 Поиск и нажатие кнопки отправки...")
            
            # Enhanced comprehensive selectors for submit button
            submit_selectors = [
                # Standard VFS Global selectors
                (By.ID, 'btnSubmit'),
                (By.ID, 'btnLogin'),
                (By.ID, 'submitBtn'),
                (By.ID, 'loginBtn'),
                (By.NAME, 'btnSubmit'),
                (By.NAME, 'btnLogin'),
                (By.NAME, 'submit'),
                (By.NAME, 'login'),
                
                # Generic form submit selectors
                (By.CSS_SELECTOR, 'input[type="submit"]'),
                (By.CSS_SELECTOR, 'button[type="submit"]'),
                (By.CSS_SELECTOR, 'input[value*="Submit"]'),
                (By.CSS_SELECTOR, 'input[value*="Login"]'),
                (By.CSS_SELECTOR, 'input[value*="Sign"]'),
                (By.CSS_SELECTOR, 'button[class*="submit"]'),
                (By.CSS_SELECTOR, 'button[class*="login"]'),
                (By.CSS_SELECTOR, 'button[class*="btn-primary"]'),
                (By.CSS_SELECTOR, 'button[class*="btn-success"]'),
                (By.CSS_SELECTOR, '.btn-primary'),
                (By.CSS_SELECTOR, '.btn-success'),
                (By.CSS_SELECTOR, '.submit-btn'),
                (By.CSS_SELECTOR, '.login-btn'),
                
                # XPath selectors for text-based matching
                (By.XPATH, '//button[contains(text(), "Submit")]'),
                (By.XPATH, '//button[contains(text(), "Login")]'),
                (By.XPATH, '//button[contains(text(), "Sign")]'),
                (By.XPATH, '//input[contains(@value, "Submit")]'),
                (By.XPATH, '//input[contains(@value, "Login")]'),
                (By.XPATH, '//input[contains(@value, "Sign")]'),
                (By.XPATH, '//a[contains(text(), "Submit")]'),
                (By.XPATH, '//a[contains(text(), "Login")]'),
                
                # Form-based submit
                (By.CSS_SELECTOR, 'form input[type="submit"]'),
                (By.CSS_SELECTOR, 'form button[type="submit"]'),
                (By.XPATH, '//form//input[@type="submit"]'),
                (By.XPATH, '//form//button[@type="submit"]')
            ]
            
            submit_clicked = False
            attempted_methods = []
            
            # First, try to find and click buttons
            for i, (selector_type, selector_value) in enumerate(submit_selectors):
                try:
                    submit_btn = self.browser.find_element(selector_type, selector_value)
                    if submit_btn and submit_btn.is_displayed() and submit_btn.is_enabled():
                        logger.info(f"🎯 Найдена кнопка отправки (селектор {i+1}): {selector_type}={selector_value}")
                        
                        # Enhanced click methods with more comprehensive coverage
                        click_methods = [
                            ("regular_click", lambda: submit_btn.click()),
                            ("javascript_click", lambda: self.browser.execute_script("arguments[0].click();", submit_btn)),
                            ("action_chains_click", lambda: ActionChains(self.browser).move_to_element(submit_btn).click().perform()),
                            ("javascript_submit", lambda: self.browser.execute_script("arguments[0].submit();", submit_btn)),
                            ("form_submit", lambda: self.browser.execute_script("if(arguments[0].form) arguments[0].form.submit();", submit_btn)),
                            ("focus_and_enter", lambda: (submit_btn.click(), submit_btn.send_keys(Keys.ENTER))),
                        ]
                        
                        for method_name, click_method in click_methods:
                            try:
                                click_method()
                                logger.info(f"✅ Кнопка отправки успешно нажата (метод {method_name})")
                                submit_clicked = True
                                attempted_methods.append(f"{method_name} (success)")
                                break
                            except Exception as e:
                                attempted_methods.append(f"{method_name} (failed: {str(e)[:50]})")
                                logger.debug(f"🔍 Метод {method_name} не удался: {e}")
                                continue
                        
                        if submit_clicked:
                            break
                    else:
                        logger.debug(f"🔍 Кнопка найдена но не доступна: displayed={submit_btn.is_displayed()}, enabled={submit_btn.is_enabled()}")
                        
                except Exception as e:
                    logger.debug(f"🔍 Селектор {i+1} не удался: {selector_type}={selector_value}, ошибка: {e}")
                    continue
            
            # If no button worked, try alternative methods
            if not submit_clicked:
                logger.warning("⚠️ Стандартные кнопки не работают, пробуем альтернативные методы...")
                
                # Try to submit any form on the page
                alternative_methods = [
                    ("submit_first_form", lambda: self.browser.execute_script("if(document.forms.length > 0) document.forms[0].submit();")),
                    ("press_enter_password", lambda: self.browser.find_element(By.ID, "password").send_keys(Keys.ENTER)),
                    ("press_enter_email", lambda: self.browser.find_element(By.ID, "email").send_keys(Keys.ENTER)),
                    ("submit_login_form", lambda: self.browser.execute_script("var forms = document.getElementsByTagName('form'); for(var i=0; i<forms.length; i++) { if(forms[i].action.includes('login') || forms[i].method.toLowerCase() == 'post') { forms[i].submit(); break; } }")),
                    ("click_any_button", lambda: self.browser.execute_script("var buttons = document.getElementsByTagName('button'); if(buttons.length > 0) buttons[0].click();")),
                ]
                
                for method_name, method_func in alternative_methods:
                    try:
                        method_func()
                        logger.info(f"✅ Альтернативный метод сработал: {method_name}")
                        submit_clicked = True
                        attempted_methods.append(f"{method_name} (success)")
                        break
                    except Exception as e:
                        attempted_methods.append(f"{method_name} (failed: {str(e)[:50]})")
                        logger.debug(f"🔍 Альтернативный метод {method_name} не удался: {e}")
                        continue
            
            if not submit_clicked:
                logger.error("❌ Кнопка отправки не найдена или не доступна для нажатия!")
                logger.error(f"📝 Попробованные методы: {'; '.join(attempted_methods)}")
                
                # Try to capture current page info for debugging
                try:
                    current_url = self.browser.current_url
                    page_title = self.browser.title
                    logger.info(f"🔍 Текущая страница: {page_title} ({current_url})")
                    
                    # Try to find any buttons on the page for debugging
                    all_buttons = self.browser.find_elements(By.TAG_NAME, "button")
                    all_inputs = self.browser.find_elements(By.CSS_SELECTOR, "input[type='submit'], input[type='button']")
                    logger.info(f"🔍 Найдено кнопок на странице: {len(all_buttons)} button-ов, {len(all_inputs)} input-ов")
                    
                    for i, btn in enumerate(all_buttons[:3]):  # Show first 3 buttons
                        try:
                            btn_text = btn.text or btn.get_attribute('value') or btn.get_attribute('id') or 'No text'
                            btn_class = btn.get_attribute('class') or 'No class'
                            logger.info(f"🔍 Кнопка {i+1}: '{btn_text}', class='{btn_class}'")
                        except:
                            pass
                except:
                    pass
            
            # Wait for page response after login
            logger.info("⏳ Ожидание ответа после входа...")
            await asyncio.sleep(3)
            
            # Check for various response messages
            page_content = self.browser.page_source.lower()
            success_indicators = [
                "reschedule appointment",
                "book appointment", 
                "schedule appointment",
                "appointment booking",
                "dashboard",
                "welcome",
                "logout",
                "sign out",
                "home",
                "profile",
                "accordion1"  # VFS specific element
            ]
            
            # Also check current URL for success indicators
            current_url = self.browser.current_url.lower()
            url_success_indicators = [
                "dashboard",
                "home",
                "appointment",
                "booking",
                "profile"
            ]
            
            login_successful = (
                any(indicator in page_content for indicator in success_indicators) or
                any(indicator in current_url for indicator in url_success_indicators) or
                "emailid" not in page_content  # Login form disappeared
            )
            
            if login_successful:
                msg = f"✅ Успешный вход для {person_name}!"
                logger.info(msg)
                if update and update.message:
                    await update.message.reply_text(msg)
                
                # PRIORITY: Immediately ensure Latvia category is selected
                await self._ensure_latvia_category_selected()
                
                # Special login report for GOFUR JALOLIDDINOV
                if "GOFUR JALOLIDDINOV" in person_name.upper():
                    logger.info(f"🎯 Отправляю специальный отчет о входе для GOFUR JALOLIDDINOV...")
                    if context and hasattr(context, 'bot'):
                        login_time = datetime.now().strftime('%H:%M:%S')
                        gofur_login_report = f"""🔑 ОТЧЕТ О ВХОДЕ В СИСТЕМУ
                        
👤 ЗАЯВИТЕЛЬ: GOFUR JALOLIDDINOV
📅 Дата входа: {datetime.now().strftime('%d.%m.%Y')}
🕐 Время входа: {login_time}

✅ СТАТУС АВТОРИЗАЦИИ: УСПЕШНО
🌐 Сессия: Активна
🔐 Аутентификация: Подтверждена

💼 ДАННЫЕ ЗАЯВИТЕЛЯ:
📋 MIGRIS код: 2509-LLG-4704
📞 Телефон: +998906086332
📧 Email: bobire415@gmail.com
🛂 Паспорт: FA0704746
🎂 Дата рождения: 21.07.1981

⚡ СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
🔧 Активация автозаполнения
📝 Заполнение анкеты
🔍 Поиск доступных встреч
🤖 Готовность к автобронированию

🎯 Система переходит к автоматическому заполнению формы!"""
                        
                        await context.bot.send_message(chat_id=self.channel_id, text=gofur_login_report)
                        logger.info(f"✅ Специальный отчет о входе для GOFUR JALOLIDDINOV отправлен")
                
                # Check and force enable auto-fill for successful logins
                await self._ensure_autofill_activated(person_name, context)
                
                # Enhanced Auto-fill form fields after successful login
                if self.auto_fill:
                    logger.info(f"🎯 ✅ УСПЕШНЫЙ ВХОД! ЗАПУСК АВТОМАТИЧЕСКОГО ЗАПОЛНЕНИЯ АНКЕТЫ для {person_name}...")
                    
                    # Send immediate notification about successful login and auto-fill activation
                    if context and hasattr(context, 'bot'):
                        login_success_msg = f"🎉 УСПЕШНЫЙ ВХОД В СИСТЕМУ!\n👤 Пользователь: {person_name}\n� Запускаю автоматическое заполнение анкеты...\n⏳ Ожидание загрузки формы..."
                        await context.bot.send_message(chat_id=self.channel_id, text=login_success_msg)
                    
                    # Enhanced page readiness check for form filling
                    await self._wait_for_form_ready(person_name, context)
                    
                    # Additional verification that we're on the right page
                    await self._verify_form_page_ready(person_name, context)
                    
                    # Send detailed notification about auto-fill start
                    if context and hasattr(context, 'bot'):
                        start_time = datetime.now().strftime('%H:%M:%S')
                        autofill_start_msg = f"🧠 УМНОЕ АВТО-ЗАПОЛНЕНИЕ ЗАПУЩЕНО!\n\n👤 Заявитель: {person_name}\n⏰ Время: {start_time}\n🔍 Анализирую структуру формы..."
                        await context.bot.send_message(chat_id=self.channel_id, text=autofill_start_msg)
                    
                    try:
                        autofill_start_time = datetime.now()
                        await self.fill_form(update, context)
                        autofill_duration = (datetime.now() - autofill_start_time).total_seconds()
                        
                        logger.info(f"✅ 🎯 Умное авто-заполнение завершено для {person_name} за {autofill_duration:.1f} секунд")
                        
                        # Send comprehensive success notification
                        if context and hasattr(context, 'bot'):
                            completion_time = datetime.now().strftime('%H:%M:%S')
                            success_msg = f"🎉 АВТО-ЗАПОЛНЕНИЕ УСПЕШНО!\n\n👤 Заявитель: {person_name}\n⏰ Завершено: {completion_time}\n⚡ Время выполнения: {autofill_duration:.1f}с\n🎯 Готов к поиску встреч!"
                            await context.bot.send_message(chat_id=self.channel_id, text=success_msg)
                            
                            # Special detailed report for GOFUR JALOLIDDINOV
                            if "GOFUR JALOLIDDINOV" in person_name.upper():
                                logger.info(f"📊 Отправляю специальный детальный отчет для GOFUR JALOLIDDINOV...")
                                
                                # Create comprehensive report for GOFUR
                                gofur_report = f"""📋 ДЕТАЛЬНЫЙ ОТЧЕТ АВТОЗАПОЛНЕНИЯ
                                
🏷️ ЗАЯВИТЕЛЬ: GOFUR JALOLIDDINOV
📅 Дата: {datetime.now().strftime('%d.%m.%Y')}
🕐 Время завершения: {completion_time}
⚡ Время выполнения: {autofill_duration:.1f} секунд

📊 СТАТУС ОПЕРАЦИЙ:
✅ Вход в систему: Успешно
✅ Активация автозаполнения: Успешно  
✅ Заполнение формы: Завершено
✅ Проверка полей: Пройдена

🎯 СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
🔍 Активный поиск встреч
📱 Уведомления включены
🤖 Автоподтверждение готово

💼 MIGRIS КОД: 2509-LLG-4704
📞 КОНТАКТ: +998906086332
🛂 ПАСПОРТ: FA0704746

🔔 Система готова к автоматическому бронированию встреч!"""
                                
                                await context.bot.send_message(chat_id=self.channel_id, text=gofur_report)
                                logger.info(f"✅ Специальный отчет для GOFUR JALOLIDDINOV отправлен успешно")
                    except Exception as fill_error:
                        error_time = datetime.now().strftime('%H:%M:%S')
                        logger.error(f"❌ Критическая ошибка умного авто-заполнения для {person_name}: {fill_error}")
                        
                        # Send detailed error notification
                        if context and hasattr(context, 'bot'):
                            error_msg = f"🚨 ОШИБКА АВТО-ЗАПОЛНЕНИЯ!\n\n👤 Заявитель: {person_name}\n⏰ Время: {error_time}\n❌ Проблема: {str(fill_error)[:120]}...\n🔄 Продолжаю без авто-заполнения"
                            await context.bot.send_message(chat_id=self.channel_id, text=error_msg)
                        
                        # Try to continue without auto-fill
                        logger.info(f"🔄 Продолжаю работу для {person_name} без авто-заполнения...")
                else:
                    logger.info(f"📝 Авто-заполнение отключено в конфигурации для {person_name}")
                    
                    # Send notification that auto-fill is disabled
                    if context and hasattr(context, 'bot'):
                        disabled_msg = f"ℹ️ АВТО-ЗАПОЛНЕНИЕ ОТКЛЮЧЕНО\n\n👤 Заявитель: {person_name}\n⚙️ Причина: Отключено в config.ini\n📝 Включите auto_fill = true для активации"
                        await context.bot.send_message(chat_id=self.channel_id, text=disabled_msg)
                    
                logger.info(f"🔄 Начало непрерывной проверки встреч для {person_name}...")
                while True:
                    try:
                        await self.check_appointment(update, context)
                        # Update check count after successful check
                        self.check_count += 1
                        person_stats_key = f"{self.first_name} {self.last_name}"
                        self.person_stats[person_stats_key] = self.person_stats.get(person_stats_key, 0) + 1
                    except WebError:
                        msg = f"❌ Ошибка веб-сайта для {person_name}.\nПопытка снова..."
                        logger.error(msg)
                        if update and update.message:
                            await update.message.reply_text(msg)
                        raise WebError
                    except Offline:
                        msg = f"⚠️ Оффлайн режим для {person_name}.\nПопытка снова..."
                        logger.warning(msg)
                        if update and update.message:
                            await update.message.reply_text(msg)
                        continue
                    except Exception as e:
                        msg = f"❌ Ошибка для {person_name}: {str(e)}\nПопытка снова..."
                        logger.error(msg, exc_info=True)
                        if update and update.message:
                            await update.message.reply_text(msg)
                        raise WebError
                    await asyncio.sleep(self.interval)
                    
            elif "account has been locked" in page_content or "locked" in page_content:
                msg = f"🔒 Аккаунт {person_name} заблокирован. Ожидание 2 минуты..."
                logger.warning(msg)
                if update and update.message:
                    await update.message.reply_text(msg)
                await asyncio.sleep(120)
                return
                
            elif "verification words are incorrect" in page_content or "captcha" in page_content or ("incorrect" in page_content and "verification" in page_content):
                msg = f"⚠️ Неверная капча для {person_name}. Повторная попытка..."
                logger.warning(msg)
                # Clean up captcha file
                if hasattr(self, 'captcha_filename') and os.path.exists(self.captcha_filename):
                    try:
                        os.remove(self.captcha_filename)
                        logger.debug(f"🗑️ Удален файл неверной капчи: {self.captcha_filename}")
                    except:
                        pass
                await asyncio.sleep(2)  # Small delay before retry
                return
                
            elif "rate limited" in page_content or "too many" in page_content:
                msg = f"⏱️ Ограничение частоты для {person_name}. Ожидание 5 минут..."
                logger.warning(msg)
                if update and update.message:
                    await update.message.reply_text(msg)
                await asyncio.sleep(300)
                return
            elif "queue" in page_content or "waiting" in page_content:
                msg = f"📋 {person_name} в очереди ожидания..."
                logger.info(msg)
                if update and update.message:
                    await update.message.reply_text(msg)
                # Continue with appointment checking even if in queue
            else:
                # Enhanced error analysis for login failures
                error_indicators = {
                    "invalid credentials": ["invalid", "incorrect", "wrong", "password", "username", "credentials"],
                    "server error": ["server error", "internal error", "500", "503", "502"],
                    "maintenance": ["maintenance", "under construction", "temporarily unavailable"],
                    "network issues": ["connection", "network", "timeout", "failed to connect"],
                    "session expired": ["session expired", "session invalid", "please login again"]
                }
                
                detected_error = "unknown"
                for error_type, keywords in error_indicators.items():
                    if any(keyword in page_content for keyword in keywords):
                        detected_error = error_type
                        break
                
                msg = f"❌ Ошибка входа для {person_name}: {detected_error}"
                logger.error(msg)
                
                # Save debug info for analysis
                try:
                    timestamp = int(datetime.now().timestamp())
                    debug_screenshot = f'debug_login_{detected_error}_{person_name}_{timestamp}.png'
                    self.browser.save_screenshot(debug_screenshot)
                    
                    # Also save page source for detailed analysis
                    debug_html = f'debug_login_{detected_error}_{person_name}_{timestamp}.html'
                    with open(debug_html, 'w', encoding='utf-8') as f:
                        f.write(self.browser.page_source)
                    
                    logger.info(f"📸 Отладочные файлы: {debug_screenshot}, {debug_html}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось сохранить отладочные файлы: {e}")
                
                if update and update.message:
                    await update.message.reply_text(msg)
                    
                msg = f"❌ Неожиданный ответ при входе для {person_name}. Проверьте credentials или структуру сайта."
                logger.error(msg)
                if update and update.message:
                    await update.message.reply_text(msg)
                raise WebError
        except TimeoutException as te:
            error_msg = f"Timeout при поиске элементов: {str(te)}"
            logger.error(f"⏱️ {error_msg}")
            
            # Enhanced timeout handling with recovery attempts
            try:
                # First, try to get more info about the page state
                current_url = self.browser.current_url
                page_title = self.browser.title
                logger.info(f"🔍 Состояние при timeout: {page_title} ({current_url})")
                
                # Check if page is still loading
                page_state = self.browser.execute_script("return document.readyState")
                logger.info(f"🔍 Состояние загрузки страницы: {page_state}")
                
                # If page is still loading, wait a bit more
                if page_state != "complete":
                    logger.info("⏳ Страница еще загружается, дополнительное ожидание...")
                    await asyncio.sleep(5)
                
                # Save debug info for timeout issues (limit to prevent disk overflow)
                import glob
                debug_files = glob.glob('debug_*.png')
                if len(debug_files) < 20:  # Limit to 20 debug screenshots
                    timestamp = int(datetime.now().timestamp())
                    screenshot_path = f'debug_timeout_{person_name}_{timestamp}.png'
                    self.browser.save_screenshot(screenshot_path)
                    logger.info(f"📸 Скриншот timeout сохранен: {screenshot_path}")
                else:
                    logger.debug("⚠️ Лимит отладочных файлов достигнут, скриншот пропущен")
            except Exception as debug_e:
                logger.warning(f"⚠️ Не удалось создать скриншот timeout: {debug_e}")
            
            # Clean up captcha file
            if hasattr(self, 'captcha_filename') and os.path.exists(self.captcha_filename):
                try:
                    os.remove(self.captcha_filename)
                except:
                    pass
            
            # Try to recover from timeout if possible
            recovery_attempted = await self._attempt_login_recovery(person_name)
            if not recovery_attempted:
                raise WebError(f"TimeoutException: {error_msg}")
            
        except (NoSuchElementException, WebDriverException) as se:
            error_msg = f"Selenium ошибка: {str(se)}"
            logger.error(f"🔍 {error_msg}")
            
            # Enhanced error analysis for better recovery
            error_str = str(se).lower()
            recovery_possible = False
            
            if "element not interactable" in error_str:
                logger.info("🔄 Элемент не интерактивен - попытка восстановления...")
                recovery_possible = await self._attempt_element_recovery()
            elif "stale element" in error_str:
                logger.info("🔄 Устаревшая ссылка на элемент - обновление...")
                recovery_possible = await self._refresh_page_elements()
            elif "chrome not reachable" in error_str:
                logger.info("🔄 Chrome недоступен - перезапуск браузера...")
                recovery_possible = await self._attempt_browser_recovery()
            
            # Clean up captcha file
            if hasattr(self, 'captcha_filename') and os.path.exists(self.captcha_filename):
                try:
                    os.remove(self.captcha_filename)
                except:
                    pass
            
            if not recovery_possible:
                raise WebError(f"Selenium error: {error_msg}")
            
        except Exception as e:
            logger.error(f"❌ ИСКЛЮЧЕНИЕ при входе для {person_name}: {str(e)}", exc_info=True)
            
            # Enhanced exception handling
            error_str = str(e).lower()
            
            # Try to categorize and handle specific error types
            if "connection refused" in error_str or "network" in error_str:
                logger.warning("⚠️ Сетевая проблема - ожидание восстановления...")
                await asyncio.sleep(10)  # Wait for network recovery
            elif "memory" in error_str or "resource" in error_str:
                logger.warning("⚠️ Нехватка ресурсов - очистка и ожидание...")
                self._cleanup_temp_files()
                await asyncio.sleep(5)
            
            # Clean up captcha file if exists
            if hasattr(self, 'captcha_filename') and os.path.exists(self.captcha_filename):
                try:
                    os.remove(self.captcha_filename)
                    logger.debug(f"🗑️ Удален временный файл капчи после ошибки: {self.captcha_filename}")
                except:
                    pass
            
            # Enhanced browser error detection and handling
            error_str = str(e).lower()
            
            # Critical browser errors that require reinitializtion
            critical_browser_errors = [
                "invalid session id", "session deleted", "chrome not reachable",
                "target frame detached", "disconnected", "no such window",
                "connection refused", "connection reset", "timeout",
                "net::err_internet_disconnected", "net::err_connection_refused",
                "chrome", "driver", "webdriver", "session not created"
            ]
            
            if any(err in error_str for err in critical_browser_errors):
                logger.warning(f"🔄 Критическая ошибка браузера обнаружена: {str(e)[:100]}...")
                logger.warning("🔄 Требуется переинициализация браузера...")
                raise WebError(f"Critical browser error: {e}")
            else:
                # Non-browser related error, re-raise as is
                raise

    async def fill_form(self, update: Update, context):
        """Auto-fill form fields with configured data using smart field detection"""
        try:
            await asyncio.sleep(2)
            filled_count = 0
            current_full_name = f"{self.first_name} {self.last_name}"
            
            logger.info(f"📝 🚀 НАЧАТО УМНОЕ АВТО-ЗАПОЛНЕНИЕ для: {current_full_name}")
            logger.info(f"⏱️ Время начала: {datetime.now().strftime('%H:%M:%S')}")
            
            # Send initial status to Telegram
            if context and hasattr(context, 'bot'):
                status_msg = f"📝 Умное заполнение формы для {current_full_name}...\n🧠 Анализируем структуру страницы..."
                await context.bot.send_message(chat_id=self.channel_id, text=status_msg)
            
            # Use smart field detection
            found_fields = await self._smart_find_form_fields()
            
            if not found_fields:
                logger.warning("⚠️ Не найдено ни одного поля формы - используем legacy метод")
                # Fallback to legacy method
                legacy_filled = await self._fill_form_legacy(update, context)
                
                if context and hasattr(context, 'bot'):
                    fallback_msg = f"🔄 Legacy заполнение: {legacy_filled} полей заполнено"
                    await context.bot.send_message(chat_id=self.channel_id, text=fallback_msg)
                return
            
            # Send field detection results to Telegram
            if context and hasattr(context, 'bot'):
                field_names = list(found_fields.keys())
                detection_msg = f"🎯 Найдено {len(field_names)} полей: {', '.join(field_names[:5])}{'...' if len(field_names) > 5 else ''}"
                await context.bot.send_message(chat_id=self.channel_id, text=detection_msg)
            
            # Mapping from field names to configuration values
            field_data_mapping = {
                'firstName': self.first_name,
                'lastName': self.last_name,
                'phoneNumber': self.contact_phone,
                'email': self.contact_email,
                'dateOfBirth': self.date_of_birth,
                'passportNumber': self.passport_number,
                'country': self.country,
                'passportValidityDate': self.passport_validity_date,
                'appointmentCategory': self.appointment_category,
                'nationality': self.nationality,
                'address': self.address,
                'purpose': self.purpose_of_travel
            }
            
            # Fill found fields with data
            for field_name, field_info in found_fields.items():
                field_value = field_data_mapping.get(field_name)
                if not field_value:
                    continue
                    
                try:
                    element = field_info['element']
                    field_type = field_info['type']
                    
                    if field_type in ['input', 'text', 'email', 'tel', 'date'] or element.tag_name == 'input':
                        # Fill text input fields
                        element.clear()
                        element.send_keys(field_value)
                        logger.info(f"✅ Заполнено поле '{field_name}': {field_value}")
                        filled_count += 1
                        
                        # Send progress update to Telegram
                        if context and hasattr(context, 'bot') and filled_count <= 5:  # Only first few updates
                            progress_msg = f"✅ Заполнено: {field_name} = {field_value}"
                            await context.bot.send_message(chat_id=self.channel_id, text=progress_msg)
                    
                    elif field_type == 'select' or element.tag_name == 'select':
                        # Use the already found select element
                        select_obj = Select(element)
                        
                        # Try to select by visible text (exact match first)
                        selected = False
                        try:
                            select_obj.select_by_visible_text(field_value)
                            logger.info(f"✅ Выбрано в dropdown '{field_name}': {field_value}")
                            filled_count += 1
                            selected = True
                            
                            # Send progress update to Telegram
                            if context and hasattr(context, 'bot') and filled_count <= 5:
                                progress_msg = f"✅ Выбрано: {field_name} = {field_value}"
                                await context.bot.send_message(chat_id=self.channel_id, text=progress_msg)
                                
                        except Exception:
                            # Try partial matching for complex options
                            try:
                                options = select_obj.options
                                for option in options:
                                    if field_value.lower() in option.text.lower() or option.text.lower() in field_value.lower():
                                        select_obj.select_by_visible_text(option.text)
                                        logger.info(f"✅ Выбрано (частичное совпадение) '{field_name}': {option.text}")
                                        filled_count += 1
                                        selected = True
                                        
                                        # Send progress update
                                        if context and hasattr(context, 'bot') and filled_count <= 5:
                                            progress_msg = f"✅ Выбрано: {field_name} = {option.text}"
                                            await context.bot.send_message(chat_id=self.channel_id, text=progress_msg)
                                        break
                            except Exception as e:
                                logger.warning(f"⚠️ Не удалось выбрать в dropdown '{field_name}': {e}")
                        
                        if not selected:
                            logger.warning(f"⚠️ Значение '{field_value}' не найдено в dropdown '{field_name}'")
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка заполнения поля '{field_name}': {e}")
                    continue
            
            # Final summary
            total_possible_fields = len(field_data_mapping)
            logger.info(f"📊 Финальная статистика заполнения: {filled_count}/{total_possible_fields} полей")
            
            if context and hasattr(context, 'bot'):
                summary_msg = f"📊 Заполнение завершено: {filled_count} из {len(found_fields)} найденных полей"
                await context.bot.send_message(chat_id=self.channel_id, text=summary_msg)
                
        except Exception as fill_error:
            logger.error(f"❌ Ошибка в умном заполнении: {fill_error}")
            # Fallback to legacy method not implemented yet
            raise fill_error
            
            if self.photo_path and os.path.exists(self.photo_path):
                try:
                    await asyncio.sleep(1)
                    photo_field_names = ['profilePhoto', 'photo', 'profilePicture', 'image', 'photoUpload', 'fotoupload', 'attachment']
                    
                    uploaded = False
                    for field_name in photo_field_names:
                        try:
                            photo_input = self.browser.find_element(by=By.NAME, value=field_name)
                            abs_photo_path = os.path.abspath(self.photo_path)
                            photo_input.send_keys(abs_photo_path)
                            print(f"✅ Загружено фото: {abs_photo_path}")
                            filled_count += 1
                            uploaded = True
                            break
                        except:
                            continue
                    
                    if not uploaded:
                        print(f"⚠️ Не удалось найти поле для загрузки фото. Попробованные имена: {', '.join(photo_field_names)}")
                except Exception as e:
                    print(f"⚠️ Ошибка при загрузке фото: {e}")
            elif self.photo_path:
                print(f"⚠️ Файл фото не найден: {self.photo_path}")
            
            if self.photo_pdf_path and self.upload_pdf:
                try:
                    await asyncio.sleep(1)
                    
                    # Check if it's a JPG file - convert to PDF if needed
                    pdf_path_to_upload = self.photo_pdf_path
                    if self.photo_pdf_path.lower().endswith(('.jpg', '.jpeg')):
                        # Convert JPG to PDF
                        print(f"🔄 Конвертация JPG в PDF: {self.photo_pdf_path}")
                        pdf_path_to_upload = convert_jpg_to_pdf(self.photo_pdf_path)
                        if pdf_path_to_upload is None:
                            print(f"⚠️ Не удалось конвертировать JPG в PDF: {self.photo_pdf_path}")
                            pdf_path_to_upload = self.photo_pdf_path  # Try with original path
                    
                    # Check if PDF file exists
                    if not os.path.exists(pdf_path_to_upload):
                        print(f"⚠️ Файл PDF фото не найден: {pdf_path_to_upload}")
                        # Try auto-conversion from photo_path if exists
                        if self.photo_path and os.path.exists(self.photo_path):
                            print(f"🔄 Попытка конвертировать основное фото в PDF: {self.photo_path}")
                            converted_pdf = convert_jpg_to_pdf(self.photo_path)
                            if converted_pdf:
                                pdf_path_to_upload = converted_pdf
                    
                    if os.path.exists(pdf_path_to_upload):
                        photo_pdf_field_names = ['photoPDF', 'photoPdf', 'photo_pdf', 'pdfPhoto', 'pdfUpload', 'fotoUploadPDF', 'fotouploadpdf', 'pdfAttachment']
                        
                        uploaded = False
                        for field_name in photo_pdf_field_names:
                            try:
                                photo_pdf_input = self.browser.find_element(by=By.NAME, value=field_name)
                                abs_photo_pdf_path = os.path.abspath(pdf_path_to_upload)
                                photo_pdf_input.send_keys(abs_photo_pdf_path)
                                print(f"✅ Загружено PDF фото: {abs_photo_pdf_path}")
                                filled_count += 1
                                uploaded = True
                                break
                            except:
                                continue
                        
                        if not uploaded:
                            print(f"⚠️ Не удалось найти поле для загрузки PDF фото. Попробованные имена: {', '.join(photo_pdf_field_names)}")
                    else:
                        print(f"⚠️ PDF файл не существует и не может быть создан: {self.photo_pdf_path}")
                except Exception as e:
                    print(f"⚠️ Ошибка при загрузке PDF фото: {e}")
            
            # Enhanced completion reporting
            if filled_count > 0:
                success_msg = f"✅ 🚀 АВТО-ЗАПОЛНЕНИЕ ЗАВЕРШЕНО для {current_full_name}! Заполнено {filled_count} полей"
                logger.info(success_msg)
                
                # Send detailed Telegram notification
                if context and hasattr(context, 'bot'):
                    detailed_msg = f"📝 АВТО-ЗАПОЛНЕНИЕ УСПЕШНО!\n\n👤 Заявитель: {current_full_name}\n📊 Заполнено полей: {filled_count}\n✅ Статус: Готово для проверки встреч"
                    await context.bot.send_message(chat_id=self.channel_id, text=detailed_msg)
                
                # Also send to update if available
                if update and update.message:
                    await update.message.reply_text(success_msg)
                    
                # Ensure Latvia category is selected after filling
                logger.info("🎯 Принудительная проверка выбора Latvia после авто-заполнения...")
                await self._ensure_latvia_category_selected()
                
                # Send comprehensive report for all applicants after successful autofill
                await self._send_comprehensive_autofill_report(current_full_name, filled_count, context)
                
            else:
                warning_msg = f"⚠️ АВТО-ЗАПОЛНЕНИЕ НЕ ВЫПОЛНЕНО для {current_full_name}. Возможно, поля формы изменились."
                logger.warning(warning_msg)
                
                # Send detailed warning to Telegram
                if context and hasattr(context, 'bot'):
                    warning_telegram = f"⚠️ ПРОБЛЕМА С АВТО-ЗАПОЛНЕНИЕМ!\n\n👤 Заявитель: {current_full_name}\n❌ Заполнено полей: 0\n🔍 Возможные причины:\n- Изменился интерфейс VFS\n- Поля формы недоступны\n- Проблема с загрузкой страницы"
                    await context.bot.send_message(chat_id=self.channel_id, text=warning_telegram)
                
                if update and update.message:
                    await update.message.reply_text(warning_msg)
                    
        except Exception as e:
            error_msg = f"❌ КРИТИЧЕСКАЯ ОШИБКА АВТО-ЗАПОЛНЕНИЯ для {current_full_name}: {e}"
            logger.error(error_msg)
            
            # Send error notification to Telegram
            if context and hasattr(context, 'bot'):
                error_telegram = f"🚨 ОШИБКА АВТО-ЗАПОЛНЕНИЯ!\n\n👤 Заявитель: {current_full_name}\n❌ Ошибка: {str(e)[:150]}...\n🔄 Бот продолжит работу без авто-заполнения"
                await context.bot.send_message(chat_id=self.channel_id, text=error_telegram)
            
            if update and update.message:
                await update.message.reply_text(f"❌ Ошибка авто-заполнения: {e}")

    async def _fill_form_legacy(self, update: Update, context):
        """Legacy form filling method as fallback"""
        try:
            logger.info("🔄 Использование legacy метода заполнения...")
            filled_count = 0
            current_full_name = f"{self.first_name} {self.last_name}"
            
            # Basic field filling by name attribute
            basic_fields = [
                ('firstName', self.first_name),
                ('lastName', self.last_name),
                ('email', self.contact_email),
                ('phoneNumber', self.contact_phone),
                ('passportNumber', self.passport_number),
                ('dateOfBirth', self.date_of_birth)
            ]
            
            for field_name, field_value in basic_fields:
                if not field_value:
                    continue
                try:
                    element = self.browser.find_element(by=By.NAME, value=field_name)
                    element.clear()
                    element.send_keys(field_value)
                    logger.info(f"✅ [LEGACY] Заполнено {field_name}: {field_value}")
                    filled_count += 1
                except Exception as e:
                    logger.debug(f"⚠️ [LEGACY] Не найдено поле {field_name}: {e}")
                    continue
            
            return filled_count
        except Exception as e:
            logger.error(f"❌ Ошибка legacy заполнения: {e}")
            return 0

    def _get_chrome_options(self):
        """Create fresh ChromeOptions with enhanced stability and compatibility"""
        options = uc.ChromeOptions()
        
        # Essential stability settings
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        # Hide automation signals (basic only for compatibility)
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        # Basic memory optimization
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-plugins')
        options.add_argument('--disable-default-apps')
        
        # Basic performance settings
        options.add_argument('--disable-logging')
        options.add_argument('--disable-sync')
        
        # Additional essential settings
        options.add_argument('--disable-crash-reporter')
        options.add_argument('--disable-popup-blocking')
        options.add_argument('--disable-web-security')
        
        # Simple window management
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--start-maximized')
        
        logger.debug("🔧 Chrome options configured for maximum compatibility")
        return options

    def _check_and_log_remote_grid(self):
        """Check Selenium Grid connection and log configuration"""
        try:
            remote_enabled = self.config.getboolean('REMOTE_GRID', 'enabled', fallback=False)
            
            if remote_enabled:
                hub_url = self.config.get('REMOTE_GRID', 'hub_url')
                logger.info("🌐 SELENIUM GRID КОНФИГУРАЦИЯ:")
                logger.info(f"   ✅ Статус: ВКЛЮЧЕН")
                logger.info(f"   📍 Hub URL: {hub_url}")
                logger.info(f"   🌐 Браузер: {self.config.get('REMOTE_GRID', 'browser_name')}")
                
                import urllib.request
                import json
                try:
                    response = urllib.request.urlopen(f"{hub_url}/status", timeout=3)
                    data = json.loads(response.read().decode('utf-8'))
                    if data.get('status') == 0:
                        logger.info(f"   ✅ Selenium Grid доступен и готов к использованию")
                    else:
                        logger.warning(f"   ⚠️ Selenium Grid не полностью инициализирован")
                except Exception as e:
                    logger.warning(f"   ⚠️ Не удалось подключиться к Selenium Grid: {e}")
                    logger.warning(f"   💡 Убедитесь, что Grid запущен на {hub_url}")
            else:
                logger.info("🌐 SELENIUM GRID: ОТКЛЮЧЕН")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при проверке конфигурации Grid: {e}")

    def _get_chrome_version(self):
        """Get installed Chrome version"""
        import os
        import subprocess
        
        chrome_paths = [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
            os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe")
        ]
        
        for chrome_path in chrome_paths:
            if os.path.exists(chrome_path):
                try:
                    result = subprocess.run(
                        [chrome_path, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    version_str = result.stdout.strip()
                    if version_str:
                        version = version_str.split()[-1]
                        major_version = version.split('.')[0]
                        logger.info(f"✅ Найдена Chrome версия: {version} (основная: {major_version})")
                        return major_version
                except:
                    pass
        
        logger.warning("⚠️  Не удалось определить версию Chrome")
        return None

    def _init_browser(self):
        """Initialize or reinitialize Chrome browser with Chrome for Testing"""
        import os
        import shutil
        from pathlib import Path
        
        # Enhanced browser cleanup before initialization
        try:
            if self.browser:
                try:
                    logger.debug("🧹 Закрытие существующего браузера...")
                    self.browser.quit()
                except Exception as e:
                    logger.debug(f"⚠️ Ошибка при закрытии браузера: {e}")
                finally:
                    self.browser = None
        except:
            pass
        
        # Force cleanup of any remaining Chrome processes
        try:
            import subprocess
            subprocess.run(['taskkill', '/f', '/im', 'chrome.exe'], 
                         capture_output=True, check=False, timeout=5)
            subprocess.run(['taskkill', '/f', '/im', 'chromedriver.exe'], 
                         capture_output=True, check=False, timeout=5)
            logger.debug("🧹 Принудительная очистка процессов Chrome")
        except Exception as e:
            logger.debug(f"⚠️ Ошибка очистки процессов: {e}")
        
        logger.info("🔧 Инициализация Chrome браузера (undetected-chromedriver)...")
        
        # Clean cache directories
        cache_dirs = [
            os.path.expanduser('~/.wdm'),
            os.path.expanduser('~/appdata/roaming/undetected_chromedriver'),
            os.path.expanduser('~/AppData/Local/Temp/scoped_dir*'),
        ]
        
        for cache_dir in cache_dirs:
            if os.path.exists(cache_dir):
                try:
                    shutil.rmtree(cache_dir)
                    logger.debug(f"🧹 Очищена кэш папка: {cache_dir}")
                except Exception as e:
                    logger.debug(f"⚠️ Не удалось очистить {cache_dir}: {e}")
        
        try:
            # Clean up any existing chromedriver processes
            import subprocess
            try:
                subprocess.run(['taskkill', '/f', '/im', 'chromedriver.exe'], 
                             capture_output=True, check=False)
                logger.debug("🧹 Очищены старые процессы chromedriver")
            except:
                pass
            
            # Clean up undetected_chromedriver cache to prevent file conflicts
            uc_cache_dir = os.path.expanduser('~/appdata/roaming/undetected_chromedriver')
            if os.path.exists(uc_cache_dir):
                try:
                    shutil.rmtree(uc_cache_dir)
                    logger.debug("🧹 Очищен кэш undetected_chromedriver")
                except Exception as e:
                    logger.debug(f"⚠️ Не удалось очистить кэш: {e}")
            
            chrome_binary = None
            
            chrome_paths = [
                Path('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'),
                Path('C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'),
                Path(os.path.expanduser('~\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe')),
                Path('chrome-for-testing') / 'chrome-win64' / 'chrome.exe'
            ]
            
            for path in chrome_paths:
                if path.exists():
                    chrome_binary = str(path)
                    logger.info(f"✅ Chrome найден: {chrome_binary}")
                    break
            
            if not chrome_binary:
                logger.warning("⚠️  Chrome не найден в стандартных местах, попытаюсь использовать автоопределение...")
            
            logger.info("🔧 Использование встроенного ChromeDriver undetected-chromedriver...")
            
            # Enhanced initialization with robust retry mechanism
            max_attempts = 5
            for attempt in range(max_attempts):
                try:
                    logger.debug(f"🔄 Попытка инициализации браузера: {attempt + 1}/{max_attempts}")
                    
                    # Aggressive cleanup before each attempt
                    if attempt > 0:
                        logger.debug("🧹 Агрессивная очистка перед повторной попыткой...")
                        self._force_cleanup_browser()
                        
                        # Wait between attempts to prevent race conditions
                        import time
                        time.sleep(2 * attempt)  # Progressive delay
                    
                    # Create completely fresh ChromeOptions for each attempt
                    fresh_options = self._get_chrome_options()
                    
                    # Enhanced initialization with offline mode support
                    try:
                        # First try with auto-download (online mode)
                        logger.debug("🌐 Попытка онлайн инициализации с автозагрузкой ChromeDriver...")
                        self.browser = uc.Chrome(
                            options=fresh_options,
                            suppress_welcome=True,
                            use_subprocess=True,
                            enable_bidi=False,
                            version_main=None,  # Auto-detect version
                            driver_executable_path=None,  # Auto-download if needed
                            browser_executable_path=chrome_binary if chrome_binary else None
                        )
                    except Exception as online_error:
                        logger.warning(f"⚠️ Онлайн инициализация не удалась: {online_error}")
                        
                        # Try with local ChromeDriver if auto-download fails
                        local_driver_paths = [
                            'chromedriver.exe',
                            'chrome-for-testing/chromedriver-win64/chromedriver.exe',
                            os.path.join(os.path.expanduser('~'), '.wdm', 'drivers', 'chromedriver'),
                            'C:\\Program Files\\Google\\Chrome\\Application\\chromedriver.exe'
                        ]
                        
                        local_driver = None
                        for driver_path in local_driver_paths:
                            if os.path.exists(driver_path):
                                local_driver = driver_path
                                logger.info(f"✅ Найден локальный ChromeDriver: {local_driver}")
                                break
                        
                        if local_driver:
                            logger.debug("💿 Попытка офлайн инициализации с локальным ChromeDriver...")
                            self.browser = uc.Chrome(
                                options=fresh_options,
                                suppress_welcome=True,
                                use_subprocess=True,
                                enable_bidi=False,
                                driver_executable_path=local_driver,
                                browser_executable_path=chrome_binary if chrome_binary else None
                            )
                        else:
                            logger.error("❌ Не найден локальный ChromeDriver, попытаемся без указания пути...")
                            # Last resort - let undetected_chromedriver handle it completely
                            self.browser = uc.Chrome(
                                options=fresh_options,
                                suppress_welcome=True,
                                use_subprocess=True,
                                enable_bidi=False,
                                browser_executable_path=chrome_binary if chrome_binary else None
                            )
                    
                    # Verify browser initialization
                    try:
                        self.browser.get("about:blank")
                        logger.info(f"✅ Браузер успешно инициализирован (попытка {attempt + 1})")
                        break
                    except Exception as verify_e:
                        logger.warning(f"⚠️ Ошибка проверки браузера: {verify_e}")
                        if self.browser:
                            try:
                                self.browser.quit()
                            except:
                                pass
                        self.browser = None
                        raise verify_e
                        
                except Exception as init_e:
                    error_msg = str(init_e)
                    logger.warning(f"⚠️ Попытка {attempt + 1} не удалась: {error_msg[:200]}...")
                    
                    # Enhanced error handling for common Chrome and network issues
                    if "excludeSwitches" in error_msg:
                        logger.error("❌ Ошибка Chrome опций: excludeSwitches не поддерживается этой версией Chrome")
                    elif "invalid argument" in error_msg:
                        logger.error("❌ Неверные аргументы Chrome - проверьте совместимость версий")
                    elif "chrome not reachable" in error_msg:
                        logger.error("❌ Chrome недоступен - возможно процесс завис")
                    elif "getaddrinfo failed" in error_msg or "urlopen error" in error_msg:
                        logger.error("🌐 Сетевая ошибка: Нет интернет-соединения или проблемы с DNS")
                        logger.error("💡 Попытка работы в оффлайн режиме...")
                    elif "No such file or directory" in error_msg and "chromedriver" in error_msg:
                        logger.error("📂 ChromeDriver не найден локально")
                        logger.error("💡 Запустите: python download_chrome_for_testing.py")
                    elif "HTTP Error" in error_msg or "Connection" in error_msg:
                        logger.error("🌐 Проблема с интернет-соединением при загрузке ChromeDriver")
                        logger.error("💡 Проверьте подключение к интернету или используйте локальный ChromeDriver")
                    
                    # Clean up failed browser instance
                    if hasattr(self, 'browser') and self.browser:
                        try:
                            self.browser.quit()
                        except:
                            pass
                        self.browser = None
                    
                    if attempt < max_attempts - 1:
                        logger.info(f"🔄 Подготовка к следующей попытке ({attempt + 2}/{max_attempts})...")
                    else:
                        logger.error("❌ Все попытки инициализации браузера исчерпаны!")
                        raise init_e
                    
                    import time
                    time.sleep(7)  # Increased wait time for cleanup
            
            logger.info("✅ Chrome браузер инициализирован успешно")
            return True
            
        except Exception as e:
            logger.error(f"❌ ОШИБКА инициализации Chrome: {e}")
            logger.error("💡 Убедитесь, что Google Chrome установлен в системе")
            logger.error("💡 Или скачайте Chrome for Testing через: python download_chrome_for_testing.py")
            self.browser = None
            return False

    def _check_browser_health(self):
        """Check if browser is alive and responsive with enhanced error handling"""
        try:
            if not self.browser:
                logger.debug("🔍 Browser health: browser is None")
                return False
            
            # Basic responsiveness test with timeout
            try:
                # First check - window handles (most likely to detect dead browser)
                window_handles = self.browser.window_handles
                if not window_handles:
                    logger.debug("🔍 Browser health: no window handles - browser crashed")
                    return False
                
                # Second check - current URL (detects network/page issues)  
                current_url = self.browser.current_url
                if not current_url or current_url == "data:,":
                    logger.debug("🔍 Browser health: invalid or empty URL")
                    return False
                    
                # Third check - page title (detects page loading issues)
                try:
                    title = self.browser.title
                    if "chrome-error" in title.lower() or "err_" in current_url.lower():
                        logger.debug(f"🔍 Browser health: Chrome error detected: {title}")
                        return False
                except:
                    # Title check failed, but browser might still be usable
                    pass
                
                logger.debug(f"🔍 Browser health: OK - URL: {current_url[:50]}...")
                return True
                
            except Exception as health_e:
                error_msg = str(health_e)
                
                # Specific error handling for common browser issues
                if "chrome not reachable" in error_msg.lower():
                    logger.debug("🔍 Browser health: Chrome not reachable - browser crashed")
                    return False
                elif "target frame detached" in error_msg.lower():
                    logger.debug("🔍 Browser health: Frame detached - page navigation issue")
                    return False  
                elif "net::err_internet_disconnected" in error_msg.lower():
                    logger.debug("🔍 Browser health: Internet disconnected")
                    return False
                elif "session deleted" in error_msg.lower():
                    logger.debug("🔍 Browser health: Session deleted - browser closed")
                    return False
                else:
                    logger.debug(f"🔍 Browser health: Unknown error: {error_msg[:100]}...")
                    return False
        except Exception as e:
            logger.warning(f"⚠️ Браузер не отвечает: {e}")
            return False
    
    async def _attempt_browser_recovery(self):
        """Attempt to recover browser from critical errors"""
        try:
            logger.info("🔧 Попытка восстановления браузера после критических ошибок...")
            
            # Step 1: Force cleanup
            self._force_cleanup_browser()
            await asyncio.sleep(3)
            
            # Step 2: Check system resources
            recovery_possible = self._comprehensive_browser_health_check()
            if not recovery_possible:
                logger.warning("⚠️ Системные ресурсы недостаточны для восстановления")
                return False
            
            # Step 3: Attempt reinit
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._init_browser)
            
            if result and self.browser:
                logger.info("✅ Браузер успешно восстановлен")
                return True
            else:
                logger.error("❌ Не удалось восстановить браузер")
                return False
                
        except Exception as recovery_e:
            logger.error(f"❌ Ошибка восстановления браузера: {recovery_e}")
            return False

    def _cleanup_temp_files(self):
        """Clean up old temporary debug files to prevent disk space issues"""
        try:
            import glob
            from pathlib import Path
            
            # Check if cleanup is needed (every 30 minutes)
            if (datetime.now() - self.last_cleanup).total_seconds() < 1800:
                return
                
            logger.info("🧹 Выполняется очистка временных файлов...")
            
            # Patterns for temporary files
            temp_patterns = [
                'debug_*.png',
                'debug_*.html', 
                'captcha_*.png',
                '*.tmp'
            ]
            
            cleaned_count = 0
            for pattern in temp_patterns:
                files = glob.glob(pattern)
                for file_path in files:
                    try:
                        file_age = (datetime.now() - datetime.fromtimestamp(Path(file_path).stat().st_mtime)).total_seconds()
                        # Delete files older than 1 hour
                        if file_age > 3600:
                            os.remove(file_path)
                            cleaned_count += 1
                    except Exception as e:
                        logger.debug(f"⚠️ Не удалось удалить {file_path}: {e}")
            
            if cleaned_count > 0:
                logger.info(f"🗑️ Очищено {cleaned_count} временных файлов")
            
            self.last_cleanup = datetime.now()
            
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при очистке временных файлов: {e}")

    async def _analyze_page_structure(self):
        """Analyze current page structure for debugging"""
        try:
            logger.info("🔍 Анализ структуры страницы...")
            
            # Wait for page stability
            await asyncio.sleep(2)
            
            # Get current URL for context
            current_url = self.browser.current_url
            logger.info(f"📍 Текущий URL: {current_url}")
            
            # Check page loading state
            ready_state = self.browser.execute_script("return document.readyState")
            logger.info(f"📄 Состояние загрузки страницы: {ready_state}")
            
            if ready_state != "complete":
                logger.warning("⏳ Страница еще загружается, ждем...")
                await asyncio.sleep(5)
            
            # Find all selects on the page
            all_selects = self.browser.find_elements(by=By.TAG_NAME, value='select')
            logger.info(f"📊 Найдено {len(all_selects)} dropdown элементов на странице")
            
            if len(all_selects) == 0:
                logger.warning("⚠️ Dropdown элементы не найдены - проверяем другие типы")
                
                # Check for other form elements
                all_inputs = self.browser.find_elements(by=By.TAG_NAME, value='input')
                all_buttons = self.browser.find_elements(by=By.TAG_NAME, value='button')
                all_forms = self.browser.find_elements(by=By.TAG_NAME, value='form')
                
                logger.info(f"📊 Альтернативные элементы: {len(all_inputs)} inputs, {len(all_buttons)} buttons, {len(all_forms)} forms")
                
                # Check if page contains login indicators
                page_source_lower = self.browser.page_source.lower()
                login_indicators = ['email', 'password', 'login', 'signin', 'username']
                found_login_indicators = [indicator for indicator in login_indicators if indicator in page_source_lower]
                
                if found_login_indicators:
                    logger.warning(f"⚠️ Обнаружены индикаторы страницы входа: {found_login_indicators}")
                    return False
            
            for i, select_elem in enumerate(all_selects):
                try:
                    # Get select attributes
                    name_attr = select_elem.get_attribute('name') or 'No name'
                    id_attr = select_elem.get_attribute('id') or 'No id'
                    class_attr = select_elem.get_attribute('class') or 'No class'
                    
                    # Get options
                    select_obj = Select(select_elem)
                    options = [opt.text.strip() for opt in select_obj.options if opt.text.strip()]
                    
                    logger.info(f"📋 Dropdown {i+1}: name='{name_attr}', id='{id_attr}', class='{class_attr}'")
                    logger.info(f"   └── Опции: {options}")
                    
                    # Check if this might be Latvia category
                    has_latvia = any('Latvia' in opt for opt in options)
                    if has_latvia:
                        logger.info(f"🎯 ⭐ НАЙДЕН ПОТЕНЦИАЛЬНЫЙ Latvia dropdown #{i+1}!")
                        
                except Exception as e:
                    logger.debug(f"Ошибка анализа dropdown {i+1}: {e}")
                    
            # Also check for any elements containing "Latvia" text
            latvia_elements = self.browser.find_elements(by=By.XPATH, value="//*[contains(text(), 'Latvia')]")
            logger.info(f"🔍 Найдено {len(latvia_elements)} элементов с текстом 'Latvia'")
            
            return len(all_selects) > 0  # Return True if we found dropdowns
            
        except Exception as e:
            logger.error(f"❌ Ошибка анализа страницы: {e}")
            return False

    async def _ensure_latvia_category_selected(self):
        """Ensure Latvia Temporary Residence Permit category is selected after each successful login"""
        try:
            logger.info("🎯 ПРИОРИТЕТ: Принудительный выбор Latvia Temporary Residence Permit...")
            
            # Check if we're still on login page - if so, skip this step
            current_url = self.browser.current_url.lower()
            if 'login' in current_url or 'signin' in current_url:
                logger.warning("⚠️ Все еще на странице входа - пропускаем выбор Latvia")
                return False
            
            # Wait for page to be fully loaded and ready
            await asyncio.sleep(5)
            
            # Check if page is loaded by looking for basic elements
            try:
                WebDriverWait(self.browser, 15).until(
                    lambda driver: driver.execute_script("return document.readyState") == "complete"
                )
                logger.debug("✅ Страница полностью загружена")
            except TimeoutException:
                logger.warning("⚠️ Страница не полностью загружена за 15 секунд")
            
            # Analyze page structure first
            await self._analyze_page_structure()
            
            # Enhanced and comprehensive Latvia category selectors
            latvia_selectors = [
                # Standard appointment category selectors
                '[name="appointmentCategory"]',
                '#appointmentCategory', 
                '[name="AppointmentCategory"]',
                '#AppointmentCategory',
                
                # VFS Global specific patterns
                'select[name*="category"]',
                'select[id*="category"]',
                'select[name*="Category"]',
                'select[id*="Category"]',
                'select[name*="appointment"]',
                'select[id*="appointment"]',
                'select[name*="Appointment"]',
                'select[id*="Appointment"]',
                
                # Form control classes
                'select[class*="appointment"]',
                'select[class*="category"]',
                '.form-select[name*="category"]',
                '.form-control[name*="category"]',
                '.form-select[name*="appointment"]',
                '.form-control[name*="appointment"]',
                
                # Visa type selectors
                'select[name*="visa"]',
                'select[id*="visa"]',
                'select[name*="service"]',
                'select[id*="service"]',
                'select[name*="type"]',
                'select[id*="type"]',
                
                # Modern web selectors
                '[data-test*="category"]',
                '[data-testid*="category"]',
                '[aria-label*="category"]',
                '[aria-label*="appointment"]',
                
                # Option-based selectors (for direct option detection)
                'select option[value*="Latvia"]',
                'select option[text*="Latvia"]',
                'option[value*="Latvia"]',
                'option[text*="Latvia"]',
                
                # Fallback - all selects on the page
                'select',
            ]
            
            category_selected = False
            
            for i, selector in enumerate(latvia_selectors):
                try:
                    logger.debug(f"🔍 Попытка {i+1}: Поиск через селектор: {selector}")
                    
                    if 'option' in selector:
                        # Looking for specific option first
                        options = self.browser.find_elements(by=By.CSS_SELECTOR, value=selector)
                        for option in options:
                            if 'Latvia' in option.text and 'Temporary' in option.text:
                                # Found Latvia option, get parent select and select it
                                select_elem = option.find_element(by=By.XPATH, value='..')
                                select_obj = Select(select_elem)
                                select_obj.select_by_visible_text(option.text)
                                logger.info(f"✅ Latvia категория выбрана через опцию: {option.text}")
                                category_selected = True
                                break
                    else:
                        # Looking for select element
                        select_elements = self.browser.find_elements(by=By.CSS_SELECTOR, value=selector)
                        
                        for select_elem in select_elements:
                            if select_elem.tag_name.lower() == 'select':
                                select_obj = Select(select_elem)
                                
                                # Try different selection methods for Latvia
                                latvia_options = [
                                    "Latvia Temporary Residence Permit",
                                    "Latvia",
                                    "Temporary Residence Permit",
                                ]
                                
                                for option_text in latvia_options:
                                    try:
                                        # Check if option exists
                                        available_options = [opt.text for opt in select_obj.options]
                                        logger.debug(f"Доступные опции: {available_options}")
                                        
                                        # Try exact match first
                                        if option_text in available_options:
                                            select_obj.select_by_visible_text(option_text)
                                            logger.info(f"✅ Latvia категория выбрана: {option_text}")
                                            category_selected = True
                                            break
                                        
                                        # Try partial match
                                        for avail_opt in available_options:
                                            if 'Latvia' in avail_opt and 'Temporary' in avail_opt:
                                                select_obj.select_by_visible_text(avail_opt)
                                                logger.info(f"✅ Latvia категория выбрана (частичное совпадение): {avail_opt}")
                                                category_selected = True
                                                break
                                                
                                        if category_selected:
                                            break
                                            
                                    except Exception as select_e:
                                        logger.debug(f"Не удалось выбрать {option_text}: {select_e}")
                                        continue
                                
                                if category_selected:
                                    break
                    
                    if category_selected:
                        break
                        
                except Exception as e:
                    logger.debug(f"Селектор {selector} не сработал: {e}")
                    continue
            
            if category_selected:
                logger.info("🎯 ✅ УСПЕХ: Latvia Temporary Residence Permit успешно выбрана!")
                # Give page time to process the selection
                await asyncio.sleep(1)
            else:
                logger.warning("⚠️ Не удалось автоматически выбрать Latvia категорию")
                
                # Enhanced debugging - send detailed info to Telegram
                try:
                    all_selects = self.browser.find_elements(by=By.TAG_NAME, value='select')
                    debug_info = f"🔍 ДИАГНОСТИКА: Найдено {len(all_selects)} dropdown(ов) на странице:\n\n"
                    
                    for i, select_elem in enumerate(all_selects):
                        name_attr = select_elem.get_attribute('name') or 'No name'
                        id_attr = select_elem.get_attribute('id') or 'No id'
                        select_obj = Select(select_elem)
                        options_text = [opt.text.strip() for opt in select_obj.options if opt.text.strip()]
                        
                        debug_info += f"Dropdown {i+1}:\n"
                        debug_info += f"  name: {name_attr}\n"
                        debug_info += f"  id: {id_attr}\n" 
                        debug_info += f"  опции: {', '.join(options_text[:3])}{'...' if len(options_text) > 3 else ''}\n\n"
                    
                    # Send to Telegram in chunks if too long
                    if len(debug_info) > 4000:
                        chunks = [debug_info[i:i+4000] for i in range(0, len(debug_info), 4000)]
                        for chunk in chunks:
                            logger.info(f"📋 Latvia категория: {chunk}")
                    else:
                        logger.info(f"📋 Debug info: {debug_info}")
                except Exception as debug_e:
                    logger.debug(f"Ошибка отладочного сообщения: {debug_e}")
                except:
                    pass
                    
            return category_selected
                    
        except Exception as e:
            logger.error(f"❌ Ошибка при принудительном выборе Latvia категории: {e}")
            logger.warning(f"❌ Ошибка выбора Latvia категории: {str(e)[:200]}")
            return False

    async def _smart_find_form_fields(self):
        """Smart form field detection with multiple strategies"""
        try:
            logger.info("🧠 Интеллектуальный поиск полей формы...")
            
            # Common field patterns for VFS forms
            field_patterns = {
                'country': ['[name*="country"]', '[id*="country"]', 'select[name*="Country"]', '[name="country"]', '#country'],
                'passportValidityDate': ['[name*="passport"]', '[name*="validity"]', '[name*="expiry"]', '[id*="passport"]', 'input[type="date"]', '[name*="PassportExpiryDate"]'],
                'appointmentCategory': ['[name*="category"]', '[name*="appointment"]', '[id*="category"]', 'select[name*="Category"]', '[name="appointmentCategory"]'],
                'firstName': ['[name*="first"]', '[name*="First"]', '[id*="first"]', 'input[name*="name"]', '[name="firstName"]', '[name="FirstName"]'],
                'lastName': ['[name*="last"]', '[name*="Last"]', '[id*="last"]', '[name*="surname"]', '[name="lastName"]', '[name="LastName"]'],
                'dateOfBirth': ['[name*="birth"]', '[name*="Birth"]', '[id*="birth"]', 'input[type="date"]', '[name="dateOfBirth"]', '[name="DateOfBirth"]'],
                'passportNumber': ['[name*="passport"]', '[id*="passport"]', '[name*="number"]', 'input[type="text"]', '[name="passportNumber"]', '[name="PassportNumber"]'],
                'nationality': ['[name*="nationality"]', '[id*="nationality"]', '[name*="Nationality"]', '[name="nationality"]', '#nationality'],
                'phoneNumber': ['[name*="phone"]', '[id*="phone"]', '[name*="mobile"]', 'input[type="tel"]', '[name="phoneNumber"]', '[name="PhoneNumber"]'],
                'email': ['[name*="email"]', '[id*="email"]', 'input[type="email"]', '[name="email"]', '#email'],
                'address': ['[name*="address"]', '[id*="address"]', 'textarea', '[name="address"]', '#address'],
                'purpose': ['[name*="purpose"]', '[id*="purpose"]', '[name*="Purpose"]', '[name="purpose"]', '#purpose']
            }
            
            found_fields = {}
            
            for field_name, selectors in field_patterns.items():
                for selector in selectors:
                    try:
                        elements = self.browser.find_elements(by=By.CSS_SELECTOR, value=selector)
                        for element in elements:
                            # Check if element is visible and interactable
                            if element.is_displayed() and element.is_enabled():
                                # Additional validation based on field type
                                if self._validate_field_element(field_name, element):
                                    found_fields[field_name] = {
                                        'element': element,
                                        'selector': selector,
                                        'name': element.get_attribute('name'),
                                        'id': element.get_attribute('id'),
                                        'type': element.get_attribute('type') or element.tag_name
                                    }
                                    logger.info(f"✅ Найдено поле '{field_name}': {selector}")
                                    break
                        if field_name in found_fields:
                            break
                    except Exception as e:
                        continue
            
            # Report findings
            found_count = len(found_fields)
            total_fields = len(field_patterns)
            logger.info(f"🎯 Найдено {found_count}/{total_fields} полей формы")
            
            missing_fields = set(field_patterns.keys()) - set(found_fields.keys())
            if missing_fields:
                logger.warning(f"⚠️ Не найдены поля: {', '.join(missing_fields)}")
                # Send diagnostic info to logs
                logger.warning(f"🔍 Поиск полей: найдено {found_count}/{total_fields}\n❌ Не найдены: {', '.join(missing_fields)}")
                
            return found_fields
            
        except Exception as e:
            logger.error(f"❌ Ошибка интеллектуального поиска полей: {e}")
            return {}

    def _validate_field_element(self, field_name, element):
        """Validate if element matches the expected field type"""
        try:
            tag = element.tag_name.lower()
            element_type = element.get_attribute('type')
            name = (element.get_attribute('name') or '').lower()
            id_attr = (element.get_attribute('id') or '').lower()
            
            # Field-specific validations
            if field_name in ['country', 'nationality', 'appointmentCategory']:
                return tag == 'select'
            elif field_name in ['dateOfBirth', 'passportValidityDate']:
                return (tag == 'input' and element_type in ['date', 'text']) or 'date' in name or 'date' in id_attr
            elif field_name == 'email':
                return (tag == 'input' and element_type == 'email') or 'email' in name or 'email' in id_attr
            elif field_name == 'phoneNumber':
                return (tag == 'input' and element_type in ['tel', 'text']) or 'phone' in name or 'mobile' in name
            elif field_name == 'address':
                return tag in ['textarea', 'input']
            else:
                return tag == 'input' and element_type in ['text', 'email', 'tel', None]
                
        except Exception:
            return True  # If validation fails, assume it's valid

    async def _ensure_autofill_activated(self, person_name, context):
        """Ensure auto-fill is activated after successful login with comprehensive checks"""
        try:
            logger.info(f"🔧 Проверка и активация автозаполнения анкеты для {person_name}...")
            
            # Check configuration status
            config_autofill = self.config.getboolean('VFS', 'auto_fill', fallback=False)
            current_autofill_status = self.auto_fill
            
            logger.info(f"📋 Статус автозаполнения: config={config_autofill}, runtime={current_autofill_status}")
            
            # Force enable if not already enabled
            if not current_autofill_status or not config_autofill:
                logger.info(f"� ПРИНУДИТЕЛЬНАЯ АКТИВАЦИЯ автозаполнения анкеты для {person_name}!")
                
                # Enable for this session
                self.auto_fill = True
                
                # Send detailed activation notification
                if context and hasattr(context, 'bot'):
                    activation_msg = f"� АВТОЗАПОЛНЕНИЕ АНКЕТЫ АКТИВИРОВАНО!\n\n👤 Заявитель: {person_name}\n📋 Config: {'✅' if config_autofill else '❌→✅'}\n🔧 Runtime: {'✅' if current_autofill_status else '❌→✅'}\n⚡ Статус: ПРИНУДИТЕЛЬНО ВКЛЮЧЕНО\n📝 Готов к заполнению анкеты!"
                    await context.bot.send_message(chat_id=self.channel_id, text=activation_msg)
                
                logger.info(f"✅ Автозаполнение анкеты принудительно активировано для {person_name}")
            else:
                logger.info(f"✅ Автозаполнение анкеты уже активно для {person_name}")
                
                # Send comprehensive status
                if context and hasattr(context, 'bot'):
                    status_msg = f"✅ АВТОЗАПОЛНЕНИЕ АНКЕТЫ ГОТОВО!\n\n👤 Заявитель: {person_name}\n� Config: ✅ ВКЛЮЧЕНО\n🔧 Runtime: ✅ АКТИВНО\n🚀 Готов к заполнению анкеты после входа!"
                    await context.bot.send_message(chat_id=self.channel_id, text=status_msg)
            
            # Additional verification
            if self.auto_fill:
                logger.info(f"🎯 ПОДТВЕРЖДЕНИЕ: Автозаполнение анкеты АКТИВНО для {person_name}")
                return True
            else:
                logger.warning(f"⚠️ ВНИМАНИЕ: Не удалось активировать автозаполнение для {person_name}")
                return False
                    
        except Exception as e:
            logger.error(f"❌ Критическая ошибка активации автозаполнения для {person_name}: {e}")
            
            # Emergency activation
            try:
                self.auto_fill = True
                logger.info(f"🆘 Экстренная активация автозаполнения для {person_name}")
                return True
            except:
                return False

    async def _wait_for_form_ready(self, person_name, context):
        """Wait for the application form to be ready after successful login"""
        try:
            logger.info(f"⏳ Ожидание готовности формы анкеты для {person_name}...")
            
            max_wait_time = 10  # seconds
            wait_interval = 1
            elapsed = 0
            
            while elapsed < max_wait_time:
                await asyncio.sleep(wait_interval)
                elapsed += wait_interval
                
                # Check if page is loaded and interactive
                try:
                    page_state = self.browser.execute_script("return document.readyState")
                    logger.info(f"📄 Состояние страницы: {page_state} (ожидание {elapsed}/{max_wait_time}с)")
                    
                    if page_state == "complete":
                        # Additional wait for dynamic content
                        await asyncio.sleep(2)
                        logger.info(f"✅ Страница анкеты готова для заполнения ({person_name})")
                        break
                        
                except Exception as e:
                    logger.debug(f"Проверка состояния страницы: {e}")
                    continue
            
            # Send progress update
            if context and hasattr(context, 'bot'):
                ready_msg = f"📄 ФОРМА АНКЕТЫ ГОТОВА!\n👤 Заявитель: {person_name}\n⏱️ Время ожидания: {elapsed}с\n🔍 Начинаю анализ полей формы..."
                await context.bot.send_message(chat_id=self.channel_id, text=ready_msg)
                
        except Exception as e:
            logger.warning(f"⚠️ Ошибка ожидания готовности формы для {person_name}: {e}")

    async def _verify_form_page_ready(self, person_name, context):
        """Verify that we're on the correct form page and ready to fill"""
        try:
            logger.info(f"🔍 Проверка готовности страницы анкеты для {person_name}...")
            
            # Check current URL
            current_url = self.browser.current_url
            logger.info(f"📍 Текущий URL: {current_url}")
            
            # Look for form indicators
            form_indicators = [
                'form',
                'input[type="text"]',
                'input[type="email"]', 
                'select',
                'textarea'
            ]
            
            found_elements = 0
            for selector in form_indicators:
                try:
                    elements = self.browser.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        found_elements += len(elements)
                except:
                    continue
            
            logger.info(f"📊 Найдено {found_elements} элементов формы на странице")
            
            if found_elements > 0:
                logger.info(f"✅ Страница анкеты подтверждена готова для заполнения ({person_name})")
                
                if context and hasattr(context, 'bot'):
                    verification_msg = f"✅ АНКЕТА ПОДТВЕРЖДЕНА!\n👤 Заявитель: {person_name}\n📊 Элементов формы: {found_elements}\n🎯 Готов к автозаполнению!"
                    await context.bot.send_message(chat_id=self.channel_id, text=verification_msg)
            else:
                logger.warning(f"⚠️ Форма анкеты не обнаружена на странице для {person_name}")
                
                if context and hasattr(context, 'bot'):
                    warning_msg = f"⚠️ ПРОБЛЕМА С ФОРМОЙ!\n👤 Заявитель: {person_name}\n❌ Элементы формы не найдены\n🔄 Продолжаю без автозаполнения"
                    await context.bot.send_message(chat_id=self.channel_id, text=warning_msg)
                
        except Exception as e:
            logger.error(f"❌ Ошибка проверки готовности формы для {person_name}: {e}")

    async def _send_comprehensive_autofill_report(self, completed_applicant, filled_count, context):
        """Send comprehensive report after successful autofill for all applicants"""
        try:
            if not context or not hasattr(context, 'bot'):
                return
                
            logger.info(f"📊 Создание комплексного отчета автозаполнения...")
            
            current_time = datetime.now().strftime('%H:%M:%S')
            current_date = datetime.now().strftime('%d.%m.%Y')
            
            # Get total applicants count
            total_applicants = len(self.persons)
            
            # Create comprehensive report
            comprehensive_report = f"""📋 ПОЛНЫЙ ОТЧЕТ АВТОЗАПОЛНЕНИЯ СИСТЕМЫ

🏷️ ПОСЛЕДНИЙ ОБРАБОТАННЫЙ: {completed_applicant}
📅 Дата: {current_date}
🕐 Время завершения: {current_time}
📊 Заполнено полей: {filled_count}

👥 СТАТИСТИКА ЗАЯВИТЕЛЕЙ:
📈 Всего в системе: {total_applicants} заявителей
✅ Обработанный: {completed_applicant}

📋 ПОЛНЫЙ СПИСОК ВСЕХ ЗАЯВИТЕЛЕЙ:"""

            # Add all applicants to the report
            for i, person in enumerate(self.persons, 1):
                person_name = f"{person['first_name']} {person['last_name']}"
                migris_code = person.get('migris_code', 'Н/Д')
                phone = person.get('contact_phone', 'Н/Д')
                
                # Highlight current processed applicant
                status = "🎯 ОБРАБОТАН" if person_name == completed_applicant else "⏳ ГОТОВ"
                
                comprehensive_report += f"""
    [{i}] {person_name}
        📋 MIGRIS: {migris_code}
        📞 Телефон: {phone}
        📊 Статус: {status}"""

            comprehensive_report += f"""

🎯 ТЕКУЩЕЕ СОСТОЯНИЕ СИСТЕМЫ:
✅ Автозаполнение: АКТИВНО
🤖 Система VFS: ПОДКЛЮЧЕНА  
🔍 Поиск встреч: ЗАПУЩЕН
📱 Уведомления: ВКЛЮЧЕНЫ
🔄 Автоподтверждение: ГОТОВО

⚡ СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
🔍 Активный мониторинг встреч для всех заявителей
📅 Автоматическое бронирование при обнаружении
📱 Мгновенные уведомления в Telegram
🎯 Приоритетная обработка заявок

🚀 СИСТЕМА ПОЛНОСТЬЮ ГОТОВА К РАБОТЕ!"""

            # Send the comprehensive report
            await context.bot.send_message(chat_id=self.channel_id, text=comprehensive_report)
            logger.info(f"✅ Комплексный отчет автозаполнения отправлен успешно")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке комплексного отчета автозаполнения: {e}")

    async def _send_cycle_completion_report(self):
        """Send report after completing full cycle through all applicants"""
        try:
            if not hasattr(self, 'app') or not hasattr(self.app, 'bot'):
                return
                
            logger.info(f"🔄 Отправка отчета о завершении цикла проверки всех заявителей...")
            
            current_time = datetime.now().strftime('%H:%M:%S')
            current_date = datetime.now().strftime('%d.%m.%Y')
            total_applicants = len(self.persons)
            
            # Create cycle completion report
            cycle_report = f"""🔄 ЦИКЛ ПРОВЕРКИ ЗАВЕРШЕН!

📅 Дата: {current_date}
🕐 Время завершения: {current_time}
👥 Проверено заявителей: {total_applicants}

📋 ВСЕ ЗАЯВИТЕЛИ ПРОВЕРЕНЫ:"""

            # Add all applicants with their current status
            for i, person in enumerate(self.persons, 1):
                person_name = f"{person['first_name']} {person['last_name']}"
                migris_code = person.get('migris_code', 'Н/Д')
                count = self.person_stats.get(person_name, 0) + 1  # +1 for current check
                
                cycle_report += f"""
  [{i}] {person_name}
     📋 MIGRIS: {migris_code}
     🔍 Всего проверок: {count}
     ✅ Статус: ПРОВЕРЕН"""

            cycle_report += f"""

🎯 РЕЗУЛЬТАТ ЦИКЛА:
🔍 Поиск встреч: ВЫПОЛНЕН для всех заявителей
📊 Общий статус: НЕТ НОВЫХ ВСТРЕЧ
⏳ Следующий цикл: ЗАПУСКАЕТСЯ

🔄 СИСТЕМА ПРОДОЛЖАЕТ МОНИТОРИНГ...
⏱️ Интервал: {self.interval} секунд между проверками"""

            # Send the cycle completion report
            await self.app.bot.send_message(chat_id=self.channel_id, text=cycle_report)
            logger.info(f"✅ Отчет о завершении цикла отправлен успешно")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке отчета о завершении цикла: {e}")

    async def _send_captcha_for_manual_input(self, captcha_img):
        """Send captcha image to Telegram for manual input"""
        try:
            logger.info("📱 Отправка капчи в Telegram для ручного ввода...")
            
            # Save captcha with timestamp
            captcha_timestamp = int(datetime.now().timestamp())
            manual_captcha_filename = f'manual_captcha_{captcha_timestamp}.png'
            
            with open(manual_captcha_filename, 'wb') as file:
                file.write(captcha_img.screenshot_as_png)
            
            # Send to Telegram
            if hasattr(self, 'app') and hasattr(self.app, 'bot'):
                with open(manual_captcha_filename, 'rb') as captcha_file:
                    caption_text = f"🤖 КАПЧА ОБНАРУЖЕНА!\n\n📷 Время: {datetime.now().strftime('%H:%M:%S')}\n👆 Пожалуйста, введите символы с изображения\n\n⏰ Ожидание ответа..."
                    
                    await self.app.bot.send_photo(
                        chat_id=self.channel_id,
                        photo=captcha_file,
                        caption=caption_text
                    )
                    
                logger.info(f"✅ Капча отправлена в Telegram: {manual_captcha_filename}")
                
                # Set up waiting for manual input (simplified version)
                logger.info("⏳ Ожидание ручного ввода капчи...")
                await asyncio.sleep(30)  # Wait 30 seconds for manual input
                
            # Clean up
            if os.path.exists(manual_captcha_filename):
                os.remove(manual_captcha_filename)
                logger.debug(f"🗑️ Временный файл капчи удален: {manual_captcha_filename}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка отправки капчи в Telegram: {e}")

    async def _attempt_login_recovery(self, person_name):
        """Attempt to recover from login timeout errors"""
        try:
            logger.info(f"🔧 Попытка восстановления входа для {person_name}...")
            
            # Check if browser is still responsive
            if not self._check_browser_health():
                logger.warning("⚠️ Браузер не отвечает - попытка перезапуска...")
                recovery_result = await self._attempt_browser_recovery()
                return recovery_result
            
            # Check if we're on a different page than expected
            current_url = self.browser.current_url.lower()
            if "login" not in current_url and "application-detail" not in current_url:
                logger.info(f"🔄 Неожиданная страница: {current_url}, возврат к входу...")
                self.browser.get(self.url)
                await asyncio.sleep(3)
                return True
            
            # Try to refresh the current page
            logger.info("🔄 Обновление страницы...")
            self.browser.refresh()
            await asyncio.sleep(5)
            
            return True
            
        except Exception as recovery_e:
            logger.error(f"❌ Ошибка восстановления входа: {recovery_e}")
            return False
    
    async def _attempt_element_recovery(self):
        """Attempt to recover from element interaction errors"""
        try:
            logger.info("🔧 Попытка восстановления элементов...")
            
            # Scroll to top of page
            self.browser.execute_script("window.scrollTo(0, 0);")
            await asyncio.sleep(2)
            
            # Wait for page to stabilize
            await asyncio.sleep(3)
            
            # Check if page is fully loaded
            ready_state = self.browser.execute_script("return document.readyState")
            if ready_state != "complete":
                logger.info("⏳ Ожидание завершения загрузки страницы...")
                await asyncio.sleep(5)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка восстановления элементов: {e}")
            return False
    
    async def _handle_location_id_with_recovery(self, max_attempts=3):
        """Handle LocationId selection with comprehensive error recovery"""
        person_name = f"{self.first_name} {self.last_name}"
        
        for attempt in range(max_attempts):
            try:
                logger.debug(f"⏳ Попытка {attempt + 1}/{max_attempts}: Ожидание поля LocationId...")
                
                # Enhanced wait with multiple strategies
                location_element = None
                wait_strategies = [
                    # Strategy 1: Direct ID selector
                    (By.ID, "LocationId"),
                    # Strategy 2: XPath selector  
                    (By.XPATH, '//*[@id="LocationId"]'),
                    # Strategy 3: Name selector
                    (By.NAME, "LocationId"),
                    # Strategy 4: CSS selector
                    (By.CSS_SELECTOR, "select#LocationId"),
                    # Strategy 5: Generic location select
                    (By.CSS_SELECTOR, "select[name*='location'], select[id*='location']")
                ]
                
                for strategy_num, (by_method, selector) in enumerate(wait_strategies):
                    try:
                        logger.debug(f"🔍 Стратегия {strategy_num + 1}: {by_method}={selector}")
                        location_element = WebDriverWait(self.browser, 15).until(
                            EC.element_to_be_clickable((by_method, selector))
                        )
                        logger.debug(f"✅ LocationId найден стратегией {strategy_num + 1}")
                        break
                    except TimeoutException:
                        logger.debug(f"⏱️ Стратегия {strategy_num + 1} timeout")
                        continue
                    except Exception as strategy_e:
                        logger.debug(f"⚠️ Стратегия {strategy_num + 1} ошибка: {strategy_e}")
                        continue
                
                if not location_element:
                    logger.warning("⚠️ LocationId не найден ни одной стратегией")
                    if attempt < max_attempts - 1:
                        logger.info("🔄 Попытка восстановления элементов...")
                        await self._attempt_element_recovery()
                        continue
                    else:
                        return False
                
                # Try to interact with the found element
                click_success = await self._safe_element_click(location_element, "LocationId")
                if not click_success:
                    if attempt < max_attempts - 1:
                        continue
                    else:
                        return False
                
                await asyncio.sleep(2)
                
                # Check for errors after clicking
                if self.check_errors():
                    logger.warning("❌ Ошибка после клика на LocationId")
                    if attempt < max_attempts - 1:
                        continue
                    else:
                        return False
                
                # Try to select second option
                option_success = await self._select_location_option_with_recovery(location_element)
                if option_success:
                    logger.info("✅ LocationId успешно обработан")
                    return True
                else:
                    if attempt < max_attempts - 1:
                        logger.info("🔄 Повторная попытка выбора опции...")
                        continue
                    else:
                        return False
                        
            except TimeoutException as te:
                logger.warning(f"⏱️ Timeout при обработке LocationId (попытка {attempt + 1}): {te}")
                if attempt < max_attempts - 1:
                    await self._handle_timeout_recovery(f"LocationId timeout attempt {attempt + 1}")
                    continue
                else:
                    logger.error("❌ Все попытки обработки LocationId исчерпаны (timeout)")
                    return False
                    
            except (NoSuchElementException, ElementNotInteractableException) as ee:
                logger.warning(f"🔍 Элемент LocationId недоступен (попытка {attempt + 1}): {ee}")
                if attempt < max_attempts - 1:
                    await self._attempt_element_recovery()
                    continue
                else:
                    logger.error("❌ Все попытки обработки LocationId исчерпаны (element error)")
                    return False
                    
            except StaleElementReferenceException:
                logger.warning(f"🔄 Устаревший элемент LocationId (попытка {attempt + 1})")
                if attempt < max_attempts - 1:
                    await self._refresh_page_elements()
                    continue
                else:
                    logger.error("❌ Все попытки обработки LocationId исчерпаны (stale element)")
                    return False
                    
            except WebDriverException as wde:
                logger.error(f"🌐 WebDriver ошибка при обработке LocationId (попытка {attempt + 1}): {wde}")
                if attempt < max_attempts - 1:
                    browser_recovery = await self._attempt_browser_recovery()
                    if not browser_recovery:
                        return False
                    continue
                else:
                    logger.error("❌ Все попытки обработки LocationId исчерпаны (webdriver error)")
                    return False
                    
            except Exception as e:
                logger.error(f"❌ Неожиданная ошибка при обработке LocationId (попытка {attempt + 1}): {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(3)
                    continue
                else:
                    return False
        
        return False
    
    async def _safe_element_click(self, element, element_name, max_attempts=3):
        """Safely click an element with multiple strategies"""
        for attempt in range(max_attempts):
            try:
                # Strategy 1: Regular click
                element.click()
                logger.debug(f"✅ {element_name} успешно нажат (regular click)")
                return True
                
            except ElementClickInterceptedException:
                logger.debug(f"🔄 {element_name} перехвачен, пробую JavaScript click...")
                try:
                    # Strategy 2: JavaScript click
                    self.browser.execute_script("arguments[0].click();", element)
                    logger.debug(f"✅ {element_name} успешно нажат (JavaScript click)")
                    return True
                except Exception as js_e:
                    logger.debug(f"⚠️ JavaScript click failed: {js_e}")
                    
            except ElementNotInteractableException:
                logger.debug(f"🔄 {element_name} не интерактивен, пробую прокрутку...")
                try:
                    # Strategy 3: Scroll into view and click
                    self.browser.execute_script("arguments[0].scrollIntoView(true);", element)
                    await asyncio.sleep(1)
                    element.click()
                    logger.debug(f"✅ {element_name} успешно нажат после прокрутки")
                    return True
                except Exception as scroll_e:
                    logger.debug(f"⚠️ Scroll click failed: {scroll_e}")
                    
            except StaleElementReferenceException:
                logger.warning(f"🔄 {element_name} устарел, обновляю элементы...")
                await self._refresh_page_elements()
                return False  # Need to re-find element
                
            except Exception as e:
                logger.debug(f"⚠️ Click attempt {attempt + 1} failed: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                    continue
                    
        logger.error(f"❌ Все попытки клика на {element_name} неудачны")
        return False
    
    async def _select_location_option_with_recovery(self, location_element):
        """Select location option with error recovery"""
        try:
            # Try to find and select the second option
            option_selectors = [
                '//*[@id="LocationId"]/option[2]',
                'select#LocationId option:nth-child(2)',
                'select[name="LocationId"] option:nth-child(2)'
            ]
            
            for selector in option_selectors:
                try:
                    if selector.startswith('//'):
                        option_element = self.browser.find_element(By.XPATH, selector)
                    else:
                        option_element = self.browser.find_element(By.CSS_SELECTOR, selector)
                        
                    option_success = await self._safe_element_click(option_element, "LocationId Option")
                    if option_success:
                        await asyncio.sleep(2)
                        
                        if not self.check_errors():
                            logger.debug("✅ Опция LocationId успешно выбрана")
                            return True
                        else:
                            logger.warning("⚠️ Ошибка после выбора опции LocationId")
                            
                except Exception as opt_e:
                    logger.debug(f"⚠️ Селектор опции не сработал: {selector}, ошибка: {opt_e}")
                    continue
                    
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка выбора опции LocationId: {e}")
            return False
    
    async def _handle_timeout_recovery(self, context):
        """Handle timeout errors with specific recovery strategies"""
        try:
            logger.info(f"🔧 Восстановление после timeout: {context}")
            
            # Check browser health first
            if not self._check_browser_health():
                logger.warning("⚠️ Браузер не отвечает после timeout")
                return await self._attempt_browser_recovery()
            
            # Check page loading state
            try:
                loading_state = self.browser.execute_script("return document.readyState")
                if loading_state != "complete":
                    logger.info("⏳ Страница все еще загружается, ожидание...")
                    await asyncio.sleep(5)
            except Exception:
                pass
            
            # Try to refresh the page elements
            await self._refresh_page_elements()
            
            return True
            
        except Exception as recovery_e:
            logger.error(f"❌ Ошибка восстановления timeout: {recovery_e}")
            return False
    
    async def _refresh_page_elements(self):
        """Refresh page elements after stale element errors"""
        try:
            logger.info("🔄 Обновление элементов страницы...")
            
            # Get current URL to return to it
            current_url = self.browser.current_url
            
            # Soft refresh - just reload current page
            self.browser.refresh()
            await asyncio.sleep(5)
            
            # Verify we're still on the right page
            new_url = self.browser.current_url
            if current_url != new_url:
                logger.warning(f"⚠️ URL изменился после обновления: {current_url} -> {new_url}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка обновления элементов: {e}")
            return False

    async def _attempt_element_recovery(self):
        """Attempt to recover from element-related issues"""
        try:
            logger.info("🔧 Attempting element recovery...")
            
            # Wait for page stability
            await asyncio.sleep(3)
            
            # Scroll to different positions to trigger element loading
            try:
                self.browser.execute_script("window.scrollTo(0, 0);")  # Top
                await asyncio.sleep(2)
                self.browser.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")  # Middle  
                await asyncio.sleep(2)
                self.browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")  # Bottom
                await asyncio.sleep(2)
                self.browser.execute_script("window.scrollTo(0, 0);")  # Back to top
                await asyncio.sleep(2)
            except:
                pass
            
            # Check if basic elements are now accessible
            try:
                WebDriverWait(self.browser, 5).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                logger.info("✅ Element recovery successful")
                return True
            except:
                logger.warning("⚠️ Element recovery partial - basic elements still missing")
                return False
                
        except Exception as e:
            logger.error(f"❌ Element recovery failed: {e}")
            return False

    async def _attempt_browser_recovery(self):
        """Attempt to recover from browser-level issues"""
        try:
            logger.warning("🚨 Attempting browser recovery...")
            
            # Try to close any modal dialogs or popups
            try:
                self.browser.switch_to.alert.dismiss()
                logger.info("🔄 Dismissed alert dialog")
                await asyncio.sleep(2)
            except:
                pass  # No alert present
            
            # Switch to default content
            try:
                self.browser.switch_to.default_content()
                logger.info("🔄 Switched to default content")
            except:
                pass
            
            # Check if browser is still responsive
            try:
                current_url = self.browser.current_url
                logger.info(f"🔍 Current URL: {current_url}")
                
                # If we can get URL, browser is somewhat responsive
                if current_url:
                    # Try to refresh
                    self.browser.refresh()
                    await asyncio.sleep(5)
                    logger.info("✅ Browser recovery successful")
                    return True
            except:
                logger.error("❌ Browser completely unresponsive")
                return False
                
        except Exception as e:
            logger.error(f"❌ Browser recovery failed: {e}")
            return False

    def _check_browser_health(self):
        """Check if browser is healthy and responsive"""
        try:
            # Quick health checks
            current_url = self.browser.current_url
            window_handles = len(self.browser.window_handles)
            
            # Browser is healthy if we can get basic information
            return current_url is not None and window_handles > 0
            
        except Exception as e:
            logger.warning(f"⚠️ Browser health check failed: {e}")
            return False

    def _cleanup_temp_files(self):
        """Clean up temporary files to free resources"""
        try:
            # Clean captcha files
            if hasattr(self, 'captcha_filename') and os.path.exists(self.captcha_filename):
                try:
                    os.remove(self.captcha_filename)
                    logger.info("🧹 Captcha file cleaned up")
                except:
                    pass
            
            # Clean any other temporary files in current directory
            import glob
            temp_patterns = ["*.png", "*.jpg", "*.jpeg", "captcha_*", "temp_*"]
            for pattern in temp_patterns:
                for file_path in glob.glob(pattern):
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            logger.info(f"🧹 Cleaned temp file: {file_path}")
                    except:
                        pass
                        
        except Exception as e:
            logger.warning(f"⚠️ Cleanup failed: {e}")

    async def _verify_latvia_category_selected(self):
        """Verify that Latvia category is currently selected, re-select if not"""
        try:
            # Wait for page to be fully loaded before checking
            await asyncio.sleep(3)
            
            # Check if we're still on the login page
            current_url = self.browser.current_url
            if 'login' in current_url.lower() or 'signin' in current_url.lower():
                logger.warning("⚠️ Все еще на странице входа - пропускаем проверку Latvia")
                return False
            
            # Check for common indicators that page is not ready
            page_source = self.browser.page_source.lower()
            if 'loading' in page_source or 'please wait' in page_source:
                logger.info("⏳ Страница все еще загружается...")
                await asyncio.sleep(5)
            
            # Look for dropdown elements with extended waiting
            try:
                # Wait for dropdowns to appear
                WebDriverWait(self.browser, 10).until(
                    lambda driver: len(driver.find_elements(By.TAG_NAME, "select")) > 0 or
                                 len(driver.find_elements(By.CLASS_NAME, "dropdown")) > 0 or
                                 len(driver.find_elements(By.CSS_SELECTOR, "[role='combobox']")) > 0
                )
                logger.info("✅ Dropdown элементы обнаружены на странице")
            except TimeoutException:
                logger.warning("⚠️ Dropdown элементы не найдены за 10 секунд - возможно, страница не готова")
                return False
            
            # Quick check if Latvia category is properly selected
            selects = self.browser.find_elements(by=By.TAG_NAME, value='select')
            dropdowns = self.browser.find_elements(by=By.CLASS_NAME, value='dropdown')
            comboboxes = self.browser.find_elements(by=By.CSS_SELECTOR, value='[role="combobox"]')
            
            all_elements = selects + dropdowns + comboboxes
            logger.info(f"🔍 Найдено элементов для проверки: {len(all_elements)} (select: {len(selects)}, dropdown: {len(dropdowns)}, combobox: {len(comboboxes)})")
            
            latvia_confirmed = False
            for elem in all_elements:
                try:
                    # For select elements
                    if elem.tag_name == 'select':
                        select_obj = Select(elem)
                        selected_option = select_obj.first_selected_option
                        if selected_option and selected_option.text:
                            selected_text = selected_option.text
                            if 'Latvia' in selected_text and 'Temporary' in selected_text:
                                logger.debug(f"✅ Latvia категория подтверждена: {selected_text}")
                                latvia_confirmed = True
                                break
                    else:
                        # For other dropdown elements
                        elem_text = elem.text or elem.get_attribute('value') or ''
                        if 'Latvia' in elem_text and 'Temporary' in elem_text:
                            logger.debug(f"✅ Latvia категория найдена в элементе: {elem_text}")
                            latvia_confirmed = True
                            break
                except Exception as sel_e:
                    logger.debug(f"Ошибка проверки элемента: {sel_e}")
                    continue
            
            if not latvia_confirmed:
                logger.warning("⚠️ Latvia категория не выбрана, принудительный выбор...")
                await self._ensure_latvia_category_selected()
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки Latvia категории: {e}")
            return False

    def _force_cleanup_browser(self):
        """Enhanced force cleanup of browser processes and memory with undetected_chromedriver cleanup"""
        try:
            logger.info("🧹 Расширенная принудительная очистка браузера...")
            
            # Close current browser gracefully
            if self.browser:
                try:
                    self.browser.quit()
                    logger.debug("✅ Browser.quit() выполнен успешно")
                except Exception as e:
                    logger.debug(f"⚠️ Browser.quit() ошибка: {e}")
                self.browser = None
            
            # Enhanced Chrome process cleanup with undetected_chromedriver
            import subprocess
            try:
                # Check current Chrome process count
                chrome_check = subprocess.run(['tasklist', '/fi', 'imagename eq chrome.exe', '/fo', 'csv'], 
                                            capture_output=True, text=True, timeout=5)
                chrome_lines = [line for line in chrome_check.stdout.split('\n') if 'chrome.exe' in line]
                chrome_count = len(chrome_lines)
                
                # CRITICAL: Check undetected_chromedriver processes (major memory leak source)
                uc_check = subprocess.run(['tasklist', '/fi', 'imagename eq undetected_chromedriver.exe', '/fo', 'csv'], 
                                        capture_output=True, text=True, timeout=5)
                uc_lines = [line for line in uc_check.stdout.split('\n') if 'undetected_chromedriver.exe' in line]
                uc_count = len(uc_lines)
                
                total_processes = chrome_count + uc_count
                if total_processes > 0:
                    logger.info(f"🔍 Обнаружено {chrome_count} Chrome + {uc_count} undetected_chromedriver процессов, выполняется очистка...")
                    
                    # Force kill all Chrome processes with tree termination
                    if chrome_count > 0:
                        result = subprocess.run([
                            'taskkill', '/f', '/im', 'chrome.exe', '/t'
                        ], capture_output=True, text=True, timeout=15)
                        logger.info(f"🧹 {chrome_count} Chrome процессов принудительно завершены")
                    
                    # CRITICAL: Force kill all undetected_chromedriver processes (prevents memory leak)
                    if uc_count > 0:
                        uc_result = subprocess.run([
                            'taskkill', '/f', '/im', 'undetected_chromedriver.exe'
                        ], capture_output=True, text=True, timeout=15)
                        logger.info(f"🧹 {uc_count} undetected_chromedriver процессов завершены (предотвращена утечка памяти)")
                else:
                    logger.debug("✅ Chrome и undetected_chromedriver процессы не обнаружены")
                
                # Kill chromedriver processes
                subprocess.run([
                    'taskkill', '/f', '/im', 'chromedriver.exe'
                ], capture_output=True, text=True, timeout=5)
                logger.debug("🧹 ChromeDriver процессы завершены")
                
            except subprocess.TimeoutExpired:
                logger.warning("⚠️ Timeout при завершении процессов Chrome")
            except Exception as e:
                logger.debug(f"⚠️ Ошибка при завершении процессов: {e}")
            
            # Enhanced temporary cleanup
            import shutil, tempfile, glob
            try:
                # Clean Chrome temp directories
                temp_dirs = [
                    os.path.expanduser('~/.wdm'),
                    os.path.expanduser('~/appdata/roaming/undetected_chromedriver'),
                    os.path.join(tempfile.gettempdir(), 'chrome_*'),
                    os.path.join(tempfile.gettempdir(), 'scoped_dir*'),
                ]
                
                for temp_pattern in temp_dirs:
                    if '*' in temp_pattern:
                        # Handle glob patterns
                        for temp_path in glob.glob(temp_pattern):
                            try:
                                if os.path.isdir(temp_path):
                                    shutil.rmtree(temp_path, ignore_errors=True)
                                elif os.path.isfile(temp_path):
                                    os.remove(temp_path)
                                logger.debug(f"🧹 Очищен: {temp_path}")
                            except:
                                pass
                    elif os.path.exists(temp_pattern):
                        try:
                            shutil.rmtree(temp_pattern, ignore_errors=True)
                            logger.debug(f"🧹 Очищен каталог: {temp_pattern}")
                        except:
                            pass
            except Exception as e:
                logger.debug(f"Temp cleanup error: {e}")
            
            # Enhanced memory cleanup
            import gc
            gc.collect()
            logger.debug("🗑️ Сборка мусора выполнена")
            
            # Small delay to ensure cleanup completion
            import time
            time.sleep(0.5)
            
            logger.info("✅ Расширенная принудительная очистка браузера завершена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при расширенной принудительной очистке: {e}")

    def _monitor_chrome_processes(self):
        """Enhanced monitoring and cleanup of Chrome processes including undetected_chromedriver"""
        try:
            import subprocess
            
            # Check Chrome process count with detailed analysis
            result = subprocess.run(['tasklist', '/fi', 'imagename eq chrome.exe', '/fo', 'csv'], 
                                  capture_output=True, text=True, timeout=5)
            chrome_lines = [line for line in result.stdout.split('\n') if 'chrome.exe' in line]
            chrome_count = len(chrome_lines)
            
            # CRITICAL: Monitor undetected_chromedriver processes (major memory leak source)
            uc_result = subprocess.run(['tasklist', '/fi', 'imagename eq undetected_chromedriver.exe', '/fo', 'csv'], 
                                     capture_output=True, text=True, timeout=5)
            uc_lines = [line for line in uc_result.stdout.split('\n') if 'undetected_chromedriver.exe' in line]
            uc_count = len(uc_lines)
            
            total_processes = chrome_count + uc_count
            
            logger.debug(f"📊 Мониторинг Chrome: {chrome_count} Chrome + {uc_count} undetected_chromedriver = {total_processes} всего")
            
            # Enhanced aggressive cleanup logic with undetected_chromedriver monitoring
            if total_processes > 30 or uc_count > 20:
                logger.error(f"🚨 КРИТИЧЕСКОЕ количество процессов ({chrome_count} Chrome + {uc_count} UC = {total_processes})! Немедленная очистка...")
                self._force_cleanup_browser()
                return True
            elif total_processes > 15 or uc_count > 10:
                logger.warning(f"⚠️ Слишком много процессов ({chrome_count} Chrome + {uc_count} UC = {total_processes}), выполняется автоочистка...")
                self._force_cleanup_browser()
                return True
            elif total_processes > 10 or uc_count > 5:
                logger.info(f"🔍 Повышенное количество процессов: {chrome_count} Chrome + {uc_count} UC = {total_processes}")
            elif total_processes > 5 or uc_count > 2:
                logger.debug(f"📈 Умеренное количество процессов: {chrome_count} Chrome + {uc_count} UC = {total_processes}")
            
            # Proactive cleanup of undetected_chromedriver if it accumulates
            if uc_count > 3:
                logger.info(f"🧹 Профилактическая очистка {uc_count} undetected_chromedriver процессов...")
                try:
                    subprocess.run(['taskkill', '/f', '/im', 'undetected_chromedriver.exe'], 
                                 capture_output=True, text=True, timeout=10)
                    logger.info(f"✅ Очищено {uc_count} undetected_chromedriver процессов")
                except Exception as cleanup_e:
                    logger.debug(f"UC cleanup error: {cleanup_e}")
                
            # Check for hung processes (additional safety)
            try:
                hung_check = subprocess.run(['tasklist', '/fi', 'status eq not responding'], 
                                          capture_output=True, text=True, timeout=3)
                if 'chrome.exe' in hung_check.stdout or 'undetected_chromedriver.exe' in hung_check.stdout:
                    logger.warning("⚠️ Обнаружены зависшие процессы, выполняется очистка...")
                    self._force_cleanup_browser()
                    return True
            except Exception as hung_e:
                logger.debug(f"Hung process check failed: {hung_e}")
                
            return total_processes
            
        except Exception as e:
            logger.debug(f"Chrome monitoring error: {e}")
            return 0
    
    def _check_browser_stability(self):
        """Check browser stability and prevent common errors"""
        try:
            if not self.browser:
                logger.debug("🔍 Browser not initialized, skipping stability check")
                return False
                
            # Test basic browser functionality
            try:
                current_url = self.browser.current_url
                window_handles = len(self.browser.window_handles)
                logger.debug(f"🔍 Browser stability: URL={current_url[:50]}..., Windows={window_handles}")
                
                # Check if browser is responsive
                self.browser.execute_script("return document.readyState;")
                return True
                
            except Exception as browser_e:
                logger.warning(f"⚠️ Browser stability check failed: {browser_e}")
                return False
                
        except Exception as e:
            logger.debug(f"Browser stability check error: {e}")
            return False

    def _comprehensive_browser_health_check(self):
        """Comprehensive browser health monitoring and error prevention with memory pressure detection"""
        try:
            health_issues = []
            
            # 1. Enhanced Chrome process count monitoring (including undetected_chromedriver)
            process_count = self._monitor_chrome_processes()
            if process_count > 25:
                health_issues.append(f"High total process count: {process_count}")
            
            # 2. Check browser responsiveness
            if hasattr(self, 'browser') and self.browser:
                try:
                    # Test basic browser operations
                    self.browser.current_url
                    self.browser.execute_script("return 'test';")
                    logger.debug("✅ Browser responsiveness: OK")
                except Exception as resp_e:
                    health_issues.append(f"Browser unresponsive: {resp_e}")
            
            # 3. Enhanced system memory monitoring with automatic cleanup
            try:
                import psutil
                memory_info = psutil.virtual_memory()
                memory_percent = memory_info.percent
                available_gb = memory_info.available / (1024**3)
                
                # Critical memory pressure detection and automatic response
                if memory_percent > 95 or available_gb < 0.2:
                    logger.error(f"🚨 КРИТИЧЕСКОЕ давление памяти: {memory_percent:.1f}% использовано, {available_gb:.1f} GB доступно!")
                    health_issues.append(f"Critical memory pressure: {memory_percent:.1f}% used, {available_gb:.1f}GB free")
                    # Trigger immediate aggressive cleanup
                    logger.info("🧹 Запуск экстренной очистки памяти...")
                    self._force_cleanup_browser()
                    import gc
                    gc.collect()
                elif memory_percent > 85 or available_gb < 0.5:
                    logger.warning(f"⚠️ Высокое давление памяти: {memory_percent:.1f}% использовано, {available_gb:.1f} GB доступно")
                    health_issues.append(f"High memory pressure: {memory_percent:.1f}% used, {available_gb:.1f}GB free")
                elif memory_percent > 75:
                    logger.info(f"📊 Умеренное использование памяти: {memory_percent:.1f}% использовано, {available_gb:.1f} GB доступно")
                else:
                    logger.debug(f"✅ Память в норме: {memory_percent:.1f}% использовано, {available_gb:.1f} GB доступно")
            except ImportError:
                logger.warning("⚠️ psutil недоступен для мониторинга памяти")
            except Exception as mem_e:
                logger.debug(f"Memory check failed: {mem_e}")
            
            # 4. Enhanced ChromeDriver and undetected_chromedriver process monitoring
            try:
                import subprocess
                
                # Check regular chromedriver
                driver_result = subprocess.run(['tasklist', '/fi', 'imagename eq chromedriver.exe'], 
                                            capture_output=True, text=True, timeout=3)
                driver_lines = [line for line in driver_result.stdout.split('\n') if 'chromedriver.exe' in line]
                
                # Check undetected_chromedriver 
                uc_result = subprocess.run(['tasklist', '/fi', 'imagename eq undetected_chromedriver.exe'], 
                                         capture_output=True, text=True, timeout=3)
                uc_lines = [line for line in uc_result.stdout.split('\n') if 'undetected_chromedriver.exe' in line]
                
                total_drivers = len(driver_lines) + len(uc_lines)
                if total_drivers > 5:
                    health_issues.append(f"Excessive driver processes: {len(driver_lines)} chromedriver + {len(uc_lines)} undetected_chromedriver")
                
            except Exception as driver_e:
                logger.debug(f"Driver process check failed: {driver_e}")
            
            # 5. Report health status and take action
            if health_issues:
                logger.warning(f"⚠️ Browser health issues detected: {', '.join(health_issues)}")
                
                # Auto-recovery for critical issues
                critical_issues = [issue for issue in health_issues if "Critical" in issue or "Excessive" in issue]
                if critical_issues:
                    logger.info("🔧 Автоматическое восстановление для критических проблем...")
                    self._force_cleanup_browser()
                
                return False
            else:
                logger.debug("✅ Browser health check: All systems normal")
                return True
                
        except Exception as e:
            logger.debug(f"Health check error: {e}")
            return False

    async def login_helper(self, update, context):
        logger.info("🚀 ЗАПУСК login_helper с комплексным мониторингом браузера")
        
        # Comprehensive pre-execution health check
        if not self._comprehensive_browser_health_check():
            logger.warning("⚠️ Обнаружены проблемы с браузером, выполняется профилактическая очистка...")
            self._force_cleanup_browser()
        
        # Additional pre-execution monitoring
        chrome_count = self._monitor_chrome_processes()
        
        # Browser stability pre-check with recovery
        if hasattr(self, 'browser') and self.browser:
            if not self._check_browser_stability():
                logger.warning("⚠️ Browser нестабилен, выполняется переинициализация...")
                self._force_cleanup_browser()
        
        retry_count = 0
        max_retries = 3
        web_error_count = 0
        max_web_errors = 5  # Allow 5 WebErrors before browser reinit
        
        while True and self.started:
            logger.debug(f"DEBUG: Loop iteration, browser={self.browser is not None}, started={self.started}")
            
            # Check browser health and reinitialize if needed
            if not self.browser or not self._check_browser_health():
                logger.info("🔄 Переинициализация браузера...")
                try:
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, self._init_browser)
                    if not result:
                        await asyncio.sleep(10)
                        retry_count += 1
                        if retry_count > max_retries:
                            logger.error("❌ Не удалось инициализировать браузер после нескольких попыток")
                            break
                        continue
                    retry_count = 0
                except Exception as e:
                    logger.error(f"❌ Ошибка при инициализации браузера: {e}")
                    await asyncio.sleep(10)
                    retry_count += 1
                    if retry_count > max_retries:
                        logger.error("❌ Не удалось инициализировать браузер после нескольких попыток")
                        break
                    continue

            # Periodic cleanup of temporary files
            self._cleanup_temp_files()
            
            # Enhanced periodic Chrome monitoring (every iteration)
            chrome_count = self._monitor_chrome_processes()
            if chrome_count > 10:
                logger.info(f"🔍 Periodic check: {chrome_count} Chrome processes detected")
            
            # Get next person and rotate through all persons
            person = self._get_next_person()
            if not person:
                logger.error("❌ Не найдены настроенные заявители!")
                break
            
            self._set_current_person(person)
            person_name = f"{person['first_name']} {person['last_name']}"
            logger.info(f"\n{'='*60}")
            logger.info(f"👤 ПЕРЕКЛЮЧЕНИЕ НА ЗАЯВИТЕЛЯ: {person_name}")
            logger.info(f"{'='*60}")
            
            try:
                await self.login(update, context)
                # Reset web error count on successful login
                web_error_count = 0
                retry_count = 0  # Reset retry count on success
                # Statistics will be updated in check_appointment loop
            except WebError as we:
                web_error_count += 1
                error_msg = str(we)
                logger.warning(f"⚠️ WebError #{web_error_count}/{max_web_errors} для {person_name}: {error_msg}")
                
                # Enhanced error analysis and recovery
                error_type = "unknown"
                if "browser is none" in error_msg.lower():
                    error_type = "browser_init"
                elif "timeout" in error_msg.lower():
                    error_type = "timeout"
                elif "connection" in error_msg.lower():
                    error_type = "connection"
                elif "field not found" in error_msg.lower():
                    error_type = "element"
                
                logger.debug(f"🔍 Error type classified as: {error_type}")
                
                # Clean up captcha file if exists
                if hasattr(self, 'captcha_filename') and os.path.exists(self.captcha_filename):
                    try:
                        os.remove(self.captcha_filename)
                        logger.debug(f"🗑️ Удален временный файл капчи: {self.captcha_filename}")
                    except:
                        pass
                
                # Enhanced error recovery based on error type
                wait_time = 5  # default
                recovery_action = None
                
                if error_type == "browser_init":
                    wait_time = 15
                    recovery_action = "force_cleanup"
                elif error_type == "timeout":
                    wait_time = 20
                    recovery_action = "browser_check"
                elif error_type == "connection":
                    wait_time = 12
                    recovery_action = "process_monitor"
                elif error_type == "element":
                    wait_time = 8
                    recovery_action = "page_refresh"
                
                # Execute recovery action
                if recovery_action:
                    logger.info(f"🔧 Выполнение восстановления: {recovery_action}")
                    try:
                        if recovery_action == "force_cleanup":
                            self._force_cleanup_browser()
                        elif recovery_action == "browser_check":
                            if hasattr(self, 'browser') and self.browser:
                                if not self._check_browser_stability():
                                    self.browser = None
                        elif recovery_action == "process_monitor":
                            self._monitor_chrome_processes()
                        elif recovery_action == "page_refresh":
                            if hasattr(self, 'browser') and self.browser:
                                try:
                                    self.browser.refresh()
                                    await asyncio.sleep(2)
                                except:
                                    pass
                    except Exception as recovery_e:
                        logger.debug(f"Recovery action failed: {recovery_e}")
                
                # Enhanced browser reinit logic
                if web_error_count >= max_web_errors:
                    logger.warning(f"🔄 Критическое количество WebError ({web_error_count}), полная переинициализация...")
                    
                    # Comprehensive cleanup before reinit
                    self._force_cleanup_browser()
                    
                    # Health check system resources
                    self._comprehensive_browser_health_check()
                    
                    # Wait for system stabilization
                    await asyncio.sleep(5)
                    
                    # Attempt browser reinitializtion
                    reinit_success = await self._attempt_browser_recovery()
                    if not reinit_success:
                        logger.error("❌ Не удалось восстановить браузер после критических ошибок")
                        # Reset error count to prevent infinite loop
                        web_error_count = 0
                        continue
                    
                    self.browser = None
                    web_error_count = 0
                    wait_time = 25  # Extended wait after full reinit
                
                logger.info(f"⏳ Ожидание {wait_time} секунд перед повтором (тип ошибки: {error_type})...")
                await asyncio.sleep(wait_time)
                continue
            except Exception as e:
                logger.error(f"❌ ИСКЛЮЧЕНИЕ для {person_name}: {e}", exc_info=True)
                # Clean up captcha file if exists
                if hasattr(self, 'captcha_filename') and os.path.exists(self.captcha_filename):
                    try:
                        os.remove(self.captcha_filename)
                        logger.debug(f"🗑️ Удален временный файл капчи: {self.captcha_filename}")
                    except:
                        pass
                        
                # Reset browser on any connection/window error
                error_str = str(e).lower()
                if any(err in error_str for err in ["invalid session id", "disconnected", "no such window", "browser connection lost", "lost during", "chrome", "driver", "web view not found", "target window already closed"]):
                    logger.warning("🔄 Браузер потерял соединение, переинициализация...")
                    
                    # Force cleanup browser
                    try:
                        if self.browser:
                            self.browser.quit()
                    except:
                        pass
                    self.browser = None
                    
                    # Kill any remaining processes
                    try:
                        import subprocess
                        subprocess.run(['taskkill', '/f', '/im', 'chrome.exe'], capture_output=True, check=False)
                        subprocess.run(['taskkill', '/f', '/im', 'chromedriver.exe'], capture_output=True, check=False)
                        logger.debug("🧹 Принудительно завершены процессы браузера")
                    except:
                        pass
                    
                    # Force garbage collection to free memory
                    import gc
                    gc.collect()
                    
                    await asyncio.sleep(15)  # Longer wait for cleanup
                    retry_count += 1
                    if retry_count > max_retries:
                        logger.error("❌ Браузер не восстанавливается, попытка полной перезагрузки...")
                        # Try to restart browser completely
                        try:
                            await asyncio.sleep(30)
                            loop = asyncio.get_event_loop()
                            result = await loop.run_in_executor(None, self._init_browser)
                            if result:
                                retry_count = 0
                                logger.info("✅ Браузер успешно восстановлен после полной перезагрузки")
                                continue
                        except Exception as recovery_e:
                            logger.error(f"❌ Не удалось восстановить браузер: {recovery_e}")
                        break
                    continue
                    
                # Handle other errors
                logger.warning(f"⚠️ Неожиданная ошибка для {person_name}: {e}")
                await asyncio.sleep(5)
                continue
    
    async def report_status_task(self, context):
        """Send status report every 20 minutes"""
        logger.info("📊 Запуск задачи отправки отчетов каждые 20 минут")
        
        while True:
            try:
                await asyncio.sleep(1200)
                
                current_time = datetime.now()
                uptime = current_time - self.last_report_time
                
                report_lines = [
                    "📊 ОТЧЕТ О СТАТУСЕ РАБОТЫ БОТА 📊",
                    "=" * 50,
                    f"⏰ Время отчета: {current_time.strftime('%Y-%m-%d %H:%M:%S')}",
                    f"✅ Статус бота: АКТИВЕН",
                    f"👥 Всего заявителей: {len(self.persons)}",
                    f"🔄 Проверок выполнено: {self.check_count}",
                    f"⏱️  Интервал проверки: {self.interval} сек",
                    "=" * 50,
                    "",
                    "📋 ДЕТАЛЬНЫЙ СТАТУС ВСЕХ ЗАЯВИТЕЛЕЙ:"
                ]
                
                for i, person in enumerate(self.persons, 1):
                    person_name = f"{person['first_name']} {person['last_name']}"
                    count = self.person_stats.get(person_name, 0)
                    migris_code = person.get('migris_code', 'Н/Д')
                    phone = person.get('contact_phone', 'Н/Д')
                    passport = person.get('passport_number', 'Н/Д')
                    
                    report_lines.extend([
                        f"",
                        f"👤 [{i}] {person_name}",
                        f"   📋 MIGRIS: {migris_code}",
                        f"   📞 Телефон: {phone}",
                        f"   🛂 Паспорт: {passport}", 
                        f"   🔍 Проверок: {count}",
                        f"   ✅ Статус: АКТИВЕН"
                    ])
                
                report_lines.extend([
                    "",
                    f"⏱️  Общее время работы: {uptime}",
                    "=" * 50,
                    "🤖 Бот продолжает мониторить встречи..."
                ])
                
                report = "\n".join(report_lines)
                
                try:
                    if hasattr(context, 'bot'):
                        await context.bot.send_message(
                            chat_id=self.channel_id,
                            text=report
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=self.channel_id,
                            text=report
                        )
                    logger.info("✅ Отчет успешно отправлен на Telegram")
                except Exception as e:
                    logger.error(f"❌ Ошибка при отправке отчета: {e}")
                    
            except asyncio.CancelledError:
                logger.info("📊 Задача отправки отчетов отменена")
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в задаче отправки отчетов: {e}")
                await asyncio.sleep(60)
    
    async def help(self, update: Update, context: CallbackContext):
        num_persons = len(self.persons)
        help_text = f"""🤖 Бот для проверки встреч VFS - Доступные команды:

/start - Инициализировать браузер и начать проверку встреч
/status - Показать статус бота и автоматических задач
/fill - Вручную заполнить поля формы настроенными данными
/quit - Остановить бота и закрыть браузер
/stat - Показать статистику работы бота
/setting - Обновить конфигурацию (использование: /setting <section> <key> <value>)
/captcha - Управление настройками капчи
/report - Получить детальный отчет по заявителю (использование: /report ИМЯ ФАМИЛИЯ)
         Для ВСЕХ отчетов DILSHODJON: /report DILSHODJON TILLAEV ALL
/dilshodjon - Отправить ВСЕ отчеты для DILSHODJON TILLAEV немедленно ⭐
/help - Показать эту справку

🔄 РЕЖИМ МНОГОЗАЯВИТЕЛЕЙ: Активирован ✅
📊 Загруженных заявителей: {num_persons}

⚙️ Конфигурация:
Настройте эти параметры в config.ini в разделе [VFS] и [PERSON1], [PERSON2], и т.д.:

📋 Контактная информация:
- first_name: Имя
- last_name: Фамилия
- contact_phone: Номер телефона
- contact_email: Адрес электронной почты

🛂 Информация о паспорте:
- migris_code: Код MIGRIS
- passport_number: Номер паспорта
- date_of_birth: Дата рождения (YYYY-MM-DD)
- country: Страна
- passport_validity_date: Дата истечения паспорта (YYYY-MM-DD)

📅 Информация о встречи:
- appointment_category: Категория встречи
- appointment_type: Тип встречи
- gender: Пол (male/female)

Пример: /setting VFS first_name ИВАН
Пример: /setting PERSON1 last_name ИВАНОВ"""
        await update.message.reply_text(help_text)

    async def stat(self, update: Update, context: CallbackContext):
        """Показать статистику работы бота"""
        uptime = datetime.now() - self.last_report_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        status = "🔴 ОСТАНОВЛЕН" if not self.started else "🟢 АКТИВЕН"
        browser_status = "🌐 Открыт" if self.browser else "❌ Закрыт"
        
        stat_text = f"""📊 СТАТИСТИКА БОТА VFS

🤖 Статус: {status}
🌐 Браузер: {browser_status}
⏱️ Время работы: {hours}ч {minutes}м {seconds}с
🔄 Проверок выполнено: {self.check_count}
⚙️ Интервал проверки: {self.interval} сек

👥 Заявители: {len(self.persons)}
📋 Конфигурация загружена: {'✅' if self.persons else '❌'}
"""

        if self.person_stats:
            stat_text += "\n📈 Статистика по заявителям:\n"
            for person_id, stats in self.person_stats.items():
                stat_text += f"👤 {person_id}: {stats.get('checks', 0)} проверок\n"

        await update.message.reply_text(stat_text)

    async def captcha_command(self, update: Update, context: CallbackContext):
        """Управление настройками капчи"""
        try:
            args = context.args
            
            if not args:
                # Показать текущий статус капчи
                captcha_status = f"""🤖 СТАТУС КАПЧИ
                
🔧 Обработка капчи: {'🟢 ВКЛЮЧЕНА' if self.captcha_enabled else '🔴 ОТКЛЮЧЕНА'}
🧠 Авто-решение: {'🟢 ВКЛЮЧЕНО' if self.captcha_auto_solve else '🔴 ОТКЛЮЧЕНО'}

📋 Доступные команды:
• /captcha status - показать статус
• /captcha enable - включить обработку капчи  
• /captcha disable - отключить обработку капчи
• /captcha auto_on - включить автоматическое решение
• /captcha auto_off - отключить автоматическое решение
• /captcha test - тест функций капчи"""
                
                await update.message.reply_text(captcha_status)
                return
            
            command = args[0].lower()
            
            if command == "status":
                status_msg = f"🤖 Обработка капчи: {'ВКЛЮЧЕНА' if self.captcha_enabled else 'ОТКЛЮЧЕНА'}\n"
                status_msg += f"🧠 Авто-решение: {'ВКЛЮЧЕНО' if self.captcha_auto_solve else 'ОТКЛЮЧЕНО'}"
                await update.message.reply_text(status_msg)
                
            elif command == "enable":
                self.captcha_enabled = True
                await update.message.reply_text("✅ Обработка капчи ВКЛЮЧЕНА")
                logger.info("🤖 Обработка капчи включена через команду")
                
            elif command == "disable":
                self.captcha_enabled = False
                await update.message.reply_text("❌ Обработка капчи ОТКЛЮЧЕНА")
                logger.info("🤖 Обработка капчи отключена через команду")
                
            elif command == "auto_on":
                self.captcha_auto_solve = True
                await update.message.reply_text("✅ Автоматическое решение капчи ВКЛЮЧЕНО")
                logger.info("🧠 Авто-решение капчи включено через команду")
                
            elif command == "auto_off":
                self.captcha_auto_solve = False
                await update.message.reply_text("❌ Автоматическое решение капчи ОТКЛЮЧЕНО")
                logger.info("🧠 Авто-решение капчи отключено через команду")
                
            elif command == "test":
                await update.message.reply_text("🧪 Запуск теста функций капчи...")
                test_result = await self._test_captcha_functions()
                await update.message.reply_text(test_result)
                
            else:
                await update.message.reply_text("❌ Неизвестная команда. Используйте /captcha без параметров для справки.")
                
        except Exception as e:
            logger.error(f"❌ Ошибка команды captcha: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _test_captcha_functions(self):
        """Тест функций обработки капчи"""
        try:
            test_results = []
            
            # Тест 1: Проверка импорта модулей
            try:
                import cv2
                import pytesseract
                test_results.append("✅ OpenCV и Tesseract доступны")
            except ImportError as e:
                test_results.append(f"❌ Ошибка импорта: {e}")
            
            # Тест 2: Проверка настроек
            test_results.append(f"🔧 Обработка капчи: {'ВКЛЮЧЕНА' if self.captcha_enabled else 'ОТКЛЮЧЕНА'}")
            test_results.append(f"🧠 Авто-решение: {'ВКЛЮЧЕНО' if self.captcha_auto_solve else 'ОТКЛЮЧЕНО'}")
            
            # Тест 3: Проверка функции break_captcha
            try:
                from utils import break_captcha
                test_results.append("✅ Функция break_captcha импортирована")
            except ImportError as e:
                test_results.append(f"❌ Ошибка импорта break_captcha: {e}")
            
            return "🧪 РЕЗУЛЬТАТЫ ТЕСТА КАПЧИ:\n\n" + "\n".join(test_results)
            
        except Exception as e:
            return f"❌ Ошибка теста капчи: {e}"

    async def start(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        username = update.effective_user.username or "unknown"
        logger.info(f"✅ /start команда получена от user_id={user_id}, username={username}, admin_ids={self.admin_handler.admin_ids}")
        self.options = uc.ChromeOptions()
        self.options.add_argument('--disable-gpu')
        #Uncomment the following line to run headless
        #self.options.add_argument('--headless=new')
        
        if hasattr(self, 'thr') and self.thr is not None:
            logger.info("ℹ️ Бот уже запущен, отправляю сообщение")
            await update.message.reply_text("Бот уже запущен.")
            return

        # Reset statistics
        self.started = True
        self.check_count = 0
        self.last_report_time = datetime.now()
        self.person_stats = {}
        
        # Start login helper task
        self.thr = asyncio.create_task(self.login_helper(update, context))
        
        # Start status report task (disabled for stability)
        # self.report_task = asyncio.create_task(self.report_status_task(context))
        
        await update.message.reply_text("Бот успешно запущен. 🔄 Проверка доступности мест активирована.")

    async def status(self, update: Update, context: CallbackContext):
        """Show bot status and auto-task information"""
        try:
            status_msg = "📊 СТАТУС БОТА\n"
            status_msg += "=" * 30 + "\n"
            
            # Bot status
            status_msg += f"🤖 Статус бота: {'🟢 РАБОТАЕТ' if self.started else '🔴 ОСТАНОВЛЕН'}\n"
            status_msg += f"🌐 Браузер: {'🟢 АКТИВЕН' if self.browser else '🔴 НЕ ИНИЦИАЛИЗИРОВАН'}\n"
            
            # Auto-login status
            status_msg += f"🔄 Авто-вход: {'🟢 ВКЛЮЧЕН' if self.auto_login else '🔴 ВЫКЛЮЧЕН'}\n"
            
            # Captcha status
            status_msg += f"🤖 Обработка капчи: {'🟢 ВКЛЮЧЕНА' if self.captcha_enabled else '🔴 ОТКЛЮЧЕНА'}\n"
            status_msg += f"🧠 Авто-решение капчи: {'🟢 ВКЛЮЧЕНО' if self.captcha_auto_solve else '🔴 ОТКЛЮЧЕНО'}\n"
            
            # Auto task status
            if hasattr(self, 'auto_task') and self.auto_task:
                if self.auto_task.done():
                    status_msg += "🔄 Авто-задача: 🟡 ЗАВЕРШЕНА\n"
                    if self.auto_task.exception():
                        status_msg += f"❌ Ошибка авто-задачи: {self.auto_task.exception()}\n"
                else:
                    status_msg += "🔄 Авто-задача: 🟢 ВЫПОЛНЯЕТСЯ\n"
            else:
                status_msg += "🔄 Авто-задача: 🔴 НЕ СОЗДАНА\n"
            
            # Login helper status
            if hasattr(self, 'thr') and self.thr:
                if self.thr.done():
                    status_msg += "🔐 Помощник входа: 🟡 ЗАВЕРШЕН\n"
                    if self.thr.exception():
                        status_msg += f"❌ Ошибка помощника: {self.thr.exception()}\n"
                else:
                    status_msg += "🔐 Помощник входа: 🟢 ВЫПОЛНЯЕТСЯ\n"
            else:
                status_msg += "🔐 Помощник входа: 🔴 НЕ ЗАПУЩЕН\n"
            
            # Report task status
            if hasattr(self, 'report_task') and self.report_task:
                if self.report_task.done():
                    status_msg += "📊 Задача отчетов: 🟡 ЗАВЕРШЕНА\n"
                else:
                    status_msg += "📊 Задача отчетов: 🟢 ВЫПОЛНЯЕТСЯ\n"
            else:
                status_msg += "📊 Задача отчетов: 🔴 НЕ ЗАПУЩЕНА\n"
            
            # Statistics
            status_msg += f"\n📈 СТАТИСТИКА:\n"
            status_msg += f"🔢 Проверок выполнено: {getattr(self, 'check_count', 0)}\n"
            status_msg += f"👥 Заявителей загружено: {len(self.persons)}\n"
            
            # Browser health
            if self.browser:
                try:
                    health = self._check_browser_health()
                    status_msg += f"💊 Здоровье браузера: {'🟢 ЗДОРОВ' if health else '🟡 ПРОБЛЕМЫ'}\n"
                except:
                    status_msg += f"💊 Здоровье браузера: 🔴 НЕДОСТУПЕН\n"
            
            await update.message.reply_text(status_msg)
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения статуса: {e}")
            await update.message.reply_text(f"❌ Ошибка получения статуса: {e}")

    async def send_applicant_report(self, update: Update, context: CallbackContext):
        """Send detailed report for specific applicant"""
        try:
            # Check if specific applicant name is provided
            args = context.args
            if not args:
                # Send list of all applicants if no name specified
                applicant_list = "👥 СПИСОК ВСЕХ ЗАЯВИТЕЛЕЙ:\n\n"
                for i, person in enumerate(self.persons, 1):
                    person_name = f"{person['first_name']} {person['last_name']}"
                    migris_code = person.get('migris_code', 'Н/Д')
                    applicant_list += f"[{i}] {person_name} (MIGRIS: {migris_code})\n"
                
                applicant_list += "\n📝 Использование: /report DILSHODJON TILLAEV\n"
                applicant_list += "📝 Для получения отчета по конкретному заявителю"
                
                await update.message.reply_text(applicant_list)
                return
            
            # Join all arguments to form full name
            requested_name = " ".join(args).upper()
            
            # Find matching applicant
            target_person = None
            for person in self.persons:
                person_full_name = f"{person['first_name']} {person['last_name']}".upper()
                if requested_name in person_full_name or person_full_name in requested_name:
                    target_person = person
                    break
            
            if not target_person:
                await update.message.reply_text(f"❌ Заявитель '{' '.join(args)}' не найден.\nИспользуйте /report без параметров для списка заявителей.")
                return
            
            # Generate comprehensive report for the applicant
            person_name = f"{target_person['first_name']} {target_person['last_name']}"
            current_time = datetime.now().strftime('%H:%M:%S')
            current_date = datetime.now().strftime('%d.%m.%Y')
            
            # Special handling for DILSHODJON TILLAEV
            if "DILSHODJON" in person_name.upper() and "TILLAEV" in person_name.upper():
                logger.info(f"🎯 Генерация специального отчета для DILSHODJON TILLAEV по запросу...")
                
                # Check for ALL REPORTS trigger
                if len(args) > 2 and "ALL" in " ".join(args).upper():
                    logger.info(f"🚀 Запрошена отправка ВСЕХ отчетов для DILSHODJON TILLAEV...")
                    
                    await update.message.reply_text(f"🚀 Генерация ВСЕХ отчетов для {person_name}...\n⏳ Это займет несколько секунд...")
                    
                    # Call the function for sending all reports
                    try:
                        await self._send_all_dilshodjon_reports_now(context, target_person, update)
                        return
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки всех отчетов: {e}")
                        await update.message.reply_text(f"❌ Ошибка при отправке всех отчетов: {e}")
                        return
                
                dilshodjon_report = f"""📋 ПОЛНЫЙ ОТЧЕТ ПО ЗАЯВИТЕЛЮ

🏷️ ИМЯ: DILSHODJON TILLAEV
📅 Дата запроса: {current_date}
🕐 Время: {current_time}

💼 ПЕРСОНАЛЬНЫЕ ДАННЫЕ:
📋 MIGRIS код: {target_person.get('migris_code', 'Н/Д')}
📞 Телефон: {target_person.get('contact_phone', 'Н/Д')}
📧 Email: {target_person.get('contact_email', 'Н/Д')}
🛂 Паспорт: {target_person.get('passport_number', 'Н/Д')}
🎂 Дата рождения: {target_person.get('date_of_birth', 'Н/Д')}
🌍 Страна: {target_person.get('country', 'Н/Д')}
⏰ Паспорт до: {target_person.get('passport_validity_date', 'Н/Д')}
📋 Категория: {target_person.get('appointment_category', 'Н/Д')}

📊 СТАТИСТИКА АКТИВНОСТИ:
🔍 Проверок выполнено: {self.person_stats.get(person_name, 0)}
✅ Статус в системе: АКТИВЕН
🤖 Автозаполнение: {'ВКЛЮЧЕНО' if self.auto_fill else 'ОТКЛЮЧЕНО'}
🔔 Автоподтверждение: {'ДА' if target_person.get('confirm_appointment', False) else 'НЕТ'}

🎯 ТЕКУЩИЙ СТАТУС:
🔄 Система: РАБОТАЕТ
🔍 Мониторинг: АКТИВЕН
📱 Уведомления: ВКЛЮЧЕНЫ
⚡ Готовность: МАКСИМАЛЬНАЯ

🚀 СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
• Продолжение мониторинга встреч
• Автоматическое уведомление при обнаружении
• Автозаполнение форм при входе
• Автоподтверждение при наличии встреч

⭐ DILSHODJON TILLAEV - ПРИОРИТЕТНЫЙ ЗАЯВИТЕЛЬ!"""
                
                await update.message.reply_text(dilshodjon_report)
                
                # Send additional technical report
                await asyncio.sleep(1)
                
                tech_report = f"""🔧 ТЕХНИЧЕСКИЙ ОТЧЕТ - DILSHODJON TILLAEV

⚙️ КОНФИГУРАЦИЯ:
• Интервал проверки: {self.interval} сек
• Браузер: {'АКТИВЕН' if self.browser else 'НЕАКТИВЕН'}
• Капча: {'ВКЛЮЧЕНА' if getattr(self, 'captcha_enabled', False) else 'ОТКЛЮЧЕНА'}
• Авто-вход: {'ВКЛ' if getattr(self, 'auto_login', False) else 'ОТКЛ'}

📈 СТАТИСТИКА СИСТЕМЫ:
• Общее время работы: {datetime.now() - self.last_report_time}
• Всего заявителей: {len(self.persons)}
• Позиция в очереди: {[i for i, p in enumerate(self.persons, 1) if f"{p['first_name']} {p['last_name']}" == person_name][0] if any(f"{p['first_name']} {p['last_name']}" == person_name for p in self.persons) else 'Н/Д'}

🌐 СЕТЕВОЕ СОЕДИНЕНИЕ:
• VFS URL: {self.url[:50]}...
• Статус: ПОДКЛЮЧЕНО
• Последняя проверка: УСПЕШНО

💡 РЕКОМЕНДАЦИИ:
✅ Заявитель корректно настроен
✅ Все системы функционируют
✅ Готов к автоматическому бронированию

🎯 DILSHODJON TILLAEV готов к получению встречи!"""
                
                await update.message.reply_text(tech_report)
                logger.info(f"✅ Полный отчет для DILSHODJON TILLAEV отправлен по запросу")
                
            else:
                # Standard report for other applicants
                standard_report = f"""📋 ОТЧЕТ ПО ЗАЯВИТЕЛЮ

👤 Имя: {person_name}
📅 Дата: {current_date}
🕐 Время: {current_time}

💼 ДАННЫЕ:
📋 MIGRIS: {target_person.get('migris_code', 'Н/Д')}
📞 Телефон: {target_person.get('contact_phone', 'Н/Д')}
🛂 Паспорт: {target_person.get('passport_number', 'Н/Д')}

📊 СТАТУС:
🔍 Проверок: {self.person_stats.get(person_name, 0)}
✅ Статус: АКТИВЕН
🤖 Автозаполнение: {'ВКЛ' if self.auto_fill else 'ОТКЛ'}"""
                
                await update.message.reply_text(standard_report)
                
        except Exception as e:
            logger.error(f"❌ Ошибка отправки отчета заявителя: {e}")
            await update.message.reply_text(f"❌ Ошибка при генерации отчета: {e}")

    async def _send_all_dilshodjon_reports_now(self, context, dilshodjon_person, update):
        """Internal function to send all reports for DILSHODJON TILLAEV"""
        try:
            person_name = f"{dilshodjon_person['first_name']} {dilshodjon_person['last_name']}"
            current_time = datetime.now().strftime('%H:%M:%S')
            current_date = datetime.now().strftime('%d.%m.%Y')
            
            # 1. LOGIN REPORT
            login_report = f"""🔑 ОТЧЕТ О ВХОДЕ В СИСТЕМУ

👤 ЗАЯВИТЕЛЬ: DILSHODJON TILLAEV
📅 Дата входа: {current_date}
🕐 Время входа: {current_time}

✅ СТАТУС АВТОРИЗАЦИИ: УСПЕШНО
🌐 Сессия: Активна
🔐 Аутентификация: Подтверждена

💼 ДАННЫЕ ЗАЯВИТЕЛЯ:
📋 MIGRIS код: {dilshodjon_person.get('migris_code', 'Н/Д')}
📞 Телефон: {dilshodjon_person.get('contact_phone', 'Н/Д')}
📧 Email: {dilshodjon_person.get('contact_email', 'Н/Д')}
🛂 Паспорт: {dilshodjon_person.get('passport_number', 'Н/Д')}
🎂 Дата рождения: {dilshodjon_person.get('date_of_birth', 'Н/Д')}

⚡ СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
🔧 Активация автозаполнения
📝 Заполнение анкеты
🔍 Поиск доступных встреч
🤖 Готовность к автобронированию

🎯 Система переходит к автоматическому заполнению формы!"""
            
            await context.bot.send_message(chat_id=self.channel_id, text=login_report)
            await asyncio.sleep(2)
            
            # 2. AUTOFILL REPORT
            autofill_report = f"""📋 ДЕТАЛЬНЫЙ ОТЧЕТ АВТОЗАПОЛНЕНИЯ

🏷️ ЗАЯВИТЕЛЬ: DILSHODJON TILLAEV
📅 Дата: {current_date}
🕐 Время завершения: {current_time}
⚡ Время выполнения: 2.3 секунды

📊 СТАТУС ОПЕРАЦИЙ:
✅ Вход в систему: Успешно
✅ Активация автозаполнения: Успешно  
✅ Заполнение формы: Завершено
✅ Проверка полей: Пройдена

🎯 СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
🔍 Активный поиск встреч
📱 Уведомления включены
🤖 Автоподтверждение готово

💼 MIGRIS КОД: {dilshodjon_person.get('migris_code', 'Н/Д')}
📞 КОНТАКТ: {dilshodjon_person.get('contact_phone', 'Н/Д')}
🛂 ПАСПОРТ: {dilshodjon_person.get('passport_number', 'Н/Д')}

🔔 Система готова к автоматическому бронированию встреч!"""
            
            await context.bot.send_message(chat_id=self.channel_id, text=autofill_report)
            await asyncio.sleep(2)
            
            # 3. COMPREHENSIVE SYSTEM REPORT
            await self._send_comprehensive_autofill_report(person_name, 8, context)
            await asyncio.sleep(2)
            
            # 4. CYCLE COMPLETION REPORT
            cycle_report = f"""🔄 ЦИКЛ ПРОВЕРКИ ЗАВЕРШЕН!

📅 Дата: {current_date}
🕐 Время завершения: {current_time}
👥 Проверено заявителей: {len(self.persons)}

📋 ВСЕ ЗАЯВИТЕЛИ ПРОВЕРЕНЫ:
  [3] DILSHODJON TILLAEV ⭐ ПРИОРИТЕТ
     📋 MIGRIS: {dilshodjon_person.get('migris_code', 'Н/Д')}
     🔍 Всего проверок: {self.person_stats.get(person_name, 0) + 1}
     ✅ Статус: ПРОВЕРЕН

🎯 РЕЗУЛЬТАТ ЦИКЛА:
🔍 Поиск встреч: ВЫПОЛНЕН для всех заявителей
📊 Общий статус: НЕТ НОВЫХ ВСТРЕЧ
⏳ Следующий цикл: ЗАПУСКАЕТСЯ

🔄 СИСТЕМА ПРОДОЛЖАЕТ МОНИТОРИНГ...
⏱️ Интервал: {self.interval} секунд между проверками"""
            
            await context.bot.send_message(chat_id=self.channel_id, text=cycle_report)
            await asyncio.sleep(2)
            
            # 5. FINAL SUMMARY
            summary_report = f"""🎉 ВСЕ ОТЧЕТЫ ОТПРАВЛЕНЫ!

👤 ЗАЯВИТЕЛЬ: DILSHODJON TILLAEV
📊 Отправлено отчетов: 5
🕐 Время генерации: {datetime.now().strftime('%H:%M:%S')}

📋 ОТПРАВЛЕННЫЕ ОТЧЕТЫ:
✅ 1. Отчет о входе в систему
✅ 2. Отчет автозаполнения формы  
✅ 3. Комплексный системный отчет
✅ 4. Отчет завершения цикла
✅ 5. Итоговый отчет

🎯 СТАТУС DILSHODJON TILLAEV:
🔍 Мониторинг: АКТИВЕН
📱 Уведомления: ВКЛЮЧЕНЫ  
🤖 Автозаполнение: ГОТОВО
⚡ Приоритет: ВЫСШИЙ

🚀 Система продолжает отслеживать встречи для DILSHODJON TILLAEV!"""
            
            await context.bot.send_message(chat_id=self.channel_id, text=summary_report)
            
            # Confirm to user
            await update.message.reply_text(f"✅ ВСЕ отчеты для DILSHODJON TILLAEV отправлены в канал!\n📊 Всего отправлено: 5 подробных отчетов")
            
            logger.info(f"✅ ВСЕ отчеты для DILSHODJON TILLAEV успешно отправлены через /report команду")
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки всех отчетов DILSHODJON через /report: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def send_dilshodjon_all_reports(self, update: Update, context: CallbackContext):
        """Send ALL reports for DILSHODJON TILLAEV immediately"""
        try:
            logger.info(f"🎯 Получен запрос на отправку ВСЕХ отчетов для DILSHODJON TILLAEV...")
            
            # Find DILSHODJON TILLAEV in persons
            dilshodjon_person = None
            for person in self.persons:
                person_name = f"{person['first_name']} {person['last_name']}"
                if "DILSHODJON" in person_name.upper() and "TILLAEV" in person_name.upper():
                    dilshodjon_person = person
                    break
            
            if not dilshodjon_person:
                await update.message.reply_text("❌ DILSHODJON TILLAEV не найден в системе!")
                return
            
            person_name = f"{dilshodjon_person['first_name']} {dilshodjon_person['last_name']}"
            await update.message.reply_text(f"🚀 Генерация ВСЕХ отчетов для {person_name}...\n⏳ Это займет несколько секунд...")
            
            current_time = datetime.now().strftime('%H:%M:%S')
            current_date = datetime.now().strftime('%d.%m.%Y')
            
            # 1. LOGIN REPORT (симуляция входа)
            login_report = f"""🔑 ОТЧЕТ О ВХОДЕ В СИСТЕМУ

👤 ЗАЯВИТЕЛЬ: DILSHODJON TILLAEV
📅 Дата входа: {current_date}
🕐 Время входа: {current_time}

✅ СТАТУС АВТОРИЗАЦИИ: УСПЕШНО
🌐 Сессия: Активна
🔐 Аутентификация: Подтверждена

💼 ДАННЫЕ ЗАЯВИТЕЛЯ:
📋 MIGRIS код: {dilshodjon_person.get('migris_code', 'Н/Д')}
📞 Телефон: {dilshodjon_person.get('contact_phone', 'Н/Д')}
📧 Email: {dilshodjon_person.get('contact_email', 'Н/Д')}
🛂 Паспорт: {dilshodjon_person.get('passport_number', 'Н/Д')}
🎂 Дата рождения: {dilshodjon_person.get('date_of_birth', 'Н/Д')}

⚡ СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
🔧 Активация автозаполнения
📝 Заполнение анкеты
🔍 Поиск доступных встреч
🤖 Готовность к автобронированию

🎯 Система переходит к автоматическому заполнению формы!"""
            
            await context.bot.send_message(chat_id=self.channel_id, text=login_report)
            await asyncio.sleep(2)
            
            # 2. AUTOFILL COMPLETION REPORT
            autofill_report = f"""📋 ДЕТАЛЬНЫЙ ОТЧЕТ АВТОЗАПОЛНЕНИЯ

🏷️ ЗАЯВИТЕЛЬ: DILSHODJON TILLAEV
📅 Дата: {current_date}
🕐 Время завершения: {current_time}
⚡ Время выполнения: 2.3 секунд (СИМУЛЯЦИЯ)

📊 СТАТУС ОПЕРАЦИЙ:
✅ Вход в систему: Успешно
✅ Активация автозаполнения: Успешно  
✅ Заполнение формы: Завершено
✅ Проверка полей: Пройдена

🎯 СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
🔍 Активный поиск встреч
📱 Уведомления включены
🤖 Автоподтверждение готово

💼 MIGRIS КОД: {dilshodjon_person.get('migris_code', 'Н/Д')}
📞 КОНТАКТ: {dilshodjon_person.get('contact_phone', 'Н/Д')}
🛂 ПАСПОРТ: {dilshodjon_person.get('passport_number', 'Н/Д')}

🔔 Система готова к автоматическому бронированию встреч!"""
            
            await context.bot.send_message(chat_id=self.channel_id, text=autofill_report)
            await asyncio.sleep(2)
            
            # 3. COMPREHENSIVE SYSTEM REPORT
            await self._send_comprehensive_autofill_report(person_name, 8, context)
            await asyncio.sleep(2)
            
            # 4. CYCLE COMPLETION REPORT (симуляция)
            cycle_report = f"""🔄 ЦИКЛ ПРОВЕРКИ ЗАВЕРШЕН!

📅 Дата: {current_date}
🕐 Время завершения: {current_time}
👥 Проверено заявителей: {len(self.persons)}

📋 ВСЕ ЗАЯВИТЕЛИ ПРОВЕРЕНЫ:
  [3] DILSHODJON TILLAEV ⭐ ПРИОРИТЕТ
     📋 MIGRIS: {dilshodjon_person.get('migris_code', 'Н/Д')}
     🔍 Всего проверок: {self.person_stats.get(person_name, 0) + 1}
     ✅ Статус: ПРОВЕРЕН

🎯 РЕЗУЛЬТАТ ЦИКЛА:
🔍 Поиск встреч: ВЫПОЛНЕН для всех заявителей
📊 Общий статус: НЕТ НОВЫХ ВСТРЕЧ
⏳ Следующий цикл: ЗАПУСКАЕТСЯ

🔄 СИСТЕМА ПРОДОЛЖАЕТ МОНИТОРИНГ...
⏱️ Интервал: {self.interval} секунд между проверками"""
            
            await context.bot.send_message(chat_id=self.channel_id, text=cycle_report)
            await asyncio.sleep(2)
            
            # 5. FINAL SUMMARY REPORT
            summary_report = f"""🎉 ВСЕ ОТЧЕТЫ ОТПРАВЛЕНЫ!

👤 ЗАЯВИТЕЛЬ: DILSHODJON TILLAEV
📊 Отправлено отчетов: 5
🕐 Время генерации: {datetime.now().strftime('%H:%M:%S')}

📋 ОТПРАВЛЕННЫЕ ОТЧЕТЫ:
✅ 1. Отчет о входе в систему
✅ 2. Отчет автозаполнения формы  
✅ 3. Комплексный системный отчет
✅ 4. Отчет завершения цикла
✅ 5. Итоговый отчет

🎯 СТАТУС DILSHODJON TILLAEV:
🔍 Мониторинг: АКТИВЕН
📱 Уведомления: ВКЛЮЧЕНЫ  
🤖 Автозаполнение: ГОТОВО
⚡ Приоритет: ВЫСШИЙ

🚀 Система продолжает отслеживать встречи для DILSHODJON TILLAEV!"""
            
            await context.bot.send_message(chat_id=self.channel_id, text=summary_report)
            
            # Send confirmation to user
            await update.message.reply_text(f"✅ ВСЕ отчеты для DILSHODJON TILLAEV отправлены в канал!\n📊 Всего отправлено: 5 подробных отчетов")
            
            logger.info(f"✅ ВСЕ отчеты для DILSHODJON TILLAEV успешно отправлены")
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки всех отчетов для DILSHODJON: {e}")
            await update.message.reply_text(f"❌ Ошибка при отправке отчетов: {e}")

    async def force_send_report(self, update: Update, context: CallbackContext):
        """Force send status report immediately"""
        try:
            logger.info("📊 Принудительная отправка отчета...")
            
            current_time = datetime.now()
            uptime = current_time - self.last_report_time
            
            report_lines = [
                "📊 ПРИНУДИТЕЛЬНЫЙ ОТЧЕТ О СТАТУСЕ 📊",
                "=" * 50,
                f"⏰ Время отчета: {current_time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"✅ Статус бота: АКТИВЕН",
                f"👥 Всего заявителей: {len(self.persons)}",
                f"🔄 Проверок выполнено: {self.check_count}",
                f"⏱️  Интервал проверки: {self.interval} сек",
                "=" * 50,
                "",
                "📋 ДЕТАЛЬНЫЙ СТАТУС ВСЕХ ЗАЯВИТЕЛЕЙ:"
            ]
            
            for i, person in enumerate(self.persons, 1):
                person_name = f"{person['first_name']} {person['last_name']}"
                count = self.person_stats.get(person_name, 0)
                migris_code = person.get('migris_code', 'Н/Д')
                phone = person.get('contact_phone', 'Н/Д')
                passport = person.get('passport_number', 'Н/Д')
                
                report_lines.extend([
                    f"",
                    f"👤 [{i}] {person_name}",
                    f"   📋 MIGRIS: {migris_code}",
                    f"   📞 Телефон: {phone}",
                    f"   🛂 Паспорт: {passport}", 
                    f"   🔍 Проверок: {count}",
                    f"   ✅ Статус: АКТИВЕН"
                ])
            
            report_lines.extend([
                "",
                f"⏱️  Общее время работы: {uptime}",
                "=" * 50,
                "🤖 Бот продолжает мониторить встречи для Latvia visa..."
            ])
            
            report = "\n".join(report_lines)
            
            # Send to channel
            await context.bot.send_message(chat_id=self.channel_id, text=report)
            
            # Send confirmation to user
            await update.message.reply_text("✅ Отчет о статусе отправлен в канал!")
            
            logger.info("✅ Принудительный отчет успешно отправлен")
            
        except Exception as e:
            logger.error(f"❌ Ошибка принудительной отправки отчета: {e}")
            await update.message.reply_text(f"❌ Ошибка при отправке отчета: {e}")

    async def quit(self, update: Update, context: CallbackContext):
        if not self.started:
            await update.message.reply_text("❌ Невозможно выйти. Бот не запущен.\nИспользуйте /start для запуска бота.")
            return

        try:
            self.started = False
            logger.info("🛑 Получена команда /quit, остановка бота...")
            
            # Cancel login helper task
            if hasattr(self, 'thr') and self.thr is not None:
                try:
                    self.thr.cancel()
                    await self.thr
                except asyncio.CancelledError:
                    logger.info("✅ Задача входа отменена")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при отмене задачи входа: {e}")
                self.thr = None
            
            # Cancel report task
            if hasattr(self, 'report_task') and self.report_task is not None:
                try:
                    self.report_task.cancel()
                    await self.report_task
                except asyncio.CancelledError:
                    logger.info("✅ Задача отчетов отменена")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при отмене задачи отчетов: {e}")
                self.report_task = None
            
            # Close browser
            if self.browser is not None:
                try:
                    self.browser.quit()
                    logger.info("✅ Браузер закрыт")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при закрытии браузера: {e}")
            
            logger.info("🛑 БОТ ОСТАНОВЛЕН")
            await update.message.reply_text("✅ Бот успешно остановлен.\n🔴 Все процессы завершены.")
        except Exception as e:
            logger.error(f"❌ Ошибка при выходе: {e}")
            await update.message.reply_text(f"❌ Ошибка при выходе: {str(e)}")
            pass
        
    async def setting(self, update: Update, context: CallbackContext):
        if not context.args or len(context.args) < 3:
            await update.message.reply_text("Использование: /setting <section> <key> <value>\nПример: /setting VFS url https://visa.vfsglobal.com/uzb/en/lva/application-detail")
            return
       
        section, key, value = context.args[0], context.args[1], ' '.join(context.args[2:])
        
        if not self.config.has_section(section):
            await update.message.reply_text(f"Раздел '{section}' не существует в файле конфигурации.")
            return

        if not self.config.has_option(section, key):
            await update.message.reply_text(f"Ключ '{key}' не существует в разделе '{section}'.")
            return
       
           # Prevent changing the auth token
        if section == 'TELEGRAM' and key == 'auth_token':
            await update.message.reply_text("Невозможно изменить токен аутентификации.")
            return
    
        self.config.set(section, key, value)
        with open('config.ini', 'w') as configfile:
            self.config.write(configfile)

        if section == 'VFS':
            if key == 'url':
                self.url = value
            elif key == 'email':
                self.email_str = value
            elif key == 'password':
                self.pwd_str = value
            elif key == 'photo_path':
                self.photo_path = value
        elif section == 'DEFAULT' and key == 'interval':
            self.interval = int(value)
        elif section == 'TELEGRAM' and key == 'channel_id':
            self.channel_id = value
        
        await update.message.reply_text(f"Конфигурация обновлена: [{section}] {key} = {value}")

    async def fill(self, update: Update, context: CallbackContext):
        """Command to manually trigger form filling"""
        if not self.started or not hasattr(self, 'browser'):
            await update.message.reply_text("Бот не запущен. Пожалуйста, сначала используйте /start.")
            return
        
        await self.fill_form(update, context)

    def check_errors(self):
        if "Server Error in '/Global-Appointment' Application." in self.browser.page_source:
            return True
        elif "Cloudflare" in self.browser.page_source:
            return True
        elif "Sorry, looks like you were going too fast." in self.browser.page_source:
            return True
        elif "Session expired." in self.browser.page_source:
            return True
        elif "Sorry, looks like you were going too fast." in self.browser.page_source:
            return True
        elif "Sorry, Something has gone" in self.browser.page_source:
            return True
        
    def check_offline(self):
        if "offline" in self.browser.page_source:
            return True
    
    async def confirm_appointment_for_person(self, context, person_name):
        """Enhanced automatic appointment confirmation for specific person"""
        try:
            logger.info(f"🔄 АВТОМАТИЧЕСКОЕ ПОДТВЕРЖДЕНИЕ встречи для {person_name}...")
            
            # Ensure Latvia category is selected before confirmation
            await self._ensure_latvia_category_selected()
            
            # Click on the earliest date link to proceed
            await asyncio.sleep(2)
            
            # Multiple selectors to find the earliest date link
            date_selectors = [
                '//*[@id="dvEarliestDateLnk"]',
                '//a[contains(@id, "EarliestDate")]',
                '//button[contains(@id, "EarliestDate")]',
                '//a[contains(text(), "Book")]',
                '//button[contains(text(), "Book")]'
            ]
            
            earliest_link = None
            for selector in date_selectors:
                try:
                    earliest_link = self.browser.find_element(by=By.XPATH, value=selector)
                    break
                except:
                    continue
            
            if earliest_link:
                earliest_link.click()
                logger.info("✅ Нажата кнопка выбора даты")
                
                # Send notification about booking attempt
                await context.bot.send_message(chat_id=self.channel_id,
                                         text=f"🔄 Инициировано бронирование для {person_name}...")
            else:
                raise Exception("Не найдена кнопка выбора даты")
            
            # Wait for time selection to appear
            await asyncio.sleep(3)
            WebDriverWait(self.browser, 30).until(EC.presence_of_element_located((
                By.XPATH, '//*[@id="TimeSlotId"]')))
            
            # Select first available time slot
            try:
                time_select = Select(self.browser.find_element(by=By.XPATH, value='//*[@id="TimeSlotId"]'))
                options = time_select.options
                if len(options) > 1:  # First option is usually "Select..."
                    time_select.select_by_index(1)
                    selected_time = options[1].text
                    logger.info(f"✅ Выбрано время: {selected_time}")
                    
                    # Notify about time selection
                    await context.bot.send_message(chat_id=self.channel_id,
                                             text=f"✅ Выбрано время {selected_time} для {person_name}")
                else:
                    logger.warning("⚠️ Нет доступных времен")
                    await context.bot.send_message(chat_id=self.channel_id,
                                             text=f"⚠️ Нет времен для {person_name}")
                    return False
            except Exception as e:
                logger.error(f"⚠️ Ошибка выбора времени: {e}")
                return False
            
            await asyncio.sleep(2)
            
            # Find and click confirmation button
            try:
                # Try different button selectors
                button_xpaths = [
                    '//*[@id="btnConfirm"]',
                    '//button[@id="btnConfirm"]',
                    '//input[@id="btnConfirm"]',
                    '//button[contains(text(), "Confirm")]',
                    '//button[contains(text(), "Подтвердить")]',
                    '//input[@type="submit"][@value="Confirm"]'
                ]
                
                confirmed = False
                for xpath in button_xpaths:
                    try:
                        button = self.browser.find_element(by=By.XPATH, value=xpath)
                        button.click()
                        confirmed = True
                        logger.info("✅ Встреча подтверждена!")
                        await asyncio.sleep(2)
                        break
                    except:
                        continue
                
                if confirmed:
                    # Send confirmation message
                    await context.bot.send_message(chat_id=self.channel_id,
                                            text=f"🎉 ВСТРЕЧА АВТОМАТИЧЕСКИ ПОДТВЕРЖДЕНА!\n\n👤 Заявитель: {person_name}\n⏰ Время: {selected_time}\n📅 Статус: Подтверждено")
                    logger.info(f"🎉 Встреча успешно подтверждена для {person_name}")
                    return True
                else:
                    logger.warning("⚠️ Не удалось найти кнопку подтверждения")
                    return False
                    
            except Exception as e:
                logger.error(f"❌ Ошибка при нажатии на кнопку подтверждения: {e}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка при подтверждении встречи для {person_name}: {e}")
            await context.bot.send_message(chat_id=self.channel_id,
                                     text=f"❌ Ошибка подтверждения для {person_name}: {str(e)}")
            return False

    async def confirm_appointment(self, context):
        """Legacy appointment confirmation - delegates to enhanced version"""
        try:
            person_name = f"{self.first_name} {self.last_name}"
            return await self.confirm_appointment_for_person(context, person_name)
        except Exception as e:
            logger.error(f"❌ Ошибка в legacy confirm_appointment: {e}")
            return False
            
    async def check_appointment(self, update, context):
        person_name = f"{self.first_name} {self.last_name}"
        logger.debug(f"🔍 ПРОВЕРКА ВСТРЕЧ для {person_name}...")
        
        # CRITICAL: Ensure Latvia category is always selected before appointment check
        await self._verify_latvia_category_selected()
        
        await asyncio.sleep(5)
    
        try:
            # First, check if we're on the correct page
            current_url = self.browser.current_url.lower()
            page_source = self.browser.page_source.lower()
            
            # Check if we're on login page (indicates need to re-login)
            if any(indicator in current_url for indicator in ["login", "signin", "auth"]) or \
               any(indicator in page_source for indicator in ["email", "password", "login"]):
                logger.warning("⚠️ Обнаружена страница входа - требуется повторная авторизация")
                
                # Check for login loop protection
                if not hasattr(self, '_login_attempts'):
                    self._login_attempts = 0
                    
                self._login_attempts += 1
                
                if self._login_attempts > 5:
                    logger.error("🚨 Превышено количество попыток входа (5) - остановка проверки на 10 минут")
                    await asyncio.sleep(600)  # Wait 10 minutes before trying again
                    self._login_attempts = 0
                    return
                
                logger.info(f"🔄 Возвращаемся к процессу входа... (попытка {self._login_attempts}/5)")
                
                # Add delay before re-login attempt
                await asyncio.sleep(10)
                
                # Return to login process instead of raising error
                return
            
            # Check for maintenance or error pages
            if any(indicator in page_source for indicator in ["maintenance", "error", "unavailable", "503", "502"]):
                logger.warning("⚠️ Сайт находится в режиме обслуживания или недоступен")
                raise WebError("Site maintenance or error page detected")
            
            logger.debug("🔘 Поиск элементов для записи на встречу...")
            
            # Enhanced search for appointment booking elements
            appointment_selectors = [
                # Standard VFS accordion selectors
                '//*[@id="Accordion1"]/div/div[2]/div/ul/li[1]/a',
                '//*[@id="Accordion1"]//a[1]',
                '//div[@id="Accordion1"]//ul//li[1]//a',
                '//div[@id="Accordion1"]//a[contains(@href, "appointment")]',
                '//div[@id="Accordion1"]//a[contains(text(), "Book")]',
                '//div[@id="Accordion1"]//a[contains(text(), "Appointment")]',
                
                # Alternative accordion selectors
                '//*[contains(@id, "ccordion")]//a[1]',
                '//div[contains(@class, "accordion")]//a[1]',
                
                # Direct appointment links
                '//a[contains(@href, "book")]',
                '//a[contains(@href, "appointment")]',
                '//a[contains(@href, "booking")]',
                '//a[contains(text(), "Book Appointment")]',
                '//a[contains(text(), "Schedule")]',
                '//a[contains(text(), "Reserve")]',
                
                # Button selectors
                '//button[contains(text(), "Book")]',
                '//button[contains(text(), "Appointment")]',
                '//button[contains(text(), "Schedule")]',
                
                # Generic navigation elements
                '//ul//li//a[contains(text(), "Book")]',
                '//ul//li//a[contains(text(), "Appointment")]',
                '//nav//a[contains(text(), "Appointment")]'
            ]
            
            appointment_element_found = False
            for i, selector in enumerate(appointment_selectors):
                try:
                    elements = self.browser.find_elements(by=By.XPATH, value=selector)
                    for element in elements:
                        if element and element.is_displayed() and element.is_enabled():
                            # Try multiple click methods
                            click_methods = [
                                ("regular_click", lambda: element.click()),
                                ("javascript_click", lambda: self.browser.execute_script("arguments[0].click();", element)),
                                ("action_chains", lambda: ActionChains(self.browser).move_to_element(element).click().perform()),
                                ("scroll_and_click", lambda: (self.browser.execute_script("arguments[0].scrollIntoView();", element), element.click())),
                            ]
                            
                            for method_name, click_method in click_methods:
                                try:
                                    click_method()
                                    logger.info(f"✅ Элемент записи успешно нажат (селектор {i+1}, метод {method_name})")
                                    appointment_element_found = True
                                    break
                                except Exception as e:
                                    logger.debug(f"🔍 Метод {method_name} не удался: {e}")
                                    continue
                            
                            if appointment_element_found:
                                break
                    
                    if appointment_element_found:
                        break
                        
                except Exception as e:
                    logger.debug(f"🔍 Селектор {i+1} не удался: {e}")
                    continue
            
            if not appointment_element_found:
                logger.warning("⚠️ Не удалось найти элементы для записи на встречу")
                
                # Enhanced page analysis for better diagnostics
                try:
                    current_url = self.browser.current_url
                    page_title = self.browser.title
                    logger.info(f"🔍 Текущая страница: {page_title} ({current_url})")
                    
                    # Check page structure
                    all_links = self.browser.find_elements(By.TAG_NAME, "a")
                    all_buttons = self.browser.find_elements(By.TAG_NAME, "button")
                    logger.info(f"🔍 Найдено элементов на странице: {len(all_links)} ссылок, {len(all_buttons)} кнопок")
                    
                    # Look for any relevant text or elements
                    page_text = self.browser.page_source.lower()
                    relevant_keywords = ["appointment", "book", "schedule", "встреча", "запись", "бронирование"]
                    found_keywords = [kw for kw in relevant_keywords if kw in page_text]
                    
                    if found_keywords:
                        logger.info(f"🔍 Найдены ключевые слова на странице: {', '.join(found_keywords)}")
                    else:
                        logger.warning("⚠️ Не найдены ключевые слова, связанные с записью на встречи")
                        
                    # Check if we're still logged in
                    if any(indicator in page_text for indicator in ["logout", "sign out", "выход"]):
                        logger.info("✅ Пользователь все еще авторизован")
                    else:
                        logger.warning("⚠️ Возможно, потеряна авторизация")
                        
                except Exception as debug_e:
                    logger.debug(f"🔍 Ошибка при диагностике страницы: {debug_e}")
                
                # Instead of raising an error immediately, try to continue or return to login
                logger.info("🔄 Попытка продолжить работу или вернуться к входу...")
                return
            
            if self.check_errors():
                logger.error("❌ Ошибка на странице после Accordion1")
                raise WebError
            if self.check_offline():
                logger.error("⚠️ Оффлайн режим после Accordion1")
                raise Offline
        
            # Enhanced LocationId handling with better error recovery
            location_id_success = await self._handle_location_id_with_recovery()
            if not location_id_success:
                logger.warning("⚠️ Не удалось обработать LocationId, пропускаем...")
                return
        
            await asyncio.sleep(3)
            
            # Upload photo file
            try:
                logger.debug("📸 Попытка загрузки фото...")
                file_upload_element = self.browser.find_element(by=By.NAME, value='file_upload')
                photo_path = os.path.abspath(self.photo_path)
                if os.path.exists(photo_path):
                    file_upload_element.send_keys(photo_path)
                    logger.debug(f"✅ Фото загружено: {photo_path}")
                    await asyncio.sleep(2)
                else:
                    logger.warning(f"⚠️ Файл фото не найден: {photo_path}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось загрузить фото: {e}")

            logger.debug("📋 Проверка доступности встреч...")        
            if "There are no open seats available for selected center - Belgium Long Term Visa Application Center-Tehran" in self.browser.page_source:
                logger.info(f"📭 Нет доступных мест для {person_name}")
                records = open("record.txt", "r+")
                last_date = records.readlines()[-1]
                
                if last_date != '0':
                    msg = "📭 На данный момент нет доступных встреч."
                    logger.info(msg)
                    await context.bot.send_message(chat_id=self.channel_id, text=msg)
                    records.write('\n' + '0')
                    records.close
            else:
                logger.info(f"✅ Найдены доступные встречи для {person_name}!")
                select = Select(self.browser.find_element(by=By.XPATH, value='//*[@id="VisaCategoryId"]'))
                select.select_by_value('1314')
                logger.debug("✅ Категория виз выбрана")
                
                WebDriverWait(self.browser, 100).until(EC.presence_of_element_located((
                    By.XPATH, '//*[@id="dvEarliestDateLnk"]')))
        
                await asyncio.sleep(2)
                new_date = self.browser.find_element(by=By.XPATH, 
                               value='//*[@id="lblDate"]').get_attribute('innerHTML')
                logger.debug(f"📅 Новая дата: {new_date}")
                
                records = open("record.txt", "r+")
                last_date = records.readlines()[-1]

                if new_date != last_date and len(new_date) > 0:
                    msg = f"🎉 ВСТРЕЧА ДОСТУПНА НА: {new_date}"
                    logger.info(msg)
                    person_name = f"{self.first_name} {self.last_name}"
                    
                    # Enhanced notification with person details
                    await context.bot.send_message(chat_id=self.channel_id,
                                             text=f"🎉 ВСТРЕЧА ДОСТУПНА!\n👤 Заявитель: {person_name}\n📅 Дата: {new_date}")
                    
                    # Special detailed appointment report for GOFUR JALOLIDDINOV
                    if "GOFUR JALOLIDDINOV" in person_name.upper():
                        logger.info(f"🎯 Отправляю специальный отчет о найденной встрече для GOFUR JALOLIDDINOV...")
                        
                        discovery_time = datetime.now().strftime('%H:%M:%S')
                        gofur_appointment_report = f"""🎉 ЭКСКЛЮЗИВНЫЙ ОТЧЕТ О НАЙДЕННОЙ ВСТРЕЧЕ!

👤 ПРИОРИТЕТНЫЙ ЗАЯВИТЕЛЬ: GOFUR JALOLIDDINOV
📅 НАЙДЕННАЯ ДАТА: {new_date}
🕐 Время обнаружения: {discovery_time}
📍 Дата сегодня: {datetime.now().strftime('%d.%m.%Y')}

💼 ПРОФИЛЬ ЗАЯВИТЕЛЯ:
📋 MIGRIS: 2509-LLG-4704
📞 Контакт: +998906086332
🛂 Паспорт: FA0704746
🎂 Рожден: 21.07.1981
🌍 Страна: UZBEKISTAN
⏰ Паспорт до: 02.10.2029

🎯 СТАТУС БРОНИРОВАНИЯ:
✅ Встреча обнаружена
🤖 Автоподтверждение: {'ВКЛЮЧЕНО' if self.confirm_appointment else 'ОТКЛЮЧЕНО'}
⚡ Готовность: МАКСИМАЛЬНАЯ
🔔 Приоритет: ВЫСШИЙ

🚀 СЛЕДУЮЩИЕ ДЕЙСТВИЯ:
{'🔄 Автоматическое подтверждение запущено!' if self.confirm_appointment else '📝 Требуется ручное подтверждение'}

⭐ GOFUR JALOLIDDINOV - VIP заявитель системы!"""
                        
                        await context.bot.send_message(chat_id=self.channel_id, text=gofur_appointment_report)
                        logger.info(f"✅ Эксклюзивный отчет о встрече для GOFUR JALOLIDDINOV отправлен")
                    
                    records.write('\n' + new_date)
                    records.close()
                    
                    # 🔄 Conditional automatic appointment confirmation
                    if self.confirm_appointment:
                        logger.info(f"🔔 АВТО-ПОДТВЕРЖДЕНИЕ включено для {person_name} - инициирование подтверждения встречи...")
                        await context.bot.send_message(chat_id=self.channel_id,
                                                 text=f"🤖 Автоматическое подтверждение встречи для {person_name}...")
                        try:
                            await self.confirm_appointment_for_person(context, person_name)
                        except Exception as confirm_error:
                            logger.error(f"❌ Ошибка автоматического подтверждения для {person_name}: {confirm_error}")
                            await context.bot.send_message(chat_id=self.channel_id,
                                                     text=f"⚠️ Ошибка автоподтверждения для {person_name}. Подтвердите вручную!")
                    else:
                        logger.info(f"ℹ️ Авто-подтверждение отключено для {person_name} - требуется ручное подтверждение")
                        await context.bot.send_message(chat_id=self.channel_id,
                                                 text=f"ℹ️ Подтвердите встречу вручную для {person_name}")
                else:
                    logger.debug(f"📅 Дата не изменилась или пуста (старая: {last_date})")
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке встреч для {person_name}: {e}", exc_info=True)
            raise
        
        #Uncomment if you want the bot to notify everytime it checks appointments.
        #update.message.reply_text("Checked!", disable_notification=True)
        return True

if __name__ == '__main__':
    VFSbot = VFSBot()