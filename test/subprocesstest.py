#
# -*- coding: utf-8 -*-
# Wireshark tests
# By Gerald Combs <gerald@wireshark.org>
#
# Ported from a set of Bash scripts which were copyright 2005 Ulf Lamping
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
'''Subprocess test case superclass'''

import config
import difflib
import io
import os
import os.path
import re
import subprocess
import sys
import unittest

# To do:
# - Add a subprocesstest.SkipUnlessCapture decorator?
# - Try to catch crashes? See the comments below in waitProcess.

# XXX This should probably be in config.py and settable from
# the command line.
process_timeout = 300 # Seconds

def capture_command(cmd, *args, **kwargs):
    '''Convert the supplied arguments into a command suitable for SubprocessTestCase.

    If shell is true, return a string. Otherwise, return a list of arguments.'''
    shell = kwargs.pop('shell', False)
    if shell:
        cap_cmd = ['"' + cmd + '"']
    else:
        cap_cmd = [cmd]
    if cmd == config.cmd_wireshark:
        cap_cmd += ('-o', 'gui.update.enabled:FALSE', '-k')
    cap_cmd += args
    if shell:
        return ' '.join(cap_cmd)
    else:
        return cap_cmd

def cat_dhcp_command(mode):
    '''Create a command string for dumping dhcp.pcap to stdout'''
    # XXX Do this in Python in a thread?
    sd_cmd = ''
    if sys.executable:
        sd_cmd = '"{}" '.format(sys.executable)
    sd_cmd += os.path.join(config.this_dir, 'util_dump_dhcp_pcap.py ' + mode)
    return sd_cmd

class LoggingPopen(subprocess.Popen):
    '''Run a process using subprocess.Popen. Capture and log its output.

    Stdout and stderr are captured to memory and decoded as UTF-8. The
    program command and output is written to log_fd.
    '''
    def __init__(self, proc_args, *args, **kwargs):
        self.log_fd = kwargs.pop('log_fd', None)
        kwargs['stdout'] = subprocess.PIPE
        kwargs['stderr'] = subprocess.PIPE
        # Make sure communicate() gives us bytes.
        kwargs['universal_newlines'] = False
        self.cmd_str = 'command ' + repr(proc_args)
        super().__init__(proc_args, *args, **kwargs)
        self.stdout_str = ''
        self.stderr_str = ''

    def wait_and_log(self):
        '''Wait for the process to finish and log its output.'''
        out_data, err_data = self.communicate(timeout=process_timeout)
        out_log = out_data.decode('UTF-8', 'replace')
        err_log = err_data.decode('UTF-8', 'replace')
        self.log_fd.flush()
        self.log_fd.write('-- Begin stdout for {} --\n'.format(self.cmd_str))
        self.log_fd.write(out_log)
        self.log_fd.write('-- End stdout for {} --\n'.format(self.cmd_str))
        self.log_fd.write('-- Begin stderr for {} --\n'.format(self.cmd_str))
        self.log_fd.write(err_log)
        self.log_fd.write('-- End stderr for {} --\n'.format(self.cmd_str))
        self.log_fd.flush()
        # Throwing a UnicodeDecodeError exception here is arguably a good thing.
        self.stdout_str = out_data.decode('UTF-8', 'strict')
        self.stderr_str = err_data.decode('UTF-8', 'strict')

    def stop_process(self, kill=False):
        '''Stop the process immediately.'''
        if kill:
            super().kill()
        else:
            super().terminate()

    def terminate(self):
        '''Terminate the process. Do not log its output.'''
        # XXX Currently unused.
        self.stop_process(kill=False)

    def kill(self):
        '''Kill the process. Do not log its output.'''
        self.stop_process(kill=True)

