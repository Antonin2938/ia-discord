import google.generativeai as genai
from dotenv import load_dotenv
import os

# --- CONFIGURATION ---
# Charge les variables d'environnement de ton fichier .env
load_dotenv()

# Prends la PREMIÈRE clé de ta liste pour s'authentifier
GEMINI_API_KEYS = os.getenv('GEMINI_API_KEYS', '')
if not GEMINI_API_KEYS:
    print("Erreur : Assure-toi que GEMINI_API_KEYS est bien défini dans ton fichier .env")
    exit()

first_key = GEMINI_API_KEYS.split(',')[0]
genai.configure(api_key=first_key)

print("--- Modèles disponibles pour ta clé API ---")

# Parcourt tous les modèles et affiche ceux qui sont utiles
for model in genai.list_models():
  # On ne garde que les modèles qui peuvent générer du contenu
  if 'generateContent' in model.supported_generation_methods:
    print(f"Nom du modèle : {model.name}")
    print(f"  Description : {model.description}\n")

print("--- Fin de la liste ---")
print("\nConseil : Pour la génération d'images, le modèle actuel est 'imagen-3.0-generate-002'.")
print("Pour le texte, 'gemini-1.5-flash' ou 'gemini-1.5-pro' sont d'excellents choix.")
