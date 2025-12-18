#include "bluetoothtransfer.h"

#include <Windows.h>
#include <string>
#include <sstream>
#include <iostream>
#include <chrono>
#include <cstdio>

#pragma comment(lib, "Ws2_32.lib")
#pragma comment(lib, "Bthprops.lib")

// Определение RFCOMM сервисного UUID
static const GUID RFCOMM_SERVICE_UUID = {
    0x00001101, 0x0000, 0x1000, {0x80, 0x00, 0x00, 0x80, 0x5F, 0x9B, 0x34, 0xFB}
};

BluetoothTransfer::BluetoothTransfer()
    : m_clientSocket(INVALID_SOCKET)
    , m_isConnected(false)
    , m_isDiscovering(false)
    , m_stopDiscovery(false)
    , m_stopEventThread(false)
    , m_deviceDiscoveredCallback(nullptr)
    , m_statusCallback(nullptr)
    , m_progressCallback(nullptr)
    , m_fileReceivedCallback(nullptr)
    , m_fileSentCallback(nullptr)
    , m_scanFinishedCallback(nullptr)
    , m_connectedCallback(nullptr)
    , m_disconnectedCallback(nullptr)
{
    WSADATA wsaData;
    WSAStartup(MAKEWORD(2, 2), &wsaData);

    // Запускаем поток обработки событий
    m_eventThread = std::thread(&BluetoothTransfer::processEvents, this);
}

BluetoothTransfer::~BluetoothTransfer()
{
    // Останавливаем потоки
    m_stopEventThread = true;
    m_stopDiscovery = true;

    m_eventCV.notify_all();
    if (m_eventThread.joinable()) {
        m_eventThread.join();
    }

    if (m_discoveryThread.joinable()) {
        m_discoveryThread.join();
    }

    cleanup();
    WSACleanup();
}

void BluetoothTransfer::setCallbacks(
    DeviceDiscoveredCallback deviceDiscovered,
    StatusCallback status,
    ProgressCallback progress,
    FileCallback fileReceived,
    FileCallback fileSent,
    ScanFinishedCallback scanFinished,
    ConnectedCallback connected,
    DisconnectedCallback disconnected)
{
    m_deviceDiscoveredCallback = deviceDiscovered;
    m_statusCallback = status;
    m_progressCallback = progress;
    m_fileReceivedCallback = fileReceived;
    m_fileSentCallback = fileSent;
    m_scanFinishedCallback = scanFinished;
    m_connectedCallback = connected;
    m_disconnectedCallback = disconnected;
}

void BluetoothTransfer::processEvents()
{
    while (!m_stopEventThread) {
        Event event;
        {
            std::unique_lock<std::mutex> lock(m_eventMutex);
            m_eventCV.wait(lock, [this]() {
                return !m_eventQueue.empty() || m_stopEventThread;
                });

            if (m_stopEventThread && m_eventQueue.empty()) {
                break;
            }

            event = m_eventQueue.front();
            m_eventQueue.pop();
        }

        switch (event.type) {
        case Event::DeviceDiscovered:
            handleDeviceDiscovered(event.str1, event.str2);
            break;
        case Event::ScanFinished:
            handleScanFinished();
            break;
        case Event::ClientConnected:
            handleClientConnected();
            break;
        case Event::ClientDisconnected:
            handleClientDisconnected();
            break;
        case Event::FileSent:
            handleFileSent();
            break;
        case Event::ProgressUpdated:
            handleProgressUpdated(event.intValue);
            break;
        case Event::StatusMessage:
            handleStatusMessage(event.str1);
            break;
        }
    }
}

void BluetoothTransfer::postEvent(const Event& event)
{
    {
        std::lock_guard<std::mutex> lock(m_eventMutex);
        m_eventQueue.push(event);
    }
    m_eventCV.notify_one();
}

void BluetoothTransfer::startDeviceDiscovery()
{
    if (m_isDiscovering) {
        return;
    }

    m_isDiscovering = true;
    m_stopDiscovery = false;

    postEvent({ Event::StatusMessage, "Scanning for devices..." });

    // Запускаем сканирование в отдельном потоке
    if (m_discoveryThread.joinable()) {
        m_discoveryThread.join();
    }

    m_discoveryThread = std::thread(&BluetoothTransfer::runDiscovery, this);
}

