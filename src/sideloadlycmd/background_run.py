"""One native CLI command in a finite, nonpersistent Background job."""
from __future__ import annotations
import json
import io
from contextlib import redirect_stdout,redirect_stderr
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
import uuid

if not __package__:
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    __package__='sideloadlycmd'

from .errors import CliError,ExitCode
from .private_state import write_json
from .xcode_backend import _run

ALLOWED={'sideload','teams','prepare','install-prepared','verify-prepared','refresh-register','refresh-native','refresh-status','native-doctor'}


def validate(arguments: list[str]) -> list[str]:
    args=list(arguments)
    if args and args[0]=='--': args=args[1:]
    if not args or args[0] not in ALLOWED:
        raise CliError('background-run accepts only native prepare, install-prepared, verify-prepared, refresh and doctor commands',ExitCode.USAGE,'background_command_not_allowed')
    from .cli import build_parser
    # Validate selectors and flags before persisting or starting anything.
    build_parser().parse_args(args)
    if '--json' not in args: args.append('--json')
    return args


def worker(config: Path):
    from .background_keychain import _security,_status
    data=json.loads(config.read_text())
    target=config.parent/'result.json'
    context={}
    try:
        context=_status(_security())
        if context.get('graphic_access') is not False or context.get('root_security_session') is not False:
            raise CliError('native command did not start in the requested Background security session',ExitCode.PREFLIGHT,'background_context_invalid')
        arguments=data['arguments']
        if arguments[0] in {'sideload','prepare','install-prepared','refresh-native'} and '--dry-run' not in arguments and context.get('unlocked') is not True:
            raise CliError('Background keychain is locked; run background-keychain unlock in a terminal first',ExitCode.NEEDS_USER,'background_keychain_locked')
        def cancelled(signum,frame): raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,cancelled)
        from .cli import main,build_parser
        parsed=build_parser().parse_args(arguments)
        if hasattr(parsed,'timeout'):
            remaining=min(parsed.timeout,data['deadline']-time.monotonic())
            if remaining<=0: raise CliError('Background command exceeded its total timeout',ExitCode.TIMEOUT,'timeout')
            arguments=[*arguments,'--timeout',str(remaining)]
        output,events=io.StringIO(),io.StringIO()
        # Execute in this isolated launchd worker, so SIGTERM reaches the native
        # CLI handler and unwinds its own subprocess-group cleanup.
        with redirect_stdout(output),redirect_stderr(events):
            returned=main(arguments)
        code=returned if returned in set(int(value) for value in ExitCode) else int(ExitCode.UNKNOWN)
        try:
            native=json.loads(output.getvalue())
            if not isinstance(native,dict): raise ValueError()
        except ValueError:
            native={'state':'failed','category':'native_reply_invalid','message':'native command did not return a structured result; no raw output was logged'}
            if code==0: code=int(ExitCode.UNKNOWN)
        # Unknown stderr is deliberately not persisted (it can contain argv).
        payload={'command':'background-run','state':'native_command_finished','native_command':arguments[0],
                 'exit_code':code,'result':native,'security_context':context,'message':native.get('message',native.get('state','native command finished'))}
    except (CliError,KeyboardInterrupt,OSError,ValueError,KeyError,TypeError) as error:
        code=int(error.code) if isinstance(error,CliError) else int(ExitCode.NEEDS_USER if isinstance(error,KeyboardInterrupt) else ExitCode.PREFLIGHT)
        payload={'command':'background-run','state':'failed','exit_code':code,
                 'category':error.category if isinstance(error,CliError) else 'background_worker_failed',
                 'message':error.message if isinstance(error,CliError) else 'Background command could not finish; inspect native install receipts before retrying',
                 'security_context':context}
    write_json(target,payload)


def run(arguments: list[str], timeout: float) -> tuple[dict,ExitCode]:
    args=validate(arguments)
    if sys.platform!='darwin':
        raise CliError('Background execution requires macOS launchd',ExitCode.PREFLIGHT,'macos_required')
    if timeout<=3:
        raise CliError('background-run timeout must exceed 3 seconds to reserve process cleanup',ExitCode.USAGE,'background_timeout_too_short')
    deadline=time.monotonic()+timeout
    label='org.sideloadlycmd.background-run.'+uuid.uuid4().hex
    domain='user/'+str(os.getuid())
    with tempfile.TemporaryDirectory(prefix='sideloadly-background-',dir='/private/tmp') as tmp:
        root=Path(tmp)
        config=root/'config.json';plist=root/'job.plist'
        write_json(config,{'arguments':args,'deadline':deadline-2})
        from .scheduler import NATIVE_PATH
        environment={'PATH':NATIVE_PATH}
        if os.environ.get('DEVELOPER_DIR'): environment['DEVELOPER_DIR']=os.environ['DEVELOPER_DIR']
        plist.write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':[sys.executable,'-I',str(Path(__file__).resolve()),'--worker',str(config)],
            'RunAtLoad':True,'LimitLoadToSessionType':'Background','ProcessType':'Background','Umask':0o077,
            'WorkingDirectory':str(Path.cwd()),'EnvironmentVariables':environment,
            'StandardOutPath':'/dev/null','StandardErrorPath':'/dev/null'}))
        os.chmod(plist,0o600)
        try:
            loaded=_run(['/bin/launchctl','bootstrap',domain,str(plist)],deadline-1)
            if loaded.returncode:
                raise CliError('could not start the one-shot Background command',ExitCode.PREFLIGHT,'background_bootstrap_failed')
            output=root/'result.json'
            while not output.exists():
                if time.monotonic()>=deadline-1:
                    raise CliError('Background command timed out; verify existing install receipts before retrying',ExitCode.TIMEOUT,'background_completion_unknown')
                time.sleep(min(.1,max(.001,deadline-1-time.monotonic())))
            result=json.loads(output.read_text())
        finally:
            # The worker and its child process groups handle SIGTERM. Reserve
            # cleanup time inside the public total deadline, not after it.
            remaining=max(.001,deadline-time.monotonic())
            try:
                subprocess.run(['/bin/launchctl','bootout',domain+'/'+label],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=remaining)
            except (OSError,subprocess.TimeoutExpired):
                pass
            remaining=max(.001,deadline-time.monotonic())
            try:
                check=subprocess.run(['/bin/launchctl','print',domain+'/'+label],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=remaining)
                removed=check.returncode!=0
            except (OSError,subprocess.TimeoutExpired):
                removed=False
        result['temporary_job_removed']=removed
        if not removed and result['exit_code']==0:
            result['exit_code']=int(ExitCode.PARTIAL)
            result['state']='native_finished_cleanup_unverified'
        return result,ExitCode(result['exit_code'])


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',required=True,type=Path)
    worker(parser.parse_args().worker)