class SubprocessTestCase(unittest.TestCase):
    '''Run a program and gather its stdout and stderr.'''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exit_ok = 0
        self.exit_command_line = 1
        self.exit_error = 2
        self.exit_code = None
        self.log_fname = None
        self.log_fd = None
        self.processes = []
        self.cleanup_files = []
        self.dump_files = []

    def log_fd_write_bytes(self, log_data):
        self.log_fd.write(log_data)

    def filename_from_id(self, filename):
        '''Generate a filename prefixed with our test ID.'''
        id_filename = self.id() + '.' + filename
        if id_filename not in self.cleanup_files:
            self.cleanup_files.append(id_filename)
        return id_filename

    def kill_processes(self):
        '''Kill any processes we've opened so far'''
        for proc in self.processes:
            try:
                proc.kill()
            except:
                pass

    def _error_count(self, result):
        if not result:
            return 0
        if hasattr(result, 'failures'):
            # Python standard unittest runner
            return len(result.failures) + len(result.errors)
        if hasattr(result, '_excinfo'):
            # pytest test runner
            return len(result._excinfo or [])
        self.fail("Unexpected test result %r" % result)

    def run(self, result=None):
        # Subclass run() so that we can do the following:
        # - Open our log file and add it to the cleanup list.
        # - Check our result before and after the run so that we can tell
        #   if the current test was successful.

        # Probably not needed, but shouldn't hurt.
        self.kill_processes()
        self.processes = []
        self.log_fname = self.filename_from_id('log')
        # Our command line utilities generate UTF-8. The log file endcoding
        # needs to match that.
        # XXX newline='\n' works for now, but we might have to do more work
        # to handle line endings in the future.
        self.log_fd = io.open(self.log_fname, 'w', encoding='UTF-8', newline='\n')
        self.cleanup_files.append(self.log_fname)
        pre_run_problem_count = self._error_count(result)
        try:
            super().run(result=result)
        except KeyboardInterrupt:
            # XXX This doesn't seem to work on Windows, which is where we need it the most.
            self.kill_processes()

        # Tear down our test. We don't do this in tearDown() because Python 3
        # updates "result" after calling tearDown().
        self.kill_processes()
        self.log_fd.close()
        if result:
            post_run_problem_count = self._error_count(result)
            if pre_run_problem_count != post_run_problem_count:
                self.dump_files.append(self.log_fname)
                # Leave some evidence behind.
                self.cleanup_files = []
                print('\nProcess output for {}:'.format(self.id()))
                with io.open(self.log_fname, 'r', encoding='UTF-8', errors='backslashreplace') as log_fd:
                    for line in log_fd:
                        sys.stdout.write(line)
        for filename in self.cleanup_files:
            try:
                os.unlink(filename)
            except OSError:
                pass
        self.cleanup_files = []

    def getCaptureInfo(self, capinfos_args=None, cap_file=None):
        '''Run capinfos on a capture file and log its output.

        capinfos_args must be a sequence.
        Default cap_file is <test id>.testout.pcap.'''
        if not cap_file:
            cap_file = self.filename_from_id('testout.pcap')
        self.log_fd.write('\nOutput of {0} {1}:\n'.format(config.cmd_capinfos, cap_file))
        capinfos_cmd = [config.cmd_capinfos]
        if capinfos_args is not None:
            capinfos_cmd += capinfos_args
        capinfos_cmd.append(cap_file)
        capinfos_data = subprocess.check_output(capinfos_cmd)
        capinfos_stdout = capinfos_data.decode('UTF-8', 'replace')
        self.log_fd.write(capinfos_stdout)
        return capinfos_stdout

    def checkPacketCount(self, num_packets, cap_file=None):
        '''Make sure a capture file contains a specific number of packets.'''
        got_num_packets = False
        capinfos_testout = self.getCaptureInfo(cap_file=cap_file)
        count_pat = r'Number of packets:\s+{}'.format(num_packets)
        if re.search(count_pat, capinfos_testout):
            got_num_packets = True
        self.assertTrue(got_num_packets, 'Failed to capture exactly {} packets'.format(num_packets))

    def countOutput(self, search_pat=None, count_stdout=True, count_stderr=False, proc=None):
        '''Returns the number of output lines (search_pat=None), otherwise returns a match count.'''
        match_count = 0
        self.assertTrue(count_stdout or count_stderr, 'No output to count.')

        if proc is None:
            proc = self.processes[-1]

        out_data = ''
        if count_stdout:
            out_data = proc.stdout_str
        if count_stderr:
            out_data += proc.stderr_str

        if search_pat is None:
            return len(out_data.splitlines())

        search_re = re.compile(search_pat)
        for line in out_data.splitlines():
            if search_re.search(line):
                match_count += 1

        return match_count

    def grepOutput(self, search_pat, proc=None):
        return self.countOutput(search_pat, count_stderr=True, proc=proc) > 0

    def diffOutput(self, blob_a, blob_b, *args, **kwargs):
        '''Check for differences between blob_a and blob_b. Return False and log a unified diff if they differ.

        blob_a and blob_b must be UTF-8 strings.'''
        lines_a = blob_a.splitlines()
        lines_b = blob_b.splitlines()
        diff = '\n'.join(list(difflib.unified_diff(lines_a, lines_b, *args, **kwargs)))
        if len(diff) > 0:
            self.log_fd.flush()
            self.log_fd.write('-- Begin diff output --\n')
            self.log_fd.writelines(diff)
            self.log_fd.write('-- End diff output --\n')
            return False
        return True

    def startProcess(self, proc_args, stdin=None, env=None, shell=False):
        '''Start a process in the background. Returns a subprocess.Popen object.

        You typically wait for it using waitProcess() or assertWaitProcess().'''
        if env is None:
            # Avoid using the test user's real environment by default.
            env = config.test_env
        proc = LoggingPopen(proc_args, stdin=stdin, env=env, shell=shell, log_fd=self.log_fd)
        self.processes.append(proc)
        return proc

    def waitProcess(self, process):
        '''Wait for a process to finish.'''
        process.wait_and_log()
        # XXX The shell version ran processes using a script called run_and_catch_crashes
        # which looked for core dumps and printed stack traces if found. We might want
        # to do something similar here. This may not be easy on modern Ubuntu systems,
        # which default to using Apport: https://wiki.ubuntu.com/Apport

    def assertWaitProcess(self, process, expected_return=0):
        '''Wait for a process to finish and check its exit code.'''
        process.wait_and_log()
        self.assertEqual(process.returncode, expected_return)

    def runProcess(self, args, env=None, shell=False):
        '''Start a process and wait for it to finish.'''
        process = self.startProcess(args, env=env, shell=shell)
        process.wait_and_log()
        return process

    def assertRun(self, args, env=None, shell=False, expected_return=0):
        '''Start a process and wait for it to finish. Check its return code.'''
        process = self.runProcess(args, env=env, shell=shell)
        self.assertEqual(process.returncode, expected_return)
        return process
