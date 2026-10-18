"""Configurable restarts use the same persisted commands from CLI and HTTP."""
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch, call

import app
from test_management import IsolatedManager


class RestartCommandTests(IsolatedManager):
    def test_cli_executes_configured_commands_against_only_the_selected_target(self):
        report=self.root/'restart-order.txt'
        def command(text):
            script=f"from pathlib import Path; p=Path({str(report)!r}); p.open('a').write({text!r})"
            return shlex.join([sys.executable,'-c',script])
        app.set_restart_commands({'app':[command('app first\n'),command('app second\n')]})
        self.assertFalse(report.exists())
        result=self.cli('restart','--service','app')
        self.assertEqual(report.read_text(),'app first\napp second\n')
        self.assertIn('exit code: 0',result['output'])
        self.cli('restart','--service','all')
        self.assertEqual(report.read_text(),'app first\napp second\napp first\napp second\n')

    def test_cli_persists_commands_privately_without_executing_and_restores_defaults(self):
        commands={'app':['echo "app with spaces"','echo status']}
        with patch('app.run_cmd') as run:
            app.set_restart_commands(commands)
            run.assert_not_called()
        path=self.fields_file(commands)
        self.cli('config','restart-commands','--file',path)
        self.assertEqual(self.cli('config','show')['restart_commands'],commands)
        self.assertEqual(self.config.stat().st_mode & 0o777,0o600)
        self.cli('config','restart-commands','--defaults')
        self.assertNotIn('restart_commands',app.read_webui_config())
        self.assertEqual(self.cli('config','show')['restart_commands'],app.default_restart_commands())

    def test_restart_uses_saved_commands_in_order_and_preserves_quoted_arguments(self):
        commands={'app':['echo "app with spaces"','echo status']}
        app.set_restart_commands(commands)
        with patch('app.run_cmd',return_value=subprocess.CompletedProcess([],0,'done','')) as run:
            result=app.restart_services()
        self.assertEqual(run.call_args_list,[call(['echo','app with spaces'],60),call(['echo','status'],60)])
        self.assertIn('done',result['output'])
        with patch('app.run_cmd') as run:
            with self.assertRaisesRegex(RuntimeError,'Unknown restart target'):app.restart_services(('codex-web',))
            run.assert_not_called()

    def test_failure_stops_subsequent_commands_and_reports_exit_and_output(self):
        app.set_restart_commands({'app':['echo first','echo failing','echo never']})
        with patch('app.run_cmd',side_effect=[subprocess.CompletedProcess([],0,'first',''),subprocess.CompletedProcess([],7,'','failed')]) as run:
            with self.assertRaises(app.ServiceCommandError) as caught:app.restart_services()
        self.assertEqual(run.call_count,2)
        self.assertIn('exit code: 7',caught.exception.output)
        self.assertIn('failed',caught.exception.output)

    def test_timeout_or_missing_executable_is_reported_with_previous_output(self):
        app.set_restart_commands({'app':['echo first','missing-command']})
        for failure in (FileNotFoundError('missing-command'),subprocess.TimeoutExpired(['missing-command'],60)):
            with self.subTest(failure=failure),patch('app.run_cmd',side_effect=[subprocess.CompletedProcess([],0,'completed first',''),failure]):
                with self.assertRaises(app.ServiceCommandError) as caught:app.restart_services()
                self.assertIn('completed first',caught.exception.output)
                self.assertIn('missing-command',caught.exception.output)

    def test_invalid_configuration_never_changes_saved_settings(self):
        saved=app.read_webui_config()
        for invalid in ([],{}, {'app':[]}, {'app':['echo "broken']},
                        {'app':['echo\nnext']},{'app':['echo\x00oops']},
                        {'app':['echo ok']*11},{'app':['""']},
                        {'app':['echo ok'],'extra':[]}):
            with self.subTest(invalid=invalid),self.assertRaises(RuntimeError):app.set_restart_commands(invalid)
            self.assertEqual(app.read_webui_config(),saved)

    def test_shell_operators_are_literal_unless_shell_is_explicit(self):
        app.set_restart_commands({'app':['echo hello && ignored',"bash -lc 'echo one && echo two'"]})
        with patch('app.run_cmd',return_value=subprocess.CompletedProcess([],0,'','')) as run:app.restart_services()
        self.assertEqual(run.call_args_list,[call(['echo','hello','&&','ignored'],60),call(['bash','-lc','echo one && echo two'],60)])

    def test_legacy_web_commands_are_never_executed_or_returned(self):
        config=app.read_webui_config()
        config['restart_commands']={'app':['echo app'], 'web':['echo must-not-run']}
        app.write_webui_config(config)
        self.assertEqual(app.settings_status()['restart_commands'],{'app':['echo app']})
        with patch('app.run_cmd',return_value=subprocess.CompletedProcess([],0,'','')) as run:
            app.restart_services()
        run.assert_called_once_with(['echo','app'],60)
        with self.assertRaises(RuntimeError):app.set_restart_commands(config['restart_commands'])

    def test_default_commands_match_the_requested_app_restart(self):
        self.assertEqual(app.default_restart_commands(),{'app':[
            'systemctl restart codex-app-server',
            'codex app-server daemon restart']})
