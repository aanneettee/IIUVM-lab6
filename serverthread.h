#ifndef SERVERTHREAD_H
#define SERVERTHREAD_H

#include <winsock2.h>
#include <ws2bth.h>
#include <bluetoothapis.h>
#include <string>
#include <functional>
#include <thread>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <queue>

// Callback типы для сервера
typedef void (*ServerStatusCallback)(const char* message);
typedef void (*FileReceivedCallback)(const char* filename);
typedef void (*ClientConnectedCallback)();
typedef void (*ClientDisconnectedCallback)();  // Добавлен callback для отключения клиента

class ServerThread
{
public:
    explicit ServerThread();
    ~ServerThread();

    void start();
    void stop();

    void setCallbacks(
        ServerStatusCallback status,
        FileReceivedCallback fileReceived,
        ClientConnectedCallback clientConnected,
        ClientDisconnectedCallback clientDisconnected = nullptr  // Добавлен необязательный callback
    );

private:
    void run();
    void processEvents();

    struct Event {
        enum Type { ClientConnected, ClientDisconnected, FileReceived, StatusMessage };
        Type type;
        std::string str;
    };

    void postEvent(const Event& event);
    void handleClientConnected();
    void handleClientDisconnected();
    void handleFileReceived(const std::string& filename);
    void handleStatusMessage(const std::string& message);

    std::thread m_serverThread;
    std::thread m_eventThread;
    std::atomic<bool> m_stopServer;
    std::atomic<bool> m_stopEventThread;

    // Thread-safe очередь для событий
    std::queue<Event> m_eventQueue;
    std::mutex m_eventMutex;
    std::condition_variable m_eventCV;

    // Callback функции
    ServerStatusCallback m_statusCallback;
    FileReceivedCallback m_fileReceivedCallback;
    ClientConnectedCallback m_clientConnectedCallback;
    ClientDisconnectedCallback m_clientDisconnectedCallback;
};

// C-совместимый интерфейс для сервера
extern "C" {
    __declspec(dllexport) ServerThread* createServerThread();
    __declspec(dllexport) void destroyServerThread(ServerThread* instance);
    __declspec(dllexport) void startServer(ServerThread* instance);
    __declspec(dllexport) void stopServer(ServerThread* instance);
    __declspec(dllexport) void registerServerCallbacks(
        ServerThread* instance,
        ServerStatusCallback status,
        FileReceivedCallback fileReceived,
        ClientConnectedCallback clientConnected,
        ClientDisconnectedCallback clientDisconnected
    );
}

#endif // SERVERTHREAD_H