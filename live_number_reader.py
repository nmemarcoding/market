import sys
import time
import subprocess
import threading

import pyautogui
import pytesseract
from PIL import Image
from pynput import mouse

from PyQt5 import QtWidgets, QtCore, QtGui


# If needed, uncomment and set the Tesseract path manually:
# pytesseract.pytesseract.tesseract_cmd = "/opt/homebrew/bin/tesseract"


# ------------ STEP 1: LET USER DRAG A BOX IN ANY WINDOW ------------

def get_region_by_drag():
    coords = {"start": None, "end": None}

    def on_click(x, y, button, pressed):
        if pressed and coords["start"] is None:
            coords["start"] = (x, y)
            print(f"Start: {coords['start']}")
        elif not pressed and coords["start"] is not None and coords["end"] is None:
            coords["end"] = (x, y)
            print(f"End:   {coords['end']}")
            return False

    print("\n========================================")
    print(" Live Number Reader (macOS, optimized)")
    print("========================================")
    print("▶ Click and drag a box around the number in your window, then release.")
    print("   (Drag directly on your app, no popup window.)\n")

    with mouse.Listener(on_click=on_click) as listener:
        listener.join()

    if not coords["start"] or not coords["end"]:
        return None

    (x1, y1), (x2, y2) = coords["start"], coords["end"]
    left = int(min(x1, x2))
    top = int(min(y1, y2))
    width = int(abs(x2 - x1))
    height = int(abs(y2 - y1))
    width = max(1, width)
    height = max(1, height)
    return (left, top, width, height)


# ------------ STEP 2: OCR + VOICE (OPTIMIZED) ------------

def say(text):
    """Non-blocking voice output"""
    if text:
        subprocess.Popen(
            ["say", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )


# Allow digits, dot, and minus sign so you get the full numeric value
OCR_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789.-"

def read_number_from_region(region):
    """Optimized OCR - minimal allocations"""
    # Make sure region is ints
    left, top, width, height = region
    region_int = (int(left), int(top), int(width), int(height))

    img = pyautogui.screenshot(region=region_int).convert("L")
    raw = pytesseract.image_to_string(img, config=OCR_CONFIG)

    # Keep digits, dot, minus
    result = []
    for ch in raw:
        if ch.isdigit() or ch in ('.', '-'):
            result.append(ch)

    return ''.join(result) if result else ''


# ------------ STEP 3: MAIN WINDOW (MUTE + SELECT NEW AREA) ------------

class NumberWindow(QtWidgets.QWidget):
    speaking_toggle = QtCore.pyqtSignal(bool)
    reselect_requested = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Market Reader")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        self.label = QtWidgets.QLabel("—", alignment=QtCore.Qt.AlignCenter)
        font = self.label.font()
        font.setPointSize(18)
        font.setBold(True)
        self.label.setFont(font)

        self.btn_mute = QtWidgets.QPushButton("Mute Voice")
        self.btn_mute.setCheckable(True)
        self.btn_mute.toggled.connect(self.toggle_state)
        self.btn_mute.setChecked(True)  # start MUTED by default

        self.btn_reselect = QtWidgets.QPushButton("Select New Area")
        self.btn_reselect.clicked.connect(self.request_reselect)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.label)
        layout.addWidget(self.btn_mute)
        layout.addWidget(self.btn_reselect)
        self.setLayout(layout)

        # Let the window be resizable so long numbers fit
        self.setMinimumSize(220, 150)

        self._last_text = None

        self.show()

    def toggle_state(self, checked):
        if checked:
            self.btn_mute.setText("Unmute Voice")
            self.speaking_toggle.emit(False)
        else:
            self.btn_mute.setText("Mute Voice")
            self.speaking_toggle.emit(True)

    def request_reselect(self):
        self.reselect_requested.emit()

    def update_number(self, text):
        if text != self._last_text:
            self.label.setText(text if text else "—")
            self._last_text = text


# ------------ STEP 3.5: CURSOR BUBBLE (FOLLOW MOUSE) ------------

