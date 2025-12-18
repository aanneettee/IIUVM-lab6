#include "serverthread.h"
#define NOMINMAX 
#include <Windows.h>
#include <string>
#include <sstream>
#include <iostream>
#include <chrono>
#include <ctime>
#include <fstream>
#include <algorithm>  // Для min/max

#pragma comment(lib, "Ws2_32.lib")
#pragma comment(lib, "Bthprops.lib")

// Определение RFCOMM сервисного UUID
static const GUID RFCOMM_SERVICE_UUID = {
    0x00001101, 0x0000, 0x1000, {0x80, 0x00, 0x00, 0x80, 0x5F, 0x9B, 0x34, 0xFB}
};

ServerThread::ServerThread()
    : m_stopServer(false)
    , m_stopEventThread(false)
    , m_statusCallback(nullptr)
    , m_fileReceivedCallback(nullptr)
    , m_clientConnectedCallback(nullptr)
    , m_clientDisconnectedCallback(nullptr)
{
    // Запускаем поток обработки событий
    m_eventThread = std::thread(&ServerThread::processEvents, this);
}

ServerThread::~ServerThread()
{
    stop();

    // Останавливаем поток событий
    m_stopEventThread = true;
    m_eventCV.notify_all();
    if (m_eventThread.joinable()) {
        m_eventThread.join();
    }
}

void ServerThread::setCallbacks(
    ServerStatusCallback status,
    FileReceivedCallback fileReceived,
    ClientConnectedCallback clientConnected,
    ClientDisconnectedCallback clientDisconnected)
{
    m_statusCallback = status;
    m_fileReceivedCallback = fileReceived;
    m_clientConnectedCallback = clientConnected;
    m_clientDisconnectedCallback = clientDisconnected;
}

void ServerThread::start()
{
    m_stopServer = false;

    if (m_serverThread.joinable()) {
        m_serverThread.join();
    }

    m_serverThread = std::thread(&ServerThread::run, this);
}

void ServerThread::stop()
{
    m_stopServer = true;

    if (m_serverThread.joinable()) {
        m_serverThread.join();
    }
}

void ServerThread::processEvents()
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
        case Event::ClientConnected:
            handleClientConnected();
            break;
        case Event::ClientDisconnected:
            handleClientDisconnected();
            break;
        case Event::FileReceived:
            handleFileReceived(event.str);
            break;
        case Event::StatusMessage:
            handleStatusMessage(event.str);
            break;
        }
    }
}

void ServerThread::postEvent(const Event& event)
{
    {
        std::lock_guard<std::mutex> lock(m_eventMutex);
        m_eventQueue.push(event);
    }
    m_eventCV.notify_one();
}

