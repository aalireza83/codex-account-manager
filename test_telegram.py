"""Telegram access checks, credential handling and native operation parity."""
import asyncio
import importlib.util
import json
import unittest
import threading
import time
from unittest.mock import AsyncMock, Mock, patch

import app
from telegram_bot import TelegramBot
from test_management import IsolatedManager
from test_accounts import auth, quota

TOKEN = '123456:' + 'synthetic_token_' * 3


class TelegramTests(IsolatedManager):
    def settings(self):
        return {'enabled': True, 'token': TOKEN, 'allowed_user_id': '42'}

    def test_private_persistence_redaction_and_validation(self):
        app.set_telegram_settings(self.settings())
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(TOKEN, json.dumps(app.settings_status()))
        self.assertTrue(app.telegram_status()['token_configured'])
        app.set_telegram_settings({'enabled': False, 'allowed_user_id': '42'})
        self.assertEqual(app.telegram_settings()['token'], TOKEN)
        previous = app.read_webui_config()
        for invalid in ({'enabled': True}, {**self.settings(), 'token': 'bad'},
                        {**self.settings(), 'allowed_user_id': '@user'}, {**self.settings(), 'enabled': 'true'}):
            with self.assertRaises(RuntimeError): app.set_telegram_settings(invalid)
            self.assertEqual(previous, app.read_webui_config())
        app.set_telegram_settings({'enabled': False, 'token': '', 'allowed_user_id': ''})
        self.assertFalse(app.telegram_status()['token_configured'])

    def test_multiple_admins_are_normalized_and_invalid_lists_do_not_overwrite_settings(self):
        app.set_telegram_settings({**self.settings(),'allowed_user_id':'42, 7,42'})
        self.assertEqual(app.telegram_status()['allowed_user_id'],'42,7')
        saved=app.read_webui_config()
        for value in ('42,,7','42,','42,abc','42,-7','42,0',[],None,True):
            with self.subTest(value=value),self.assertRaises(RuntimeError):
                app.set_telegram_settings({**self.settings(),'allowed_user_id':value})
            self.assertEqual(app.read_webui_config(),saved)
        self.assertEqual(TelegramBot.user_ids(42),('42',))

    def test_native_operations_and_aliases_are_shared(self):
        self.store.import_documents(auth(), 'Work')
        self.assertIn('Work', app.telegram_command('/accounts'))
        with patch('app.do_switch', return_value={'message': 'switched'}) as switch:
            self.assertEqual(app.telegram_command('/switch@my_bot Work'), 'switched')
            switch.assert_called_once_with('Work')
        with patch('app.run_account_ping', return_value='OK') as ping:
            self.assertEqual(app.telegram_command('/ping Work'), 'OK')
            ping.assert_called_once_with('Work')
        self.assertIn('/switch', app.telegram_command('/help'))
        self.assertIn('missing argument', app.telegram_command('/switch'))


    def test_overview_uses_native_usage_and_reports_the_effective_active_account(self):
        self.store.import_documents(auth(), 'Work')
        with self.store.switching('Work'):pass
        with patch('accounts.request_json', return_value=quota()):
            rows=app.telegram_choices(True)
        self.assertTrue(rows[0]['active'])
        self.assertEqual(rows[0]['usage']['primary']['remaining_percent'],77)
        self.assertEqual(rows[0]['usage']['secondary']['remaining_percent'],60)
        self.assertEqual(rows[0]['plan'],'plus')
        self.assertNotIn('access_token',json.dumps(rows))
        self.assertNotIn('refresh-user',json.dumps(rows))

    def test_api_usage_refresh_and_selection_are_in_the_same_overview(self):
        profile=app.create_api_profile({'name':'Provider','base_url':app.API_BASE_URL,'api_key':'synthetic-api-secret','model':'example-model'})
        with patch('app.AUTO_PING_STOP',threading.Event()),patch('app.fetch_api_profile_usage',return_value={
                'quota':{'remaining':42},'api_key':'synthetic-api-secret'}) as fetch:
            rows=app.telegram_choices(True)
        fetch.assert_called_once_with(profile['id'])
        self.assertEqual(rows[0]['usage']['remaining'],42)
        self.assertNotIn('synthetic-api-secret',json.dumps(rows))
        config=app.read_webui_config();config['active_profile']=profile['id'];app.write_webui_config(config)
        self.assertTrue(app.telegram_choices()[0]['active'])