class CursorBubble(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Window
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)

        self.label = QtWidgets.QLabel("—", self)
        self.label.setAlignment(QtCore.Qt.AlignCenter)

        font = self.label.font()
        font.setPointSize(14)
        font.setBold(True)
        self.label.setFont(font)

        self.label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 180);
                color: white;
                padding: 4px 8px;
                border-radius: 8px;
            }
        """)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self.setLayout(layout)

        self.adjustSize()
        self.show()

        self.follow_timer = QtCore.QTimer(self)
        self.follow_timer.timeout.connect(self.follow_cursor)
        self.follow_timer.start(50)  # 20 fps

        self._last_pos = None
        self._last_text = None

    def follow_cursor(self):
        pos = QtGui.QCursor.pos()
        new_x = pos.x() + 16
        new_y = pos.y() + 20

        if self._last_pos is None or \
           abs(new_x - self._last_pos[0]) > 3 or \
           abs(new_y - self._last_pos[1]) > 3:
            self.move(new_x, new_y)
            self._last_pos = (new_x, new_y)

    def update_number(self, text):
        if text != self._last_text:
            self.label.setText(text if text else "—")
            self.adjustSize()
            self._last_text = text


# ------------ STEP 4: READER THREAD (REGION CAN CHANGE) ------------

class ReaderThread(QtCore.QThread):
    number_changed = QtCore.pyqtSignal(str)

    def __init__(self, region):
        super().__init__()
        self.region = region
        self.region_lock = threading.Lock()

        self.INTERVAL = 0.05
        self.MIN_STABLE_READS = 2

        self.speaking_enabled = False  # start MUTED by default
        self._stop = False
        self.paused = False

        self.last_spoken = None
        self.last_emitted = None
        self.candidate_value = None
        self.candidate_count = 0

    def set_speaking(self, enabled: bool):
        self.speaking_enabled = enabled

    def set_paused(self, paused: bool):
        self.paused = paused

    def update_region(self, region):
        with self.region_lock:
            self.region = region
        # Reset detection state so we don't mix old/new area
        self.last_spoken = None
        self.last_emitted = None
        self.candidate_value = None
        self.candidate_count = 0

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            if self.paused:
                time.sleep(self.INTERVAL)
                continue

            with self.region_lock:
                region = self.region

            value = read_number_from_region(region)

            if not value:
                time.sleep(self.INTERVAL)
                continue

            if value == self.candidate_value:
                self.candidate_count += 1
            else:
                self.candidate_value = value
                self.candidate_count = 1

            if self.candidate_count >= self.MIN_STABLE_READS:
                if self.candidate_value != self.last_emitted:
                    self.number_changed.emit(self.candidate_value)
                    self.last_emitted = self.candidate_value

                if self.speaking_enabled and self.candidate_value != self.last_spoken:
                    say(self.candidate_value)
                    self.last_spoken = self.candidate_value

                self.candidate_count = 0

            time.sleep(self.INTERVAL)


# ------------ STEP 5: MAIN ------------

def main():
    region = get_region_by_drag()
    if not region:
        print("❌ No region selected. Exiting.")
        return

    print(f"\n🎯 Selected region: {region}")
    print("🟢 Now watching (optimized for ~1Hz changes).")
    print("🪟 Main window shows the live number + mute toggle + 'Select New Area'.")
    print("📍 Tiny bubble follows your mouse showing the same number.")
    print("🔇 Voice is MUTED by default (click 'Unmute Voice' to enable).")
    print("✋ Press Ctrl+C in terminal or close the window to quit.\n")

    app = QtWidgets.QApplication(sys.argv)

    main_window = NumberWindow()
    bubble = CursorBubble()
    reader = ReaderThread(region)

    reader.number_changed.connect(main_window.update_number)
    reader.number_changed.connect(bubble.update_number)
    main_window.speaking_toggle.connect(reader.set_speaking)

    # Handle "Select New Area" button
    def handle_reselect():
        print("\n🔁 Reselect requested. Pausing reader and asking for new area...")
        reader.set_paused(True)

        def _reselect_worker():
            new_region = get_region_by_drag()
            if new_region:
                print(f"✅ New region selected: {new_region}")
                reader.update_region(new_region)
            else:
                print("⚠️ Reselect cancelled or failed, keeping old region.")
            reader.set_paused(False)

        threading.Thread(target=_reselect_worker, daemon=True).start()

    main_window.reselect_requested.connect(handle_reselect)

    def on_quit():
        reader.stop()
        reader.wait(1000)

    app.aboutToQuit.connect(on_quit)

    reader.start()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
