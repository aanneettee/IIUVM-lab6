﻿import sys
import os
import ctypes
import logging
import hashlib
from ctypes import c_char_p, c_int, c_void_p, CFUNCTYPE, POINTER
from datetime import datetime
from pathlib import Path
import time
from typing import Optional, Dict, List, Set

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QListWidget, QListWidgetItem, QLabel, 
                             QProgressBar, QLineEdit, QCheckBox, QSlider, 
                             QFileDialog, QMessageBox, QGroupBox)
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QPalette, QColor, QIcon
import pygame

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bluetooth_transfer.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Определение типов callback функций для основной библиотеки
DeviceDiscoveredCallback = CFUNCTYPE(None, c_char_p, c_char_p)
StatusCallback = CFUNCTYPE(None, c_char_p)
ProgressCallback = CFUNCTYPE(None, c_int)
FileCallback = CFUNCTYPE(None, c_char_p)
ScanFinishedCallback = CFUNCTYPE(None)
ConnectedCallback = CFUNCTYPE(None)
DisconnectedCallback = CFUNCTYPE(None)  # Добавлен callback для отключения

# Определение типов callback функций для сервера
ServerStatusCallback = CFUNCTYPE(None, c_char_p)
FileReceivedCallback = CFUNCTYPE(None, c_char_p)
ClientConnectedCallback = CFUNCTYPE(None)
ClientDisconnectedCallback = CFUNCTYPE(None)  # Добавлен callback для отключения клиента

