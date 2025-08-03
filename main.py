import discord
import os
import google.generativeai as genai
from google.generativeai import types
from dotenv import load_dotenv
from datetime import datetime
import dateparser
from openai import OpenAI
import shlex
import itertools
import re
import sys
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO

# --- AVERTISSEMENT IMPORTANT ---
# Ce script est un "self-bot". Son utilisation est une violation des
# Conditions d'Utilisation de Discord et peut mener au bannissement du compte.
# À utiliser à des fins éducatives et conceptuelles uniquement.

# --- CONFIGURATION ---
print("Chargement de la configuration...")
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEYS = os.getenv('GEMINI_API_KEYS', '')
PPLX_API_KEY = os.getenv('PPLX_API_KEY')
OWNER_ID = os.getenv('OWNER_ID')
WHITELIST_IDS = os.getenv('WHITELIST_IDS', '')

MEMORY_FILE = "memory.txt"
convo_mode_users = set()

if not all([DISCORD_TOKEN, GEMINI_API_KEYS, PPLX_API_KEY, OWNER_ID]):
    print("Erreur : Variables d'environnement manquantes.")
    exit()

# --- FONCTIONS POUR GÉRER LE FICHIER .ENV ---
def update_env_file(key_to_update, new_value):
    env_lines = []
    key_found = False
    try:
        with open('.env', 'r', encoding='utf-8') as f:
            env_lines = f.readlines()
        with open('.env', 'w', encoding='utf-8') as f:
            for line in env_lines:
                if line.strip().startswith(key_to_update):
                    f.write(f'{key_to_update}="{new_value}"\n')
                    key_found = True
                else:
                    f.write(line)
            if not key_found:
                 f.write(f'\n{key_to_update}="{new_value}"\n')
        return True
    except Exception as e:
        print(f"Erreur lors de la mise à jour du fichier .env : {e}")
        return False

# --- PRÉPARATION DES UTILISATEURS AUTORISÉS ---
authorized_ids = []
try:
    OWNER_ID_INT = int(OWNER_ID)
    authorized_ids.append(OWNER_ID_INT)
    if WHITELIST_IDS:
        whitelisted = [int(user_id) for user_id in WHITELIST_IDS.split(',') if user_id]
        authorized_ids.extend(whitelisted)
    print(f"Utilisateurs autorisés : {authorized_ids}")
except (ValueError, TypeError):
    print("Erreur : ID invalide dans le .env.")
    exit()

# --- PRÉPARATION DU POOL DE CLÉS GEMINI ---
gemini_keys_list = [key.strip() for key in GEMINI_API_KEYS.split(',')]
if not all(gemini_keys_list):
    print("Erreur : GEMINI_API_KEYS mal formatée.")
    exit()
key_cycler = itertools.cycle(gemini_keys_list)
print(f"{len(gemini_keys_list)} clés API Gemini chargées.")

pplx_client = OpenAI(api_key=PPLX_API_KEY, base_url="https://api.perplexity.ai")
print("Configuration terminée.")

# --- FONCTIONS UTILITAIRES ---
def read_memory():
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f: return f.read()
    except FileNotFoundError:
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f: pass
        return ""

def add_to_memory(text):
    with open(MEMORY_FILE, 'a', encoding='utf-8') as f: f.write(text + "\n")

def clear_memory():
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f: pass