void ServerThread::run()
{
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
        postEvent({ Event::StatusMessage, "WSAStartup failed" });
        return;
    }

    SOCKADDR_BTH sockaddrBth = { 0 };
    sockaddrBth.addressFamily = AF_BTH;
    sockaddrBth.serviceClassId = RFCOMM_SERVICE_UUID;
    sockaddrBth.port = 6;

    SOCKET serverSocket = socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM);
    if (serverSocket == INVALID_SOCKET) {
        postEvent({ Event::StatusMessage, "Error creating server socket" });
        WSACleanup();
        return;
    }

    if (bind(serverSocket,
        reinterpret_cast<sockaddr*>(&sockaddrBth),
        sizeof(sockaddrBth)) == SOCKET_ERROR) {
        postEvent({ Event::StatusMessage, "Bind failed" });
        closesocket(serverSocket);
        WSACleanup();
        return;
    }

    if (listen(serverSocket, SOMAXCONN) == SOCKET_ERROR) {
        postEvent({ Event::StatusMessage, "Listen failed" });
        closesocket(serverSocket);
        WSACleanup();
        return;
    }

    postEvent({ Event::StatusMessage, "Server started, waiting for connections..." });

    while (!m_stopServer) {
        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(serverSocket, &readSet);

        timeval timeout{ 1, 0 };
        int sel = select(0, &readSet, nullptr, nullptr, &timeout);
        if (sel == SOCKET_ERROR) break;
        if (sel == 0) continue;

        SOCKADDR_BTH clientAddr = { 0 };
        int clientAddrSize = sizeof(clientAddr);
        SOCKET clientSocket = accept(serverSocket,
            reinterpret_cast<sockaddr*>(&clientAddr),
            &clientAddrSize);
        if (clientSocket == INVALID_SOCKET) continue;

        postEvent({ Event::ClientConnected });
        postEvent({ Event::StatusMessage, "Client connected" });

        char sizeBuf[21] = { 0 };
        int received = 0;

        while (received < 20 && !m_stopServer) {
            int r = recv(clientSocket, sizeBuf + received, 20 - received, 0);
            if (r <= 0) break;
            received += r;
        }

        if (m_stopServer) {
            closesocket(clientSocket);
            break;
        }

        // Проверяем, успешно ли получен размер файла
        if (received < 20) {
            postEvent({ Event::ClientDisconnected });
            postEvent({ Event::StatusMessage, "Client disconnected before sending file size" });
            closesocket(clientSocket);
            continue;
        }

        int dataSize = atoi(sizeBuf);
        if (dataSize <= 0) {
            postEvent({ Event::StatusMessage, "Invalid file size received" });
            closesocket(clientSocket);
            continue;
        }

        // Создаем уникальное имя файла с временной меткой
        auto now = std::chrono::system_clock::now();
        auto in_time_t = std::chrono::system_clock::to_time_t(now);
        std::tm tm_buf;
        localtime_s(&tm_buf, &in_time_t);

        char timeStr[100];
        strftime(timeStr, sizeof(timeStr), "%Y%m%d_%H%M%S", &tm_buf);

        // Создаем папку для полученных файлов если её нет
        std::string downloadDir = "received_files";
        CreateDirectoryA(downloadDir.c_str(), NULL);

        std::string fileName = downloadDir + "\\received_file_" + std::string(timeStr) + ".mp3";

        // Открываем файл для записи
        std::ofstream outFile(fileName, std::ios::binary);
        if (!outFile.is_open()) {
            postEvent({ Event::StatusMessage, "Cannot create output file" });
            closesocket(clientSocket);
            continue;
        }

        int remaining = dataSize;
        char buffer[1024];
        int total = 0;

        while (remaining > 0 && !m_stopServer) {
            int want = (std::min)(static_cast<int>(sizeof(buffer)), remaining);
            int r = recv(clientSocket, buffer, want, 0);
            if (r <= 0) break;

            outFile.write(buffer, r);
            remaining -= r;
            total += r;

            int percent = (total * 100) / dataSize;
            if (percent % 10 == 0) {  // Отправляем статус каждые 10%
                postEvent({ Event::StatusMessage, "Receiving: " + std::to_string(percent) + "%" });
            }
        }
        outFile.close();

        if (remaining == 0) {
            postEvent({ Event::FileReceived, fileName });
            postEvent({ Event::StatusMessage, "File received successfully" });
        }
        else {
            postEvent({ Event::StatusMessage, "File transfer incomplete" });
            // Удаляем неполный файл
            DeleteFileA(fileName.c_str());
        }

        closesocket(clientSocket);
        postEvent({ Event::ClientDisconnected });
        postEvent({ Event::StatusMessage, "Client disconnected" });
    }

    closesocket(serverSocket);
    WSACleanup();
    postEvent({ Event::StatusMessage, "Server stopped" });
}

void ServerThread::handleClientConnected()
{
    if (m_clientConnectedCallback) {
        m_clientConnectedCallback();
    }
}

void ServerThread::handleClientDisconnected()
{
    if (m_clientDisconnectedCallback) {
        m_clientDisconnectedCallback();
    }
}

void ServerThread::handleFileReceived(const std::string& filename)
{
    if (m_fileReceivedCallback) {
        m_fileReceivedCallback(filename.c_str());
    }
}

void ServerThread::handleStatusMessage(const std::string& message)
{
    if (m_statusCallback) {
        m_statusCallback(message.c_str());
    }
}

// C interface implementation
extern "C" {
    __declspec(dllexport) ServerThread* createServerThread()
    {
        return new ServerThread();
    }

    __declspec(dllexport) void destroyServerThread(ServerThread* instance)
    {
        delete instance;
    }

    __declspec(dllexport) void startServer(ServerThread* instance)
    {
        instance->start();
    }

    __declspec(dllexport) void stopServer(ServerThread* instance)
    {
        instance->stop();
    }

    __declspec(dllexport) void registerServerCallbacks(
        ServerThread* instance,
        ServerStatusCallback status,
        FileReceivedCallback fileReceived,
        ClientConnectedCallback clientConnected,
        ClientDisconnectedCallback clientDisconnected)
    {
        instance->setCallbacks(status, fileReceived, clientConnected, clientDisconnected);
    }
}