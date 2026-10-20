"""Private aiogram account overview with inline Ping and Switch controls."""
import asyncio
import math
import re
import secrets
import time


class TelegramBot:
    PAGE_SIZE = 25

    def __init__(self, settings, command, choices=lambda refresh=False: []):
        self.settings = settings
        self.command = command
        self.choices = choices
        self.state = 'Stopped'
        self.actions = {}

    @staticmethod
    def user_ids(value):
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError('Telegram user IDs must be comma-separated numbers')
        text = str(value).strip()
        if not text:
            return ()
        parts = [part.strip() for part in text.split(',')]
        if any(not re.fullmatch(r'[1-9][0-9]{0,15}', part) for part in parts):
            raise ValueError('Enter positive numeric Telegram user IDs separated by commas')
        return tuple(dict.fromkeys(parts))

    @classmethod
    def identity(cls, settings):
        try:
            users = tuple(sorted(cls.user_ids(settings.get('allowed_user_id', ''))))
        except ValueError:
            users = ()
        return (settings.get('token'), users, settings.get('enabled') is True)

    def authorized(self, user, chat, settings, stop):
        return (not stop.is_set() and settings.get('enabled') is True
                and self.identity(self.settings()) == self.identity(settings)
                and user is not None and not user.is_bot and chat.type == 'private'
                and str(user.id) in self.identity(settings)[1] and chat.id == user.id)

    @staticmethod
    def number(value):
        if isinstance(value, bool):
            return None
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def clean(value, limit=100):
        return ' '.join(str(value or '').split())[:limit]

    @staticmethod
    def duration(seconds):
        minutes = max(0, int(seconds // 60))
        if minutes >= 1440:
            return f'{minutes // 1440}d {minutes % 1440 // 60}h'
        return f'{minutes // 60}h {minutes % 60}m' if minutes >= 60 else f'{minutes}m'

    def describe(self, item, number):
        label = self.clean(item['label'])
        lines = [f"{number}. {'●' if item.get('active') else '○'} {label}" +
                 (' · Active' if item.get('active') else '')]
        if item['kind'] == 'api':
            lines.append('API · Model: ' + (self.clean(item.get('model'), 80) or 'Default'))
            usage = item.get('usage') or {}
            remaining = self.number(usage.get('remaining'))
            if remaining is not None:
                lines.append(f'Quota remaining: {remaining:g}')
            else:
                lines.append('Quota: unavailable' if usage.get('failed') else 'Quota: not fetched')
            if usage.get('failed') and remaining is not None:
                lines.append('Refresh failed; showing cached quota')
            lines.append('Ping: unavailable for API accounts')
        else:
            email = self.clean(item.get('email'))
            if email and email != label:
                lines.append(email)
            lines.append('Plan: ' + (self.clean(item.get('plan'), 40) or 'Unknown'))
            usage = item.get('usage') or {}
            for duration, title in ((300, '5h'), (10080, '7d')):
                window = next((value for value in usage.values() if isinstance(value, dict)
                               and self.number(value.get('window_minutes')) == duration), {})
                remaining = self.number(window.get('remaining_percent'))
                if remaining is None:
                    used = self.number(window.get('used_percent'))
                    remaining = max(0, min(100, 100 - used)) if used is not None else None
                line = title + ': ' + (f'{max(0, min(100, remaining)):g}% remaining' if remaining is not None else 'Unknown')
                reset = self.number(window.get('resets_at'))
                if reset is not None:
                    line += ' · reset ' + ('due' if reset <= time.time() else 'in ' + self.duration(reset - time.time()))
                lines.append(line)
            credits = usage.get('credits') or {}
            balance = self.number(credits.get('balance'))
            if credits.get('unlimited'):
                lines.append('Credits: unlimited')
            elif balance is not None:
                lines.append(f'Credits: {balance:g}')
            resets = self.number(usage.get('reset_credits'))
            if resets is not None:
                lines.append(f'Reset credits: {resets:g}')
        updated = self.number(usage.get('updated_at'))
        if updated is not None:
            lines.append('Usage checked ' + self.duration(time.time() - updated) + ' ago')
        if (usage.get('refresh') or {}).get('status') == 'error':
            lines.append('Refresh failed; showing cached limits')
        return '\n'.join(lines)

    async def render(self, bot, chat_id, text, keyboard, message_id=None):
        if message_id is None:
            await bot.send_message(chat_id, text, reply_markup=keyboard)
        else:
            from aiogram.exceptions import TelegramBadRequest
            try:
                await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=keyboard)
            except TelegramBadRequest as error:
                if 'message is not modified' not in error.message.lower():
                    raise

    def button(self, label, command, item, settings, page, chat_id):
        from aiogram.types import InlineKeyboardButton
        key = secrets.token_urlsafe(12)
        self.actions[key] = (command, time.monotonic(), self.identity(settings), page, self.clean(item['label']), chat_id)
        return InlineKeyboardButton(text=label, callback_data='do:' + key)

    async def show_overview(self, bot, chat_id, settings, page=0, message_id=None, refresh=False, notice=''):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        try:
            items = await asyncio.to_thread(self.choices, refresh)
            sections = [(item, self.describe(item, index + 1)) for index, item in enumerate(items)]
        except Exception:
            sections = []
            notice = 'Could not load accounts. Try Refresh usage.'
        # Keep all accounts together until Telegram's text/button limits require pages.
        pages = [[]]
        size = 0
        for item, text in sections:
            units = len(text.encode('utf-16-le')) // 2 + 2
            if pages[-1] and (len(pages[-1]) >= self.PAGE_SIZE or size + units > 3300):
                pages.append([])
                size = 0
            pages[-1].append((item, text))
            size += units
        page = max(0, min(page, len(pages) - 1))
        now = time.monotonic()
        self.actions = {key: value for key, value in self.actions.items() if now - value[1] < 120}
        rows = []
        for item, _ in pages[page]:
            label = self.clean(item['label'], 60)
            ping = '/ping ' + item['id'] if item['kind'] == 'account' else None
            switch = ('/profile ' if item['kind'] == 'api' else '/switch ') + item['id']
            rows.append([self.button(label, None, item, settings, page, chat_id),
                         self.button('Ping' if ping else '—', ping, item, settings, page, chat_id),
                         self.button('✓ Active' if item.get('active') else 'Switch',
                                     None if item.get('active') else switch, item, settings, page, chat_id)])
        while len(self.actions) > 2048:
            self.actions.pop(next(iter(self.actions)))
        navigation = []
        if page:
            navigation.append(InlineKeyboardButton(text='Previous', callback_data=f'view:{page - 1}'))
        if page + 1 < len(pages):
            navigation.append(InlineKeyboardButton(text='Next', callback_data=f'view:{page + 1}'))
        if navigation:
            rows.append(navigation)
        rows.append([InlineKeyboardButton(text='Refresh usage', callback_data=f'refresh:{page}')])
        text = 'Accounts' + (f' · {page + 1}/{len(pages)}' if len(pages) > 1 else '')
        if notice:
            text += '\n' + notice
        text += '\n\n' + ('\n\n'.join(section for _, section in pages[page]) or 'No accounts saved.')
        text += '\n\nName / email | Ping | Switch'
        await self.render(bot, chat_id, text, InlineKeyboardMarkup(inline_keyboard=rows), message_id)

    async def on_message(self, message, bot, settings, stop):
        if not self.authorized(message.from_user, message.chat, settings, stop):
            return
        if time.time() - message.date.timestamp() > 120 or not message.text:
            return
        if message.text.strip().split('@', 1)[0] == '/start':
            from aiogram.types import ReplyKeyboardRemove
            from aiogram.exceptions import TelegramAPIError
            sent = await bot.send_message(message.chat.id, 'Keyboard updated.', reply_markup=ReplyKeyboardRemove())
            await self.show_overview(bot, message.chat.id, settings)
            try:
                await bot.delete_message(message.chat.id, sent.message_id)
            except TelegramAPIError:
                pass
        else:
            await self.show_overview(bot, message.chat.id, settings)

    async def on_callback(self, callback, bot, settings, stop):
        from aiogram.types import Message
        message = callback.message
        if not isinstance(message, Message) or not self.authorized(callback.from_user, message.chat, settings, stop):
            await callback.answer('Access denied.', show_alert=True)
            return
        data = callback.data or ''
        parts = data.split(':')
        if len(parts) == 2 and parts[0] in ('view', 'refresh') and parts[1].isdigit():
            refresh = parts[0] == 'refresh'
            await callback.answer('Refreshing usage…' if refresh else None)
            await self.show_overview(bot, message.chat.id, settings, int(parts[1]), message.message_id, refresh)
            return
        key = data[3:] if data.startswith('do:') else ''
        action = self.actions.get(key)
        if not action or time.monotonic() - action[1] > 120 or action[2] != self.identity(settings) or action[5] != message.chat.id:
            await callback.answer('Buttons refreshed. Choose an account again.', show_alert=True)
            await self.show_overview(bot, message.chat.id, settings, message_id=message.message_id)
            return
        command, _, _, page, label, _ = action
        if command is None:
            await callback.answer(label + ' · use Ping or Switch; API Ping is unavailable.')
            return
        self.actions.pop(key, None)
        await callback.answer('Running…')
        if stop.is_set() or self.identity(self.settings()) != self.identity(settings):
            return
        try:
            await asyncio.to_thread(self.command, command)
            notice = ('Ping completed: ' if command.startswith('/ping ') else 'Account selected: ') + label
        except Exception:
            notice = 'Operation failed. Check the account and server configuration in WebUI.'
        await self.show_overview(bot, message.chat.id, settings, page, message.message_id, notice=notice)

    async def session(self, settings, stop):
        from aiogram import Bot, Dispatcher
        from aiogram.client.session.aiohttp import AiohttpSession
        dispatcher = Dispatcher(disable_fsm=True)
        async def message_handler(message, bot):
            await self.on_message(message, bot, settings, stop)
        async def callback_handler(callback_query, bot):
            await self.on_callback(callback_query, bot, settings, stop)
        dispatcher.message.register(message_handler)
        dispatcher.callback_query.register(callback_handler)
        bot = Bot(settings['token'], session=AiohttpSession(timeout=15))
        try:
            # Discard backlog without changing any existing webhook configuration.
            updates = await bot.get_updates(offset=-1, limit=1, timeout=0,
                                            allowed_updates=['message', 'callback_query'])
            offset = updates[-1].update_id + 1 if updates else None
            self.actions.clear()
            while not stop.is_set() and self.identity(self.settings()) == self.identity(settings):
                updates = await bot.get_updates(offset=offset, timeout=2,
                                                allowed_updates=['message', 'callback_query'])
                self.state = 'Connected (aiogram)'
                for update in updates:
                    offset = update.update_id + 1
                    if stop.is_set() or self.identity(self.settings()) != self.identity(settings):
                        break
                    try:
                        await dispatcher.feed_update(bot, update)
                    except Exception:
                        # Never log raw updates, account output or credential-bearing errors.
                        self.state = 'Telegram reply failed; retrying polling.'
        finally:
            await bot.session.close()
            await dispatcher.storage.close()
            self.actions.clear()

    async def run_async(self, stop):
        try:
            while not stop.is_set():
                try:
                    settings = self.settings()
                    token, user_id, enabled = self.identity(settings)
                    if not enabled or not token or not user_id:
                        self.state = 'Disabled'
                        await asyncio.sleep(.5)
                        continue
                    await self.session(settings, stop)
                except ImportError:
                    self.state = 'Install Telegram dependencies: pip install -r requirements-telegram.txt'
                    await asyncio.sleep(1)
                except Exception:
                    self.state = 'Connection failed; check token, network and existing webhook/poller.'
                    for _ in range(10):
                        if stop.is_set():
                            break
                        await asyncio.sleep(.5)
        finally:
            self.state = 'Stopped'

    def run(self, stop):
        asyncio.run(self.run_async(stop))
