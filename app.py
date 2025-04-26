import eventlet
eventlet.monkey_patch()

import cv2
import threading
import time
from flask import Flask, Response, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")


latest_frame = None
frame_lock   = threading.Lock()
clients      = set()

def capture_frames():
    global latest_frame
    cap = cv2.VideoCapture(0)  
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    
    
    


    if not cap.isOpened():
        raise RuntimeError("Could not open video source")

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        ret2, jpeg = cv2.imencode(".jpg", frame)
        if not ret2:
            continue

        with frame_lock:
            latest_frame = jpeg.tobytes()


        time.sleep(0.01)

    cap.release()

def generate_frames():
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    while True:
       
        with frame_lock:
            frame = latest_frame
        if frame:
            yield boundary + frame + b"\r\n"
       
        eventlet.sleep(0)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

@socketio.on("connect")
def handle_connect():
    ip = request.remote_addr
    clients.add(ip)
    emit("viewer_count", {"count": len(clients)}, broadcast=True)

@socketio.on("disconnect")
def handle_disconnect():
    ip = request.remote_addr
    clients.discard(ip)
    emit("viewer_count", {"count": len(clients)}, broadcast=True)

@socketio.on("chat_message")
def handle_chat(data):
    ip  = request.remote_addr
    msg = data.get("msg", "").strip()
    if msg:
        emit("chat_message", {"user": ip, "msg": msg}, broadcast=True)

if __name__ == "__main__":
    
    t = threading.Thread(target=capture_frames, daemon=True)
    t.start()
    
    socketio.run(app, host="0.0.0.0", port=5000)
