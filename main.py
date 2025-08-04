import discord
import os
import google.generativeai as genai
from google.generativeai import types
from dotenv import load_dotenv
from datetime import datetime, timedelta
import dateparser
from openai import OpenAI
import shlex
import itertools
import re
import sys
import requests
from bs4 import BeautifulSoup
import asyncio
import json
import uuid

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
WHITELIST_IDS_FILE = os.getenv('WHITELIST_IDS_FILE', 'whitelist.txt')
WHITELIST_DARK = os.getenv('WHITELIST_DARK', '')

MEMORY_FILE = "memory.txt"
DARK_PROMPT_FILE = "dark.txt"
REMINDERS_FILE = "reminders.json"
convo_history = {}
dm_auto_reply_enabled = True

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

# --- FONCTIONS POUR GÉRER LA WHITELIST ---
def read_whitelist():
    try:
        with open(WHITELIST_IDS_FILE, 'r', encoding='utf-8') as f:
            return [int(line.strip()) for line in f if line.strip().isdigit()]
    except FileNotFoundError:
        with open(WHITELIST_IDS_FILE, 'w', encoding='utf-8') as f: pass
        return []
def write_whitelist(ids_list):
    with open(WHITELIST_IDS_FILE, 'w', encoding='utf-8') as f:
        for user_id in ids_list:
            f.write(str(user_id) + "\n")

# --- PRÉPARATION DES UTILISATEURS AUTORISÉS ---
authorized_ids = []
dark_authorized_ids = []
try:
    OWNER_ID_INT = int(OWNER_ID)
    authorized_ids.append(OWNER_ID_INT)
    authorized_ids.extend(read_whitelist())
    authorized_ids = list(set(authorized_ids))
    print(f"Utilisateurs autorisés : {authorized_ids}")

    dark_authorized_ids.append(OWNER_ID_INT)
    if WHITELIST_DARK:
        whitelisted_dark = [int(user_id) for user_id in WHITELIST_DARK.split(',') if user_id]
        dark_authorized_ids.extend(whitelisted_dark)
    dark_authorized_ids = list(set(dark_authorized_ids))
    print(f"Utilisateurs autorisés pour le mode --dark : {dark_authorized_ids}")

except (ValueError, TypeError):
    print("Erreur : ID invalide dans le .env ou whitelist.txt.")
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

# --- FONCTIONS POUR GÉRER LES RAPPELS ---
def load_reminders():
    try:
        with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_reminders(reminders):
    with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(reminders, f, indent=4)

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
        text = '\n'.join(chunk for chunk in (phrase.strip() for line in (line.strip() for line in soup.get_text().splitlines()) for phrase in line.split("  ")) if chunk)
        return text[:15000]
    except requests.RequestException as e:
        print(f"Erreur lors de la récupération de l'URL {url}: {e}")
        return None
