# Code Citation - VFSBot Dependencies

This document outlines all external libraries and dependencies used in the VFSBot project, along with their purposes and sources.

## Dependencies Overview

### Core Web Automation
**Selenium** (v4.9.0)
- **Purpose**: Web browser automation framework for automated testing and web scraping
- **Source**: https://www.selenium.dev/
- **Repository**: https://github.com/SeleniumHQ/selenium
- **Usage in VFSBot**: Automates login to VFS Global website, navigates appointment pages, interacts with web elements
- **License**: Apache License 2.0

**undetected-chromedriver** (v3.4.6)
- **Purpose**: Anti-detection Chrome automation library that bypasses detection mechanisms
- **Source**: https://github.com/ultrafunkamsterdam/undetected-chromedriver
- **Usage in VFSBot**: Provides undetected Chrome WebDriver to avoid bot detection on VFS Global website
- **License**: GNU General Public License v3.0

---

### Image Processing & OCR
**OpenCV-Python** (v4.7.0.72)
- **Purpose**: Computer vision library for image processing and analysis
- **Source**: https://opencv.org/
- **Repository**: https://github.com/opencv/opencv-python
- **Usage in VFSBot**: Image preprocessing for CAPTCHA solving, including:
  - Image thresholding
  - Contrast adjustment
  - Noise reduction
  - Image transformation
- **License**: Apache License 2.0

**pytesseract** (v0.3.10)
- **Purpose**: Python wrapper for Tesseract-OCR engine for optical character recognition
- **Source**: https://github.com/madmaze/pytesseract
- **Usage in VFSBot**: OCR recognition of CAPTCHA text from preprocessed images
- **License**: Apache License 2.0
- **External Dependency**: Requires Tesseract OCR binary installation
  - Download: https://github.com/tesseract-ocr/tesseract

**Pillow** (v10.0.0)
- **Purpose**: Python Imaging Library for image processing
- **Source**: https://python-pillow.org/
- **Repository**: https://github.com/python-pillow/Pillow
- **Usage in VFSBot**: Image file I/O operations, image format conversions
- **License**: HPND License

**NumPy** (v1.24.0)
- **Purpose**: Numerical computing library for array and matrix operations
- **Source**: https://numpy.org/
- **Repository**: https://github.com/numpy/numpy
- **Usage in VFSBot**: Array operations for image processing and data manipulation
- **License**: BSD License

---

### Telegram Bot Integration
**python-telegram-bot** (v21.7)
- **Purpose**: Python wrapper for Telegram Bot API
- **Source**: https://python-telegram-bot.org/
- **Repository**: https://github.com/python-telegram-bot/python-telegram-bot
- **Usage in VFSBot**: 
  - Bot command handlers (/start, /quit, /setting, /help)
  - Sending notifications to users
  - Managing admin access
  - Telegram chat integration
- **License**: LGPL-3.0 License

---

## Python Standard Library

The following Python standard library modules are used:

| Module | Purpose |
|--------|---------|
| `asyncio` | Asynchronous I/O for concurrent operations |
| `sys` | System-specific parameters and functions |
| `io` | Core tools for working with streams |
| `os` | Operating system interface (file paths, environment) |
| `logging` | Event logging system for debugging and monitoring |
| `configparser` | Configuration file parsing (config.ini) |
| `datetime` | Date and time handling |
| `re` | Regular expression operations for text parsing |

---

## External System Requirements

### Tesseract OCR
- **Purpose**: Required external dependency for pytesseract OCR functionality
- **Download**: https://github.com/tesseract-ocr/tesseract/wiki/Downloads
- **Installation**: Must be installed separately on the system
- **Configuration**: Path configured in utils.py

### Chrome/Chromium Browser
- **Purpose**: Required for Selenium web automation
- **Download**: https://www.google.com/chrome/

### ChromeDriver
- **Purpose**: WebDriver for Chrome browser automation
- **Download**: https://chromedriver.chromium.org/
- **Installation**: Must be placed in repository root directory

---

## Version Management

All dependencies are pinned to specific versions in `requirements.txt` to ensure reproducibility and stability:

```
selenium==4.9.0
opencv-python==4.7.0.72
pytesseract==0.3.10
python-telegram-bot==21.7
undetected-chromedriver==3.4.6
numpy==1.24.0
Pillow==10.0.0
```

To install all dependencies:
```bash
pip install -r requirements.txt
```

---

## License Compatibility

All dependencies are compatible with open-source projects. The project uses:
- Apache License 2.0 (Selenium, OpenCV)
- GNU GPL v3.0 (undetected-chromedriver)
- LGPL-3.0 (python-telegram-bot)
- BSD License (NumPy)
- HPND License (Pillow)

---

## Security & Maintenance Notes

1. **Regular Updates**: Consider updating dependencies periodically for security patches
2. **Compatibility**: Versions are tested with Python 3.11
3. **Anti-Detection**: undetected-chromedriver may require updates if detection methods change
4. **API Changes**: Monitor python-telegram-bot for API changes in Telegram Bot API

---

*Last Updated: 2024*