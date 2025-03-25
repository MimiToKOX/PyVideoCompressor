import os
import sys
import subprocess
from PyQt6.QtWidgets import QApplication, QMainWindow, QFileDialog, QInputDialog, QMessageBox
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QThread, pyqtSignal, QTimer
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QObject, pyqtSlot

def get_ffmpeg_path(exe_name):
    base = os.path.abspath("./ffmpeg/bin")
    if sys.platform == "win32":
        return os.path.join(base, exe_name + ".exe")
    return os.path.join(base, exe_name)


class CompressorThread(QThread):
    progress_updated = pyqtSignal(int)
    compression_done = pyqtSignal(str)

    def __init__(self, input_path, output_path, target_size_mb, duration_sec):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.target_size_mb = target_size_mb
        self.duration_sec = duration_sec

    def run(self):
        ffmpeg_path = get_ffmpeg_path("ffmpeg")
        audio_bitrate = 128 * 1024
        target_total_bits = self.target_size_mb * 1024 * 1024 * 8
        video_bitrate = int((target_total_bits / self.duration_sec) - audio_bitrate)
        if video_bitrate < 100_000:
            video_bitrate = 100_000

        command = [
            ffmpeg_path,
            "-y",
            "-i", self.input_path,
            "-c:v", "libx264",
            "-b:v", str(video_bitrate),
            "-preset", "slow",
            "-profile:v", "high",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-vf", "scale='min(1280,iw)':-2",
            self.output_path
        ]

        process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)

        for line in process.stderr:
            if "time=" in line:
                timestamp = line.split("time=")[-1].split(" ")[0]
                h, m, s = [float(x) for x in timestamp.split(":")]
                seconds = h * 3600 + m * 60 + s
                percent = int((seconds / self.duration_sec) * 100)
                self.progress_updated.emit(min(percent, 100))

        process.wait()
        if os.path.exists(self.output_path):
            self.compression_done.emit(self.output_path)


