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

MEMORY_FILE = "memory.txt"
WHITELIST_FILE = "whitelist.txt"
# Dictionnaire pour stocker l'historique par salon, puis par utilisateur
# Structure : { channel_id: { user_id: [messages] } }
convo_history = {}

if not all([DISCORD_TOKEN, GEMINI_API_KEYS, PPLX_API_KEY, OWNER_ID]):
    print("Erreur : Variables d'environnement manquantes.")
    exit()

# --- FONCTIONS POUR GÉRER LA WHITELIST ---
def read_whitelist():
    """Lit les ID depuis le fichier whitelist.txt et les retourne sous forme de liste d'entiers."""
    try:
        with open(WHITELIST_FILE, 'r', encoding='utf-8') as f:
            return [int(line.strip()) for line in f if line.strip().isdigit()]
    except FileNotFoundError:
        with open(WHITELIST_FILE, 'w', encoding='utf-8') as f: pass
        return []

def add_to_whitelist(user_id):
    """Ajoute un ID utilisateur au fichier whitelist.txt."""
    with open(WHITELIST_FILE, 'a', encoding='utf-8') as f:
        f.write(str(user_id) + "\n")

def remove_from_whitelist(user_id):
    """Retire un ID utilisateur du fichier whitelist.txt."""
    current_ids = read_whitelist()
    new_ids = [uid for uid in current_ids if uid != int(user_id)]
    with open(WHITELIST_FILE, 'w', encoding='utf-8') as f:
        for uid in new_ids:
            f.write(str(uid) + "\n")

# --- PRÉPARATION DES UTILISATEURS AUTORISÉS ---
authorized_ids = []
try:
    OWNER_ID_INT = int(OWNER_ID)
    authorized_ids.append(OWNER_ID_INT)
    authorized_ids.extend(read_whitelist())
    authorized_ids = list(set(authorized_ids))
    print(f"Utilisateurs autorisés : {authorized_ids}")
except (ValueError, TypeError):
    print("Erreur : OWNER_ID n'est pas un nombre valide.")
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

# --- FONCTIONS UTILITAIRES (MÉMOIRE, URL) ---
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
        text = '\n'.join(chunk for chunk in (phrase.strip() for line in (line.strip() for line in soup.get_text().splitlines()) for phrase in line.split("  ")) if chunk)
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
            if isinstance(destination, discord.User) and message_to_reply: message_to_reply = None
            if first_message and message_to_reply: await message_to_reply.reply(chunk); first_message = False
            else: await destination.send(chunk)
        except discord.errors.Forbidden: break

@client.event
async def on_ready():
    print("---")
    print(f'Connecté en tant que compte utilisateur : {client.user}')
    print("Prêt à recevoir des commandes !")
    print("---")