def read_dark_prompt():
    try:
        with open(DARK_PROMPT_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        print(f"AVERTISSEMENT : Le fichier {DARK_PROMPT_FILE} n'a pas été trouvé.")
        return "MODE DARK ACTIF :"

# --- INITIALISATION DU CLIENT DISCORD ---
intents = discord.Intents.all()
client = discord.Client(intents=intents)

async def send_long_message(destination, text, message_to_reply=None):
    if len(text) > 6000:
        try:
            with open("response.txt", "w", encoding="utf-8") as f: f.write(text)
            await destination.send("La réponse est trop longue, la voici dans un fichier :", file=discord.File("response.txt"))
            os.remove("response.txt")
            return
        except Exception as e:
            print(f"Erreur lors de l'envoi du fichier : {e}")
            await destination.send("La réponse est très longue et une erreur est survenue lors de la création du fichier.")
            return

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

# --- TÂCHE DE FOND POUR VÉRIFIER LES RAPPELS ---
async def check_reminders_loop():
    await client.wait_until_ready()
    print("La boucle de vérification des rappels est active.")
    while not client.is_closed():
        reminders = load_reminders()
        now = datetime.now()
        reminders_to_keep = []
        reminders_processed = False

        for reminder in reminders:
            remind_time = datetime.fromisoformat(reminder['time'])
            if now >= remind_time:
                reminders_processed = True
                print(f"Envoi du rappel ID {reminder['id']}...")
                try:
                    destination = None
                    target_id = int(reminder['target_id'])
                    if reminder['is_dm']:
                        target_user = await client.fetch_user(target_id)
                        if target_user:
                            destination = target_user
                    else:
                        destination = client.get_channel(int(reminder['channel_id']))

                    if destination:
                        await destination.send(f"⏰ **Rappel de la part de <@{reminder['author_id']}> :**\n> {reminder['message']}")
                    
                    if reminder.get('repeat_interval'):
                        interval = timedelta(days=reminder['repeat_interval'])
                        reminder['time'] = (remind_time + interval).isoformat()
                        reminders_to_keep.append(reminder)
                except Exception as e:
                    print(f"Erreur lors de l'envoi du rappel {reminder['id']}: {e}")
            else:
                reminders_to_keep.append(reminder)
        
        if reminders_processed:
            save_reminders(reminders_to_keep)

        await asyncio.sleep(60)

@client.event
async def on_ready():
    print("---")
    print(f'Connecté en tant que compte utilisateur : {client.user}')
    print("Prêt à recevoir des commandes !")
    client.loop.create_task(check_reminders_loop())
    print("---")

@client.event
async def on_message(message):
    global dm_auto_reply_enabled
    is_dm_channel = isinstance(message.channel, discord.DMChannel)
    is_dm_active = is_dm_channel and dm_auto_reply_enabled
    is_mention = f'<@{client.user.id}>' in message.content or f'<@!{client.user.id}>' in message.content
    is_convo_mode = not is_dm_channel and message.channel.id in convo_history and message.author.id in convo_history.get(message.channel.id, {})
    
    if not (is_dm_active or is_mention or is_convo_mode) or message.author == client.user or message.author.id not in authorized_ids:
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Commande détectée par : {message.author.name}")

    try:
        if is_mention:
            command_content = message.content.replace(f'<@{client.user.id}>', '').replace(f'<@!{client.user.id}>', '').strip()
        else:
            command_content = message.content.strip()

        first_word = command_content.split()[0].lower() if command_content else ''
        
        # --- GESTION DES COMMANDES SPÉCIALES ---
        if first_word in ['rappel', 'remind']:
            cmd_parts = command_content.split()
            if len(cmd_parts) > 1 and cmd_parts[1].lower() == 'liste':
                reminders = load_reminders()
                user_reminders = [r for r in reminders if r['author_id'] == message.author.id]
                if not user_reminders:
                    await message.author.send("Vous n'avez aucun rappel programmé.")
                    return
                response = "**Voici vos rappels programmés :**\n"
                for r in user_reminders:
                    remind_time = datetime.fromisoformat(r['time']).strftime('%d/%m/%Y à %H:%M')
                    response += f"- **ID:** `{r['id']}` | **Pour :** <@{r['target_id']}> | **Quand :** {remind_time}\n"
                    response += f"  > *Message :* {r['message']}\n"
                await message.author.send(response)
                return

            if len(cmd_parts) > 1 and cmd_parts[1].lower() == 'supprime':
                if len(cmd_parts) < 3:
                    await message.reply("❌ Usage : `rappel supprime [ID]`")
                    return
                reminder_id_to_delete = cmd_parts[2]
                reminders = load_reminders()
                new_reminders = [r for r in reminders if not (r['id'] == reminder_id_to_delete and r['author_id'] == message.author.id)]
                if len(new_reminders) < len(reminders):
                    save_reminders(new_reminders)
                    await message.reply(f"✅ Le rappel avec l'ID `{reminder_id_to_delete}` a été supprimé.")
                else:
                    await message.reply("❌ Impossible de trouver ce rappel, ou vous n'avez pas la permission de le supprimer.")
                return

            is_dm = '--dm' in command_content
            separator = ' que ' if ' que ' in command_content else ' de ' if ' de ' in command_content else None
            if not separator:
                await message.reply("❌ Usage : `@IA rappel [@qui] [quand] que [quoi]`")
                return

            parts = command_content.split(separator, 1)
            target_and_time_part = parts[0][len(first_word):].strip()
            raw_reminder_message = parts[1].strip().replace('--dm', '').strip()
            
            target_user = None
            if message.mentions and client.user in message.mentions and len(message.mentions) > 1:
                target_user = [m for m in message.mentions if m.id != client.user.id][0]
            elif 'moi' in target_and_time_part.lower():
                target_user = message.author
            
            if not target_user:
                await message.reply("❌ Vous devez spécifier une cible (`moi` ou une @mention).")
                return

            time_str = target_and_time_part.replace('moi', '').replace(f'<@{target_user.id}>', '').strip()
            remind_time = dateparser.parse(time_str, settings={'PREFER_DATES_FROM': 'future'})

            if not remind_time:
                await message.reply(f"❌ Je n'ai pas compris le moment du rappel : `{time_str}`.")
                return

            # NOUVEAU : Reformulation du message par l'IA
            await message.reply(f"✅ Rappel programmé pour <@{target_user.id}> le {remind_time.strftime('%d/%m/%Y à %H:%M')}. Je reformule le message...")
            next_api_key = next(key_cycler)
            genai.configure(api_key=next_api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            reformulation_prompt = (
                f"La tâche est de reformuler une demande de rappel en un message clair, amical et bienveillant. "
                f"Le message final sera envoyé à '{target_user.display_name}'. "
                f"La demande originale de la part de '{message.author.display_name}' est : '{raw_reminder_message}'. "
                f"Rédige le message du rappel final. Ne commence pas par 'Rappel :' ou 'Salut', va directement à l'essentiel de manière naturelle."
            )
            response = await model.generate_content_async(reformulation_prompt)
            final_reminder_message = response.text

            if is_dm and target_user.id != client.user.id:
                is_friend = any(friend.id == target_user.id for friend in client.user.friends)
                if not is_friend:
                    try:
                        await target_user.add_friend()
                        await message.reply(f"ℹ️ Pour que le rappel en DM fonctionne, <@{target_user.id}> et moi devons être amis. Je viens de lui envoyer une demande.")
                    except Exception as e:
                        print(f"Impossible d'envoyer une demande d'ami : {e}")

            new_reminder = {
                'id': str(uuid.uuid4())[:8], 'author_id': message.author.id, 'target_id': target_user.id,
                'channel_id': message.channel.id, 'time': remind_time.isoformat(), 'message': final_reminder_message,
                'is_dm': is_dm, 'repeat_interval': None
            }

            reminders = load_reminders()
            reminders.append(new_reminder)
            save_reminders(reminders)
            return

        if first_word in ['start-convo', 'start-conversation', 'end-convo', 'end-conversation', 'resume-url', 'summarize-url']:
            # ... (code identique)
            return

        # --- GESTION DES COMMANDES ADMINISTRATIVES ET MÉMOIRE (Propriétaire uniquement) ---
        admin_commands = ['reboot', 'r', 'purge', 'add-whitelist', 'awl', 'remove-whitelist', 'rwl', 'mem', 'memorise', 'mémorise', 'oublie tout', 'clear-memory', 'montre-memoire', 'show-memory', 'dm-reply', 'dmreply', 'awtd', 'add-whitelist-dark']
        if first_word in admin_commands:
            if message.author.id != OWNER_ID_INT:
                await message.reply("❌ Désolé, cette commande est réservée au propriétaire du bot.")
                return
            # ... (le reste du code admin est identique)
            return

        # --- PARSING DE LA COMMANDE IA ---
        aliases = { '-p': '--private', '-w': '--web', '-dk': '--dark', '-nc': '--no-context', '-m': '--contexte_messages', '-d': '--contexte_depuis', '-cml': '--contexte_message_lien' }
        command_parts = command_content.split()
        command_parts = [aliases.get(part, part) for part in command_parts]
        question_parts = []
        params = { '--private': 'non', '--web': 'non', '--modele': 'gemini-2.5-flash-lite', '--no-memoire': 'non', '--dark': 'non', '--no-context': 'non' }
        i = 0
        while i < len(command_parts):
            part = command_parts[i]
            if part.startswith('--'):
                flag = part.lower()
                if flag in ['--user', '--contexte_messages', '--contexte_depuis', '--contexte_message_lien', '--modele']:
                    if i + 1 < len(command_parts) and not command_parts[i+1].startswith('--'):
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
        is_dark_mode = params.get('--dark', 'non').lower() == 'oui'
        no_context = params.get('--no-context', 'non').lower() == 'oui'
        
        if is_dark_mode and message.author.id not in dark_authorized_ids:
            await message.reply("❌ Vous n'avez pas la permission d'utiliser le mode `--dark`.")
            return
        
        if message.author.id != OWNER_ID_INT or not is_private:
            destination = message.channel
            message_to_reply = message
        else:
            destination = message.author
            message_to_reply = None

        contexte_final = ""
        historique_brut = []
        if not is_dm_channel and message.channel.id in convo_history and message.author.id in convo_history[message.channel.id]:
            contexte_final = "\n".join(convo_history[message.channel.id][message.author.id])
        elif not no_context:
            contexte_messages_str = params.get('--contexte_messages')
            contexte_depuis_str = params.get('--contexte_depuis')
            message_link = params.get('--contexte_message_lien')

            if message_link:
                match = re.search(r'/channels/\d+/(\d+)/(\d+)', message_link)
                if match:
                    try:
                        channel_id, message_id = map(int, match.groups())
                        linked_channel = client.get_channel(channel_id)
                        if linked_channel:
                            linked_message = await linked_channel.fetch_message(message_id)
                            historique_brut.append(linked_message)
                        else:
                            print(f"Impossible de trouver le salon avec l'ID {channel_id}")
                    except Exception as e:
                        print(f"Erreur lors de la récupération du message lié : {e}")
                
            if contexte_depuis_str or contexte_messages_str or not (contexte_depuis_str or contexte_messages_str or message_link):
                limit = int(contexte_messages_str) if contexte_messages_str else 10
                after = dateparser.parse(contexte_depuis_str, settings={'PREFER_DATES_FROM': 'past', 'DATE_ORDER': 'DMY'}) if contexte_depuis_str else None
                
                async for msg in message.channel.history(limit=limit + 1, after=after, oldest_first=True if after else False):
                    historique_brut.append(msg)
                if not after: historique_brut.reverse()
            
            if target_user_str:
                target_user_id = re.findall(r'\d+', target_user_str)
                if target_user_id:
                    target_user_id = int(target_user_id[0])
                    historique_brut = [msg for msg in historique_brut if msg.author.id == target_user_id]
            
            unique_messages = {msg.id: msg for msg in historique_brut}.values()
            historique_brut = sorted(list(unique_messages), key=lambda m: m.created_at)

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

        if is_dark_mode:
            base_prompt = read_dark_prompt()
            prompt_system = (
                f"{base_prompt}\n\n"
                "--- MÉMOIRE À LONG TERME ---\n"
                f"{long_term_memory if long_term_memory else 'Aucune information en mémoire.'}\n"
                "--- FIN MÉMOIRE ---\n\n"
                "--- CONTEXTE DE LA CONVERSATION ---\n"
                f"{contexte_final if contexte_final else 'Aucun contexte fourni.'}\n"
                "--- FIN CONTEXTE ---\n\n"
                f"Question de {user_title} : {question}"
            )
        else:
            prompt_system = (
                "Tu es 'AI-Context', un assistant personnel. Tu as accès à trois types d'informations :\n"
                "1. Une mémoire à long terme avec des faits importants.\n"
                "2. Un contexte de conversation récent.\n"
                "3. La question actuelle de l'utilisateur.\n\n"
                "--- MÉMOIRE À LONG TERME ---\n"
                f"{long_term_memory if long_term_memory else 'Aucune information en mémoire.'}\n"
                "--- FIN MÉMOIRE ---\n\n"
                "--- CONTEXTE DE LA CONVERSATION ---\n"
                f"{contexte_final if contexte_final else 'Aucun contexte fourni.'}\n"
                "--- FIN CONTEXTE ---\n\n"
                f"Question de {user_title} : {question}"
            )

        if use_web:
            model_name_pplx = "sonar"
            model_name_display = f"Perplexity ({model_name_pplx})"
            print(f"Utilisation du modèle Web Perplexity...")
            prompt_pplx = prompt_system.replace("Tu es 'AI-Context', un assistant personnel.", "You are a helpful AI assistant.")
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
        
        if not is_dm_channel and message.channel.id in convo_history and message.author.id in convo_history[message.channel.id]:
            convo_history[message.channel.id][message.author.id].append(f"Utilisateur ({message.author.display_name}): {question}")
            convo_history[message.channel.id][message.author.id].append(f"Assistant (AI-Context): {reponse_finale}")

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
