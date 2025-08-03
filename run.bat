@echo off
:start
echo Lancement du script Python...
python main.py
echo Le script s'est arrete. Redemarrage dans 5 secondes...
timeout /t 5
goto start