class BluetoothBackend:
    """Класс для взаимодействия с C++ библиотекой"""
    
    def __init__(self):
        # Настройка поиска библиотеки
        self.lib_path = self._find_library("bluetooth_transfer")
        if not self.lib_path:
            raise RuntimeError("Не удалось найти библиотеку bluetooth_transfer.dll")
        
        logger.info(f"Загружаем библиотеку: {self.lib_path}")
        self.lib = ctypes.CDLL(self.lib_path)
        
        # Определение функций C API
        self.lib.createBluetoothTransfer.restype = c_void_p
        self.lib.createBluetoothTransfer.argtypes = []
        
        self.lib.destroyBluetoothTransfer.argtypes = [c_void_p]
        
        self.lib.startDiscovery.argtypes = [c_void_p]
        
        self.lib.connectDevice.argtypes = [c_void_p, c_char_p]
        self.lib.connectDevice.restype = c_int
        
        self.lib.disconnectDevice.argtypes = [c_void_p]  # Добавлена функция отключения
        
        self.lib.setSendFile.argtypes = [c_void_p, c_char_p]
        
        self.lib.sendFileData.argtypes = [c_void_p]
        self.lib.sendFileData.restype = c_int
        
        self.lib.isDeviceConnected.argtypes = [c_void_p]
        self.lib.isDeviceConnected.restype = c_int
        
        self.lib.getLastErrorMessage.argtypes = [c_void_p]
        self.lib.getLastErrorMessage.restype = c_char_p
        
        self.lib.registerCallbacks.argtypes = [
            c_void_p,
            DeviceDiscoveredCallback,
            StatusCallback,
            ProgressCallback,
            FileCallback,
            FileCallback,
            ScanFinishedCallback,
            ConnectedCallback,
            DisconnectedCallback  # Добавлен параметр
        ]
        
        # Создание экземпляра
        logger.info("Создаем экземпляр BluetoothTransfer")
        self.instance = self.lib.createBluetoothTransfer()
        
        # Callback функции
        self._device_discovered_cb = DeviceDiscoveredCallback(self._on_device_discovered)
        self._status_cb = StatusCallback(self._on_status)
        self._progress_cb = ProgressCallback(self._on_progress)
        self._file_received_cb = FileCallback(self._on_file_received)
        self._file_sent_cb = FileCallback(self._on_file_sent)
        self._scan_finished_cb = ScanFinishedCallback(self._on_scan_finished)
        self._connected_cb = ConnectedCallback(self._on_connected)
        self._disconnected_cb = DisconnectedCallback(self._on_disconnected)  # Добавлен callback
        
        # Регистрация callback функций
        self.lib.registerCallbacks(
            self.instance,
            self._device_discovered_cb,
            self._status_cb,
            self._progress_cb,
            self._file_received_cb,
            self._file_sent_cb,
            self._scan_finished_cb,
            self._connected_cb,
            self._disconnected_cb
        )
        
        # Callback для GUI
        self.on_device_discovered = None
        self.on_status = None
        self.on_progress = None
        self.on_file_received = None
        self.on_file_sent = None
        self.on_scan_finished = None
        self.on_connected = None
        self.on_disconnected = None  # Добавлен callback
        
    def _find_library(self, base_name: str) -> Optional[str]:
        """Поиск библиотеки в возможных местах"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Список возможных путей (кросс-платформенный подход)
        possible_paths = [
            # Рядом с исполняемым файлом
            os.path.join(current_dir, f"{base_name}.dll"),
            
            # В подпапках проекта
            os.path.join(current_dir, "lib", f"{base_name}.dll"),
            os.path.join(current_dir, "../lib", f"{base_name}.dll"),
            os.path.join(current_dir, "../../lib", f"{base_name}.dll"),
            
            # В папке сборки (если известна структура проекта)
            os.path.join(current_dir, "x64", "Debug", f"{base_name}.dll"),
            os.path.join(current_dir, "x64", "Release", f"{base_name}.dll"),
            os.path.join(current_dir, "build", f"{base_name}.dll"),
            
            # В текущей директории
            f"./{base_name}.dll",
            
            # Пользовательская директория (можно задать через переменную окружения)
            os.path.join(os.environ.get("BLUETOOTH_LIB_PATH", ""), f"{base_name}.dll")
        ]
        
        # Добавляем абсолютный путь только если он существует
        hardcoded_path = r"D:\3 курс\ИиУВМ\лаба1\x64\Debug\bluetooth_transfer.dll"
        if os.path.exists(hardcoded_path):
            possible_paths.insert(0, hardcoded_path)
        
        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"Найдена библиотека: {path}")
                return path
        
        logger.error(f"Библиотека {base_name}.dll не найдена в следующих местах:")
        for path in possible_paths:
            logger.error(f"  - {path}")
        
        # Создаем информационное сообщение для пользователя
        msg = f"Библиотека {base_name}.dll не найдена.\n\n"
        msg += "Пожалуйста, убедитесь что:\n"
        msg += "1. Библиотека находится в одной из следующих папок:\n"
        for path in possible_paths[:5]:  # Показываем только первые 5 путей
            msg += f"   - {path}\n"
        msg += "2. Вы правильно скомпилировали C++ код\n"
        msg += "3. Вы используете правильную архитектуру (x64 или x86)"
        
        QMessageBox.critical(None, "Ошибка", msg)
        return None
    
    # Callback методы
    def _on_device_discovered(self, name: bytes, address: bytes):
        try:
            if self.on_device_discovered:
                name_str = name.decode('utf-8', errors='ignore')
                address_str = address.decode('utf-8', errors='ignore')
                self.on_device_discovered(name_str, address_str)
        except Exception as e:
            logger.error(f"Ошибка в callback устройства: {e}")
    
    def _on_status(self, message: bytes):
        try:
            if self.on_status:
                message_str = message.decode('utf-8', errors='ignore')
                self.on_status(message_str)
        except Exception as e:
            logger.error(f"Ошибка в callback статуса: {e}")
    
    def _on_progress(self, percent: int):
        try:
            if self.on_progress:
                self.on_progress(percent)
        except Exception as e:
            logger.error(f"Ошибка в callback прогресса: {e}")
    
    def _on_file_received(self, filename: bytes):
        try:
            if self.on_file_received:
                filename_str = filename.decode('utf-8', errors='ignore')
                self.on_file_received(filename_str)
        except Exception as e:
            logger.error(f"Ошибка в callback получения файла: {e}")
    
    def _on_file_sent(self, _: bytes):
        try:
            if self.on_file_sent:
                self.on_file_sent()
        except Exception as e:
            logger.error(f"Ошибка в callback отправки файла: {e}")
    
    def _on_scan_finished(self):
        try:
            if self.on_scan_finished:
                self.on_scan_finished()
        except Exception as e:
            logger.error(f"Ошибка в callback завершения сканирования: {e}")
    
    def _on_connected(self):
        try:
            if self.on_connected:
                self.on_connected()
        except Exception as e:
            logger.error(f"Ошибка в callback подключения: {e}")
    
    def _on_disconnected(self):
        try:
            if self.on_disconnected:
                self.on_disconnected()
        except Exception as e:
            logger.error(f"Ошибка в callback отключения: {e}")
    
    # Public методы
    def start_discovery(self):
        """Запуск сканирования устройств"""
        logger.info("Запуск сканирования Bluetooth устройств")
        self.lib.startDiscovery(self.instance)
    
    def connect_to_device(self, address: str) -> bool:
        """Подключение к устройству по адресу"""
        logger.info(f"Попытка подключения к устройству {address}")
        result = self.lib.connectDevice(self.instance, address.encode('utf-8')) == 1
        logger.info(f"Результат подключения: {'Успешно' if result else 'Неудачно'}")
        return result
    
    def disconnect_device(self):
        """Отключение от устройства"""
        logger.info("Отключение от устройства")
        self.lib.disconnectDevice(self.instance)
    
    def set_file_to_send(self, file_path: str):
        """Установка файла для отправки"""
        if not os.path.exists(file_path):
            logger.error(f"Файл не существует: {file_path}")
            raise FileNotFoundError(f"Файл не существует: {file_path}")
        
        logger.info(f"Установлен файл для отправки: {file_path}")
        self.lib.setSendFile(self.instance, file_path.encode('utf-8'))
    
    def send_file(self) -> bool:
        """Отправка файла"""
        logger.info("Начало отправки файла")
        result = self.lib.sendFileData(self.instance) == 1
        logger.info(f"Результат отправки: {'Успешно' if result else 'Неудачно'}")
        return result
    
    def is_connected(self) -> bool:
        """Проверка подключения"""
        result = self.lib.isDeviceConnected(self.instance) == 1
        return result
    
    def get_last_error(self) -> str:
        """Получение последней ошибки"""
        error_msg = self.lib.getLastErrorMessage(self.instance)
        if error_msg:
            error_str = error_msg.decode('utf-8', errors='ignore')
            logger.error(f"Получена ошибка: {error_str}")
            return error_str
        return "Неизвестная ошибка"
    
    def cleanup(self):
        """Очистка ресурсов"""
        self.lib.cleanupTransfer(self.instance)
    
    def __del__(self):
        """Деструктор"""
        if hasattr(self, 'instance') and self.instance:
            try:
                logger.info("Уничтожение экземпляра BluetoothTransfer")
                self.lib.destroyBluetoothTransfer(self.instance)
                self.instance = None
            except Exception as e:
                logger.error(f"Ошибка при уничтожении экземпляра: {e}")

class ServerBackend:
    """Класс для взаимодействия с серверной библиотекой"""
    
    def __init__(self):
        # Загрузка библиотеки
        self.lib_path = self._find_library("serverthread")
        if not self.lib_path:
            raise RuntimeError("Не удалось найти библиотеку serverthread.dll")
        
        logger.info(f"Загружаем библиотеку сервера: {self.lib_path}")
        self.lib = ctypes.CDLL(self.lib_path)
        
        # Определение функций C API
        self.lib.createServerThread.restype = c_void_p
        self.lib.createServerThread.argtypes = []
        
        self.lib.destroyServerThread.argtypes = [c_void_p]
        
        self.lib.startServer.argtypes = [c_void_p]
        
        self.lib.stopServer.argtypes = [c_void_p]
        
        self.lib.registerServerCallbacks.argtypes = [
            c_void_p,
            ServerStatusCallback,
            FileReceivedCallback,
            ClientConnectedCallback,
            ClientDisconnectedCallback
        ]
        
        # Создание экземпляра
        logger.info("Создаем экземпляр ServerThread")
        self.instance = self.lib.createServerThread()
        
        # Callback функции
        self._status_cb = ServerStatusCallback(self._on_status)
        self._file_received_cb = FileReceivedCallback(self._on_file_received)
        self._client_connected_cb = ClientConnectedCallback(self._on_client_connected)
        self._client_disconnected_cb = ClientDisconnectedCallback(self._on_client_disconnected)
        
        # Регистрация callback функций
        self.lib.registerServerCallbacks(
            self.instance,
            self._status_cb,
            self._file_received_cb,
            self._client_connected_cb,
            self._client_disconnected_cb
        )
        
        # Callback для GUI
        self.on_status = None
        self.on_file_received = None
        self.on_client_connected = None
        self.on_client_disconnected = None
        
    def _find_library(self, base_name: str) -> Optional[str]:
        """Поиск библиотеки в возможных местах"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Список возможных путей (аналогично BluetoothBackend)
        possible_paths = [
            os.path.join(current_dir, f"{base_name}.dll"),
            os.path.join(current_dir, "lib", f"{base_name}.dll"),
            os.path.join(current_dir, "../lib", f"{base_name}.dll"),
            os.path.join(current_dir, "../../lib", f"{base_name}.dll"),
            os.path.join(current_dir, "x64", "Debug", f"{base_name}.dll"),
            os.path.join(current_dir, "x64", "Release", f"{base_name}.dll"),
            f"./{base_name}.dll",
            f"../{base_name}.dll",
        ]
        
        # Добавляем абсолютный путь только если он существует
        hardcoded_path = r"D:\3 курс\ИиУВМ\лаба1\x64\Debug\serverthread.dll"
        if os.path.exists(hardcoded_path):
            possible_paths.insert(0, hardcoded_path)
        
        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"Найдена библиотека сервера: {path}")
                return path
        
        logger.error(f"Библиотека {base_name}.dll не найдена")
        return None
    
    # Callback методы
    def _on_status(self, message: bytes):
        try:
            if self.on_status:
                message_str = message.decode('utf-8', errors='ignore')
                self.on_status(message_str)
        except Exception as e:
            logger.error(f"Ошибка в callback статуса сервера: {e}")
    
    def _on_file_received(self, filename: bytes):
        try:
            if self.on_file_received:
                filename_str = filename.decode('utf-8', errors='ignore')
                self.on_file_received(filename_str)
        except Exception as e:
            logger.error(f"Ошибка в callback получения файла сервером: {e}")
    
    def _on_client_connected(self):
        try:
            if self.on_client_connected:
                self.on_client_connected()
        except Exception as e:
            logger.error(f"Ошибка в callback подключения клиента: {e}")
    
    def _on_client_disconnected(self):
        try:
            if self.on_client_disconnected:
                self.on_client_disconnected()
        except Exception as e:
            logger.error(f"Ошибка в callback отключения клиента: {e}")
    
    # Public методы
    def start(self):
        """Запуск сервера"""
        logger.info("Запуск Bluetooth сервера")
        self.lib.startServer(self.instance)
    
    def stop(self):
        """Остановка сервера"""
        logger.info("Остановка Bluetooth сервера")
        self.lib.stopServer(self.instance)
    
    def __del__(self):
        """Деструктор"""
        if hasattr(self, 'instance') and self.instance:
            try:
                logger.info("Уничтожение экземпляра ServerThread")
                self.lib.destroyServerThread(self.instance)
                self.instance = None
            except Exception as e:
                logger.error(f"Ошибка при уничтожении экземпляра сервера: {e}")

