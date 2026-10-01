@echo off
echo Installing dependencies...
python -m pip install -r requirements.txt
python -m pip install pyinstaller
echo.
echo Building executable...
python -m PyInstaller --noconfirm --onefile --windowed --name "ReactiveImage" ^
  --hidden-import=PIL._tkinter_finder ^
  --hidden-import=sounddevice ^
  --hidden-import=numpy ^
  --hidden-import=pynput.keyboard ^
  --hidden-import=serial ^
  --hidden-import=serial.tools.list_ports ^
  reactive_image.py
echo.
echo Done. Executable is in: dist\ReactiveImage.exe
pause
