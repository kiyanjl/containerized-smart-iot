#!/usr/bin/env python3
"""Helper script to get your Telegram chat ID."""

import requests

# Your bot token from BotFather
BOT_TOKEN = "8662597519:AAFnHEMcjDK7QDtjZDDtxPp9pbDH8K4Be4s"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def get_chat_id():
    """Get the most recent chat ID from your bot."""
    try:
        # Get updates from the bot
        response = requests.get(f"{TELEGRAM_API}/getUpdates", timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("ok") or not data.get("result"):
            print("No messages found! Please send a message to your bot first!")
            print("   Open Telegram, find @smartwarehouse_alert_bot, and send '/start'")
            return None
        
        # Get the most recent update
        latest_update = data["result"][-1]
        chat_id = latest_update["message"]["chat"]["id"]
        first_name = latest_update["message"]["chat"].get("first_name", "User")
        
        print("Found your chat ID!")
        print(f"   Name: {first_name}")
        print(f"   Chat ID: {chat_id}")
        print()
        print("Add this to your .env file:")
        print(f"   TELEGRAM_CHAT_ID={chat_id}")
        return chat_id
        
    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    print("Getting your Telegram Chat ID...")
    print()
    chat_id = get_chat_id()
    if chat_id:
        print()
        print("Next steps:")
        print("1. Add TELEGRAM_CHAT_ID to your .env file")
        print("2. Run: docker-compose restart alert-service")