def fetch_url_content(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        for script_or_style in soup(['script', 'style']):
            script_or_style.decompose()
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        return text[:15000]
    except requests.RequestException as e:
        print(f"Erreur lors de la récupération de l'URL {url}: {e}")
        return None

# --- INITIALISATION DU CLIENT DISCORD ---
intents = discord.Intents.all()
client = discord.Client(intents=intents)

async def send_long_message(destination, text, message_to_reply=None):
    chunks = []
    if len(text) <= 2000: chunks.append(text)
    else:
        current_chunk = ""
        for line in text.split('\n'):
            if len(current_chunk) + len(line) + 1 > 2000:
                chunks.append(current_chunk)
                current_chunk = ""
            current_chunk += line + "\n"
        if current_chunk: chunks.append(current_chunk)
    first_message = True
    for chunk in chunks:
        try:
            # En DM, on ne peut pas utiliser "reply", on envoie simplement le message
            if isinstance(destination, discord.User) and message_to_reply:
                message_to_reply = None

            if first_message and message_to_reply:
                await message_to_reply.reply(chunk)
                first_message = False
            else:
                await destination.send(chunk)
        except discord.errors.Forbidden: break

@client.event
async def on_ready():
    print("---")
    print(f'Connecté en tant que compte utilisateur : {client.user}')
    print("Prêt à recevoir des commandes !")
    print("---")

@client.event
async def on_message(message):
    # --- FILTRES DE DÉCLENCHEMENT ---
    # 1. Ignorer ses propres messages et ceux des utilisateurs non autorisés
    if message.author == client.user or message.author.id not in authorized_ids:
        return

    # 2. Définir les conditions de déclenchement
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = f'<@{client.user.id}>' in message.content or f'<@!{client.user.id}>' in message.content
    is_convo_mode = message.author.id in convo_mode_users and not is_dm

    # 3. Si aucune condition n'est remplie, on arrête
    if not (is_dm or is_mention or is_convo_mode):
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Commande détectée par : {message.author.name}")

    try:
        # On prépare le contenu de la commande en enlevant la mention si besoin
        if is_mention:
            command_content = message.content.replace(f'<@{client.user.id}>', '').replace(f'<@!{client.user.id}>', '').strip()
        else: # En DM ou en mode convo, tout le message est la commande
            command_content = message.content.strip()

        first_word = command_content.split()[0].lower() if command_content else ''
        
        # --- GESTION DES COMMANDES SPÉCIALES ---
        if first_word in ['start-convo', 'start-conversation']:
            if is_dm:
                await message.channel.send("Le mode conversation n'est pas nécessaire en message privé.")
                return
            convo_mode_users.add(message.author.id)
            await message.reply("✅ Mode conversation activé. Je réagirai à tous vos messages dans ce salon. Dites `end-convo` pour arrêter.")
            return
        if first_word in ['end-convo', 'end-conversation']:
            if is_dm: return # Commande inutile en DM
            convo_mode_users.discard(message.author.id)
            await message.reply("☑️ Mode conversation désactivé.")
            return

        if first_word in ['resume-url', 'summarize-url']:
            url = command_content[len(first_word):].strip()
            if not url.startswith('http'):
                await message.reply("❌ Erreur : Veuillez fournir une URL valide (ex: https://...).")
                return
            
            await message.reply(f"⏳ Je lis la page web `{url}`...")
            page_text = fetch_url_content(url)
            
            if not page_text:
                await message.reply("🔥 Impossible de récupérer le contenu de cette page.")
                return
            
            model_name_display = "Perplexity (sonar)"
            prompt_pplx = f"Voici le contenu d'une page web. Fais-en un résumé clair et concis en français.\n\n--- CONTENU ---\n{page_text}"
            messages_pplx = [{"role": "system", "content": "You are a summarization expert."}, {"role": "user", "content": prompt_pplx}]
            reponse_ia = pplx_client.chat.completions.create(model="sonar", messages=messages_pplx)
            reponse_finale = reponse_ia.choices[0].message.content
            
            message_final = f"**Résumé de l'URL :** {url}\n**Modèle utilisé :** `{model_name_display}`\n\n---\n\n{reponse_finale}"
            await send_long_message(message.channel, message_final, message_to_reply=message)
            return

        # --- GESTION DES COMMANDES ADMINISTRATIVES ET MÉMOIRE (Propriétaire uniquement) ---
        if message.author.id == OWNER_ID_INT:
            cmd_parts = command_content.split()
            command = cmd_parts[0].lower() if cmd_parts else ''

            if command == 'reboot':
                await message.reply("🔄 Redémarrage en cours...")
                await client.close()
                sys.exit(0)

            if command in ['add-whitelist', 'awl']:
                if len(cmd_parts) < 2:
                    await message.reply("❌ Usage : `add-whitelist [@mention ou ID]`")
                    return
                user_id_to_add = re.findall(r'\d+', cmd_parts[1])[0]
                current_whitelist = os.getenv('WHITELIST_IDS', '').split(',')
                current_whitelist = [uid for uid in current_whitelist if uid]
                if user_id_to_add in current_whitelist:
                    await message.reply(f"ℹ️ L'utilisateur `{user_id_to_add}` est déjà dans la whitelist.")
                    return
                current_whitelist.append(user_id_to_add)
                if update_env_file('WHITELIST_IDS', ",".join(current_whitelist)):
                    await message.reply(f"✅ L'utilisateur `{user_id_to_add}` a été ajouté. Redémarrez le script pour appliquer.")
                else:
                    await message.reply("🔥 Erreur lors de l'écriture dans le fichier .env.")
                return
            if command in ['remove-whitelist', 'rwl']:
                if len(cmd_parts) < 2:
                    await message.reply("❌ Usage : `remove-whitelist [@mention ou ID]`")
                    return
                user_id_to_remove = re.findall(r'\d+', cmd_parts[1])[0]
                current_whitelist = os.getenv('WHITELIST_IDS', '').split(',')
                if user_id_to_remove not in current_whitelist:
                    await message.reply(f"ℹ️ L'utilisateur `{user_id_to_remove}` n'est pas dans la whitelist.")
                    return
                current_whitelist.remove(user_id_to_remove)
                if update_env_file('WHITELIST_IDS', ",".join(current_whitelist)):
                    await message.reply(f"✅ L'utilisateur `{user_id_to_remove}` a été retiré. Redémarrez le script pour appliquer.")
                else:
                    await message.reply("🔥 Erreur lors de l'écriture dans le fichier .env.")
                return
            
            if command in ['mem', 'memorise', 'mémorise']:
                info_to_remember = command_content[len(command):].strip()
                if info_to_remember:
                    add_to_memory(info_to_remember)
                    await message.reply(f"✅ C'est noté ! J'ai ajouté à ma mémoire : \"{info_to_remember}\"")
                else:
                    await message.reply("❌ Erreur : Tu dois me donner quelque chose à mémoriser.")
                return
            if command_content.lower() in ['oublie tout', 'clear-memory']:
                clear_memory()
                await message.reply("🗑️ C'est fait, j'ai vidé ma mémoire.")
                return
            if command_content.lower() in ['montre-memoire', 'show-memory']:
                memory_content = read_memory()
                await send_long_message(message.channel, f"**Contenu de ma mémoire :**\n---\n{memory_content or 'Ma mémoire est vide.'}", message_to_reply=message)
                return

        # --- PARSING DE LA COMMANDE IA ---
        aliases = { '-p': '--private', '-w': '--web', '-m': '--contexte_messages', '-d': '--contexte_depuis' }
        command_parts = command_content.split()
        command_parts = [aliases.get(part, part) for part in command_parts]
        question_parts = []
        params = { '--private': 'non', '--web': 'non', '--modele': 'gemini-2.5-flash-lite', '--no-memoire': 'non' }
        i = 0
        while i < len(command_parts):
            part = command_parts[i]
            if part.startswith('--'):
                flag = part.lower()
                if flag == '--contexte_depuis':
                    value_parts = []
                    i += 1
                    while i < len(command_parts) and not command_parts[i].startswith('--'):
                        value_parts.append(command_parts[i])
                        i += 1
                    params[flag] = " ".join(value_parts)
                    continue
                if flag == '--user':
                    if i + 1 < len(command_parts):
                        params[flag] = command_parts[i+1]
                        i += 2
                    else: i += 1
                    continue
                if i + 1 < len(command_parts) and not command_parts[i+1].startswith('--'):
                    params[flag] = command_parts[i+1]
                    i += 2
                else:
                    params[flag] = 'oui'
                    i += 1
            else:
                question_parts.append(part)
                i += 1
        
        question = " ".join(question_parts).strip()

        if not question:
            await message.reply("Salut ! Mentionne-moi avec une question ou utilise une commande spéciale.")
            return

        is_private = params.get('--private', 'non').lower() == 'oui'
        ignore_memory = params.get('--no-memoire', 'non').lower() == 'oui'
        target_user_str = params.get('--user')
        
        if message.author.id != OWNER_ID_INT or not is_private:
            destination = message.channel
            message_to_reply = message
        else:
            destination = message.author
            message_to_reply = None

        historique_brut = []
        contexte_messages_str = params.get('--contexte_messages')
        contexte_depuis_str = params.get('--contexte_depuis')
        if contexte_depuis_str or contexte_messages_str:
            limit = int(contexte_messages_str) if contexte_messages_str else None
            after = dateparser.parse(contexte_depuis_str, settings={'PREFER_DATES_FROM': 'past', 'DATE_ORDER': 'DMY'}) if contexte_depuis_str else None
            async for msg in message.channel.history(limit=limit, after=after, oldest_first=True if after else False):
                historique_brut.append(msg)
            if not after: historique_brut.reverse()
        
        if target_user_str:
            target_user_id = re.findall(r'\d+', target_user_str)
            if target_user_id:
                target_user_id = int(target_user_id[0])
                historique_brut = [msg for msg in historique_brut if msg.author.id == target_user_id]
        
        contexte_final = "\n".join([f"[{msg.created_at.strftime('%d/%m %H:%M')}] {msg.author.display_name}: {msg.content}" for msg in historique_brut if msg.id != message.id])
        
        reponse_finale = ""
        model_name_display = ""
        long_term_memory = "" if ignore_memory else read_memory()
        use_web = params.get('--web', 'non').lower() == 'oui'
        modele_gemini = params.get('--modele').strip('\'"')

        prompt_system = (
            "Tu es 'AI-Context', un assistant personnel. Tu as accès à trois types d'informations :\n"
            "1. Une mémoire à long terme avec des faits importants que ton propriétaire t'a demandés de retenir.\n"
            "2. Un contexte de conversation récent.\n"
            "3. La question actuelle de l'utilisateur.\n\n"
            "--- MÉMOIRE À LONG TERME ---\n"
            f"{long_term_memory if long_term_memory else 'Aucune information en mémoire.'}\n"
            "--- FIN MÉMOIRE ---\n\n"
            "--- CONTEXTE DE LA CONVERSATION ---\n"
            f"{contexte_final if contexte_final else 'Aucun contexte fourni.'}\n"
            "--- FIN CONTEXTE ---\n\n"
            f"Question de l'utilisateur ({message.author.display_name}) : {question}"
        )

        if use_web:
            model_name_pplx = "sonar"
            model_name_display = f"Perplexity ({model_name_pplx})"
            print(f"Utilisation du modèle Web Perplexity...")
            messages_pplx = [{"role": "system", "content": prompt_system.replace("Tu es 'AI-Context'...", "You are a helpful AI assistant...")}, {"role": "user", "content": question}]
            reponse_ia_complete = pplx_client.chat.completions.create(model=model_name_pplx, messages=messages_pplx)
            reponse_finale = reponse_ia_complete.choices[0].message.content
            if hasattr(reponse_ia_complete, 'search_results') and reponse_ia_complete.search_results:
                reponse_finale += "\n\n**Sources :**\n" + "\n".join([f"{i+1}. {s.get('title', 'N/A')} (<{s.get('url', '#')}>)" for i, s in enumerate(reponse_ia_complete.search_results)])
        else:
            next_api_key = next(key_cycler)
            print(f"Utilisation de la clé API Gemini se terminant par ...{next_api_key[-4:]}")
            genai.configure(api_key=next_api_key)
            model_name_display = f"Gemini ({modele_gemini})"
            print(f"Utilisation du modèle standard Gemini '{modele_gemini}'...")
            model = genai.GenerativeModel(modele_gemini)
            reponse_ia = await model.generate_content_async(prompt_system)
            reponse_finale = reponse_ia.text
        
        message_final = f"**Question :** {question}\n**Modèle utilisé :** `{model_name_display}`\n\n---\n\n{reponse_finale}"

        await send_long_message(destination, message_final, message_to_reply=message)
        print("Réponse envoyée avec succès.")

    except Exception as e:
        print(f"Une erreur inattendue est survenue : {e}")
        try:
            await message.author.send(f"Désolé, une erreur est survenue : `{e}`")
        except discord.errors.Forbidden:
            await message.reply(f"Désolé, une erreur est survenue et je n'ai pas pu vous envoyer les détails en privé.")

# --- DÉMARRAGE DU SCRIPT ---
print("Tentative de connexion à Discord avec un token utilisateur...")
try:
    client.run(DISCORD_TOKEN, bot=False)
except Exception as e:
    print(f"Une erreur critique est survenue : {e}")
