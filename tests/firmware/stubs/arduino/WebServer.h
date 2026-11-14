// Host stub of the ESP32 WebServer: registers handlers and captures what a
// handler sends, so a test can "GET" a path and inspect the response.
#pragma once
#include <Arduino.h>

#include <functional>
#include <map>
#include <string>

class WebServer {
 public:
  explicit WebServer(int port = 80) : port_(port) {}

  void on(const char *uri, std::function<void()> handler) { handlers_[uri] = std::move(handler); }
  void begin() { begun_ = true; }
  void handleClient() { handleClientCalls_++; }
  void send(int code, const char *contentType, const String &content) {
    response_.code = code;
    response_.contentType = contentType ? contentType : "";
    response_.body = content.std_str();
    response_.sent = true;
  }

  // ── test hooks ──
  struct Response {
    bool sent = false;
    int code = 0;
    std::string contentType;
    std::string body;
  };
  // Runs the handler registered for `uri`; code 404 if there is none.
  Response testGet(const std::string &uri) {
    response_ = Response();
    auto it = handlers_.find(uri);
    if (it == handlers_.end()) {
      response_.code = 404;
      return response_;
    }
    it->second();
    return response_;
  }
  bool testBegun() const { return begun_; }
  int testPort() const { return port_; }
  unsigned long testHandleClientCalls() const { return handleClientCalls_; }

 private:
  int port_;
  bool begun_ = false;
  unsigned long handleClientCalls_ = 0;
  std::map<std::string, std::function<void()>> handlers_;
  Response response_;
};
