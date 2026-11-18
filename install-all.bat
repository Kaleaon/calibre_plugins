@echo off
rem Download and install/update the whole calibre plugin pack (needs Python 3 and calibre on PATH,
rem or edit install_all.py calls below). Extra arguments are passed through to install_all.py.
where python >nul 2>nul && (python "%~dp0install_all.py" --download %* & exit /b %errorlevel%)
where py >nul 2>nul && (py -3 "%~dp0install_all.py" --download %* & exit /b %errorlevel%)
echo Python 3 was not found on PATH. Install Python, or install the pack from inside Calibre
echo with the Plugin Pack Manager plugin (PluginPackManager.zip).
exit /b 2
