import sys
import time
import subprocess

import pyautogui
import pytesseract
from PIL import Image
from pynput import mouse

from PyQt5 import QtWidgets, QtCore, QtGui


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
        subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Pre-compiled config string (avoid recreation)
OCR_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789."

def read_number_from_region(region):
    """Optimized OCR - minimal allocations"""
    # Direct screenshot without intermediate variables
    img = pyautogui.screenshot(region=region).convert("L")
    
    # OCR with pre-compiled config
    raw = pytesseract.image_to_string(img, config=OCR_CONFIG)
    
    # Fast cleaning with early termination
    result = []
    for ch in raw:
        if ch.isdigit() or ch == '.':
            result.append(ch)
    
    return ''.join(result) if result else ''


# ------------ STEP 3: MAIN WINDOW (MINIMAL REPAINTS) ------------

class NumberWindow(QtWidgets.QWidget):
    speaking_toggle = QtCore.pyqtSignal(bool)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Market Reader")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        self.label = QtWidgets.QLabel("—", alignment=QtCore.Qt.AlignCenter)
        font = self.label.font()
        font.setPointSize(18)
        font.setBold(True)
        self.label.setFont(font)

        self.btn = QtWidgets.QPushButton("Mute Voice")
        self.btn.setCheckable(True)
        self.btn.toggled.connect(self.toggle_state)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.label)
        layout.addWidget(self.btn)
        self.setLayout(layout)

        self.setFixedSize(200, 110)
        
        # Cache to prevent unnecessary updates
        self._last_text = None
        
        self.show()

    def toggle_state(self, checked):
        if checked:
            self.btn.setText("Unmute Voice")
            self.speaking_toggle.emit(False)
        else:
            self.btn.setText("Mute Voice")
            self.speaking_toggle.emit(True)

    def update_number(self, text):
        # Only update if changed (critical for efficiency)
        if text != self._last_text:
            self.label.setText(text if text else "—")
            self._last_text = text


# ------------ STEP 3.5: CURSOR BUBBLE (OPTIMIZED FOR FREQUENT UPDATES) ------------

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

        # Optimized cursor following - 20 fps is sweet spot
        self.follow_timer = QtCore.QTimer(self)
        self.follow_timer.timeout.connect(self.follow_cursor)
        self.follow_timer.start(50)  # 20 fps
        
        # Cache for optimization
        self._last_pos = None
        self._last_text = None

    def follow_cursor(self):
        pos = QtGui.QCursor.pos()
        new_x = pos.x() + 16
        new_y = pos.y() + 20
        
        # Only move if position changed by >3px (reduce micro-moves)
        if self._last_pos is None or \
           abs(new_x - self._last_pos[0]) > 3 or \
           abs(new_y - self._last_pos[1]) > 3:
            self.move(new_x, new_y)
            self._last_pos = (new_x, new_y)

    def update_number(self, text):
        # Only update if text changed (critical when updating every second)
        if text != self._last_text:
            self.label.setText(text if text else "—")
            self.adjustSize()
            self._last_text = text


# ------------ STEP 4: READER THREAD (OPTIMIZED FOR 1-SECOND UPDATES) ------------

class ReaderThread(QtCore.QThread):
    number_changed = QtCore.pyqtSignal(str)

    def __init__(self, region):
        super().__init__()
        self.region = region

        # Fixed interval optimized for 1Hz updates
        # 50ms = 20 reads/sec, ensures we catch every change
        self.INTERVAL = 0.05
        self.MIN_STABLE_READS = 2

        self.speaking_enabled = True
        self._stop = False

    def set_speaking(self, enabled: bool):
        self.speaking_enabled = enabled

    def stop(self):
        self._stop = True

    def run(self):
        last_spoken = None
        last_emitted = None
        candidate_value = None
        candidate_count = 0

        while not self._stop:
            value = read_number_from_region(self.region)

            if not value:
                time.sleep(self.INTERVAL)
                continue

            # Check for stability
            if value == candidate_value:
                candidate_count += 1
            else:
                candidate_value = value
                candidate_count = 1

            # Once stable, process
            if candidate_count >= self.MIN_STABLE_READS:
                # Only emit if value changed (reduces Qt signal overhead)
                if candidate_value != last_emitted:
                    self.number_changed.emit(candidate_value)
                    last_emitted = candidate_value

                # Only speak if enabled AND different from last spoken
                if self.speaking_enabled and candidate_value != last_spoken:
                    say(candidate_value)
                    last_spoken = candidate_value

                candidate_count = 0

            time.sleep(self.INTERVAL)


# ------------ STEP 5: MAIN ------------

def main():
    region = get_region_by_drag()
    if not region:
        print("❌ No region selected. Exiting.")
        return

    print(f"\n🎯 Selected region: {region}")
    print("🟢 Now watching (optimized for 1Hz updates).")
    print("🪟 Main window shows the live number + mute toggle.")
    print("📍 Tiny bubble follows your mouse showing the same number.")
    print("✋ Press Ctrl+C in terminal or close the window to quit.\n")

    app = QtWidgets.QApplication(sys.argv)

    main_window = NumberWindow()
    bubble = CursorBubble()
    reader = ReaderThread(region)

    # Connect signals (Qt handles thread safety efficiently)
    reader.number_changed.connect(main_window.update_number)
    reader.number_changed.connect(bubble.update_number)
    main_window.speaking_toggle.connect(reader.set_speaking)

    def on_quit():
        reader.stop()
        reader.wait(1000)
        
    app.aboutToQuit.connect(on_quit)

    reader.start()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

    