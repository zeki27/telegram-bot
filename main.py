import telebot
from pymongo import MongoClient
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import csv
import io
import datetime

# Telegram bot token
TELEGRAM_TOKEN = '7587069331:AAEYqXltmvhpO6BMuncXtTMPoeu0O7nIbu4'
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# MongoDB connection URI
MONGO_URI = 'mongodb+srv://zcount:1234@zcount.aw7ur.mongodb.net/?retryWrites=true&w=majority&appName=zcount'
client = MongoClient(MONGO_URI)
db = client['zcount']
collection = db['zcount']

# Threshold for high activity
HIGH_ACTIVITY_THRESHOLD = 100
TRACKED_USERS = set()  # Set of users to monitor closely

# Function to add user data to the database
def add_user_to_db(group_id, user_id, added_by):
    added_by = added_by or "Unknown"
    if added_by == "Unknown":
        return  # Skip adding if the added_by is unknown
    
    try:
        existing_user = collection.find_one({'group_id': group_id, 'user_id': user_id})
        if not existing_user:
            collection.insert_one({
                'group_id': group_id,
                'user_id': user_id,
                'added_by': added_by,
                'added_at': datetime.datetime.utcnow(),
                'left': False  # New field to track if the user has left
            })
            print(f"User {user_id} added by {added_by} in group {group_id}.")
        else:
            # Update the 'left' status if the user rejoined
            collection.update_one(
                {'group_id': group_id, 'user_id': user_id},
                {'$set': {'left': False}}
            )
            print(f"User {user_id} rejoined the group {group_id}.")
    except Exception as e:
        print(f"Error adding user to database: {e}")

# Function to mark a user as left when they leave the group
def remove_user_from_db(group_id, user_id):
    try:
        user_record = collection.find_one_and_update(
            {'group_id': group_id, 'user_id': user_id},
            {'$set': {'left': True}}  # Mark the user as left
        )
        if user_record:
            print(f"User {user_id} marked as left in group {group_id}.")
        else:
            print(f"User {user_id} was not found in group {group_id}.")
    except Exception as e:
        print(f"Error marking user as left in database: {e}")

# Listener for new members joining
@bot.message_handler(content_types=['new_chat_members'])
def handle_new_members(message):
    group_id = message.chat.id
    added_by = message.from_user.username or message.from_user.first_name or "Unknown"
    for new_member in message.new_chat_members:
        add_user_to_db(group_id, new_member.id, added_by)
        check_high_activity(group_id, added_by)
        if added_by in TRACKED_USERS:
            bot.send_message(group_id, f"🔍 Tracked user @{added_by} just added a new member.")

# Listener for users leaving
@bot.message_handler(content_types=['left_chat_member'])
def handle_left_members(message):
    group_id = message.chat.id
    left_member_id = message.left_chat_member.id
    remove_user_from_db(group_id, left_member_id)

# High activity check
def check_high_activity(group_id, username):
    count = collection.count_documents({'group_id': group_id, 'added_by': username, 'left': False})
    if count >= HIGH_ACTIVITY_THRESHOLD:
        try:
            for admin in bot.get_chat_administrators(group_id):
                bot.send_message(admin.user.id, f"⚠️ High activity alert! @{username} has added {count} users.")
        except Exception as e:
            print(f"Error sending high activity alert: {e}")

# Command for /count and /count @username
@bot.message_handler(commands=['count'])
def count_users(message):
    group_id = message.chat.id
    chat_id = message.from_user.id

    if not is_admin(group_id, chat_id):
        bot.send_message(group_id, "⚠️ You have no permission to use this command.")
        return

    args = message.text.split()
    if len(args) == 1:  # /count command without username
        # Count all active members in the group
        total_active_members = collection.count_documents({'group_id': group_id, 'left': False})
        
        # Display how many users each member has added
        result = collection.aggregate([
            {"$match": {"group_id": group_id, "added_by": {"$ne": "Unknown"}, "left": False}},
            {"$group": {"_id": "$added_by", "total": {"$sum": 1}}}
        ])
        
        count_message = f"*Total Active Members in Group*: {total_active_members}\n\n*User Addition Summary:*\n"
        for record in result:
            added_by = record['_id'] if record['_id'] else "Unknown"
            count_message += f"👤 *{added_by}* added *{record['total']}* users\n"
        
        bot.send_message(chat_id, count_message or "No user additions recorded.", parse_mode='Markdown')
    elif len(args) == 2 and args[1].startswith('@'):  # /count @username command
        username = args[1][1:]
        user_count = collection.count_documents({'group_id': group_id, 'added_by': username, 'left': False})
        bot.send_message(chat_id, f"👤 *{username}* has added *{user_count}* users (net).", parse_mode='Markdown')
    else:
        bot.send_message(chat_id, "⚠️ Invalid command usage. Use /count or /count @username.")

# Command for detailed report
@bot.message_handler(commands=['report'])
def send_report(message):
    group_id = message.chat.id
    chat_id = message.from_user.id

    if is_admin(group_id, chat_id):
        result = collection.aggregate([
            {"$match": {"group_id": group_id, "added_by": {"$ne": "Unknown"}, "left": False}},
            {"$group": {"_id": "$added_by", "total": {"$sum": 1}}}
        ])
        report = "*Detailed User Addition Report*\n"
        for record in result:
            added_by = record['_id'] if record['_id'] else "Unknown"
            report += f"👤 *{added_by}* added *{record['total']}* users\n"
        bot.send_message(chat_id, report or "No user additions recorded.", parse_mode='Markdown')
    else:
        bot.send_message(group_id, "⚠️ You have no permission to use this command.")

# Command to export user data to CSV
@bot.message_handler(commands=['export'])
def export_user_data(message):
    group_id = message.chat.id
    chat_id = message.from_user.id

    if is_admin(group_id, chat_id):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Group ID', 'User ID', 'Added By', 'Added At', 'Left'])
        
        for record in collection.find({'group_id': group_id, 'added_by': {"$ne": "Unknown"}}):
            writer.writerow([record['group_id'], record['user_id'], record['added_by'], record['added_at'], record['left']])
        
        output.seek(0)
        bot.send_document(chat_id, io.BytesIO(output.getvalue().encode('utf-8')), filename="group_report.csv")
        output.close()
    else:
        bot.send_message(group_id, "⚠️ You have no permission to use this command.")

# Function to check if a user is an admin
def is_admin(group_id, user_id):
    try:
        return user_id in [admin.user.id for admin in bot.get_chat_administrators(group_id)]
    except Exception as e:
        print(f"Error checking admin status: {e}")
        return False

# Main polling for bot
print("Bot is running...")
bot.polling()