void BluetoothTransfer::runDiscovery()
{
    BLUETOOTH_DEVICE_SEARCH_PARAMS searchParams = { sizeof(BLUETOOTH_DEVICE_SEARCH_PARAMS) };
    searchParams.fReturnAuthenticated = TRUE;
    searchParams.fReturnConnected = TRUE;
    searchParams.fReturnRemembered = TRUE;
    searchParams.fReturnUnknown = TRUE;
    searchParams.cTimeoutMultiplier = 8;

    BLUETOOTH_DEVICE_INFO deviceInfo = { sizeof(BLUETOOTH_DEVICE_INFO), 0 };

    HBLUETOOTH_DEVICE_FIND hFind = BluetoothFindFirstDevice(&searchParams, &deviceInfo);
    if (hFind) {
        do {
            if (m_stopDiscovery) break;

            // Преобразование wide char в string с учетом кодировки
            std::wstring wname(deviceInfo.szName);

            // Преобразование Unicode в UTF-8 для кросс-платформенной совместимости
            int size_needed = WideCharToMultiByte(CP_UTF8, 0, wname.c_str(), (int)wname.size(), NULL, 0, NULL, NULL);
            std::string name(size_needed, 0);
            WideCharToMultiByte(CP_UTF8, 0, wname.c_str(), (int)wname.size(), &name[0], size_needed, NULL, NULL);

            // Форматирование адреса
            std::ostringstream oss;
            oss << std::hex << deviceInfo.Address.ullLong;
            std::string address = oss.str();

            postEvent({ Event::DeviceDiscovered, name, address });

            // Небольшая задержка для обработки событий
            std::this_thread::sleep_for(std::chrono::milliseconds(10));

        } while (BluetoothFindNextDevice(hFind, &deviceInfo));

        BluetoothFindDeviceClose(hFind);
    }

    m_isDiscovering = false;
    postEvent({ Event::ScanFinished });
}

bool BluetoothTransfer::connectToDevice(const char* address)
{
    cleanup();

    std::string addrStr = address;
    BTH_ADDR addr;

    try {
        addr = std::stoull(addrStr, nullptr, 16);
    }
    catch (...) {
        m_lastError = "Invalid device address";
        postEvent({ Event::StatusMessage, "Invalid device address" });
        return false;
    }

    SOCKADDR_BTH sockaddrBthServer = { 0 };
    sockaddrBthServer.addressFamily = AF_BTH;
    sockaddrBthServer.serviceClassId = RFCOMM_SERVICE_UUID;
    sockaddrBthServer.port = 6;
    sockaddrBthServer.btAddr = addr;

    m_clientSocket = socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM);
    if (m_clientSocket == INVALID_SOCKET) {
        m_lastError = "Error creating client socket";
        postEvent({ Event::StatusMessage, "Error creating client socket" });
        return false;
    }

    if (::connect(m_clientSocket, reinterpret_cast<sockaddr*>(&sockaddrBthServer), sizeof(sockaddrBthServer)) == SOCKET_ERROR) {
        int errorCode = WSAGetLastError();
        m_lastError = "Connection failed with error: " + std::to_string(errorCode);
        postEvent({ Event::StatusMessage, "Connection failed with error: " + std::to_string(errorCode) });
        cleanup();
        return false;
    }

    m_isConnected = true;
    postEvent({ Event::ClientConnected });
    postEvent({ Event::StatusMessage, "Connected to device" });
    return true;
}

void BluetoothTransfer::disconnect()
{
    if (m_isConnected) {
        cleanup();
        postEvent({ Event::ClientDisconnected });
        postEvent({ Event::StatusMessage, "Disconnected from device" });
    }
}

void BluetoothTransfer::setFileToSend(const char* filePath)
{
    m_fileToSendPath = filePath;
}