class MusicPlayer:
    """Простой музыкальный плеер на pygame"""
    
    def __init__(self):
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
            self.current_file = None
            self.is_playing = False
            logger.info("Музыкальный плеер инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации музыкального плеера: {e}")
            self.current_file = None
            self.is_playing = False
    
    def play(self, file_path: str) -> bool:
        """Воспроизведение файла"""
        try:
            if not os.path.exists(file_path):
                logger.error(f"Файл не существует: {file_path}")
                return False
            
            if self.current_file != file_path:
                pygame.mixer.music.load(file_path)
                self.current_file = file_path
            
            pygame.mixer.music.play()
            self.is_playing = True
            logger.info(f"Воспроизведение файла: {os.path.basename(file_path)}")
            return True
        except Exception as e:
            logger.error(f"Ошибка воспроизведения: {e}")
            return False
    
    def pause(self):
        """Пауза воспроизведения"""
        if self.is_playing:
            pygame.mixer.music.pause()
            self.is_playing = False
            logger.info("Воспроизведение приостановлено")
    
    def resume(self):
        """Возобновление воспроизведения"""
        if not self.is_playing and self.current_file:
            pygame.mixer.music.unpause()
            self.is_playing = True
            logger.info("Воспроизведение возобновлено")
    
    def stop(self):
        """Остановка воспроизведения"""
        pygame.mixer.music.stop()
        self.is_playing = False
        self.current_file = None
        logger.info("Воспроизведение остановлено")
    
    def set_volume(self, volume: float):
        """Установка громкости (0.0 до 1.0)"""
        pygame.mixer.music.set_volume(max(0.0, min(1.0, volume)))
    
    def is_initialized(self) -> bool:
        """Проверка инициализации плеера"""
        return pygame.mixer.get_init() is not None