@client.event
async def on_message(message):
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = f'<@{client.user.id}>' in message.content or f'<@!{client.user.id}>' in message.content
    is_convo_mode = not is_dm and message.channel.id in convo_history and message.author.id in convo_history[message.channel.id]
    
    if not (is_dm or is_mention or is_convo_mode) or message.author == client.user or message.author.id not in authorized_ids:
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Commande détectée par : {message.author.name}")

    try:
        if is_mention:
            command_content = message.content.replace(f'<@{client.user.id}>', '').replace(f'<@!{client.user.id}>', '').strip()
        else:
            command_content = message.content.strip()

        first_word = command_content.split()[0].lower() if command_content else ''
        
        # --- GESTION DES COMMANDES SPÉCIALES ---
        if first_word in ['start-convo', 'start-conversation']:
            if is_dm:
                await message.channel.send("Le mode conversation n'est pas disponible en message privé.")
                return
            if message.channel.id not in convo_history:
                convo_history[message.channel.id] = {}
            convo_history[message.channel.id][message.author.id] = []
            await message.reply("✅ Mémoire de conversation activée pour ce salon. Je me souviendrai de nos échanges. Dites `end-convo` pour oublier.")
            return
        if first_word in ['end-convo', 'end-conversation']:
            if is_dm: return
            if message.channel.id in convo_history:
                convo_history[message.channel.id].pop(message.author.id, None)
            await message.reply("☑️ Mémoire de conversation désactivée pour ce salon. J'ai oublié notre discussion.")
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
            
            next_api_key = next(key_cycler)
            genai.configure(api_key=next_api_key)
            model_name_display = "Gemini (gemini-1.5-flash)"
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt_gemini = f"Fais un résumé clair et concis en français du contenu de la page web suivante :\n\n--- CONTENU DE LA PAGE ---\n{page_text}"
            reponse_ia = await model.generate_content_async(prompt_gemini)
            reponse_finale = reponse_ia.text
            
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

            if command in ['r', 'purge']:
                if len(cmd_parts) < 2 or not cmd_parts[1].isdigit():
                    await message.reply("❌ Usage : `r [nombre]` (ex: `r 10`)")
                    return
                
                amount_to_delete = int(cmd_parts[1])
                if not 1 <= amount_to_delete <= 100:
                    await message.reply("❌ Le nombre doit être entre 1 et 100.")
                    return
                
                try:
                    await message.delete()
                except discord.Forbidden:
                    print("Je n'ai pas la permission de supprimer le message de commande.")

                messages_to_delete = []
                async for msg in message.channel.history(limit=200):
                    if msg.author == client.user:
                        messages_to_delete.append(msg)
                        if len(messages_to_delete) >= amount_to_delete:
                            break
                
                if messages_to_delete:
                    await message.channel.delete_messages(messages_to_delete)
                return

            if command in ['add-whitelist', 'awl']:
                if len(cmd_parts) < 2:
                    await message.reply("❌ Usage : `add-whitelist [@mention ou ID]`")
                    return
                try:
                    user_id_to_add = int(re.findall(r'\d+', cmd_parts[1])[0])
                    current_authorized = [OWNER_ID_INT] + read_whitelist()
                    if user_id_to_add in current_authorized:
                        await message.reply(f"ℹ️ L'utilisateur `{user_id_to_add}` est déjà autorisé.")
                        return
                    add_to_whitelist(user_id_to_add)
                    authorized_ids.append(user_id_to_add)
                    await message.reply(f"✅ L'utilisateur `{user_id_to_add}` a été ajouté à la whitelist.")
                except (IndexError, ValueError):
                    await message.reply("❌ ID invalide.")
                return

            if command in ['remove-whitelist', 'rwl']:
                if len(cmd_parts) < 2:
                    await message.reply("❌ Usage : `remove-whitelist [@mention ou ID]`")
                    return
                try:
                    user_id_to_remove = int(re.findall(r'\d+', cmd_parts[1])[0])
                    if user_id_to_remove == OWNER_ID_INT:
                        await message.reply("❌ Vous ne pouvez pas vous retirer vous-même de la liste.")
                        return
                    current_authorized = [OWNER_ID_INT] + read_whitelist()
                    if user_id_to_remove not in current_authorized:
                        await message.reply(f"ℹ️ L'utilisateur `{user_id_to_remove}` n'est pas dans la whitelist.")
                        return
                    remove_from_whitelist(user_id_to_remove)
                    if user_id_to_remove in authorized_ids: authorized_ids.remove(user_id_to_remove)
                    await message.reply(f"✅ L'utilisateur `{user_id_to_remove}` a été retiré de la whitelist.")
                except (IndexError, ValueError):
                    await message.reply("❌ ID invalide.")
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

        if not question and not message.attachments:
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

        # --- NOUVEAU : LECTURE DES FICHIERS ATTACHÉS ---
        file_context = ""
        if message.attachments:
            for attachment in message.attachments:
                if attachment.filename.lower().endswith('.txt'):
                    try:
                        file_content_bytes = await attachment.read()
                        file_content_str = file_content_bytes.decode('utf-8')
                        file_context += f"\n--- CONTENU DU FICHIER '{attachment.filename}' ---\n"
                        file_context += file_content_str
                        file_context += f"\n--- FIN DU FICHIER '{attachment.filename}' ---\n"
                        print(f"Fichier .txt '{attachment.filename}' lu et ajouté au contexte.")
                    except Exception as e:
                        print(f"Erreur lors de la lecture du fichier {attachment.filename}: {e}")
                        await message.reply(f"Désolé, je n'ai pas pu lire le fichier `{attachment.filename}`.")

        # --- LOGIQUE DE CONTEXTE AMÉLIORÉE ---
        contexte_final = ""
        if not is_dm and message.channel.id in convo_history and message.author.id in convo_history[message.channel.id]:
            contexte_final = "\n".join(convo_history[message.channel.id][message.author.id])
            print(f"Utilisation du contexte de conversation pour {message.author.name} dans le salon {message.channel.name}.")
        else:
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

        if message.author.id == OWNER_ID_INT:
            user_title = f"ton propriétaire, {message.author.display_name}"
        else:
            user_title = f"l'utilisateur autorisé, {message.author.display_name}"

        # NOUVEAU : Le prompt système inclut maintenant le contenu des fichiers
        prompt_system = (
            "Tu es 'AI-Context', un assistant personnel. Tu as accès à plusieurs types d'informations pour répondre à la question :\n"
            "1. Une mémoire à long terme avec des faits importants.\n"
            "2. Le contenu d'un ou plusieurs fichiers texte fournis par l'utilisateur.\n"
            "3. Un contexte de conversation récent.\n\n"
            "--- MÉMOIRE À LONG TERME ---\n"
            f"{long_term_memory if long_term_memory else 'Aucune information en mémoire.'}\n"
            "--- FIN MÉMOIRE ---\n\n"
            "--- CONTENU DES FICHIERS ATTACHÉS ---\n"
            f"{file_context if file_context else 'Aucun fichier .txt fourni.'}\n"
            "--- FIN DES FICHIERS ---\n\n"
            "--- CONTEXTE DE LA CONVERSATION ---\n"
            f"{contexte_final if contexte_final else 'Aucun contexte fourni.'}\n"
            "--- FIN CONTEXTE ---\n\n"
            f"Question de {user_title} : {question if question else 'Analyse le(s) document(s) fourni(s) et fais-en un résumé pertinent.'}"
        )

        if use_web:
            model_name_pplx = "sonar"
            model_name_display = f"Perplexity ({model_name_pplx})"
            print(f"Utilisation du modèle Web Perplexity...")
            prompt_pplx = prompt_system.replace("Tu es 'AI-Context'...", "You are a helpful AI assistant...")
            messages_pplx = [{"role": "system", "content": "You are a helpful AI assistant that answers questions using web search and provided context."}, {"role": "user", "content": prompt_pplx}]
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
        
        # Mise à jour de l'historique de conversation par salon
        if not is_dm and message.channel.id in convo_history and message.author.id in convo_history[message.channel.id]:
            convo_history[message.channel.id][message.author.id].append(f"Utilisateur ({message.author.display_name}): {question}")
            convo_history[message.channel.id][message.author.id].append(f"Assistant (AI-Context): {reponse_finale}")

        message_final = f"**Question :** {question or 'Analyse de document'}\n**Modèle utilisé :** `{model_name_display}`\n\n---\n\n{reponse_finale}"

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
