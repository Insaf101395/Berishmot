import threading

def keep_alive():
    app.run(host='0.0.0.0', port=8080)

def run():
    threading.Thread(target=keep_alive).start()

if __name__ == "__main__":
    run()