class Bridge(QObject):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.percent = 0
        
    @pyqtSlot()
    def selectFile(self):
        self.main_window.select_file_and_start()
        
    @pyqtSlot(str)
    def openFolder(self, path):
        self.main_window.open_folder(path)
        
    @pyqtSlot(int)
    def setPercent(self, val):
        self.percent = val
        
    @pyqtSlot(result=int)
    def getPercent(self):
        return self.percent


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.view = QWebEngineView()
        self.setCentralWidget(self.view)
        self.setWindowTitle("VideoCompressor")
        
        self.bridge = Bridge(self)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        
        self.view.setHtml(self.html_content())
        self.resize(900, 600)
        QTimer.singleShot(500, self.show_welcome_screen)

    def show_welcome_screen(self):
        self.view.page().runJavaScript("showWelcomeScreen();")

    def select_file_and_start(self):
        input_path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik wideo", "", "Pliki wideo (*.mp4 *.avi *.mkv *.mov *.wmv)")
        if not input_path:
            self.view.page().runJavaScript("showWelcomeScreen();")
            return

        try:
            duration = self.get_duration(input_path)
        except Exception as e:
            QMessageBox.critical(self, "Błąd FFprobe", f"Nie udało się odczytać długości filmu:\n{e}")
            self.view.page().runJavaScript("showWelcomeScreen();")
            return

        target_size_mb, ok = QInputDialog.getInt(self, "Rozmiar docelowy", "Do ilu MB chcesz zmniejszyć?", 20, 1, 500)
        if not ok:
            self.view.page().runJavaScript("showWelcomeScreen();")
            return

        base, ext = os.path.splitext(input_path)
        output_path = base + f"_compressed{ext}"

        self.view.page().runJavaScript("showCompressionScreen();")
        
        self.thread = CompressorThread(input_path, output_path, target_size_mb, duration)
        self.thread.progress_updated.connect(self.update_progress)
        self.thread.compression_done.connect(self.compression_finished)
        self.thread.start()

    def get_duration(self, input_path):
        ffprobe_path = get_ffmpeg_path("ffprobe")
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError(result.stderr)
        return float(result.stdout.strip())

    def update_progress(self, percent):
        self.view.page().runJavaScript(f"updateProgress({percent});")

    def compression_finished(self, output_path):
        js_path = output_path.replace('\\', '\\\\')
        self.view.page().runJavaScript(f"showCompletionScreen('{js_path}');")

    def open_folder(self, output_path):
        folder_path = os.path.dirname(output_path)
        if sys.platform == 'win32':
            os.startfile(folder_path)
        elif sys.platform == 'darwin':
            subprocess.run(["open", folder_path])
        else:
            subprocess.run(["xdg-open", folder_path])

    def html_content(self):
        return """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>VideoCompressor</title>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background: linear-gradient(135deg, #000000, #121212, #1e1e1e);
            color: #fff;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            overflow: hidden;
        }
        
        .container {
            width: 80%;
            max-width: 800px;
            text-align: center;
            background: rgba(30, 30, 30, 0.7);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            border: 1px solid rgba(50, 50, 50, 0.5);
        }
        
        h1 {
            font-size: 36px;
            margin-bottom: 20px;
            color: #ffffff;
            text-shadow: 0 2px 5px rgba(0, 0, 0, 0.5);
        }
        
        p {
            font-size: 18px;
            margin-bottom: 30px;
            line-height: 1.6;
            color: #cccccc;
        }
        
        .btn {
            background: #333333;
            color: white;
            border: none;
            padding: 15px 30px;
            font-size: 18px;
            border-radius: 50px;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.3);
            outline: none;
            margin: 10px;
            font-weight: bold;
        }
        
        .btn:hover {
            background: #444444;
            transform: translateY(-3px);
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.4);
        }
        
        .btn:active {
            transform: translateY(1px);
        }
        
        .btn-secondary {
            background: #222222;
            border: 1px solid #444444;
        }
        
        .btn-secondary:hover {
            background: #333333;
        }
        
        .progress-container {
            width: 100%;
            background: rgba(40, 40, 40, 0.5);
            border-radius: 30px;
            overflow: hidden;
            height: 30px;
            margin: 30px 0;
            box-shadow: inset 0 2px 5px rgba(0, 0, 0, 0.3);
            position: relative;
        }
        
        .progress-bar {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #333333, #555555);
            text-align: center;
            line-height: 30px;
            color: white;
            font-weight: bold;
            transition: width 0.3s ease;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.5);
        }
        
        .screen {
            display: none;
        }
        
        .active {
            display: block;
            animation: fadeIn 0.5s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .icon {
            font-size: 80px;
            margin-bottom: 20px;
            color: #ffffff;
        }
        
        .file-info {
            background: rgba(20, 20, 20, 0.5);
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
            text-align: left;
            border: 1px solid #333333;
        }
        
        .file-info p {
            margin: 5px 0;
            font-size: 16px;
            color: #aaaaaa;
        }
        
        .completion-message {
            font-size: 24px;
            color: #7f7f7f;
            margin: 20px 0;
            font-weight: bold;
        }
        
        .logo {
            font-size: 24px;
            font-weight: bold;
            position: absolute;
            top: 20px;
            left: 20px;
            color: white;
            text-shadow: 0 2px 5px rgba(0, 0, 0, 0.5);
        }
        
        .particles {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: -1;
        }
        
        .particle {
            position: absolute;
            background: rgba(100, 100, 100, 0.3);
            border-radius: 50%;
            pointer-events: none;
        }
        
        @keyframes float {
            0% {
                transform: translateY(0) translateX(0);
            }
            50% {
                transform: translateY(-20px) translateX(10px);
            }
            100% {
                transform: translateY(0) translateX(0);
            }
        }
    </style>
</head>
<body>
    <div class="particles" id="particles"></div>
    <div class="logo">VideoCompressor</div>
    
    <div class="container screen" id="welcomeScreen">
        <div class="icon">📹</div>
        <h1>VideoCompressor</h1>
        <p>Witaj w aplikacji do kompresji wideo! Wybierz plik wideo, który chcesz skompresować, a my zajmiemy się resztą.</p>
        <button class="btn" id="selectFileBtn">Wybierz plik wideo</button>
    </div>
    
    <div class="container screen" id="compressionScreen">
        <h1>Kompresowanie wideo...</h1>
        <p>Trwa kompresja twojego pliku. Proszę czekać.</p>
        <div class="progress-container">
            <div class="progress-bar" id="progressBar">0%</div>
        </div>
    </div>
    
    <div class="container screen" id="completionScreen">
        <div class="icon">✅</div>
        <h1>Kompresja zakończona!</h1>
        <p class="completion-message">Twój plik został pomyślnie skompresowany.</p>
        <div class="file-info" id="fileInfo">
            <p id="filePath"></p>
        </div>
        <button class="btn" id="openFolderBtn">Otwórz folder</button>
        <button class="btn btn-secondary" id="newCompressionBtn">Nowa kompresja</button>
    </div>
    
    <script>
        var bridge = null;
        var currentFilePath = "";
        
        document.addEventListener("DOMContentLoaded", function() {
            initializeApp();
        });
        
        function initializeApp() {
            new QWebChannel(qt.webChannelTransport, function(channel) {
                bridge = channel.objects.bridge;
                setupEventListeners();
                createParticles();
                showWelcomeScreen();
            });
        }
        
        function setupEventListeners() {
            document.getElementById("selectFileBtn").addEventListener("click", function() {
                if (bridge) {
                    bridge.selectFile();
                }
            });
            
            document.getElementById("openFolderBtn").addEventListener("click", function() {
                if (bridge) {
                    bridge.openFolder(currentFilePath);
                }
            });
            
            document.getElementById("newCompressionBtn").addEventListener("click", function() {
                showWelcomeScreen();
            });
        }
        
        function showWelcomeScreen() {
            hideAllScreens();
            document.getElementById("welcomeScreen").classList.add("active");
        }
        
        function showCompressionScreen() {
            hideAllScreens();
            document.getElementById("compressionScreen").classList.add("active");
            resetProgress();
        }
        
        function showCompletionScreen(filePath) {
            hideAllScreens();
            currentFilePath = filePath;
            document.getElementById("filePath").textContent = "Lokalizacja: " + filePath;
            document.getElementById("completionScreen").classList.add("active");
        }
        
        function hideAllScreens() {
            const screens = document.querySelectorAll(".screen");
            screens.forEach(screen => {
                screen.classList.remove("active");
            });
        }
        
        function updateProgress(val) {
            const bar = document.getElementById("progressBar");
            bar.style.width = val + "%";
            bar.innerText = val + "%";
        }
        
        function resetProgress() {
            updateProgress(0);
        }
        
        function createParticles() {
            const particlesContainer = document.getElementById("particles");
            const particleCount = 20;
            
            for (let i = 0; i < particleCount; i++) {
                const particle = document.createElement("div");
                particle.classList.add("particle");
                
                const size = Math.random() * 4 + 2;
                particle.style.width = size + "px";
                particle.style.height = size + "px";
                
                particle.style.left = Math.random() * 100 + "%";
                particle.style.top = Math.random() * 100 + "%";
                
                particle.style.opacity = Math.random() * 0.5 + 0.1;
                
                const duration = Math.random() * 10 + 10;
                particle.style.animation = `float ${duration}s linear infinite`;
                particle.style.animationDelay = `${Math.random() * 5}s`;
                
                particlesContainer.appendChild(particle);
            }
        }
    </script>
</body>
</html>
        """

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()