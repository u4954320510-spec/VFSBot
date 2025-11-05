import cv2
import re
import pytesseract
import telegram
import numpy as np
import os
import logging
from datetime import datetime
from configparser import ConfigParser
from telegram import Update
from telegram.ext import filters, CallbackContext
from PIL import Image

# Configure logging
def setup_logger():
    """Setup comprehensive logging with file and console output"""
    logger = logging.getLogger('VFSBot')
    logger.setLevel(logging.INFO)
    
    # Create logs directory if doesn't exist
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    # File handler - timestamped logs (UTF-8 encoding for Unicode support)
    log_file = f'logs/bot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    
    # File handler - bot.log (main log file)
    bot_log_file = 'bot.log'
    bot_log_handler = logging.FileHandler(bot_log_file, encoding='utf-8')
    bot_log_handler.setLevel(logging.INFO)
    
    # Console handler - info level (UTF-8 encoding for Unicode/emoji support)
    import sys
    import io
    console_handler = logging.StreamHandler(sys.stdout)
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        console_handler.stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    console_handler.setLevel(logging.INFO)
    
    # Formatter with timestamp
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    bot_log_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(bot_log_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()

# Load configuration
config = ConfigParser()
config.read('config.ini')

# Set Tesseract path from config if available
if config.has_section('OCR') and config.has_option('OCR', 'tesseract_path'):
    tesseract_path = config.get('OCR', 'tesseract_path')
    if os.path.exists(tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
    else:
        print(f"Предупреждение: путь Tesseract в конфигурации ({tesseract_path}) не существует.")
else:
    # Default path as fallback
    pytesseract.pytesseract.tesseract_cmd = 'C:/Program Files/Tesseract-OCR/tesseract.exe'

class WebError(Exception):
    pass

class Offline(Exception):
    pass

class TesseractNotFoundError(Exception):
    pass

def check_tesseract_installed():
    """Check if Tesseract is installed and accessible."""
    try:
        pytesseract.get_tesseract_version()
        return True
    except pytesseract.TesseractNotFoundError:
        raise TesseractNotFoundError("Tesseract не установлен или не найден в PATH")

class AdminHandler:
    def __init__(self, admin_ids):
        self.admin_ids = admin_ids

    async def unauthorized_access(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        username = update.effective_user.username or "unknown"
        logger.warning(f"🚫 ПОПЫТКА НЕСАНКЦИОНИРОВАННОГО ДОСТУПА: ID={user_id}, Username={username}")
        await update.message.reply_text(f'🚫 Несанкционированный доступ!\nВаш ID: {user_id}\nДобавьте его в admin_ids в config.ini')

    def filter_admin(self):
        return filters.User(user_id=self.admin_ids)

def break_captcha(filename="captcha.png"):
    """
    Process the captcha image and extract text using OCR.
    
    Args:
        filename: Path to the captcha image file
    
    Returns:
        str: The extracted and cleaned captcha text
    """
    try:
        logger.info(f"🔍 КАПЧА: Начало обработки файла - {filename}")
        
        # Check if Tesseract is installed
        check_tesseract_installed()
        logger.debug("✅ Tesseract найден и доступен")
        
        # Check if file exists
        if not os.path.exists(filename):
            logger.error(f"❌ Файл капчи не найден: {filename}")
            raise FileNotFoundError(f"Файл капчи не найден: {filename}")
        
        logger.debug(f"✅ Файл капчи найден: {filename} (размер: {os.path.getsize(filename)} байт)")
        
        # Read and preprocess the image
        image = cv2.imread(filename)
        if image is None:
            logger.error(f"❌ Не удалось прочитать изображение: {filename}")
            raise FileNotFoundError(f"Не удалось прочитать изображение капчи: {filename}")
        
        logger.debug(f"✅ Изображение загружено (размер: {image.shape})")
        logger.debug("🔄 Предварительная обработка: конвертация в серый цвет")
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        logger.debug("🔄 Предварительная обработка: добавление границы")
        image = cv2.copyMakeBorder(image, 5, 5, 5, 5, cv2.BORDER_CONSTANT, value=[250])
        
        logger.debug("🔄 Предварительная обработка: применение фильтра размытия")
        image = cv2.filter2D(image, -1, np.ones((4, 4), np.float32) / 16)

        logger.debug("🔄 Предварительная обработка: морфологические операции")
        se = cv2.getStructuringElement(cv2.MORPH_RECT, (8,8))
        bg = cv2.morphologyEx(image, cv2.MORPH_DILATE, se)
        image = cv2.divide(image, bg, scale=255)
        image = cv2.filter2D(image, -1, np.ones((3, 4), np.float32) / 12)
        image = cv2.threshold(image, 0, 255, cv2.THRESH_OTSU)[1]

        logger.debug("🔄 Предварительная обработка: добавление границы")
        image = cv2.copyMakeBorder(image, 5, 5, 5, 5, cv2.BORDER_CONSTANT, value=[250])

        # Get OCR configuration from config file
        psm_mode = 13  # Default PSM mode
        char_whitelist = "ABCDEFGHIJKLMNPQRSTUVWYZ"  # Default whitelist
        
        if config.has_section('OCR'):
            if config.has_option('OCR', 'psm_mode'):
                psm_mode = config.get('OCR', 'psm_mode')
            if config.has_option('OCR', 'char_whitelist'):
                char_whitelist = config.get('OCR', 'char_whitelist')
        
        logger.debug(f"🔬 OCR конфигурация: PSM={psm_mode}, Whitelist={char_whitelist}")
        
        # Apply OCR to extract text with retry logic for empty results
        ocr_config = f'--psm {psm_mode} -c tessedit_char_whitelist={char_whitelist}'
        captcha = ""
        max_retries = 3
        
        logger.info(f"📖 OCR: Начало распознавания текста (макс. попыток: {max_retries})")
        for attempt in range(max_retries):
            logger.debug(f"  Попытка {attempt + 1}/{max_retries}...")
            try:
                captcha = pytesseract.image_to_string(image, config=ocr_config)
                if captcha and captcha.strip():
                    logger.debug(f"  ✅ Текст распознан: '{captcha.strip()}'")
                    break
                else:
                    logger.debug(f"  ⚠️ Пустой результат, повторная попытка...")
                    # Try different PSM modes on retry
                    if attempt < max_retries - 1:
                        alternative_psm = [6, 7, 8, 13][attempt % 4]
                        ocr_config = f'--psm {alternative_psm} -c tessedit_char_whitelist={char_whitelist}'
                        logger.debug(f"  🔄 Переключение на PSM={alternative_psm}")
            except Exception as ocr_error:
                logger.debug(f"  ❌ Ошибка OCR на попытке {attempt + 1}: {str(ocr_error)}")
                if attempt == max_retries - 1:
                    raise ocr_error
        
        # Clean up the extracted text
        if captcha:
            denoised_captcha = re.sub('[\W_]+', '', captcha).strip()
            if denoised_captcha:
                logger.info(f"✅ КАПЧА РЕШЕНА: '{denoised_captcha}' (длина: {len(denoised_captcha)})")
                return denoised_captcha
            else:
                logger.warning("⚠️ После очистки капча оказалась пустой")
        
        logger.warning("⚠️ Не удалось распознать капчу после всех попыток")
        return ""
    except TesseractNotFoundError as e:
        logger.error(f"❌ Tesseract не найден: {e}")
        raise
    except FileNotFoundError as e:
        logger.error(f"❌ Файл не найден: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка при решении капчи: {str(e)}", exc_info=True)
        return ""


def convert_jpg_to_pdf(jpg_path, pdf_path=None):
    """
    Convert JPG image to PDF file.
    
    Args:
        jpg_path: Path to the JPG image file
        pdf_path: Path to save the PDF file (if None, uses same name with .pdf extension)
    
    Returns:
        str: Path to the generated PDF file or None if failed
    """
    try:
        # Check if JPG file exists
        if not os.path.exists(jpg_path):
            print(f"❌ Файл JPG не найден: {jpg_path}")
            return None
        
        # If PDF path not specified, use same directory with .pdf extension
        if pdf_path is None:
            base_path = os.path.splitext(jpg_path)[0]
            pdf_path = base_path + '.pdf'
        
        # Check if PDF already exists and is valid
        if os.path.exists(pdf_path):
            try:
                # Try to open to verify it's a valid PDF
                from PIL import PdfImagePlugin
                img = Image.open(pdf_path)
                print(f"✅ PDF файл уже существует и валиден: {pdf_path}")
                return pdf_path
            except:
                pass  # PDF invalid or not accessible, recreate it
        
        # Open the JPG image and convert to RGB (in case it's RGBA or other format)
        image = Image.open(jpg_path)
        
        # Convert RGBA to RGB if necessary (PDF doesn't support transparency)
        if image.mode in ('RGBA', 'LA', 'P'):
            # Create white background
            background = Image.new('RGB', image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
            image = background
        else:
            image = image.convert('RGB')
        
        # Save as PDF
        image.save(pdf_path, 'PDF')
        print(f"✅ PDF успешно создан: {pdf_path}")
        return pdf_path
    
    except Exception as e:
        print(f"❌ Ошибка при конвертации JPG в PDF: {str(e)}")
        return None
