import sys
import subprocess
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def install_and_restart():
    print("Detecting missing dependencies. Installing...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "../requirements.txt"])
        print("Installation successful! Restarting...")

        os.execl(sys.executable, sys.executable, *sys.argv)
    except Exception as e:
        print(f"An error occurred during installation: {e}")
        sys.exit(1)


try:
    import requests
    import mutagen
    import textual
    from ui import MusicDownloaderApp
except ImportError as e:
    print(f"CRITICAL IMPORT ERROR: {e}")
    install_and_restart()

if __name__ == "__main__":
    app = MusicDownloaderApp()
    app.run()