@unittest.skipUnless(importlib.util.find_spec('aiogram'), 'Optional aiogram dependency is not installed')
class AiogramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from aiogram.types import Message
        self.settings={'enabled':True,'token':TOKEN,'allowed_user_id':'42'}
        self.stop=threading.Event()
        self.command=Mock(return_value='OK')
        self.choices=[{'id':'stable-key','label':'Work','email':'work@example.test','plan':'plus','kind':'account',
                       'usage':{'primary':{'window_minutes':300,'remaining_percent':77,'resets_at':time.time()+7200},
                                'secondary':{'window_minutes':10080,'remaining_percent':60},'updated_at':time.time()}},
                      {'id':'api-test','label':'API','kind':'api','model':'example-model'}]
        self.snapshot=Mock(side_effect=lambda refresh=False:self.choices)
        self.bot=TelegramBot(lambda:self.settings,self.command,self.snapshot)
        self.client=AsyncMock()
        self.message_data={'message_id':5,'date':int(time.time()),'text':'/start',
                           'from':{'id':42,'is_bot':False,'first_name':'Owner'},'chat':{'id':42,'type':'private'}}
        self.message=Message.model_validate(self.message_data)

    def callback(self,data,user_id=42):
        from aiogram.types import CallbackQuery
        return CallbackQuery.model_validate({'id':'callback-1','chat_instance':'private',
                   'from':{'id':user_id,'is_bot':False,'first_name':'Owner'},
                   'message':self.message_data,'data':data}).as_(self.client)

    async def overview(self):
        await self.bot.show_overview(self.client,42,self.settings)
        return self.client.send_message.call_args.kwargs['reply_markup']

    async def test_start_shows_limits_and_three_columns_without_an_extra_menu(self):
        from aiogram.types import InlineKeyboardMarkup,ReplyKeyboardRemove
        self.client.send_message.return_value.message_id=99
        await self.bot.on_message(self.message,self.client,self.settings,self.stop)
        calls=self.client.send_message.call_args_list
        self.assertIsInstance(calls[0].kwargs['reply_markup'],ReplyKeyboardRemove)
        text=calls[-1].args[1]
        for expected in ('work@example.test','Plan: plus','77% remaining','60% remaining','reset in','API','example-model'):
            self.assertIn(expected,text)
        self.assertNotIn('Active profile',text)
        markup=calls[-1].kwargs['reply_markup']
        self.assertIsInstance(markup,InlineKeyboardMarkup)
        self.assertEqual([button.text for button in markup.inline_keyboard[0]],['Work','Ping','Switch'])
        self.assertEqual([button.text for button in markup.inline_keyboard[1]],['API','—','Switch'])
        self.assertEqual(len(markup.inline_keyboard),3)
        self.client.edit_message_text.assert_not_called()
        self.client.delete_message.assert_awaited_once_with(42,99)
        self.command.assert_not_called()

    async def test_cleanup_notice_is_never_edited_and_delete_failure_is_harmless(self):
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import EditMessageText,DeleteMessage
        self.client.edit_message_text.side_effect=TelegramBadRequest(
            method=EditMessageText(text='Accounts',chat_id=42,message_id=99),message="message can't be edited")
        self.client.delete_message.side_effect=TelegramBadRequest(
            method=DeleteMessage(chat_id=42,message_id=99),message="message can't be deleted")
        await self.bot.on_message(self.message,self.client,self.settings,self.stop)
        self.assertIn('77% remaining',self.client.send_message.call_args.args[1])
        self.client.edit_message_text.assert_not_called()

    async def test_ping_targets_stable_id_and_cannot_execute_twice(self):
        markup=await self.overview()
        data=markup.inline_keyboard[0][1].callback_data
        self.choices.reverse()
        await self.bot.on_callback(self.callback(data),self.client,self.settings,self.stop)
        self.command.assert_called_once_with('/ping stable-key')
        self.assertIn('Ping completed: Work',self.client.edit_message_text.call_args.args[0])
        await self.bot.on_callback(self.callback(data),self.client,self.settings,self.stop)
        self.command.assert_called_once()

    async def test_native_and_api_switch_buttons_share_the_overview(self):
        markup=await self.overview()
        await self.bot.on_callback(self.callback(markup.inline_keyboard[0][2].callback_data),self.client,self.settings,self.stop)
        self.command.assert_called_with('/switch stable-key')
        markup=self.client.edit_message_text.call_args.kwargs['reply_markup']
        await self.bot.on_callback(self.callback(markup.inline_keyboard[1][2].callback_data),self.client,self.settings,self.stop)
        self.command.assert_called_with('/profile api-test')
        self.assertIn('Account selected: API',self.client.edit_message_text.call_args.args[0])

    async def test_active_state_updates_after_switch_and_api_ping_is_unavailable(self):
        self.command.side_effect=lambda command:self.choices[1].update(active=True)
        markup=await self.overview()
        await self.bot.on_callback(self.callback(markup.inline_keyboard[1][2].callback_data),self.client,self.settings,self.stop)
        markup=self.client.edit_message_text.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[1][2].text,'✓ Active')
        self.command.reset_mock()
        for button in markup.inline_keyboard[1]:
            await self.bot.on_callback(self.callback(button.callback_data),self.client,self.settings,self.stop)
        self.command.assert_not_called()

    async def test_access_checks_and_expired_buttons_prevent_operations(self):
        from aiogram.types import Message
        for change in ({'from':{'id':7,'is_bot':False,'first_name':'Other'}},
                       {'chat':{'id':-42,'type':'group'}},{'date':1}):
            await self.bot.on_message(Message.model_validate({**self.message_data,**change}),self.client,self.settings,self.stop)
        self.client.send_message.assert_not_called()
        markup=await self.overview()
        data=markup.inline_keyboard[0][2].callback_data
        await self.bot.on_callback(self.callback(data,user_id=7),self.client,self.settings,self.stop)
        await self.bot.on_callback(self.callback(data),self.client,{**self.settings,'token':'changed'},self.stop)
        key=data[3:]
        action=self.bot.actions[key]
        self.bot.actions[key]=(action[0],action[1]-121,*action[2:])
        await self.bot.on_callback(self.callback(data),self.client,self.settings,self.stop)
        self.command.assert_not_called()
        self.stop.set()
        await self.bot.on_callback(self.callback(data),self.client,self.settings,self.stop)
        self.command.assert_not_called()

    async def test_multiple_admins_have_access_but_buttons_are_bound_to_their_chat(self):
        from aiogram.types import CallbackQuery,Message
        self.settings['allowed_user_id']='42, 7'
        markup=await self.overview()
        owner_button=markup.inline_keyboard[0][2].callback_data
        data={**self.message_data,'from':{'id':7,'is_bot':False,'first_name':'Other admin'},
              'chat':{'id':7,'type':'private'}}
        await self.bot.on_message(Message.model_validate(data),self.client,self.settings,self.stop)
        admin_markup=self.client.send_message.call_args.kwargs['reply_markup']
        def callback(button):
            return CallbackQuery.model_validate({'id':'other','chat_instance':'private','message':data,
                       'from':data['from'],'data':button}).as_(self.client)
        await self.bot.on_callback(callback(owner_button),self.client,self.settings,self.stop)
        self.command.assert_not_called()
        await self.bot.on_callback(callback(admin_markup.inline_keyboard[0][2].callback_data),self.client,self.settings,self.stop)
        self.command.assert_called_once_with('/switch stable-key')
        self.command.reset_mock()
        self.settings['allowed_user_id']='42'
        await self.bot.on_callback(callback(admin_markup.inline_keyboard[0][1].callback_data),self.client,self.settings,self.stop)
        self.command.assert_not_called()

    async def test_refresh_button_fetches_usage_without_any_command(self):
        markup=await self.overview()
        await self.bot.on_callback(self.callback(markup.inline_keyboard[-1][0].callback_data),self.client,self.settings,self.stop)
        self.snapshot.assert_called_with(True)
        self.command.assert_not_called()

    async def test_empty_unknown_usage_and_failed_refresh_remain_usable(self):
        self.choices[0]['usage']={'refresh':{'status':'error'}}
        await self.overview()
        text=self.client.send_message.call_args.args[1]
        self.assertIn('5h: Unknown',text)
        self.assertIn('Refresh failed',text)
        self.choices.clear()
        markup=await self.overview()
        self.assertIn('No accounts saved',self.client.send_message.call_args.args[1])
        self.assertEqual(markup.inline_keyboard[0][0].callback_data,'refresh:0')

    async def test_large_lists_fit_telegram_limits_and_have_matching_rows(self):
        self.choices[:]=[{'id':str(i),'label':'Account '+str(i)+'x'*100,'kind':'account','email':'y'*100} for i in range(60)]
        markup=await self.overview()
        text=self.client.send_message.call_args.args[1]
        self.assertLessEqual(len(text.encode('utf-16-le'))//2,4096)
        account_rows=[row for row in markup.inline_keyboard if len(row)==3]
        self.assertGreater(len(account_rows),1)
        for row in account_rows:
            for button in row:self.assertLessEqual(len(button.callback_data.encode()),64)
        next_button=next(b for row in markup.inline_keyboard for b in row if b.text=='Next')
        await self.bot.on_callback(self.callback(next_button.callback_data),self.client,self.settings,self.stop)
        self.assertIn('Accounts · 2/',self.client.edit_message_text.call_args.args[0])

    async def test_operation_errors_do_not_disclose_credentials(self):
        markup=await self.overview()
        self.command.side_effect=RuntimeError('secret-output '+TOKEN)
        await self.bot.on_callback(self.callback(markup.inline_keyboard[0][1].callback_data),self.client,self.settings,self.stop)
        text=self.client.edit_message_text.call_args.args[0]
        self.assertIn('Operation failed',text)
        self.assertNotIn('secret-output',text)
        self.assertNotIn(TOKEN,text)

    async def test_dispatcher_routes_start_and_switch_and_closes_session(self):
        from aiogram.types import Update
        self.client.id=123456
        calls=0
        async def get_updates(**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:
                self.assertEqual(kwargs['offset'],-1)
                return [Update.model_validate({'update_id':1,'message':self.message_data})]
            if calls==2:return [Update.model_validate({'update_id':2,'message':self.message_data})]
            if calls==3:
                markup=self.client.send_message.call_args.kwargs['reply_markup']
                return [Update(update_id=3,callback_query=self.callback(markup.inline_keyboard[0][2].callback_data))]
            self.stop.set();return []
        self.client.get_updates.side_effect=get_updates
        with patch('aiogram.Bot',return_value=self.client):await self.bot.session(self.settings,self.stop)
        self.command.assert_called_once_with('/switch stable-key')
        self.client.session.close.assert_awaited_once()

    async def test_missing_dependency_reports_installation_hint(self):
        async def missing(settings,stop):raise ImportError('aiogram')
        async def sleep(seconds):
            self.assertIn('requirements-telegram.txt',self.bot.state);self.stop.set()
        with patch.object(self.bot,'session',side_effect=missing),patch('telegram_bot.asyncio.sleep',side_effect=sleep):
            await self.bot.run_async(self.stop)
        self.assertEqual(self.bot.state,'Stopped')