class BluetoothGUI(QWidget):
    """Основной графический интерфейс"""
    
    def __init__(self):
        super().__init__()
        
        # Настройка логирования для GUI
        self.logger = logging.getLogger('GUI')
        
        # Создаём таймер здесь, чтобы он был доступен в update_mode
        self.auto_scan_timer = QTimer(self)
        self.auto_scan_timer.timeout.connect(self.on_auto_scan)
        
        # Инициализация бэкендов
        try:
            self.backend = BluetoothBackend()
            self.backend.on_device_discovered = self.on_device_discovered
            self.backend.on_status = self.on_status
            self.backend.on_progress = self.on_progress
            self.backend.on_file_received = self.on_file_received
            self.backend.on_file_sent = self.on_file_sent
            self.backend.on_scan_finished = self.on_scan_finished
            self.backend.on_connected = self.on_connected
            self.backend.on_disconnected = self.on_disconnected
            self.logger.info("Bluetooth бэкенд инициализирован")
        except Exception as e:
            self.logger.error(f"Не удалось загрузить бэкенд Bluetooth: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить бэкенд Bluetooth: {e}")
            sys.exit(1)
        
        try:
            self.server_backend = ServerBackend()
            self.server_backend.on_status = self.on_server_status
            self.server_backend.on_file_received = self.on_server_file_received
            self.server_backend.on_client_connected = self.on_server_client_connected
            self.server_backend.on_client_disconnected = self.on_server_client_disconnected
            self.logger.info("Серверный бэкенд инициализирован")
        except Exception as e:
            self.logger.error(f"Не удалось загрузить серверный бэкенд: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить серверный бэкенд: {e}")
            sys.exit(1)
        
        # Инициализация плеера
        self.player = MusicPlayer()
        
        # Переменные состояния
        self.current_mode = "client"
        self.selected_file = ""
        self.received_files = []
        self.discovered_devices = {}  # Хранение устройств для фильтрации дубликатов
        self.server_started = False
        
        # Настройка интерфейса
        self.init_ui()
        self.setup_styles()
        
        # Автосканирование каждые 30 секунд
        self.auto_scan_timer.start(30000)
        
        # Устанавливаем заголовок
        self.setWindowTitle("🎮 KIM5+ Bluetooth File Transfer - Лабораторная работа 6")
        
    def init_ui(self):
        """Инициализация пользовательского интерфейса"""
        self.setMinimumSize(700, 750)
        
        # Основной layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # Заголовок
        title_label = QLabel("🎮 KIM5+ BLUETOOTH FILE TRANSFER - ЛАБОРАТОРНАЯ РАБОТА 6")
        title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("""
            QLabel {
                color: #00ff00;
                padding: 12px;
                background-color: #1a1a1a;
                border-radius: 8px;
                border: 2px solid #2c3e50;
            }
        """)
        main_layout.addWidget(title_label)
        
        # Панель режима
        mode_layout = QHBoxLayout()
        
        self.mode_label = QLabel("Режим работы:")
        self.mode_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.mode_label.setStyleSheet("color: #cccccc;")
        
        self.mode_switch = QCheckBox("Серверный режим")
        self.mode_switch.setFont(QFont("Arial", 10))
        self.mode_switch.toggled.connect(self.on_mode_changed)
        
        # Кнопка отключения (только в клиентском режиме)
        self.disconnect_button = QPushButton("🔌 Отключиться")
        self.disconnect_button.clicked.connect(self.on_disconnect_clicked)
        self.disconnect_button.setEnabled(False)
        
        mode_layout.addWidget(self.mode_label)
        mode_layout.addWidget(self.mode_switch)
        mode_layout.addStretch()
        mode_layout.addWidget(self.disconnect_button)
        
        main_layout.addLayout(mode_layout)
        
        # Группа для клиентского режима
        self.client_group = QGroupBox("Клиентский режим")
        self.client_group.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        client_layout = QVBoxLayout()
        
        # Кнопки сканирования и подключения
        scan_connect_layout = QHBoxLayout()
        self.scan_button = QPushButton("🔍 Сканировать устройства")
        self.scan_button.clicked.connect(self.on_scan_clicked)
        self.connect_button = QPushButton("🔗 Подключиться")
        self.connect_button.clicked.connect(self.on_connect_clicked)
        
        scan_connect_layout.addWidget(self.scan_button)
        scan_connect_layout.addWidget(self.connect_button)
        
        client_layout.addLayout(scan_connect_layout)
        
        # Список устройств
        devices_label = QLabel("Найденные устройства:")
        devices_label.setStyleSheet("color: #cccccc; font-weight: bold;")
        client_layout.addWidget(devices_label)
        
        self.devices_list = QListWidget()
        self.devices_list.setMinimumHeight(120)
        client_layout.addWidget(self.devices_list)
        
        # Выбор файла
        file_label = QLabel("Выбор файла для отправки:")
        file_label.setStyleSheet("color: #cccccc; font-weight: bold;")
        client_layout.addWidget(file_label)
        
        file_layout = QHBoxLayout()
        self.select_file_button = QPushButton("📁 Выбрать файл")
        self.select_file_button.clicked.connect(self.on_select_file_clicked)
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setReadOnly(True)
        self.send_button = QPushButton("📤 Отправить файл")
        self.send_button.clicked.connect(self.on_send_clicked)
        
        file_layout.addWidget(self.select_file_button)
        file_layout.addWidget(self.file_path_edit, 1)
        file_layout.addWidget(self.send_button)
        
        client_layout.addLayout(file_layout)
        
        # Прогресс бар
        progress_label = QLabel("Прогресс отправки:")
        progress_label.setStyleSheet("color: #cccccc; font-weight: bold;")
        client_layout.addWidget(progress_label)
        self.progress_bar = QProgressBar()
        client_layout.addWidget(self.progress_bar)
        
        self.client_group.setLayout(client_layout)
        main_layout.addWidget(self.client_group)
        
        # Группа для серверного режима
        self.server_group = QGroupBox("Серверный режим")
        self.server_group.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        server_layout = QVBoxLayout()
        
        # Кнопки управления сервером
        server_buttons_layout = QHBoxLayout()
        self.start_server_button = QPushButton("▶ Запустить сервер")
        self.start_server_button.clicked.connect(self.on_start_server_clicked)
        self.stop_server_button = QPushButton("■ Остановить сервер")
        self.stop_server_button.clicked.connect(self.on_stop_server_clicked)
        self.stop_server_button.setEnabled(False)
        
        server_buttons_layout.addWidget(self.start_server_button)
        server_buttons_layout.addWidget(self.stop_server_button)
        server_buttons_layout.addStretch()
        
        server_layout.addLayout(server_buttons_layout)
        
        # Статус сервера
        server_status_label = QLabel("Статус сервера:")
        server_status_label.setStyleSheet("color: #cccccc; font-weight: bold;")
        server_layout.addWidget(server_status_label)
        
        self.server_status_label = QLabel("⏹ Сервер не запущен")
        self.server_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.server_status_label.setStyleSheet("""
            QLabel {
                background-color: #7D3C98;
                color: white;
                padding: 10px;
                border-radius: 6px;
                font-weight: bold;
                border: 1px solid #5D2A7F;
            }
        """)
        server_layout.addWidget(self.server_status_label)
        
        # Список полученных файлов
        received_label = QLabel("Полученные файлы:")
        received_label.setStyleSheet("color: #cccccc; font-weight: bold;")
        server_layout.addWidget(received_label)
        
        self.received_files_list = QListWidget()
        self.received_files_list.setMinimumHeight(120)
        self.received_files_list.itemClicked.connect(self.on_file_selected)
        self.received_files_list.itemDoubleClicked.connect(self.on_file_double_clicked)
        server_layout.addWidget(self.received_files_list)
        
        # Кнопки управления файлами
        file_buttons_layout = QHBoxLayout()
        self.clear_files_button = QPushButton("🗑️ Очистить список")
        self.clear_files_button.clicked.connect(self.on_clear_files_clicked)
        self.open_folder_button = QPushButton("📁 Открыть папку")
        self.open_folder_button.clicked.connect(self.on_open_folder_clicked)
        
        file_buttons_layout.addWidget(self.clear_files_button)
        file_buttons_layout.addWidget(self.open_folder_button)
        file_buttons_layout.addStretch()
        
        server_layout.addLayout(file_buttons_layout)
        
        # Управление воспроизведением
        player_label = QLabel("Управление воспроизведением:")
        player_label.setStyleSheet("color: #cccccc; font-weight: bold;")
        server_layout.addWidget(player_label)
        
        player_layout = QHBoxLayout()
        self.play_button = QPushButton("▶ Воспроизвести")
        self.play_button.clicked.connect(self.on_play_clicked)
        self.stop_button = QPushButton("■ Остановить")
        self.stop_button.clicked.connect(self.on_stop_clicked)
        
        player_layout.addWidget(self.play_button)
        player_layout.addWidget(self.stop_button)
        player_layout.addStretch()
        
        # Громкость
        volume_label = QLabel("Громкость:")
        volume_label.setStyleSheet("color: #cccccc;")
        player_layout.addWidget(volume_label)
        
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        player_layout.addWidget(self.volume_slider)
        
        server_layout.addLayout(player_layout)
        
        self.server_group.setLayout(server_layout)
        main_layout.addWidget(self.server_group)
        
        # Статус бар
        self.status_label = QLabel("✅ Готов к работе")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: #2c3e50;
                color: #00ff00;
                padding: 12px;
                border-radius: 6px;
                font-weight: bold;
                border: 1px solid #34495e;
            }
        """)
        main_layout.addWidget(self.status_label)
        
        # Информация о правах
        admin_info = QLabel("🎮 KIM5+ Bluetooth File Transfer - Для корректной работы требуется права администратора")
        admin_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        admin_info.setStyleSheet("""
            QLabel {
                color: #27AE60;
                font-size: 9px;
                padding: 8px;
                background-color: #1a1a1a;
                border-radius: 4px;
            }
        """)
        main_layout.addWidget(admin_info)
        
        self.setLayout(main_layout)
        
        # Изначально показываем клиентский режим
        self.update_mode()
        
    def setup_styles(self):
        """Настройка стилей интерфейса"""
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a1a;
                color: #ECF0F1;
                font-family: 'Arial';
            }
            
            QGroupBox {
                font-weight: bold;
                border: 2px solid #2c3e50;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 18px;
                background-color: #2c3e50;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px 0 8px;
                color: #00ff00;
            }
            
            QPushButton {
                background-color: #2c3e50;
                color: white;
                border: 2px solid #34495e;
                padding: 10px 18px;
                border-radius: 6px;
                font-weight: bold;
                min-width: 120px;
                font-size: 11px;
            }
            
            QPushButton:hover {
                background-color: #3498DB;
                border-color: #2980B9;
            }
            
            QPushButton:pressed {
                background-color: #1F618D;
                border-color: #154360;
            }
            
            QPushButton:disabled {
                background-color: #7F8C8D;
                border-color: #616A6B;
                color: #BDC3C7;
            }
            
            QListWidget {
                background-color: #34495E;
                border: 2px solid #2c3e50;
                border-radius: 6px;
                color: white;
                font-size: 11px;
                padding: 4px;
            }
            
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #4A6583;
                background-color: #2c3e50;
                margin: 2px;
                border-radius: 4px;
            }
            
            QListWidget::item:selected {
                background-color: #3498DB;
                color: white;
                border: 1px solid #2980B9;
            }
            
            QLineEdit {
                background-color: #34495E;
                border: 2px solid #2c3e50;
                border-radius: 6px;
                padding: 8px;
                color: white;
                font-size: 11px;
            }
            
            QProgressBar {
                border: 2px solid #2c3e50;
                border-radius: 6px;
                text-align: center;
                background-color: #34495E;
                color: white;
                font-weight: bold;
                height: 24px;
            }
            
            QProgressBar::chunk {
                background-color: #3498DB;
                border-radius: 4px;
            }
            
            QSlider::groove:horizontal {
                border: 1px solid #2c3e50;
                height: 10px;
                background: #34495E;
                margin: 2px 0;
                border-radius: 5px;
            }
            
            QSlider::handle:horizontal {
                background: #3498DB;
                border: 2px solid #1F618D;
                width: 20px;
                height: 20px;
                margin: -6px 0;
                border-radius: 10px;
            }
            
            QCheckBox {
                spacing: 10px;
                color: #cccccc;
                font-size: 11px;
            }
            
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
            }
            
            QCheckBox::indicator:checked {
                background-color: #3498DB;
                border: 2px solid #2980B9;
                border-radius: 4px;
                image: url();
            }
            
            QCheckBox::indicator:unchecked {
                background-color: #34495E;
                border: 2px solid #2c3e50;
                border-radius: 4px;
            }
            
            QLabel {
                color: #BDC3C7;
            }
        """)
        
        # Устанавливаем темную палитру
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(26, 26, 26))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(204, 204, 204))
        palette.setColor(QPalette.ColorRole.Base, QColor(44, 62, 80))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(52, 73, 94))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(26, 26, 26))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(204, 204, 204))
        palette.setColor(QPalette.ColorRole.Text, QColor(204, 204, 204))
        palette.setColor(QPalette.ColorRole.Button, QColor(44, 62, 80))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(204, 204, 204))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(52, 152, 219))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        self.setPalette(palette)
    
    def update_mode(self):
        """Обновление видимости элементов в зависимости от режима"""
        is_server = self.mode_switch.isChecked()
        
        # Клиентский режим
        self.client_group.setVisible(not is_server)
        self.scan_button.setEnabled(not is_server)
        self.connect_button.setEnabled(not is_server)
        self.disconnect_button.setVisible(not is_server)
        self.select_file_button.setEnabled(not is_server)
        self.send_button.setEnabled(not is_server and self.backend.is_connected())
        
        # Серверный режим
        self.server_group.setVisible(is_server)
        self.play_button.setEnabled(is_server)
        self.stop_button.setEnabled(is_server)
        self.volume_slider.setEnabled(is_server)
        self.start_server_button.setEnabled(is_server and not self.server_started)
        self.stop_server_button.setEnabled(is_server and self.server_started)
        self.clear_files_button.setEnabled(is_server)
        self.open_folder_button.setEnabled(is_server)
        
        if is_server:
            self.status_label.setText("🔵 Серверный режим активирован")
            self.auto_scan_timer.stop()
        else:
            self.status_label.setText("✅ Клиентский режим: Готов к работе")
            self.auto_scan_timer.start(30000)
            # Автосканирование при переходе в клиентский режим
            QTimer.singleShot(1000, self.on_scan_clicked)
    
    # Обработчики событий
    def on_mode_changed(self, checked: bool):
        """Обработчик изменения режима"""
        self.current_mode = "server" if checked else "client"
        self.logger.info(f"Переключение в режим: {self.current_mode}")
        
        # Останавливаем сервер при переходе в клиентский режим
        if self.current_mode == "client" and self.server_started:
            self.on_stop_server_clicked()
        
        # Останавливаем воспроизведение
        if self.player.is_playing:
            self.player.stop()
            self.play_button.setText("▶ Воспроизвести")
        
        self.update_mode()
    
    def on_scan_clicked(self):
        """Обработчик кнопки сканирования"""
        if self.current_mode != "client":
            return
            
        self.discovered_devices.clear()
        self.devices_list.clear()
        self.status_label.setText("🔍 Сканирование устройств...")
        self.backend.start_discovery()
        self.logger.info("Запущено сканирование устройств")
    
    def on_connect_clicked(self):
        """Обработчик кнопки подключения"""
        current_item = self.devices_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "Предупреждение", "Выберите устройство из списка")
            return
        
        # Получаем адрес из данных элемента
        address = current_item.data(Qt.ItemDataRole.UserRole)
        if not address:
            QMessageBox.warning(self, "Ошибка", "Не удалось получить адрес устройства")
            return
        
        self.status_label.setText(f"🔗 Подключение к {address}...")
        self.logger.info(f"Попытка подключения к устройству: {address}")
        
        if self.backend.connect_to_device(address):
            QMessageBox.information(self, "Успех", "✅ Успешно подключено к устройству")
            self.disconnect_button.setEnabled(True)
            self.send_button.setEnabled(True)
        else:
            error_msg = self.backend.get_last_error()
            QMessageBox.critical(self, "Ошибка", f"❌ Ошибка подключения: {error_msg}")
    
    def on_disconnect_clicked(self):
        """Обработчик кнопки отключения"""
        self.logger.info("Инициация отключения от устройства")
        self.backend.disconnect_device()
        self.disconnect_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.status_label.setText("✅ Отключено от устройства")
    
    def on_select_file_clicked(self):
        """Обработчик выбора файла"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите аудиофайл",
            "",
            "Аудиофайлы (*.mp3 *.wav *.flac *.ogg *.m4a);;Все файлы (*.*)"
        )
        
        if file_path:
            if not os.path.exists(file_path):
                QMessageBox.warning(self, "Ошибка", "Файл не существует или был удален")
                self.selected_file = ""
                self.file_path_edit.clear()
                return
            
            try:
                file_size = os.path.getsize(file_path)
                if file_size == 0:
                    QMessageBox.warning(self, "Ошибка", "Файл пустой")
                    return
                
                self.selected_file = file_path
                file_name = os.path.basename(file_path)
                self.file_path_edit.setText(f"{file_name} ({self._format_file_size(file_size)})")
                self.backend.set_file_to_send(file_path)
                self.logger.info(f"Выбран файл для отправки: {file_name} ({file_size} bytes)")
            except Exception as e:
                self.logger.error(f"Ошибка при выборе файла: {e}")
                QMessageBox.critical(self, "Ошибка", f"Не удалось открыть файл: {e}")
    
    def on_send_clicked(self):
        """Обработчик отправки файла"""
        if not self.selected_file:
            QMessageBox.warning(self, "Предупреждение", "Сначала выберите файл для отправки")
            return
        
        if not os.path.exists(self.selected_file):
            QMessageBox.warning(self, "Ошибка", "Файл не существует или был удален. Выберите другой файл.")
            self.selected_file = ""
            self.file_path_edit.clear()
            return
        
        if not self.backend.is_connected():
            QMessageBox.warning(self, "Предупреждение", "Сначала подключитесь к устройству")
            return
        
        # Сброс прогресс-бара
        self.progress_bar.reset()
        self.status_label.setText("📤 Отправка файла...")
        
        # Запускаем отправку файла
        if self.backend.send_file():
            QMessageBox.information(self, "Успех", "✅ Файл успешно отправлен")
        else:
            error_msg = self.backend.get_last_error()
            QMessageBox.critical(self, "Ошибка", f"❌ Ошибка отправки: {error_msg}")
            self.progress_bar.setValue(0)  # Сброс прогресс-бара при ошибке
    
    def on_start_server_clicked(self):
        """Запуск сервера"""
        if self.current_mode != "server":
            return
        
        try:
            self.server_backend.start()
            self.server_started = True
            self.start_server_button.setEnabled(False)
            self.stop_server_button.setEnabled(True)
            self.server_status_label.setText("▶ Сервер запущен")
            self.server_status_label.setStyleSheet("""
                QLabel {
                    background-color: #27AE60;
                    color: white;
                    padding: 10px;
                    border-radius: 6px;
                    font-weight: bold;
                    border: 1px solid #1E8449;
                }
            """)
            self.logger.info("Сервер успешно запущен")
            QMessageBox.information(self, "Успех", "✅ Сервер успешно запущен")
        except Exception as e:
            self.logger.error(f"Не удалось запустить сервер: {e}")
            QMessageBox.critical(self, "Ошибка", f"❌ Не удалось запустить сервер: {e}")
    
    def on_stop_server_clicked(self):
        """Остановка сервера"""
        try:
            self.server_backend.stop()
            self.server_started = False
            self.start_server_button.setEnabled(True)
            self.stop_server_button.setEnabled(False)
            self.server_status_label.setText("⏹ Сервер остановлен")
            self.server_status_label.setStyleSheet("""
                QLabel {
                    background-color: #7D3C98;
                    color: white;
                    padding: 10px;
                    border-radius: 6px;
                    font-weight: bold;
                    border: 1px solid #5D2A7F;
                }
            """)
            self.logger.info("Сервер успешно остановлен")
        except Exception as e:
            self.logger.error(f"Ошибка при остановке сервера: {e}")
            QMessageBox.critical(self, "Ошибка", f"❌ Ошибка при остановке сервера: {e}")
    
    def on_play_clicked(self):
        """Обработчик кнопки воспроизведения/паузы"""
        current_item = self.received_files_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "Предупреждение", "Выберите файл для воспроизведения")
            return
        
        file_path = current_item.data(Qt.ItemDataRole.UserRole)
        if not os.path.exists(file_path):
            QMessageBox.critical(self, "Ошибка", "Файл не найден")
            return
        
        if self.player.is_playing:
            self.player.pause()
            self.play_button.setText("▶ Воспроизвести")
            self.status_label.setText("⏸ Воспроизведение приостановлено")
        else:
            if self.player.play(file_path):
                self.play_button.setText("⏸ Пауза")
                file_name = os.path.basename(file_path)
                self.status_label.setText(f"🎵 Воспроизведение: {file_name}")
            else:
                QMessageBox.critical(self, "Ошибка", "❌ Не удалось воспроизвести файл")
    
    def on_stop_clicked(self):
        """Обработчик кнопки остановки"""
        self.player.stop()
        self.play_button.setText("▶ Воспроизвести")
        self.status_label.setText("⏹ Воспроизведение остановлено")
    
    def on_volume_changed(self, value: int):
        """Обработчик изменения громкости"""
        volume = value / 100.0
        self.player.set_volume(volume)
    
    def on_file_selected(self, item: QListWidgetItem):
        """Обработчик выбора файла в списке"""
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path and os.path.exists(file_path):
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            self.status_label.setText(f"📄 Выбран файл: {file_name} ({self._format_file_size(file_size)})")
    
    def on_file_double_clicked(self, item: QListWidgetItem):
        """Обработчик двойного клика по файлу"""
        self.on_play_clicked()
    
    def on_clear_files_clicked(self):
        """Очистка списка файлов"""
        reply = QMessageBox.question(
            self, 
            "Подтверждение",
            "Вы уверены что хотите очистить список файлов?\n(Файлы на диске не будут удалены)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.received_files_list.clear()
            self.status_label.setText("🗑️ Список файлов очищен")
    
    def on_open_folder_clicked(self):
        """Открытие папки с полученными файлами"""
        download_dir = "received_files"
        if os.path.exists(download_dir):
            os.startfile(download_dir)  # Windows
            # Для других ОС можно использовать:
            # import subprocess
            # subprocess.run(['open', download_dir])  # macOS
            # subprocess.run(['xdg-open', download_dir])  # Linux
        else:
            QMessageBox.information(self, "Информация", f"Папка '{download_dir}' не существует")
    
    def on_auto_scan(self):
        """Автоматическое сканирование в клиентском режиме"""
        if self.current_mode == "client" and self.isVisible():
            self.logger.debug("Автоматическое сканирование устройств")
            self.on_scan_clicked()
    
    # Callback методы от бэкенда
    def on_device_discovered(self, name: str, address: str):
        """Callback при обнаружении устройства"""
        # Фильтрация дубликатов
        if address in self.discovered_devices:
            return
        
        self.discovered_devices[address] = name
        
        item_text = f"{name} ({address})"
        item = QListWidgetItem(item_text)
        item.setData(Qt.ItemDataRole.UserRole, address)
        self.devices_list.addItem(item)
        
        # Сортировка по имени
        self.devices_list.sortItems()
    
    def on_status(self, message: str):
        """Callback статусных сообщений"""
        self.status_label.setText(message)
        self.logger.info(f"Статус: {message}")
    
    def on_progress(self, percent: int):
        """Callback обновления прогресса"""
        self.progress_bar.setValue(percent)
        if percent % 10 == 0:  # Логируем каждые 10%
            self.logger.debug(f"Прогресс отправки: {percent}%")
    
    def on_file_received(self, filename: str):
        """Callback при получении файла (клиент)"""
        # В клиентском режиме этот callback не используется
        pass
    
    def on_file_sent(self):
        """Callback при успешной отправке файла"""
        self.progress_bar.setValue(100)
        QMessageBox.information(self, "Успех", "✅ Файл успешно отправлен")
        self.logger.info("Файл успешно отправлен")
    
    def on_scan_finished(self):
        """Callback завершения сканирования"""
        device_count = len(self.discovered_devices)
        self.status_label.setText(f"✅ Сканирование завершено. Найдено устройств: {device_count}")
        self.logger.info(f"Сканирование завершено. Найдено устройств: {device_count}")
    
    def on_connected(self):
        """Callback успешного подключения"""
        self.status_label.setText("✅ Успешно подключено к устройству")
        self.disconnect_button.setEnabled(True)
        self.send_button.setEnabled(True)
        self.logger.info("Успешно подключено к устройству")
    
    def on_disconnected(self):
        """Callback отключения от устройства"""
        self.status_label.setText("✅ Отключено от устройства")
        self.disconnect_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.logger.info("Отключено от устройства")
    
    # Callback методы от серверного бэкенда
    def on_server_status(self, message: str):
        """Callback статусных сообщений сервера"""
        self.server_status_label.setText(message)
        self.logger.info(f"Статус сервера: {message}")
    
    def on_server_file_received(self, filename: str):
        """Callback при получении файла сервером"""
        if os.path.exists(filename):
            # Добавляем в список полученных файлов
            file_name = os.path.basename(filename)
            file_size = os.path.getsize(filename)
            item_text = f"📄 {file_name} ({self._format_file_size(file_size)})"
            
            # Проверяем, нет ли уже этого файла в списке
            for i in range(self.received_files_list.count()):
                item = self.received_files_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == filename:
                    return
            
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, filename)
            self.received_files_list.addItem(item)
            
            self.logger.info(f"Получен файл: {file_name} ({file_size} bytes)")
            QMessageBox.information(self, "Успех", f"✅ Получен файл: {file_name}")
            
            # Автоматически воспроизводим если в серверном режиме
            if self.current_mode == "server":
                self.received_files_list.setCurrentItem(item)
                QTimer.singleShot(500, lambda: self.on_play_clicked())
        else:
            self.logger.error(f"Получен файл не найден: {filename}")
    
    def on_server_client_connected(self):
        """Callback при подключении клиента к серверу"""
        QMessageBox.information(self, "Уведомление", "✅ Клиент подключился к серверу")
        self.logger.info("Клиент подключился к серверу")
    
    def on_server_client_disconnected(self):
        """Callback при отключении клиента от сервера"""
        self.logger.info("Клиент отключился от сервера")
        # Можно добавить уведомление, если нужно
    
    # Вспомогательные методы
    def _format_file_size(self, size_bytes: int) -> str:
        """Форматирование размера файла"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"
    
    def closeEvent(self, event):
        """Обработчик закрытия окна"""
        self.logger.info("Закрытие приложения")
        
        # Останавливаем таймер
        self.auto_scan_timer.stop()
        
        # Останавливаем воспроизведение
        self.player.stop()
        
        # Останавливаем сервер если он запущен
        if hasattr(self, 'server_backend') and self.server_backend and self.server_started:
            try:
                self.server_backend.stop()
                self.logger.info("Сервер остановлен при закрытии приложения")
            except Exception as e:
                self.logger.error(f"Ошибка при остановке сервера: {e}")
        
        # Отключаемся от устройства
        if hasattr(self, 'backend') and self.backend and self.backend.is_connected():
            try:
                self.backend.disconnect_device()
                self.logger.info("Отключено от устройства при закрытии приложения")
            except Exception as e:
                self.logger.error(f"Ошибка при отключении: {e}")
        
        # Завершаем pygame
        if pygame.mixer.get_init():
            pygame.mixer.quit()
        
        event.accept()

def main():
    """Главная функция"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    # Устанавливаем имя приложения
    app.setApplicationName("KIM5+ Bluetooth File Transfer - Лабораторная работа 6")
    app.setApplicationVersion("1.0.0")
    
    try:
        window = BluetoothGUI()
        window.show()
        logger.info("Приложение успешно запущено")
        
        return app.exec()
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске приложения: {e}")
        QMessageBox.critical(None, "Критическая ошибка", 
                           f"Не удалось запустить приложение:\n{e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())