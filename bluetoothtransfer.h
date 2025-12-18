#ifndef BLUETOOTHTRANSFER_H
#define BLUETOOTHTRANSFER_H

#include <winsock2.h>
#include <ws2bth.h>
#include <bluetoothapis.h>
#include <string>
#include <functional>
#include <thread>
#include <atomic>
#include <queue>
#include <mutex>
#include <condition_variable>

// Callback типы для взаимодействия с Python
typedef void (*DeviceDiscoveredCallback)(const char* name, const char* address);
typedef void (*StatusCallback)(const char* message);
typedef void (*ProgressCallback)(int percent);
typedef void (*FileCallback)(const char* filename);
typedef void (*ScanFinishedCallback)();
typedef void (*ConnectedCallback)();
typedef void (*DisconnectedCallback)();  // Добавлен callback для отключения

class BluetoothTransfer
{
public:
    explicit BluetoothTransfer();
    ~BluetoothTransfer();

    // Python-совместимые методы
    void startDeviceDiscovery();
    bool connectToDevice(const char* address);
    void setFileToSend(const char* filePath);
    bool sendFile();
    void cleanup();
    void disconnect();  // Добавлен метод для отключения

    // Методы для Python
    bool isConnected() const { return m_isConnected; }
    const char* getLastError() const { return m_lastError.c_str(); }

    // Установка callback-функций из Python
    void setCallbacks(
        DeviceDiscoveredCallback deviceDiscovered,
        StatusCallback status,
        ProgressCallback progress,
        FileCallback fileReceived,
        FileCallback fileSent,
        ScanFinishedCallback scanFinished,
        ConnectedCallback connected,
        DisconnectedCallback disconnected = nullptr  // Добавлен необязательный callback
    );

private:
    void runDiscovery();
    void handleStatusMessage(const std::string& message);
    void handleDeviceDiscovered(const std::string& name, const std::string& address);
    void handleScanFinished();
    void handleClientConnected();
    void handleClientDisconnected();  // Добавлен обработчик отключения
    void handleFileSent();
    void handleProgressUpdated(int percent);

    SOCKET m_clientSocket;
    std::string m_fileToSendPath;
    std::atomic<bool> m_isConnected;
    std::atomic<bool> m_isDiscovering;
    std::string m_lastError;

    std::thread m_discoveryThread;
    std::atomic<bool> m_stopDiscovery;

    // Callback функции
    DeviceDiscoveredCallback m_deviceDiscoveredCallback;
    StatusCallback m_statusCallback;
    ProgressCallback m_progressCallback;
    FileCallback m_fileReceivedCallback;
    FileCallback m_fileSentCallback;
    ScanFinishedCallback m_scanFinishedCallback;
    ConnectedCallback m_connectedCallback;
    DisconnectedCallback m_disconnectedCallback;  // Добавлен callback отключения

    // Thread-safe очередь для событий
    struct Event {
        enum Type {
            DeviceDiscovered, ScanFinished, ClientConnected,
            ClientDisconnected, FileSent, ProgressUpdated, StatusMessage
        };
        Type type;
        std::string str1;
        std::string str2;
        int intValue;
    };

    std::queue<Event> m_eventQueue;
    std::mutex m_eventMutex;
    std::condition_variable m_eventCV;
    std::thread m_eventThread;
    std::atomic<bool> m_stopEventThread;

    void processEvents();
    void postEvent(const Event& event);
};

// C-совместимый интерфейс для Python
extern "C" {
    __declspec(dllexport) BluetoothTransfer* createBluetoothTransfer();
    __declspec(dllexport) void destroyBluetoothTransfer(BluetoothTransfer* instance);
    __declspec(dllexport) void startDiscovery(BluetoothTransfer* instance);
    __declspec(dllexport) int connectDevice(BluetoothTransfer* instance, const char* address);
    __declspec(dllexport) void disconnectDevice(BluetoothTransfer* instance);  // Добавлена функция
    __declspec(dllexport) void setSendFile(BluetoothTransfer* instance, const char* filePath);
    __declspec(dllexport) int sendFileData(BluetoothTransfer* instance);
    __declspec(dllexport) void cleanupTransfer(BluetoothTransfer* instance);
    __declspec(dllexport) int isDeviceConnected(BluetoothTransfer* instance);
    __declspec(dllexport) const char* getLastErrorMessage(BluetoothTransfer* instance);

    // Callback регистрация
    __declspec(dllexport) void registerCallbacks(
        BluetoothTransfer* instance,
        DeviceDiscoveredCallback deviceDiscovered,
        StatusCallback status,
        ProgressCallback progress,
        FileCallback fileReceived,
        FileCallback fileSent,
        ScanFinishedCallback scanFinished,
        ConnectedCallback connected,
        DisconnectedCallback disconnected
    );
}

#endif // BLUETOOTHTRANSFER_H