bool BluetoothTransfer::sendFile()
{
    if (m_fileToSendPath.empty() || !m_isConnected) {
        m_lastError = "No file set or not connected";
        return false;
    }

    // Проверяем существование файла
    if (GetFileAttributesA(m_fileToSendPath.c_str()) == INVALID_FILE_ATTRIBUTES) {
        m_lastError = "File does not exist";
        postEvent({ Event::StatusMessage, "File does not exist" });
        return false;
    }

    FILE* file = fopen(m_fileToSendPath.c_str(), "rb");
    if (!file) {
        m_lastError = "Cannot open file for reading";
        postEvent({ Event::StatusMessage, "Cannot open file for reading" });
        return false;
    }

    fseek(file, 0, SEEK_END);
    long fileSize = ftell(file);
    fseek(file, 0, SEEK_SET);

    if (fileSize == 0) {
        m_lastError = "File is empty";
        postEvent({ Event::StatusMessage, "File is empty" });
        fclose(file);
        return false;
    }

    std::string sizeStr = std::to_string(fileSize);
    sizeStr.resize(20, ' ');

    int bytesSent = send(m_clientSocket, sizeStr.c_str(), 20, 0);
    if (bytesSent != 20) {
        m_lastError = "Failed to send file size";
        postEvent({ Event::StatusMessage, "Failed to send file size" });
        fclose(file);
        return false;
    }

    long totalSent = 0;
    char buffer[1024];
    size_t bytesRead;

    while ((bytesRead = fread(buffer, 1, sizeof(buffer), file)) > 0) {
        bytesSent = send(m_clientSocket, buffer, bytesRead, 0);

        if (bytesSent <= 0) {
            m_lastError = "Error sending file data";
            postEvent({ Event::StatusMessage, "Error sending file data" });
            break;
        }

        totalSent += bytesSent;

        int progress = (int)((totalSent * 100) / fileSize);
        postEvent({ Event::ProgressUpdated, "", "", progress });
    }

    fclose(file);

    if (totalSent == fileSize) {
        postEvent({ Event::FileSent });
        return true;
    }
    else {
        m_lastError = "File transfer incomplete";
        postEvent({ Event::StatusMessage, "File transfer incomplete" });
        return false;
    }
}

void BluetoothTransfer::cleanup()
{
    if (m_clientSocket != INVALID_SOCKET) {
        closesocket(m_clientSocket);
        m_clientSocket = INVALID_SOCKET;
    }
    m_isConnected = false;
}

// Обработчики событий
void BluetoothTransfer::handleDeviceDiscovered(const std::string& name, const std::string& address)
{
    if (m_deviceDiscoveredCallback) {
        m_deviceDiscoveredCallback(name.c_str(), address.c_str());
    }
}

void BluetoothTransfer::handleScanFinished()
{
    if (m_scanFinishedCallback) {
        m_scanFinishedCallback();
    }
    if (m_statusCallback) {
        m_statusCallback("Scan finished");
    }
}

void BluetoothTransfer::handleClientConnected()
{
    if (m_connectedCallback) {
        m_connectedCallback();
    }
}

void BluetoothTransfer::handleClientDisconnected()
{
    if (m_disconnectedCallback) {
        m_disconnectedCallback();
    }
}

void BluetoothTransfer::handleFileSent()
{
    if (m_fileSentCallback) {
        m_fileSentCallback("");
    }
}

void BluetoothTransfer::handleProgressUpdated(int percent)
{
    if (m_progressCallback) {
        m_progressCallback(percent);
    }
}

void BluetoothTransfer::handleStatusMessage(const std::string& message)
{
    if (m_statusCallback) {
        m_statusCallback(message.c_str());
    }
}

// C interface implementation
extern "C" {
    __declspec(dllexport) BluetoothTransfer* createBluetoothTransfer()
    {
        return new BluetoothTransfer();
    }

    __declspec(dllexport) void destroyBluetoothTransfer(BluetoothTransfer* instance)
    {
        delete instance;
    }

    __declspec(dllexport) void startDiscovery(BluetoothTransfer* instance)
    {
        instance->startDeviceDiscovery();
    }

    __declspec(dllexport) int connectDevice(BluetoothTransfer* instance, const char* address)
    {
        return instance->connectToDevice(address) ? 1 : 0;
    }

    __declspec(dllexport) void disconnectDevice(BluetoothTransfer* instance)
    {
        instance->disconnect();
    }

    __declspec(dllexport) void setSendFile(BluetoothTransfer* instance, const char* filePath)
    {
        instance->setFileToSend(filePath);
    }

    __declspec(dllexport) int sendFileData(BluetoothTransfer* instance)
    {
        return instance->sendFile() ? 1 : 0;
    }

    __declspec(dllexport) void cleanupTransfer(BluetoothTransfer* instance)
    {
        instance->cleanup();
    }

    __declspec(dllexport) int isDeviceConnected(BluetoothTransfer* instance)
    {
        return instance->isConnected() ? 1 : 0;
    }

    __declspec(dllexport) const char* getLastErrorMessage(BluetoothTransfer* instance)
    {
        return instance->getLastError();
    }

    __declspec(dllexport) void registerCallbacks(
        BluetoothTransfer* instance,
        DeviceDiscoveredCallback deviceDiscovered,
        StatusCallback status,
        ProgressCallback progress,
        FileCallback fileReceived,
        FileCallback fileSent,
        ScanFinishedCallback scanFinished,
        ConnectedCallback connected,
        DisconnectedCallback disconnected)
    {
        instance->setCallbacks(deviceDiscovered, status, progress,
            fileReceived, fileSent, scanFinished, connected, disconnected);
    }
}