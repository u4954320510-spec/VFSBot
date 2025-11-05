# Multi-User Guide for VFSBot

This guide explains how to set up VFSBot for multiple users and administrators.

## Overview

VFSBot supports multiple administrators and can notify multiple Telegram channels or groups. Each user can control their own instance through Telegram commands with proper access control.

## Configuration

### Admin Setup

Admins can control the bot using Telegram commands. Configure admins in `config.ini`:

```ini
[TELEGRAM]
auth_token = your_bot_token_here
channel_id = your_channel_id
admin_ids = admin_id_1 admin_id_2 admin_id_3
```

**How to get admin IDs:**
1. Add your bot to a chat and send a message
2. Visit: `https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates`
3. Find your `from.id` in the response

### Multiple Channels/Groups

To notify multiple Telegram channels, you have two options:

#### Option 1: Single Bot, Multiple Channels (Recommended)
Modify `VFSBot.py` to send notifications to multiple channels:

```python
# In the notify section of check_appointment():
channels = [self.channel_id, other_channel_id, another_channel_id]
for channel in channels:
    await context.bot.send_message(chat_id=channel, text=message)
```

Update `config.ini`:
```ini
[TELEGRAM]
channel_ids = channel_id_1 channel_id_2 channel_id_3
```

#### Option 2: Multiple Bot Instances
Run separate VFSBot instances for different VFS centers or users:

1. Create separate directories for each instance:
   ```
   VFSBot/
   VFSBot-admin1/
   VFSBot-admin2/
   ```

2. Each directory has its own `config.ini` with different:
   - `url` (VFS center)
   - `email` and `password`
   - `channel_id` and `admin_ids`
   - `interval`

3. Run each instance as a separate process

## User Roles

### Admin (Full Control)
- `/start` - Start appointment checking
- `/quit` - Stop the bot
- `/setting` - Update configuration
- `/help` - Display help

### Regular Users (View Only)
- Can receive notifications in the channel
- Cannot execute bot commands

To restrict access to specific admins, the `AdminHandler` class checks `user_id` against `admin_ids` in the configuration.

## Setup Steps

### 1. Create Telegram Bot
```bash
# Contact @BotFather on Telegram
# Use /newbot command
# Get your API token
```

### 2. Configure Admins
Edit `config.ini` and add all admin user IDs (space-separated):
```ini
admin_ids = 123456789 987654321
```

### 3. Add Bot to Channel/Group
1. Create a Telegram channel or group
2. Add your bot as an administrator
3. Get the channel ID (negative number like `-1001234567890`)
4. Set it in `config.ini`:
   ```ini
   channel_id = -1001234567890
   ```

### 4. Run the Bot
```bash
python VFSBot.py
```

## Best Practices

### Security
- Keep `auth_token` private - never commit it to Git
- Use strong passwords for VFS credentials
- Rotate tokens if compromised
- Only add trusted admins

### Performance
- Use appropriate `interval` values (avoid too frequent checks)
- If monitoring multiple centers, use separate instances
- Monitor bot uptime and restart automatically using systemd or similar

### Monitoring Multiple Centers
For each VFS center, create a separate instance:

```ini
# config-uzb.ini (Uzbekistan)
[VFS]
url = https://visa.vfsglobal.com/uzb/ru/lva

# config-kaz.ini (Kazakhstan)
[VFS]
url = https://visa.vfsglobal.com/kz/en/lva
```

Run multiple instances:
```bash
python VFSBot.py config-uzb.ini &
python VFSBot.py config-kaz.ini &
```

### Persistence
- `record.txt` stores previously notified appointments
- Each bot instance should have its own `record.txt`
- This prevents duplicate notifications

## Troubleshooting

### Bot not responding to commands
- Check that user ID is in `admin_ids`
- Verify `auth_token` is correct
- Ensure bot has permission to send messages in the channel

### Notifications not sent
- Check `channel_id` is correct and negative (for private channels/groups)
- Ensure bot is an admin in the channel with message permissions
- Check `record.txt` exists and is writable

### Multiple instances interfering
- Use separate directories with separate configs
- Ensure each has unique `channel_id` and credentials
- Check port conflicts if using webhooks

## Example Multi-Admin Setup

```ini
[VFS]
url = https://visa.vfsglobal.com/uzb/ru/lva
email = user@example.com
password = secure_password
interval = 30

[TELEGRAM]
auth_token = 1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh
channel_id = -1001234567890
admin_ids = 111111111 222222222 333333333
```

Users with IDs 111111111, 222222222, and 333333333 can control the bot. All users in the channel receive notifications.

## Additional Resources

- [python-telegram-bot Documentation](https://python-telegram-bot.readthedocs.io/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Get Your Chat ID](https://stackoverflow.com/questions/32423837/telegram-bot-how-to-get-a-group-chat-id)
